"""Character State Tracker — lightweight finite state machine for literary characters.

Replaces the old game-oriented FSM (patrol→alert→attack). Pure threshold-based
state transitions on entity metrics — no spatial/distance computation.

States marked as command_states are handed off to LLM for final decision.
All other states produce deterministic actions via action_map, bypassing LLM.
"""
from __future__ import annotations

from typing import Any

import numpy as np

from .base import AlgorithmModule, ModuleContext


class FiniteStateMachineModule(AlgorithmModule):
    """Discrete state transition engine for character behavior autonomy.

    Reads: ctx.arrays (entity metrics) for threshold evaluation.
    Config: transition_rules, action_map, command_states, default_state.
    Writes: ctx.metadata["fsm.agent_states"], ctx.metadata["fsm.agent_actions"],
            ctx.metadata["fsm.command_states"].
    """

    REQUIRED_SIGNALS: list[str] = []
    OUTPUT_SIGNALS: list[str] = [
        "fsm.agent_states",
        "fsm.agent_actions",
        "fsm.command_states",
    ]

    def __init__(self) -> None:
        self._rules: list[dict[str, Any]] = []
        self._default_state: str = "neutral"
        self._action_map: dict[str, dict[str, Any] | None] = {}
        self._command_states: set[str] = {"crisis"}

    @property
    def name(self) -> str:
        return "finite_state_machine"

    @property
    def description(self) -> str:
        return "角色状态追踪——基于指标阈值的文学角色状态转移与动作映射"

    def configure(self, params: dict[str, Any]) -> None:
        self._rules = list(params.get("transition_rules", []))
        self._default_state = str(params.get("default_state", "neutral"))
        self._action_map = dict(params.get("action_map", {}))
        command = params.get("command_states", ["crisis"])
        self._command_states = set(command if isinstance(command, list) else [command])

    def execute(self, ctx: ModuleContext) -> ModuleContext:
        n = len(next(iter(ctx.arrays.values()))) if ctx.arrays else 0
        if n == 0:
            return ctx

        prev_states: list[str] = ctx.metadata.get(
            "fsm.agent_states", [self._default_state] * n
        )
        if isinstance(prev_states, dict):
            prev_states = [prev_states.get(i, self._default_state) for i in range(n)]
        if len(prev_states) != n:
            prev_states = [self._default_state] * n

        new_states = list(prev_states)

        for rule in self._rules:
            from_state = rule.get("from", "")
            to_state = rule.get("to", "")
            condition = rule.get("condition", {})
            if not from_state or not to_state:
                continue
            for i in range(n):
                if new_states[i] != from_state:
                    continue
                if self._match_condition(ctx, i, condition):
                    new_states[i] = to_state

        ctx.metadata["fsm.agent_states"] = new_states
        ctx.metadata["fsm.command_states"] = list(self._command_states)

        agent_actions: list[dict[str, Any] | None] = []
        for i in range(n):
            state = new_states[i]
            if state in self._command_states:
                agent_actions.append(None)
            else:
                mapped = self._action_map.get(state)
                if mapped is None:
                    agent_actions.append({
                        "action_type": "observe", "intensity": 0.3,
                        "target": "", "rationale": f"[FSM] {state}",
                    })
                elif isinstance(mapped, dict):
                    agent_actions.append({
                        "action_type": mapped.get("action_type", "observe"),
                        "intensity": float(mapped.get("intensity", 0.5)),
                        "target": str(mapped.get("target", "") or ""),
                        "rationale": f"[FSM] {state}",
                    })
                else:
                    agent_actions.append(None)
        ctx.metadata["fsm.agent_actions"] = agent_actions

        return ctx

    @staticmethod
    def _match_condition(ctx: ModuleContext, idx: int, condition: dict) -> bool:
        for metric, (op, threshold) in condition.items():
            arr = ctx.arrays.get(metric)
            if arr is None or idx >= len(arr):
                return False
            val = float(arr[idx])
            if op == "<" and not (val < float(threshold)):
                return False
            if op == ">" and not (val > float(threshold)):
                return False
            if op == "<=" and not (val <= float(threshold)):
                return False
            if op == ">=" and not (val >= float(threshold)):
                return False
            if op == "==" and not (abs(val - float(threshold)) < 1e-9):
                return False
        return True
