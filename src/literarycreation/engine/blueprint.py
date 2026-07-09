"""Phase 1.6: Story Blueprint Generation — LLM 从前提生成结构化故事大纲。

产出一份 StoryBlueprint（单一脊柱 + 唯一麦高芬 + 揭示节奏 + POV 计划 +
角色弧光 + 逐章 beat + 关键事件），供下游 blueline 模式严格执行。

设计要点：
  - 仅在会话未提供 outline（或缺 key_events）时调用；已有则尊重人工大纲。
  - 生成失败/结构非法时返回 None，调用方安全降级回 freeform 自由续写。
  - 输出结构向后兼容 EventScheduler.from_outline（characters / key_events）。
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from ._utils import extract_text

logger = logging.getLogger(__name__)


_PROMPT = """你是一位资深小说结构编辑。请依据下面的创作素材与配置，产出一份用于指导长篇写作的\
【结构化故事大纲】，严格输出 JSON。

## 配置
- {style_directive}
- 计划章数：{chapters}
- 目标总字数：{target_words}
{domain_metrics}

## 创作素材（前提/设定/已有片段）
{source}

## 输出 JSON 格式（只输出 JSON，不要解释）
```json
{{
  "logline": "一句话主线，锁定单一故事脊柱",
  "detected_style": "素材原生风格（从 现实主义/浪漫主义/悬疑/史诗/宫廷剧 中选最接近的一项，反映素材本身，与目标风格无关）",
  "established_facts": {{"deaths": [{{"name": "师父", "before_story": true, "note": "开场前已被刺杀"}}], "timeline": ["开场前：师父遇害"]}},
  "pov": {{"mode": "single", "anchor": "主角姓名"}},
  "macguffins": [
    {{"id": "核心物件名", "desc": "它是什么、为何重要", "reveal_round": 9,
      "states": ["前期：下落不明", "中期：确认存在", "高潮：被取得"]}}
  ],
  "reveal_schedule": [
    {{"round": 1, "reveals": "本章只能揭示到——是什么（案件/困境的表象）"}}
  ],
  "characters": [
    {{"name": "姓名", "persona": "性格与说话方式", "arc": "从X到Y的转变",
      "first_appearance": 1, "last_appearance": 8, "exit": "退场方式（如：掩护主角撤退时被围，生死不明）",
      "initial_state": {{"tension": 50, "mystery": 70}}, "final_state": {{"tension": 20, "mystery": 10}}}}
  ],
  "chapters": [
    {{"round": 1, "goal": "本章目标", "solves": "解决的旧谜", "hook": "抛出的新钩子",
      "scene_seeds": ["驿站正堂的灯光在风雪中忽明忽暗", "暗格边缘有一道新鲜的划痕", "灶台上残留着未烧尽的纸灰"]}}
  ],
  "key_events": [
    {{"round": 1, "event": "本章必须发生的关键事件", "level": "hard"}}
  ]
}}
```

## 硬性规则
1. 只能有【一个】核心麦高芬（macguffins 至多 1 项），它只在 reveal_round 被完整取得/揭晓一次，前面章节只允许"接近/侧写"，不得重复"发现同一物件"。
2. pov.mode 默认 "single"，anchor 为唯一主角；除非素材明确要求多线，否则不要用 multi。
3. reveal_schedule 必须逐层递进：先"是什么"，再"手法/如何"，再"为什么"，再"是谁"，最后"对决/结局"，严禁提前泄底。
4. key_events 覆盖每一章（round 从 1 到 {chapters}），每章 1-2 条，level 取 hard/soft/optional。
5. characters 中主角与主要反派必须各有清晰弧光；反派须在中段章节登场，不能只被提及；每个重要配角都要给出 first_appearance/last_appearance/exit，保证有明确的登场与退场（不得"用完即弃"）。
6. chapters 覆盖每一章，goal/solves/hook 三者具体、互不重复；每章 scene_seeds 提供 3-5 个【具体可感的场景素材】（环境、光线、声音、气味、物件纹理等感官细节），避免空泛情节。
7. 所有 round 为 1..{chapters} 的整数。
8. initial_state 和 final_state 必须是上面列出的指标名到 0-100 浮点数的映射，严禁使用自然语言描述或空对象。
9. detected_style 必须客观反映创作素材本身的风格，不受目标风格影响。
10. established_facts.deaths 必须列出【在故事开始前或种子材料中已经死亡】的角色（如师父），before_story=true 表示故事开始前已死；这些角色全程不得以活人身份行动。
11. 只返回 JSON。"""


def _get_metrics_hint(domain: str) -> str:
    """从规则包提取领域指标名和范围，构建供 LLM 提示使用的格式说明。"""
    try:
        from .rule_engine import RuleEngine
        engine = RuleEngine.from_domain(domain)
        metrics = engine.metrics()
        if not metrics:
            return ""
        labels: dict[str, str] = {
            "trust": "信任", "tension": "张力", "affection": "情感",
            "power": "权力", "mystery": "悬疑", "fatigue": "疲惫",
        }
        lines = [f"{m}({labels.get(m, m)} 0-100)" for m in metrics]
        lines.append("initial_state/final_state 必须使用上述指标名作为键（0-100 浮点数），严禁使用自然语言描述或空对象")
        return "- 角色状态指标:\n  " + ", ".join(lines)
    except Exception:
        return ""


async def generate_blueprint(
    source_material: str,
    *,
    domain: str = "literary_realism",
    total_rounds: int = 10,
    target_words: int = 0,
    style_mode: str = "manual",
    target_style: str = "",
    log_fn: Any = None,
) -> dict[str, Any] | None:
    """调用 LLM 生成 StoryBlueprint；失败返回 None（调用方降级 freeform）。

    style_mode="auto" 时按素材自动判定风格；"manual" 时以 target_style 为目标风格构建。
    无论哪种模式都会输出 detected_style（素材原生风格），供风格迁移使用。
    """
    from literarycreation.core.llm_client import DeductionLLMClient, Message

    def _log(msg: str) -> None:
        if log_fn:
            import contextlib
            with contextlib.suppress(Exception):
                log_fn("blueprint", msg)

    if style_mode == "auto":
        style_directive = "风格：请根据下面的创作素材自动判定其自然风格并采用，不要套用任何预设题材。"
    else:
        _t = target_style or "现实主义"
        style_directive = (f"目标风格：「{_t}」。即使素材原生风格与之不同，也必须按「{_t}」"
                           f"风格构建大纲与写作（后续章节会逐步向该风格迁移）。")

    prompt = _PROMPT.format(
        style_directive=style_directive,
        chapters=max(1, int(total_rounds or 1)),
        target_words=target_words or "不限",
        domain_metrics=_get_metrics_hint(domain),
        source=(source_material or "（无素材）")[:8000],
    )
    client = DeductionLLMClient()
    try:
        resp = await client.chat(
            [Message(role="user", content=prompt)],
            system="你是小说结构编辑，只输出规范 JSON 大纲。",
            temperature=0.4,
            max_tokens=8192,
        )
        raw = extract_text(resp)
    except Exception as e:  # noqa: BLE001
        logger.warning("[Blueprint] LLM 生成失败: %s", e)
        _log(f"大纲生成失败，降级自由续写: {e}")
        return None

    blueprint = _parse_blueprint(raw, total_rounds)
    if blueprint is None:
        _log("大纲解析失败或结构非法，降级自由续写")
        return None
    _style_info = (f"自动→采用素材风格「{blueprint.get('detected_style') or '—'}」"
                   if style_mode == "auto"
                   else f"目标风格「{target_style or '—'}」/素材风格「{blueprint.get('detected_style') or '—'}」")
    _log(
        f"故事大纲已生成：{len(blueprint.get('key_events', []))} 个关键事件、"
        f"{len(blueprint.get('characters', []))} 个角色、"
        f"麦高芬「{(blueprint.get('macguffins') or [{}])[0].get('id', '—')}」、"
        f"POV={blueprint.get('pov', {}).get('mode', 'single')}、{_style_info}"
    )
    return blueprint


def _parse_blueprint(raw: str, total_rounds: int) -> dict[str, Any] | None:
    """从 LLM 原始输出解析并规范化 blueprint；结构非法返回 None。"""
    match = re.search(r"\{[\s\S]*\}", raw or "")
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        # 容错：尝试去除尾随逗号
        cleaned = re.sub(r",\s*([}\]])", r"\1", match.group(0))
        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            return None
    if not isinstance(data, dict):
        return None
    return normalize_blueprint(data, total_rounds)


def normalize_blueprint(data: dict[str, Any], total_rounds: int) -> dict[str, Any] | None:
    """规范化 blueprint 结构，补默认值并裁剪越界项。key_events 为空则视为非法。"""
    n = max(1, int(total_rounds or 1))

    def _clamp_round(v: Any) -> int:
        try:
            r = int(v)
        except (TypeError, ValueError):
            return 1
        return max(1, min(n, r))

    # ── pov ──
    pov_raw = data.get("pov") or {}
    if not isinstance(pov_raw, dict):
        pov_raw = {}
    mode = str(pov_raw.get("mode", "single")).strip().lower()
    if mode not in ("single", "multi"):
        mode = "single"
    pov = {"mode": mode, "anchor": str(pov_raw.get("anchor", "") or "").strip()}

    # ── macguffins (至多 1) ──
    macguffins: list[dict[str, Any]] = []
    for m in (data.get("macguffins") or [])[:1]:
        if not isinstance(m, dict):
            continue
        mid = str(m.get("id", "") or "").strip()
        if not mid:
            continue
        macguffins.append({
            "id": mid,
            "desc": str(m.get("desc", "") or "").strip(),
            "reveal_round": _clamp_round(m.get("reveal_round", n)),
            "states": [str(s) for s in (m.get("states") or []) if str(s).strip()],
        })

    # ── reveal_schedule ──
    reveal_schedule: list[dict[str, Any]] = []
    for e in data.get("reveal_schedule") or []:
        if not isinstance(e, dict):
            continue
        reveals = str(e.get("reveals", e.get("reveal", "")) or "").strip()
        if not reveals:
            continue
        reveal_schedule.append({"round": _clamp_round(e.get("round", 1)), "reveals": reveals})

    # ── characters ──
    characters: list[dict[str, Any]] = []
    for c in data.get("characters") or []:
        if not isinstance(c, dict):
            continue
        name = str(c.get("name", "") or "").strip()
        if not name:
            continue
        entry: dict[str, Any] = {
            "name": name,
            "persona": str(c.get("persona", "") or "").strip(),
            "arc": str(c.get("arc", "") or "").strip(),
        }
        if c.get("first_appearance") is not None:
            entry["first_appearance"] = _clamp_round(c.get("first_appearance"))
        if c.get("last_appearance") is not None:
            entry["last_appearance"] = _clamp_round(c.get("last_appearance"))
        if str(c.get("exit", "") or "").strip():
            entry["exit"] = str(c["exit"]).strip()
        if isinstance(c.get("initial_state"), dict):
            entry["initial_state"] = dict(c["initial_state"])
        if isinstance(c.get("final_state"), dict):
            entry["final_state"] = dict(c["final_state"])
        characters.append(entry)

    # ── chapters ──
    chapters: list[dict[str, Any]] = []
    for ch in data.get("chapters") or []:
        if not isinstance(ch, dict):
            continue
        chapters.append({
            "round": _clamp_round(ch.get("round", 1)),
            "goal": str(ch.get("goal", "") or "").strip(),
            "solves": str(ch.get("solves", "") or "").strip(),
            "hook": str(ch.get("hook", "") or "").strip(),
            "scene_seeds": [str(s).strip() for s in (ch.get("scene_seeds") or []) if str(s).strip()][:5],
        })

    # ── key_events (必需) ──
    key_events: list[dict[str, Any]] = []
    for e in data.get("key_events") or []:
        if not isinstance(e, dict):
            continue
        desc = str(e.get("event", e.get("description", "")) or "").strip()
        if not desc:
            continue
        level = str(e.get("level", "hard")).strip().lower()
        if level not in ("hard", "soft", "optional"):
            level = "hard"
        ev: dict[str, Any] = {"round": _clamp_round(e.get("round", 1)), "event": desc, "level": level}
        if isinstance(e.get("required_outcome"), dict):
            ev["required_outcome"] = e["required_outcome"]
        key_events.append(ev)

    if not key_events:
        # 无关键事件 → blueline 无法执行，视为非法
        return None

    # ── established_facts (预设既定事实：故事前已死角色等) ──
    ef_raw = data.get("established_facts") or {}
    ef_deaths: list[dict[str, Any]] = []
    if isinstance(ef_raw, dict):
        for d in ef_raw.get("deaths") or []:
            if not isinstance(d, dict):
                continue
            nm = str(d.get("name", "") or "").strip()
            if not nm:
                continue
            ef_deaths.append({
                "name": nm,
                "before_story": bool(d.get("before_story", True)),
                "note": str(d.get("note", "") or "").strip(),
            })
    ef_timeline = [str(t).strip() for t in (ef_raw.get("timeline") or []) if str(t).strip()] \
        if isinstance(ef_raw, dict) else []
    established_facts = {"deaths": ef_deaths, "timeline": ef_timeline}

    return {
        "logline": str(data.get("logline", "") or "").strip(),
        "detected_style": str(data.get("detected_style", "") or "").strip(),
        "established_facts": established_facts,
        "pov": pov,
        "macguffins": macguffins,
        "reveal_schedule": sorted(reveal_schedule, key=lambda x: x["round"]),
        "characters": characters,
        "chapters": sorted(chapters, key=lambda x: x["round"]),
        "key_events": sorted(key_events, key=lambda x: x["round"]),
    }
