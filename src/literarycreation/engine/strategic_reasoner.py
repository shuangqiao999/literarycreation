"""Reasoner — LLM决策推理，叙事导向。

角色基于人格、记忆、弧光自然推理行动，无预定义动作约束。
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


def _metrics_to_narrative(state: Any) -> str:
    """将数值指标转换为叙事语言，让 LLM 理解角色的情感状态。"""
    m = state.metrics if hasattr(state, "metrics") else {}
    lines = [f"{state.name if hasattr(state, 'name') else '角色'}当前的内心状态："]

    trust = m.get("trust", 50)
    if trust < 20:
        lines.append("- 你对身边的人几乎完全失去了信任")
    elif trust < 40:
        lines.append("- 你对大多数人心存戒备，只信任极少数亲近之人")
    elif trust > 80:
        lines.append("- 你对周围的人抱有深厚的信任")

    tension = m.get("tension", 20)
    if tension > 70:
        lines.append("- 局势已经紧张到了极点，冲突一触即发")
    elif tension > 40:
        lines.append("- 空气中弥漫着不安，你感到冲突在逼近")
    elif tension < 15:
        lines.append("- 眼下局势相对平静，但暗流涌动")

    affection = m.get("affection", 40)
    if affection > 70:
        lines.append("- 你内心深处涌动着强烈的情感，渴望向某人表达")
    elif affection < 25:
        lines.append("- 你的情感正在冷却，对周围人越来越疏远")

    power = m.get("power", 40)
    if power > 70:
        lines.append("- 你手中握有相当的影响力，可以改变局势")
    elif power < 25:
        lines.append("- 你感到自己的力量正在流失，处境被动")

    mystery = m.get("mystery", 30)
    if mystery > 70:
        lines.append("- 真相仍然隐藏在迷雾之中，你渴望揭开它")
    elif mystery < 20:
        lines.append("- 谜团所剩无几，真相即将大白")

    fatigue = m.get("fatigue", 10)
    if fatigue > 70:
        lines.append("- 你已经筋疲力尽，每一步都沉重无比")
    elif fatigue > 40:
        lines.append("- 疲惫开始侵蚀你的判断，你需要喘息")
    elif fatigue < 15:
        lines.append("- 你精神尚可，仍有行动的余力")

    return "\n".join(lines)


class StrategicReasoner:
    """LLM-based decision maker for literary creation.

    Two modes:
      - reason_quantified(): Freeform writing — agents decide freely
      - reason_narrative(): Blueprint execution — agents follow scheduled events
    """

    def __init__(
        self,
        chat_fn: Any = None,
        preprocessor: Any = None,
        immutable_goals: list[str] | None = None,
    ) -> None:
        self._chat_fn = chat_fn
        self._preprocessor = preprocessor
        self._immutable_goals = immutable_goals or []

    async def reason_quantified(
        self,
        *,
        agent: Any,
        round_number: int,
        state: Any,
        other_context: str = "",
        relationship_context: str = "",
        static_knowledge: str = "",
        dynamic_memory: str = "",
        recent_events: str = "",
        env_context: str = "",
        user_cmd: str = "",
        self_memory: str = "",
    ) -> dict[str, Any]:
        """自由创作：角色基于人格和处境，自由决定下一步行动。"""
        goals = agent.goals if hasattr(agent, "goals") else []
        goals_txt = "\n".join(f"- {g}" for g in goals) if goals else "（无具体目标）"
        imm = "；".join(self._immutable_goals) if self._immutable_goals else "无"
        narrative_state = _metrics_to_narrative(state)

        header = (
            f"你是「{agent.name}」。\n\n"
            f"## 你的人格\n{agent.persona or '（无）'}\n\n"
            f"## 你的目标\n{goals_txt}\n\n"
            f"## 你当前的状态\n{narrative_state}\n"
        )
        if self_memory:
            header += f"\n## 你最近做过的事\n{self_memory}\n"
        if user_cmd:
            header += f"\n## 外部干预指令（最高优先级）\n{user_cmd}\n"
        if other_context:
            header += f"\n## 其他角色状态\n{other_context}\n"
        if relationship_context:
            header += f"\n## 你的关系网络\n{relationship_context}\n"
        if dynamic_memory:
            header += f"\n## 相关的过往事件\n{dynamic_memory}\n"
        if recent_events:
            header += f"\n## 近期局势\n{recent_events}\n"
        if static_knowledge:
            header += f"\n## 原著背景参考\n{static_knowledge}\n"
        if env_context:
            header += f"\n## 环境\n{env_context}\n"

        header += (
            f"\n现在，请以{agent.name}的身份，基于以上所有信息，决定你下一步的行动。\n"
            "你可以做任何符合你性格和处境的行动，不需要局限于预定义的类型。\n"
            "冲突和意外是故事的燃料——不要害怕做出大胆的选择。\n\n"
            + _FREE_SPEC
        )

        return await self._call_llm(
            header,
            system="你是一位文学创作中的角色。基于你的人格、记忆和处境，自由决定下一步行动。只输出 JSON。",
        )

    async def reason_narrative(
        self,
        *,
        agent: Any,
        round_number: int,
        state: Any,
        event_mandate: str = "",
        correction_level: str = "none",
        other_context: str = "",
        relationship_context: str = "",
        static_knowledge: str = "",
        dynamic_memory: str = "",
        recent_events: str = "",
        env_context: str = "",
        self_memory: str = "",
    ) -> dict[str, Any]:
        """蓝图执行：角色在既定事件结构内自由演绎。"""
        goals = agent.goals if hasattr(agent, "goals") else []
        goals_txt = "\n".join(f"- {g}" for g in goals) if goals else "（无）"
        narrative_state = _metrics_to_narrative(state)

        correction_directive = ""
        if correction_level == "soft":
            correction_directive = "【注意】当前剧情走向与提纲目标略有偏离，请在决策中优先靠近目标方向。"
        elif correction_level == "strong":
            correction_directive = "【强制引导】当前剧情已偏离提纲目标。你的决策必须优先推动剧情回到目标轨迹。"
        elif correction_level == "event_inject":
            correction_directive = (
                f"【强剧情推力】系统检测到重大偏离。以下外部事件将迫使剧情转向，请围绕此事件做出反应。\n"
                f"外部事件：{event_mandate or '重大事件发生'}"
            )

        header = (
            f"你是「{agent.name}」，这是一个文学创作中的关键场景。\n\n"
            f"## 你的人格\n{agent.persona or '（无）'}\n\n"
            f"## 你的目标\n{goals_txt}\n\n"
            f"## 你当前的状态\n{narrative_state}\n"
        )
        if self_memory:
            header += f"\n## 你最近做过的事\n{self_memory}\n"
        if event_mandate:
            header += f"\n## 【本轮必须推动的剧情】\n{event_mandate}\n"
        if correction_directive:
            header += f"\n## {correction_directive}\n"
        if other_context:
            header += f"\n## 其他角色状态\n{other_context}\n"
        if relationship_context:
            header += f"\n## 你的关系网络\n{relationship_context}\n"
        if dynamic_memory:
            header += f"\n## 相关的过往事件\n{dynamic_memory}\n"
        if static_knowledge:
            header += f"\n## 原著背景参考\n{static_knowledge}\n"
        if env_context:
            header += f"\n## 环境\n{env_context}\n"

        header += (
            f"\n请以{agent.name}的身份，自由演绎本轮的剧情。"
            "你可以做任何符合角色性格的选择，不需要局限于预定义的动作类型。\n\n"
            + _FREE_SPEC
        )

        return await self._call_llm(
            header,
            system="你是文学创作中的角色。基于你的人格、记忆和处境，自由演绎剧情。只输出 JSON。",
        )

    async def _call_llm(self, prompt: str, system: str) -> dict[str, Any]:
        from literarycreation.core.llm_client import DeductionLLMClient, Message

        if self._chat_fn is not None:
            try:
                resp = await self._chat_fn(
                    messages=[Message(role="user", content=prompt)],
                    system=system, temperature=0.7,
                )
                return _parse_json(resp.text)
            except Exception as e:
                logger.warning(f"[Reasoner] LLM failed: {e}")
                return {}

        client = DeductionLLMClient()
        resp = await client.chat(
            [Message(role="user", content=prompt)],
            system=system, temperature=0.7,
        )
        return _parse_json(resp.text)


def _parse_json(raw: str) -> dict[str, Any]:
    match = re.search(r"\{[\s\S]*\}", raw)
    if not match:
        return {}
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return {}


_FREE_SPEC = """## 输出 JSON（仅 JSON，无解释）
```json
{
  "action_type": "你的具体行动描述（自由文本，如'潜入档案室寻找证据'、'向陆远坦白自己对师父之死的怀疑'）",
  "target": "行动的涉及对象或留空",
  "intensity": 0.0到1.0,
  "rationale": "20-50字理由"
}
```"""
