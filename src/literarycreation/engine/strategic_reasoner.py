"""Strategic Reasoner — LLM决策推理，支持自由推演与蓝图执行双模式。

信任矩阵：记录角色间交互情感，为决策提供关系上下文。
不再使用风险惩罚的启发式评分 — 叙事质量由LLM自身判断。
"""
from __future__ import annotations

import json
import logging
import random
import re
from collections import defaultdict
from string import Template
from typing import Any

logger = logging.getLogger(__name__)


class TrustMatrix:
    """Records pairwise trust scores derived from agent interactions."""

    def __init__(self) -> None:
        self._scores: dict[tuple[str, str], float] = defaultdict(float)

    def record_interaction(
        self, actor_id: str, target_id: str, action_type: str, effect_summary: dict[str, float]
    ) -> None:
        if action_type in ("ally", "protect", "confess", "trust"):
            self._scores[(actor_id, target_id)] += 0.5
        elif action_type in ("betray", "confront", "manipulate"):
            self._scores[(actor_id, target_id)] -= 0.8
        elif action_type in ("investigate", "observe"):
            decay = sum(effect_summary.values()) / 200.0
            self._scores[(actor_id, target_id)] -= decay

    def get(self, actor_id: str, target_id: str) -> float:
        return self._scores.get((actor_id, target_id), 0.0)

    def reset(self) -> None:
        self._scores.clear()


class StrategicReasoner:
    """LLM-based decision maker for literary creation.

    Two modes:
      - reason_quantified(): Mode A freeform writing, agents decide freely
      - reason_narrative(): Mode B blueprint execution, agents are guided by scheduled events
    """

    def __init__(
        self,
        chat_fn: Any = None,
        preprocessor: Any = None,
        candidate_count: int = 1,
        immutable_goals: list[str] | None = None,
    ) -> None:
        self._chat_fn = chat_fn
        self._preprocessor = preprocessor
        self._candidate_count = max(1, candidate_count)
        self._immutable_goals = immutable_goals or []
        self._trust = TrustMatrix()

    @property
    def trust(self) -> TrustMatrix:
        return self._trust

    def seed_trust(self, entity_id: str, allies: list[str], foes: list[str]) -> None:
        """Initialize trust scores from graph relationships."""
        for ally in allies:
            self._trust._scores[(entity_id, ally)] = 1.0
        for foe in foes:
            self._trust._scores[(entity_id, foe)] = -1.0

    def record_interaction(
        self, actor_id: str, target_id: str, action_type: str, effect_summary: dict[str, float]
    ) -> None:
        self._trust.record_interaction(actor_id, target_id, action_type, effect_summary)

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
        spatial_context: str = "",
        env_context: str = "",
        user_cmd: str = "",
        cached_action_catalog: str = "",
        enable_multi_action: bool = False,
    ) -> dict[str, Any]:
        """Mode A: agent makes a free decision based on character and context.

        No risk penalty, no heuristic scoring — the LLM's judgment alone drives the choice.
        """
        goals = agent.goals if hasattr(agent, "goals") else []
        goals_txt = "\n".join(f"- {g}" for g in goals) if goals else "（无具体目标）"
        imm = "；".join(self._immutable_goals) if self._immutable_goals else "无"

        if enable_multi_action and cached_action_catalog:
            select_hint = "从可选行动中分配资源给一个或多个动作，给出每个动作的投入力度"
            output_spec = _QUANTIFIED_MULTI_SPEC
        else:
            select_hint = "从可选行动中选择一个并给出投入力度"
            output_spec = _QUANTIFIED_SINGLE_SPEC

        # Narrative quality hint: encourage dramatic, engaging decisions
        narrative_hint = (
            "你的决策应推动故事向更精彩的结局发展。适当的对抗、冲突和意外会增强叙事张力。"
        )

        header = (
            f"你是「{agent.name}」，正处于一场文学创作推演的第 {round_number} 轮。\n"
            f"请基于你的人格、目标与当前数值状态，{select_hint}。\n\n"
            f"{narrative_hint}\n\n"
            f"## 你的人格\n{agent.persona or '（无）'}\n\n"
            f"## 你的目标\n{goals_txt}\n\n"
            f"## 不可变战略指令（最高优先级）\n{imm}\n"
        )
        if user_cmd:
            header += f"\n## 外部干预指令（最高优先级）\n{user_cmd}\n"
        header += f"\n## 你的当前状态\n{state.to_prompt_context()}\n"
        if other_context:
            header += f"\n## 其他参与方状态\n{other_context}\n"
        if relationship_context:
            header += f"\n## 关系网络\n{relationship_context}\n"
        if static_knowledge:
            header += f"\n## 原著背景\n{static_knowledge}\n"
        if dynamic_memory:
            header += f"\n## 历史记忆\n{dynamic_memory}\n"
        if recent_events:
            header += f"\n## 近期局势\n{recent_events}\n"
        if spatial_context:
            header += f"\n## 空间环境\n{spatial_context}\n"
        if env_context:
            header += f"\n## 地形与天气\n{env_context}\n"
        if cached_action_catalog:
            header += f"\n## 可选行动\n{cached_action_catalog}\n"
        header += f"\n## 输出 JSON\n{output_spec}"

        return await self._call_llm(
            agent.name, round_number, header,
            system="你是量化推演中的战略决策者，从给定行动中选择最优方案。只输出 JSON。",
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
        cached_action_catalog: str = "",
    ) -> dict[str, Any]:
        """Mode B: agent is guided by scheduled events with graduated enforcement.

        When correction_level rises, the prompt becomes increasingly directive.
        """
        goals = agent.goals if hasattr(agent, "goals") else []
        goals_txt = "\n".join(f"- {g}" for g in goals) if goals else "（无）"

        # Build correction directive based on level
        correction_directive = ""
        if correction_level == "soft":
            correction_directive = "【注意】当前剧情走向与提纲目标略有偏离，请在决策中优先靠近目标方向。"
        elif correction_level == "strong":
            correction_directive = (
                "【强制引导】当前剧情已偏离提纲目标。你的决策必须优先推动剧情回到目标轨迹。"
            )
        elif correction_level == "event_inject":
            correction_directive = (
                "【强剧情推力】系统检测到重大偏离。以下外部事件将迫使剧情转向，请围绕此事件做出反应。\n"
                f"外部事件：{event_mandate or '重大事件发生'}"
            )

        # Build header
        narrative_role = (
            "你是一位文学创作中的角色。当前轮次有必须推动的剧情事件，"
            "你的任务是：为角色的行动找到可信的内心动机，然后以角色的身份演绎这个事件。"
            "你可以选择如何推动事件（方式、情感、语气），但事件本身必须发生。"
        )
        header = (
            f"{narrative_role}\n\n"
            f"## 你的角色\n"
            f"名称：{agent.name}\n"
            f"人格：{agent.persona or '（无）'}\n"
            f"目标：{goals_txt}\n\n"
            f"## 当前状态\n{state.to_prompt_context()}\n"
        )

        if event_mandate:
            header += f"\n## 【本轮必须推动的剧情】\n{event_mandate}\n"

        if correction_directive:
            header += f"\n## {correction_directive}\n"

        if other_context:
            header += f"\n## 其他角色状态\n{other_context}\n"
        if relationship_context:
            header += f"\n## 关系网络\n{relationship_context}\n"
        if dynamic_memory:
            header += f"\n## 近期事件\n{dynamic_memory}\n"
        if static_knowledge:
            header += f"\n## 原著背景\n{static_knowledge}\n"
        if env_context:
            header += f"\n## 环境\n{env_context}\n"
        if cached_action_catalog:
            header += f"\n## 可选行动\n{cached_action_catalog}\n"
        header += f"\n## 输出 JSON\n{_QUANTIFIED_SINGLE_SPEC}"

        return await self._call_llm(
            agent.name, round_number, header,
            system="你是文学创作中的角色，为剧情事件找到可信动机并演绎。只输出 JSON。",
        )

    # ── internal ──

    async def _call_llm(
        self, agent_name: str, round_number: int, prompt: str, system: str
    ) -> dict[str, Any]:
        from literarycreation.core.llm_client import DeductionLLMClient, Message

        if self._chat_fn is not None:
            try:
                resp = await self._chat_fn(
                    messages=[Message(role="user", content=prompt)],
                    system=system, temperature=0.7,
                )
                return _parse_json(resp.text)
            except Exception as e:
                logger.warning(f"[Reasoner] failed for {agent_name} r{round_number}: {e}")
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


_QUANTIFIED_SINGLE_SPEC = """```json
{
  "action_type": "上面之一",
  "target": "目标方名称",
  "intensity": 0.0到1.0,
  "rationale": "20-50字理由"
}
```"""

_QUANTIFIED_MULTI_SPEC = """```json
{
  "budget": 0.0到1.0,
  "actions": [
    {"action_type": "...", "weight": 0.0到1.0, "target": "..."}
  ],
  "rationale": "20-50字理由"
}
```"""
