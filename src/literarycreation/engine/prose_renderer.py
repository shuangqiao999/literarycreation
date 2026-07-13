"""Phase 5 (literary): Prose Renderer — 将创作素材渲染为小说/剧本正文。

输入种子文本、角色最终状态、关键事件序列与叙事风格，
调用 LLM 生成散文/剧本；LLM 失败时降级为结构化摘要。
"""
from __future__ import annotations

import logging
from typing import Any

from ._utils import extract_text

logger = logging.getLogger(__name__)

# P4: 重复段落检测 — 检测连续相同句子数 >3 或 50字以上段落重复出现
def _detect_repetition(text: str) -> bool:
    sentences = [s.strip() for s in text.replace("\n", " ").split("。") if len(s.strip()) > 10]
    if len(sentences) < 5:
        return False
    seen = set()
    repeat_count = 0
    for s in sentences:
        short = s[:40]  # 前40字做指纹
        if short in seen:
            repeat_count += 1
        else:
            repeat_count = 0
            seen.add(short)
        if repeat_count >= 3:
            return True
    return False

# ── P5: 叙事阶段动态匹配 ──

NARRATIVE_PHASES: list[tuple[float, str, str]] = [
    (0.15, "setup",
     "本阶段建立世界观和核心悬疑。每章解决一个小问题，但引出更大的问题。避免所有线索一次性抛出——让读者保持好奇心。"
     "可用配角短暂POV（1-3段）引入故事的另一面，然后切回主角视角。"),
    (0.35, "rising",
     "引入更多线索和角色。每个配角都应有自己的动机，而非推动剧情的工具。节奏适度加快，场景之间可以有小幅跳跃。"
     "减少冗长的内心独白，用具体的行动和对话推进。"),
    (0.50, "midpoint",
     "关键转折点——主角对事件的认知应被颠覆。此前建立的假设至少有一条要被推翻。"
     "一个意外的发现、一次背叛、或一个隐藏的真相浮出水面。"),
    (0.70, "escalation",
     "外部压力加剧，主角处境恶化。多条线索开始交汇。信息不对称开始收束——此前分散的线索要逐渐产生联系。"
     "配角立场要明确——有人站到主角一边，有人背离。"),
    (0.85, "climax",
     "高潮铺垫——每个配角都要表明最终立场。场景张力持续上升，对话应比行动更有力量。"
     "主角面临不可逆的抉择。文笔在此处可以更凝练、更有力量感。"),
    (1.00, "resolution",
     "终章阶段。不必收束所有线索——文学作品的力量在于留白。"
     "用意象、场景描写或意味深长的对话收束，而非逐一解释。最后一个场景的余韵比结局本身更重要。"),
]


def get_technique(chapter_idx: int, total_chapters: int, allow_pov_switch: bool = True) -> str:
    """根据章号在总章数中的位置比例，动态匹配叙事阶段指导。

    allow_pov_switch=False（单一主角视角）时，抑制一切"切换视角"类建议。
    """
    fraction = chapter_idx / max(1, total_chapters)
    for limit, label, text in NARRATIVE_PHASES:
        if fraction <= limit:
            technique = f"[当前阶段：{label}，第{chapter_idx}/{total_chapters}章] {text}"
            # 长篇章(≥15章)在中间区域每3章注入多样性提示
            if (allow_pov_switch and total_chapters >= 15
                    and 0.35 <= fraction <= 0.65 and chapter_idx % 3 == 0):
                technique += "\n【多样性提示】本章尝试切换场景或视角，避免与前2章相同的叙事结构。"
            if not allow_pov_switch:
                technique += ("\n【视角约束】本作为单一主角视角，忽略上文任何"
                              "“切换/配角视角”的建议；如需换气，用场景切换而非视角切换。")
            return technique
    return ""


def build_pov_text(outline: dict[str, Any] | None) -> str:
    """构建 POV 约束块。默认单一主角贴身视角。"""
    if not outline:
        return ""
    pov = outline.get("pov") or {}
    if not isinstance(pov, dict):
        return ""
    mode = str(pov.get("mode", "single")).lower()
    anchor = str(pov.get("anchor", "") or "").strip()
    if mode == "single" and anchor:
        return (f"【视角锁定 — 全程第三人称贴身跟随主角「{anchor}」，"
                f"不得切入其他角色的内心视角，不得用分节切换到他人独立视角】")
    if mode == "single":
        return "【视角锁定 — 全程单一主角第三人称贴身视角，不得切换到其他角色的内心视角】"
    return ""


def pov_allows_switch(outline: dict[str, Any] | None) -> bool:
    """是否允许配角/多视角切换（默认否——单一视角）。"""
    if not outline:
        return True  # 无大纲时维持原有自由行为
    pov = outline.get("pov") or {}
    if not isinstance(pov, dict):
        return True
    return str(pov.get("mode", "single")).lower() == "multi"


def build_reveal_text(outline: dict[str, Any] | None, chapter_idx: int) -> str:
    """按揭示节奏表，构建本章"信息层级"约束，防止提前泄底。"""
    if not outline:
        return ""
    sched = outline.get("reveal_schedule") or []
    cur = None
    for e in sched:
        if not isinstance(e, dict):
            continue
        try:
            if int(e.get("round", 0)) == chapter_idx:
                cur = e
                break
        except (TypeError, ValueError):
            continue
    if cur is None:
        return ""
    reveals = str(cur.get("reveals", cur.get("reveal", "")) or "").strip()
    if not reveals:
        return ""
    return (f"【本章揭示层级 — 本章只能揭示到：{reveals}。"
            f"严禁提前泄露后续章节才应揭晓的真相】")


def build_scene_seeds_text(outline: dict[str, Any] | None, chapter_idx: int) -> str:
    """取本章场景种子，构建"可用素材"提示块（供 LLM 自由选用，丰富描写）。"""
    if not outline:
        return ""
    for ch in outline.get("chapters") or []:
        if not isinstance(ch, dict):
            continue
        try:
            if int(ch.get("round", 0)) == chapter_idx:
                seeds = [str(s).strip() for s in (ch.get("scene_seeds") or []) if str(s).strip()]
                if seeds:
                    return ("【本章可用场景素材（可选取以丰富描写，不必全用，也可自行拓展）】\n"
                            + "\n".join(f"- {s}" for s in seeds))
                return ""
        except (TypeError, ValueError):
            continue
    return ""


def build_style_migration(detected: str, target: str, chapter_idx: int, total_chapters: int) -> str:
    """手选风格与素材原生风格冲突时，逐章渐进迁移。

    迁移在 ~70% 章处收敛到 100%（末段稳定贴合目标，避免只在末章突变）。
    """
    detected = (detected or "").strip()
    target = (target or "").strip()
    if not detected or not target or detected == target:
        return ""
    import math
    converge_at = max(1, math.ceil(total_chapters * 0.7))
    ratio = min(1.0, chapter_idx / converge_at)
    pct = int(round(ratio * 100))
    if pct >= 100:
        return (f"【风格过渡】本作最终风格为「{target}」。本章应已完全贴合「{target}」，"
                f"不再保留「{detected}」的痕迹。")
    return (f"【风格过渡】本作目标风格为「{target}」，素材原生风格偏「{detected}」。"
            f"本章为第{chapter_idx}/{total_chapters}章，过渡进度约 {pct}%：在保留「{detected}」质感的"
            f"基础上，让语气、意象、节奏向「{target}」倾斜约 {pct}%；越往后越贴近「{target}」，收尾完全贴合。")


# ── P6: 短语追踪 ──

def track_repeated_phrases(text: str, top_n: int = 5) -> list[str]:
    """提取高频4字短语用于注入避重提示。"""
    import re
    # 按标点和空格分段，避免跨句匹配
    segments = re.split(r'[，。；：、！？\n\r]', text)
    all_phrases: list[str] = []
    for seg in segments:
        seg = seg.strip()
        if len(seg) < 4:
            continue
        # 滑动窗口提取4字短语
        for i in range(len(seg) - 3):
            all_phrases.append(seg[i:i+4])
    if not all_phrases:
        return []
    from collections import Counter
    c = Counter(all_phrases)
    return [p for p, cnt in c.most_common(top_n) if cnt >= 2]


def build_phrase_hint(story_state: dict) -> str:
    """从累积文本中获取高频短语并构建避重提示。"""
    all_phrases = story_state.get("tracked_phrases", [])
    parts = []
    if all_phrases:
        parts.append("[避免重复以下短语] " + "、".join(all_phrases[:5]))
    # 整句/箴言避重
    rep_sents = story_state.get("repeated_sentences", [])
    if rep_sents:
        parts.append("[以下句子已多次出现，严禁再原样使用，请换新表达] "
                     + "；".join(s[:30] for s in rep_sents[:5]))
    return "\n".join(parts)


def track_repeated_sentences(text: str, threshold: int = 2) -> list[str]:
    """检测本章内完全相同的完整句子（长度>15字，出现≥threshold次）。"""
    from collections import Counter
    sentences = [s.strip() for s in text.replace("\n", " ").split("。") if len(s.strip()) > 15]
    counter = Counter(sentences)
    return [s for s, cnt in counter.items() if cnt >= threshold]


def update_repeated_sentences(story_state: dict, text: str, cross_threshold: int = 2) -> None:
    """跨章累计整句出现次数，把出现≥cross_threshold章的句子写入 repeated_sentences。

    解决"活着的人不会走死人的路"跨多章重复沦为口头禅的问题。
    """
    import re
    counts: dict[str, int] = story_state.setdefault("sentence_counts", {})
    seen_this_ch: set[str] = set()
    for s in re.split(r"[。！？\n]", text):
        s = s.strip()
        if len(s) < 8 or s in seen_this_ch:
            continue
        seen_this_ch.add(s)
        counts[s] = counts.get(s, 0) + 1
    # 内存上限：超过 400 条时丢弃只出现 1 次的
    if len(counts) > 400:
        for k in [k for k, v in counts.items() if v <= 1]:
            del counts[k]
    story_state["repeated_sentences"] = sorted(
        [s for s, c in counts.items() if c >= cross_threshold],
        key=lambda s: -counts[s])[:8]

# Style is now configured per rule pack, not hardcoded
_DEFAULT_STYLE = "现实主义"

_PROSE_PROMPT = """你是一位资深文学作家。请根据以下创作素材，续写或复现一部小说/剧本的正文。

【叙事风格】{style}

【原文风格参考（仅供文笔与语气参考）】
{seed_text}

【角色设定与最终状态】
{character_summary}

【关键事件序列（按章节）】
{events_summary}
{outline_block}
写作要求：
- 以小说/剧本正文格式输出，包含场景描写、人物对话、内心独白。
- 人物言行须与其设定和状态变化一致，情感/信任/张力的演变要在文字中自然体现。
- 逻辑自洽，情节连贯，呼应关键事件序列。
- 直接输出正文，不要输出解释、大纲或标题以外的说明。
- 篇幅不少于 3000 字。"""

_OUTLINE_BLOCK = """
【提纲约束（必须遵守）】
关键事件必须在文本中按序发生；各角色须沿其设定的弧光（初始→最终状态）演变。
"""

_CHAPTER_PROMPT = """你是一位资深文学作家，正在逐章创作一部小说/剧本。请写出【第{idx}章 / 共{total}章】的正文。

【叙事风格】{style}

【全书文笔与语气参考】
{seed_text}

【上一章结尾（本章需自然承接）】
{prev_tail}

【本章角色状态】
{states}

【本章应展开的情节与事件】
{events}
{outline_block}
写作要求：
- 只输出【第{idx}章】的正文，包含场景描写、人物对话、内心独白。
- 自然承接上一章结尾，与全书风格一致；人物言行须与其状态变化吻合。
- {length_req}
- 【展示，不要告知】禁止'他感到愤怒''她非常悲伤'类抽象情感陈述。情绪必须通过具体的动作和感官细节传达：'她把茶杯捏到指节发白'（愤怒）、'他盯着窗外，不敢眨眼睛'（恐惧）、'他的声音低得像桌底的灰尘'（疲惫）。
- 【时间感】不要用'几个小时过去''第二天一早'等抽象时间词。用物理世界的细节标记时间流逝：阳光从东窗移到西窗、茶已凉了三遍、街上的脚步声从密到疏、蜡烛燃掉了三分之一。
- 直接输出本章正文，不要输出章节标题、大纲或任何解释说明。"""

_SKELETON_PROMPT = """你是一位小说结构师。将以下情节素材拼接为一段连贯的叙事骨架。

【叙事风格】{style}

【上一章结尾】{prev_tail}

【本章情节素材】
{events}

## 要求
- 输出一段 400-800 字的精简叙事流，用动作和因果推动，不要细节描写。
- 每一段用一句话写出核心动作：谁在什么情境下做了什么、产生了什么后果。
- 用"→"连接因果："阿米娜发现证据 → 她决定在议会上公开 → 拉扎尔当场否认"。
- 保留所有关键转折和情感节拍，但不要场景描写、不要对话展开、不要氛围渲染。
- 只输出骨架叙事流，不要章节标题、不要解释。"""


def _check_dialogue_style(text: str, agents: list, chapter_idx: int) -> list[str]:
    """检测每个角色的对话是否符合其 speech_style 约束。返回违规列表。"""
    import re as _re
    violations: list[str] = []
    for a in agents:
        ss = getattr(a, "speech_style", "")
        if not ss:
            continue
        name_esc = _re.escape(a.name)
        pattern = _re.compile(
            rf'{name_esc}(?:道|说|问道|喊道|低声道|答道|笑道|喝道|开口)[：:]\s*(.+?)(?=(?:{name_esc}|[""」]|\n\n|$))',
            _re.DOTALL,
        )
        matches = pattern.findall(text)
        for line in matches:
            word_count = len(line.replace(" ", "").replace("\n", ""))
            if "10字" in ss and word_count > 20:
                violations.append(
                    f"第{chapter_idx}章: {a.name} 一条对话 {word_count} 字 "
                    f"超出语言风格约束（{ss}）")
            if "反问" in ss and "？" not in line:
                violations.append(
                    f"第{chapter_idx}章: {a.name} 应使用反问句但未使用（{ss}）")
            if "敬语" in ss and word_count < 5:
                violations.append(
                    f"第{chapter_idx}章: {a.name} 对话过短（{word_count}字），"
                    f"与敬语习惯冲突（{ss}）")
    return violations


def _check_chapter_hook(text: str) -> str | None:
    """检测章尾300字是否包含钩子元素。无钩子返回建议文本，有钩子返回None。"""
    tail = text[-300:]
    markers = [
        ("？", "以一个问题收尾——一个角色或读者都无法立即回答的问题"),
        ("突然", "以突发事件打断——角色正要做什么时意外发生了"),
        ("决定", "以一个不可逆的决定收尾——'从今天起'不如'从现在起'"),
        ("却", "以矛盾或反转收束——前面建立的东西在最后一句被颠覆了"),
        ("从未", "以揭示收束——一个悬而未决的线索浮出水面"),
        ("等着", "以期待收尾——角色或事件在等待某个时刻的到来"),
    ]
    for marker, advice in markers:
        if marker in tail:
            return None
    return ("【章尾钩子缺失】本章结尾缺乏吸引读者翻页的力量。" + advice + "。请在下一章的开篇以回响方式填补这一缺失。")


def _analyze_rhythm(text: str) -> dict[str, float]:
    """分析本章节奏：动作密度 vs 反思密度。"""
    sentences = [s.strip() for s in text.replace("\n","").split("。") if len(s.strip()) > 5]
    if not sentences:
        return {"action_ratio": 0, "reflect_ratio": 0}
    action_verbs = ("跑","冲","打","杀","推","拉","拔","跳","躲","追","逃","闯",
                    "抓","扔","砸","砍","刺","撞","扑","摔","抽","射")
    reflect_words = ("想","觉得","感到","回忆起","曾经","如果","或许","也许",
                     "仿佛","似乎","大概","可能","应该","必然","注定")
    act = sum(1 for s in sentences if any(v in s for v in action_verbs))
    ref = sum(1 for s in sentences if any(v in s for v in reflect_words))
    n = len(sentences)
    return {"action_ratio": round(act/n, 3), "reflect_ratio": round(ref/n, 3)}


def _compute_chapter_weight(text: str) -> float:
    """计算情节重量：关键动作密度 × 转折密度。"""
    sentences = [s.strip() for s in text.replace("\n","").split("。") if len(s.strip()) > 10]
    if not sentences:
        return 0.0
    pivot_words = ("原来","终于","竟然","却是","并非","突然","没想到","不料","发现","决定")
    action_words = ("杀","死","打","冲","救","逃","抓","背叛","告白","揭露","闯入")
    scores = sum(1 for s in sentences if any(w in s for w in pivot_words))
    actions = sum(1 for s in sentences if any(w in s for w in action_words))
    return round((scores * 2 + actions) / max(1, len(sentences)), 3)


async def _generate_skeleton(client, style: str, prev_tail: str,
                              events_txt: str, target_words: int) -> str:
    """生成叙事骨架：将事件列表转化为精简的因果叙事流。"""
    from literarycreation.core.llm_client import Message
    try:
        p = _SKELETON_PROMPT.format(
            style=style, prev_tail=(prev_tail or "（开篇）")[-400:],
            events=events_txt[:2000],
        )
        kwargs = {}
        if target_words and target_words > 0:
            kwargs["max_tokens"] = int(target_words * 1.2)
        resp = await client().chat(
            [Message(role="user", content=p)],
            system="你是小说结构师，输出精简叙事骨架。",
            temperature=0.5, **kwargs,
        )
        text = extract_text(resp).strip()
        if text and len(text) > 80:
            return text[:1200]
    except Exception:
        pass
    return ""


async def _retry_prose(client, prompt, story_context, target_words, chapter_idx):
    """LLM prose generation with up to 3 retries at decreasing complexity."""
    from literarycreation.core.llm_client import Message

    # 中文约 1.5-2 tokens/字，取 2 倍 + 20% 冗余
    max_tok_full = int(target_words * 2.4) if target_words and target_words > 0 else 0
    max_tok_half = max(2048, int(target_words * 1.2)) if target_words and target_words > 0 else 0

    strategies = [
        (prompt, "你是文学作家，逐章创作小说/剧本正文，文笔细腻、承接自然。",
         0.85, max_tok_full),
        (prompt.replace(f"本章篇幅约 {target_words} 字",
                         f"本章篇幅约 {max(target_words // 2, 500)} 字"),
         "你是文学作家，输出小说正文。", 0.5, max_tok_half),
        (f"【当前剧情进度】\n{story_context[:800]}\n\n请用300字续写，推进主线剧情：",
         "你是一位作家，输出小说正文。", 0.3, 800),
    ]

    last_error = None
    for idx, (p, sys, temp, max_tok) in enumerate(strategies):
        try:
            kwargs = {"max_tokens": max_tok} if max_tok else {}
            resp = await client().chat([Message(role="user", content=p)],
                                       system=sys, temperature=temp, **kwargs)
            text = extract_text(resp).strip()
            if text and len(text) > 80:
                # P4: 重复段落检测 — 超过50字完全相同视为退化输出
                if _detect_repetition(text) and idx < len(strategies) - 1:
                    logger.info("[ProseRenderer] 第%d章第%d次重试检测到重复，跳过", chapter_idx, idx + 1)
                    continue
                if idx > 0:
                    logger.info("[ProseRenderer] 第%d章在第%d次重试成功", chapter_idx, idx + 1)
                return text
        except Exception as e:
            last_error = e
            continue

    raise last_error or ValueError(f"empty chapter after 3 retries")


# ── 故事状态追踪 ──

SIGNIFICANT_CHANGE = {
    "trust": 20, "tension": 10, "affection": 15,
    "power": 15, "mystery": 10, "fatigue": 15,
}


def extract_synopsis(text: str, max_chars: int = 250) -> str:
    """优先提取含关键动作或对话的句子作为章节概要。"""
    sentences = text.replace("\n", " ").split("。")
    scored: list[tuple[int, str]] = []
    for s in sentences:
        s = s.strip()
        if not s or len(s) < 8:
            continue
        score = 0
        if any(v in s for v in ("说道", "喊道", "问道", "质问", "答")):
            score += 3
        if any(v in s for v in ("发现", "找到", "得知", "推断", "决定", "冲向", "推开", "拔出", "潜入", "闯", "杀", "死", "救")):
            score += 2
        if any(v in s for v in ("原来", "终于", "竟然", "却是", "并非")):
            score += 1
        scored.append((score, s))
    scored.sort(key=lambda x: -x[0])
    result = "。".join(s[1] for s in scored[:5] if s[1])
    return result[:max_chars]


def build_story_context(story_state: dict, chapter_idx: int) -> str:
    """分级注入：最近3章详细 + 更早概括。控制在 ~900 字以内。"""
    recent: list[str] = []
    older: list[str] = []
    for line in story_state.get("summary", "").split("\n"):
        if not line.strip():
            continue
        # Extract chapter number from line like "第3章概要：..."
        try:
            cid = int(line.split("第")[1].split("章")[0]) if "章" in line else 999
        except (IndexError, ValueError):
            cid = 999
        if cid >= chapter_idx - 3 and cid < chapter_idx:
            recent.append(line[:150])
        elif cid < chapter_idx:
            older.append(line[:30])

    parts = ["## 全书剧情进度"]
    if older:
        parts.append(f"前{chapter_idx-3}章概要：{'；'.join(older)}")
    if recent:
        parts.append("\n## 最近章节进展\n" + "\n".join(recent))
    if story_state.get("open_threads"):
        parts.append("\n## 待推进悬念\n" + "\n".join(
            f"- {t}" for t in story_state["open_threads"][:3]))
    # 上一章的文学质检提示
    hook_hint = story_state.pop("next_chapter_hint", "")
    if hook_hint:
        parts.append(f"\n## {hook_hint}")
    rhythm_hint = story_state.pop("rhythm_hint", "")
    if rhythm_hint:
        parts.append(f"\n【节奏建议】{rhythm_hint}")
    weight_hint = story_state.pop("weight_hint", "")
    if weight_hint:
        parts.append(f"\n【重量建议】{weight_hint}")
    # 支线进度提醒
    subplots = story_state.get("subplots") or []
    for sp in subplots:
        beats = sp.get("beats") or []
        next_beat = None
        for b in beats:
            try:
                if int(b.get("round", 0)) == chapter_idx:
                    next_beat = b
                    break
            except (TypeError, ValueError):
                pass
        if next_beat:
            parts.append(f"【支线—{sp.get('name','')}】本章推进支线节拍：{next_beat.get('beat','')}")
        elif chapter_idx >= int(sp.get("starts_at",999)) and chapter_idx <= int(sp.get("resolves_at",0)):
            # 支线活跃期但无节拍——提醒保持存在感
            last_beat_round = max((int(b.get("round",0)) for b in beats if int(b.get("round",0)) < chapter_idx), default=0)
            if chapter_idx - last_beat_round > 3:
                parts.append(f"【支线—{sp.get('name','')}】支线已 {chapter_idx-last_beat_round} 章未推进，请在本章以侧写方式重提——哪怕只是一个细节或一句对话")
    # 主题回响检测
    themes = story_state.get("themes") or []
    apper = story_state.get("theme_appearances", {})
    for t in themes:
        name = t.get("name", "")
        recent_count = sum(int(apper.get(f"{chapter_idx-d}_{name}", 0)) for d in range(1, 4))
        if recent_count == 0 and chapter_idx > 3:
            parts.append(f"【主题回响 — 已连续3章未触及主题'{name}'。"
                         f"请在合适的时机让一个场景或对话自然地呼应这一主题。】")

    context = "\n".join(parts)
    return context[:1200]


def append_chapter_summary(story_state: dict, chapter_idx: int,
                           prose_text: str, snapshots: dict) -> None:
    """从刚生成的章节中提取摘要，更新全局故事状态。"""
    synopsis = extract_synopsis(prose_text)
    story_state.setdefault("summary", "")
    story_state["summary"] += f"\n第{chapter_idx}章概要：{synopsis}"

    story_state.setdefault("alive_characters", {})
    for eid, st in (snapshots or {}).items():
        name = st.get("name", eid)
        metrics = st.get("metrics", {})
        prev = story_state["alive_characters"].get(name, {})
        changes = []
        for k, v in metrics.items():
            old = float(prev.get(k, v))
            threshold = SIGNIFICANT_CHANGE.get(k, 15)
            if abs(v - old) > threshold:
                direction = "↑" if v > old else "↓"
                changes.append(f"{k}{direction}{abs(v-old):.0f}")
        if changes:
            story_state["summary"] += (
                f"\n  第{chapter_idx}章后{name}状态变化：{', '.join(changes)}")
        story_state["alive_characters"][name] = {k: float(v) for k, v in metrics.items()}

    # 追踪未解决悬念 (上限3条)
    story_state.setdefault("open_threads", [])
    if "？" in prose_text:
        for seg in prose_text.split("？"):
            seg = seg.strip()
            if not seg:
                continue
            q = seg[-60:] + "？"
            if any(k in q for k in ("真相", "谁", "为什么", "如何", "究竟", "难道")):
                if q not in story_state["open_threads"] and q not in story_state.get("resolved_plots", []):
                    story_state["open_threads"].append(q)
    while len(story_state["open_threads"]) > 3:
        story_state["open_threads"].pop(0)
    # 已解决清除
    story_state.setdefault("resolved_plots", [])
    for q in story_state["open_threads"][:]:
        keyword = q.replace("？", "").replace("谁", "").replace("为什么", "")
        if keyword.strip() and keyword in prose_text:
            story_state["open_threads"].remove(q)
            story_state["resolved_plots"].append(f"✓ {q} → 本章已解答")


# ── P0: 文本指纹去重 ──

def fingerprint_text(text: str, top_n: int = 5) -> list[str]:
    """提取关键句的短哈希作为文本指纹，用于跨章去重检测。"""
    sentences = [s.strip() for s in text.replace("\n", " ").split("。") if len(s.strip()) > 15]
    scored = []
    for s in sentences:
        score = 0
        if any(v in s for v in ("决定", "发现", "原来", "终于", "必须", "不能")):
            score += 1
        scored.append((score, s))
    scored.sort(key=lambda x: -x[0])
    import hashlib
    return [hashlib.md5(s[1].encode()).hexdigest()[:8] for s in scored[:top_n]]


def build_anti_repeat_context(story_state: dict) -> str:
    """检查已用文本指纹，构建防重复警告。"""
    used = story_state.get("used_fingerprints", [])
    used_scenes = story_state.get("used_scenes", "")
    if not used and not used_scenes:
        return ""
    parts = ["【禁止重复 — 以下内容已在前面章节出现过，请勿再次写出相同或高度相似的场景】"]
    if used_scenes:
        parts.append(used_scenes)
    return "\n".join(parts)


# ── P3: 风格守卫 ──

STYLE_GUARDS: dict[str, str] = {
    "悬疑": "保持克制的叙事风格。避免超自然元素（如迷魂阵、墨色虚空、神识碎片）。"
           "所有现象必须有现实逻辑解释。主角的推理应基于物证和观察，而非灵力感知。",
    "史诗": "使用宏大叙事视角，描写要有命运感、史诗感。"
          "战斗描写可以激烈但避免修仙式打斗，保持历史的厚重感。",
    "现实主义": "强调真实可信。角色行为要有心理依据，社会背景要符合史实。"
              "避免巧合和天降奇兵，所有转折应有铺垫。",
    "浪漫主义": "情感描写可以浓烈，场景可以有诗意化的处理。"
              "角色之间的情感纠葛是推动主线的重要力量。",
    "宫廷剧": "聚焦权力博弈。对话要有潜台词，每个角色的言行都要考虑政治后果。"
            "宫廷礼仪和等级制度要严格遵守。",
}


class ProseRenderer:
    """散文渲染器：量化推演结果 → 文学正文。"""

    def __init__(self, llm_client: Any = None, style: str = "现实主义") -> None:
        self._llm = llm_client
        self.style = style or _DEFAULT_STYLE

    def _client(self) -> Any:
        if self._llm is None:
            from literarycreation.core.llm_client import DeductionLLMClient
            self._llm = DeductionLLMClient()
        return self._llm

    async def render_chapter(
        self,
        *,
        chapter_idx: int,
        total_chapters: int,
        seed_text: str,
        round_events: list[str],
        round_narration: str,
        round_states: dict[str, Any],
        prev_tail: str,
        outline_event: str = "",
        target_words: int = 0,
        chapter_context: Any = None,
        story_context: str = "",
        style_anchors: str = "",
    ) -> str:
        """逐章生成：生成第 chapter_idx 章正文。

        story_context: 累积的剧情摘要，注入 prompt 提供跨章连贯性。
        style_anchors: LanceDB 检索的原文风格锚点片段。
        """
        states_txt = "\n".join(
            f"- {v.get('name', k)}：" + "，".join(
                f"{mk}={float(mv):.0f}" for mk, mv in (v.get("metrics") or {}).items())
            for k, v in (round_states or {}).items()
        ) or "（无角色状态）"
        ev_parts = [str(e)[:160] for e in (round_events or []) if e]
        if round_narration:
            ev_parts.insert(0, f"本轮概述：{round_narration[:200]}")

        # 优先使用结构化 ChapterContext
        ctx_block = ""
        if chapter_context is not None:
            ctx_parts = []
            phase = getattr(chapter_context, "narrative_phase", "")
            if phase:
                ctx_parts.append(f"叙事阶段: {phase}")
            mand = getattr(chapter_context, "mandatory_events", [])
            if mand:
                ctx_parts.append("【本章必须推动】" + "；".join(
                    e.get("description", "") for e in mand))
            soft = getattr(chapter_context, "soft_goals", [])
            if soft:
                ctx_parts.append("【本章建议推动】" + "；".join(
                    e.get("description", "") for e in soft))
            snap = getattr(chapter_context, "character_snapshots", {})
            if snap:
                snap_lines = []
                for name, ms in snap.items():
                    snap_lines.append(f"  {name}：" + "，".join(f"{k}={v:.0f}" for k, v in ms.items()))
                ctx_parts.append("角色当前状态：\n" + "\n".join(snap_lines))
            ctx_block = "\n".join(ctx_parts)
        elif outline_event:
            ctx_block = f"【本章必须推动】{outline_event}"

        if ctx_block:
            ev_parts.insert(0, ctx_block)

        # 从事件中提取叙事片段素材，优先作为场景骨架
        scene_fragments: list[str] = []
        for e in round_events:
            if "[场景]" in e:
                frag = e.split("[场景]", 1)[-1].strip()[:150]
                if frag:
                    scene_fragments.append(frag)
            elif "[内心]" in e:
                frag = e.split("[内心]", 1)[-1].strip()[:80]
                if frag:
                    scene_fragments.append(f"（内心）{frag}")
        if len(scene_fragments) >= 2:
            scene_block = (
                "【以下为已确定的场景素材——请按时间线拼接并进行文学性润色，"
                "不得修改关键事实，但可以丰富场景描写、对话活动和心理活动】\n"
                + "\n".join(f"{j+1}. {s}" for j, s in enumerate(scene_fragments))
            )
            ev_parts.insert(0, scene_block)

        events_txt = "\n".join(f"- {p}" for p in ev_parts) or "（承接前文自然推进）"

        # 阶段A：生成叙事骨架（将事件列表转化为精简因果叙事流）
        skeleton = ""
        if chapter_idx > 1:
            skeleton = await _generate_skeleton(
                self._client, self.style, prev_tail, events_txt, target_words // 4)
        if skeleton:
            events_txt = ("【叙事骨架 — 以下为本章的情节结构，请基于此骨架扩写为完整的文学章节，"
                          "不要偏离骨架的关键事件和因果关系】\n" + skeleton + "\n\n"
                          + "【原始素材参考】\n" + events_txt)

        length_req = (f"本章篇幅约 {target_words} 字（可上下浮动 15%）。"
                      if target_words and target_words > 0 else "本章篇幅不少于 2000 字。")

        # 第4章起种子文本缩减为文笔参考，避免重复开头场景
        effective_seed = (seed_text or "（无参考原文）")[:2000]
        if chapter_idx > 3:
            effective_seed = (seed_text or "")[:200] + "\n...（前文从略，仅作文笔参考）"

        style_guard = STYLE_GUARDS.get(self.style, "")

        def _compose(length_req_text: str, seg_prev_tail: str,
                     seg_directive: str, seed_for_prompt: str) -> str:
            p = _CHAPTER_PROMPT.format(
                idx=chapter_idx, total=total_chapters, style=self.style,
                seed_text=seed_for_prompt,
                prev_tail=(seg_prev_tail or "（本章为开篇）")[-600:],
                states=states_txt, events=events_txt,
                outline_block=(_OUTLINE_BLOCK if outline_event else ""),
                length_req=length_req_text,
            )
            ctx = story_context
            if seg_directive:
                ctx = (seg_directive + "\n\n" + ctx) if ctx else seg_directive
            if ctx:
                p = ctx + "\n\n" + p
            if style_guard:
                p = f"【风格约束 — 确保本章符合{self.style}风格要求】\n{style_guard}\n\n{p}"
            if style_anchors:
                p = style_anchors + "\n\n" + p
            return p

        try:
            _SEG_SIZE = 2500
            if target_words and target_words > _SEG_SIZE:
                # 分段生成：每段约 2500 字，逐段承接拼接，稳定逼近目标字数
                import math
                k = min(8, max(2, math.ceil(target_words / _SEG_SIZE)))
                seg_target = max(800, target_words // k)
                parts: list[str] = []
                seg_tail = prev_tail
                for s in range(1, k + 1):
                    if s == 1:
                        directive = (f"【分段写作】本章共分 {k} 段，现在写【第 1 段】（约 {seg_target} 字）："
                                     f"从本章开头写起，只写本章约前 1/{k}，自然停在可续接处，本段不要收尾。")
                        seed_for = effective_seed
                    else:
                        tail_role = "本段需推进并【收束本章】。" if s == k else "只写本章中间的一部分，自然停在可续接处。"
                        directive = (f"【分段写作】本章共分 {k} 段，现在写【第 {s} 段】（约 {seg_target} 字）："
                                     f"紧接下方【上文结尾】继续写，严禁重复已写内容、严禁从头开场。{tail_role}")
                        seed_for = (seed_text or "")[:150] + "\n...（仅作文笔参考，勿复述开头）"
                    p = _compose(f"本段篇幅约 {seg_target} 字。", seg_tail, directive, seed_for)
                    seg_text = await _retry_prose(self._client, p, story_context, seg_target, chapter_idx)
                    if seg_text and "正文生成失败" not in seg_text[:20]:
                        parts.append(seg_text.strip())
                        seg_tail = seg_text[-400:]
                text = "\n\n".join(parts)
                if not text:
                    raise ValueError("segmented render produced empty text")
                logger.info("[ProseRenderer] 第%d章分 %d 段生成，合计 %d 字", chapter_idx, k, len(text))
                return text

            prompt = _compose(length_req, prev_tail, "", effective_seed)
            return await _retry_prose(self._client, prompt, story_context, target_words, chapter_idx)
        except Exception as e:  # noqa: BLE001
            logger.warning("[ProseRenderer] 第%d章生成失败，降级摘要: %s", chapter_idx, e)
            lines = [f"【第{chapter_idx}章 · 正文生成失败，以下为本章摘要】", "", "角色状态：", states_txt, "", "情节：", events_txt]
            return "\n".join(lines)

    async def render(
        self,
        seed_text: str,
        final_states: dict[str, Any],
        events: list[dict[str, Any]],
        characters: list[dict[str, Any]],
        outline: dict[str, Any] | None = None,
    ) -> str:
        prompt = self._build_prompt(seed_text, final_states, events, characters, outline)
        client = self._llm
        if client is None:
            from literarycreation.core.llm_client import DeductionLLMClient
            client = DeductionLLMClient()
        try:
            from literarycreation.core.llm_client import Message
            resp = await client.chat(
                [Message(role="user", content=prompt)],
                system="你是文学作家，输出小说/剧本正文，文笔细腻、情节连贯。",
                temperature=0.8,
                max_tokens=32768,
            )
            text = extract_text(resp).strip()
            if not text:
                raise ValueError("empty prose")
            return text
        except Exception as e:  # noqa: BLE001
            logger.warning("[ProseRenderer] LLM 生成失败，降级为结构化摘要: %s", e)
            return self._fallback(final_states, events, characters)

    # ── prompt 组装 ──
    def _build_prompt(
        self,
        seed_text: str,
        final_states: dict[str, Any],
        events: list[dict[str, Any]],
        characters: list[dict[str, Any]],
        outline: dict[str, Any] | None,
    ) -> str:
        char_lines: list[str] = []
        for c in characters or []:
            name = c.get("name", "?")
            persona = c.get("persona", "") or c.get("arc", "")
            metrics = c.get("metrics") or {}
            mtxt = "，".join(f"{k}={float(v):.0f}" for k, v in metrics.items())
            char_lines.append(f"- {name}：{persona[:80]}（最终状态：{mtxt}）")
        character_summary = "\n".join(char_lines) or "（无角色数据）"

        ev_lines: list[str] = []
        for e in events or []:
            rnd = e.get("round", "?")
            desc = e.get("content") or e.get("description") or e.get("event") or ""
            if desc:
                ev_lines.append(f"第{rnd}轮：{str(desc)[:120]}")
        events_summary = "\n".join(ev_lines[-40:]) or "（无事件记录）"

        outline_block = ""
        if outline and (outline.get("key_events") or outline.get("characters")):
            outline_block = _OUTLINE_BLOCK

        return _PROSE_PROMPT.format(
            style=self.style,
            seed_text=(seed_text or "（无参考原文）")[:3000],
            character_summary=character_summary,
            events_summary=events_summary,
            outline_block=outline_block,
        )

    # ── 降级模板 ──
    @staticmethod
    def _fallback(
        final_states: dict[str, Any],
        events: list[dict[str, Any]],
        characters: list[dict[str, Any]],
    ) -> str:
        parts: list[str] = ["【文学正文生成失败，以下为结构化摘要】", "", "一、角色最终状态"]
        for c in characters or []:
            metrics = c.get("metrics") or {}
            mtxt = "，".join(f"{k}={float(v):.0f}" for k, v in metrics.items())
            parts.append(f"- {c.get('name','?')}：{mtxt}")
        if not characters and final_states:
            for _eid, s in final_states.items():
                metrics = s.get("metrics", {}) if isinstance(s, dict) else {}
                mtxt = "，".join(f"{k}={float(v):.0f}" for k, v in metrics.items())
                parts.append(f"- {s.get('name','?') if isinstance(s, dict) else _eid}：{mtxt}")
        parts += ["", "二、关键事件序列"]
        for e in events or []:
            rnd = e.get("round", "?")
            desc = e.get("content") or e.get("description") or e.get("event") or ""
            if desc:
                parts.append(f"第{rnd}轮：{str(desc)[:120]}")
        return "\n".join(parts)
