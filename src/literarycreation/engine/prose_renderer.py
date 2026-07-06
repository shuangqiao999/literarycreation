"""Phase 5 (literary): Prose Renderer — 将创作素材渲染为小说/剧本正文。

输入种子文本、角色最终状态、关键事件序列与叙事风格，
调用 LLM 生成散文/剧本；LLM 失败时降级为结构化摘要。
"""
from __future__ import annotations

import logging
from typing import Any

from ._utils import extract_text

logger = logging.getLogger(__name__)

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
- 直接输出本章正文，不要输出章节标题、大纲或任何解释说明。"""


async def _retry_prose(client, prompt, story_context, target_words, chapter_idx):
    """LLM prose generation with up to 3 retries at decreasing complexity."""
    from literarycreation.core.llm_client import Message

    strategies = [
        (prompt, "你是文学作家，逐章创作小说/剧本正文，文笔细腻、承接自然。", 0.85),
        (prompt.replace(f"本章篇幅约 {target_words} 字",
                         f"本章篇幅约 {max(target_words // 2, 500)} 字"),
         "你是文学作家，输出小说正文。", 0.5),
        (f"【当前剧情进度】\n{story_context[:800]}\n\n请用300字续写，推进主线剧情：",
         "你是一位作家，输出小说正文。", 0.3),
    ]

    last_error = None
    for idx, (p, sys, temp) in enumerate(strategies):
        try:
            resp = await client().chat([Message(role="user", content=p)],
                                       system=sys, temperature=temp)
            text = extract_text(resp).strip()
            if text and len(text) > 80:
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

    context = "\n".join(parts)
    return context[:1000]


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
    ) -> str:
        """逐章生成：生成第 chapter_idx 章正文。

        story_context: 累积的剧情摘要，注入 prompt 提供跨章连贯性。
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
        events_txt = "\n".join(f"- {p}" for p in ev_parts) or "（承接前文自然推进）"

        length_req = (f"本章篇幅约 {target_words} 字（可上下浮动 15%）。"
                      if target_words and target_words > 0 else "本章篇幅不少于 2000 字。")

        # 第4章起种子文本缩减为文笔参考，避免重复开头场景
        effective_seed = (seed_text or "（无参考原文）")[:2000]
        if chapter_idx > 3:
            effective_seed = (seed_text or "")[:200] + "\n...（前文从略，仅作文笔参考）"

        prompt = _CHAPTER_PROMPT.format(
            idx=chapter_idx, total=total_chapters, style=self.style,
            seed_text=effective_seed,
            prev_tail=(prev_tail or "（本章为开篇）")[-600:],
            states=states_txt, events=events_txt,
            outline_block=(_OUTLINE_BLOCK if outline_event else ""),
            length_req=length_req,
        )
        # 在 prompt 中注入累积剧情上下文
        if story_context:
            prompt = story_context + "\n\n" + prompt

        try:
            text = await _retry_prose(self._client, prompt, story_context, target_words, chapter_idx)
            return text
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
