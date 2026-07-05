"""Outline Control — Mode 2 提纲复现的弧光门控模块（post-decision）。

按角色 initial_state→final_state 的线性插值算出每轮目标带，对比当前指标；
偏离超阈值时产出 nudge（软提示），由 simulator 注入下一轮决策上下文与事件记忆，
以「强软实现」逼近提纲弧光，而不直接改写状态（保住文本自然度）。

无提纲（Mode 1 续写）时 execute() 立即返回，零副作用。
"""
from __future__ import annotations

from typing import Any

from .base import AlgorithmModule, ModuleContext


class OutlineControlModule(AlgorithmModule):
    """提纲弧光门控——检测角色指标偏离目标轨迹并生成软性纠偏提示。

    Reads:  ctx.arrays（角色指标）、ctx.metadata["entity_names"]（数组序↔角色名）。
    Config: outline.characters（name→initial/final）、total_rounds、deviation_threshold。
    Writes: ctx.metadata["outline.nudges"]、ctx.metadata["outline.arc_report"]。
    """

    REQUIRED_SIGNALS: list[str] = []
    OUTPUT_SIGNALS: list[str] = ["outline.nudges", "outline.arc_report"]
    IS_FINALIZER = False

    def __init__(self) -> None:
        self._chars: dict[str, dict[str, dict[str, float]]] = {}
        self._total_rounds: int = 10
        self._threshold: float = 12.0

    @property
    def name(self) -> str:
        return "outline_control"

    @property
    def description(self) -> str:
        return "提纲弧光门控——按角色目标轨迹检测偏离并生成软性纠偏提示"

    def configure(self, params: dict[str, Any]) -> None:
        self._threshold = float(params.get("deviation_threshold", 12.0))
        try:
            self._total_rounds = max(1, int(params.get("total_rounds", 10)))
        except (TypeError, ValueError):
            self._total_rounds = 10
        outline = params.get("outline") or {}
        for c in outline.get("characters", []) or []:
            name = c.get("name")
            if not name:
                continue
            self._chars[name] = {
                "initial": {k: float(v) for k, v in (c.get("initial_state") or {}).items()},
                "final": {k: float(v) for k, v in (c.get("final_state") or {}).items()},
            }

    def execute(self, ctx: ModuleContext) -> ModuleContext:
        if not self._chars:
            return ctx
        names: list[str] = ctx.metadata.get("entity_names", []) or []
        if not names:
            return ctx
        frac = min(1.0, max(0.0, ctx.round_number / max(1, self._total_rounds)))
        nudges: list[dict[str, Any]] = []
        arc_report: list[dict[str, Any]] = []
        for name, spec in self._chars.items():
            if name not in names:
                continue
            idx = names.index(name)
            for metric, init_v in spec["initial"].items():
                fin_v = float(spec["final"].get(metric, init_v))
                target = init_v + (fin_v - init_v) * frac
                arr = ctx.arrays.get(metric)
                if arr is None or idx >= len(arr):
                    continue
                cur = float(arr[idx])
                gap = target - cur
                arc_report.append({
                    "name": name, "metric": metric,
                    "target": round(target, 1), "current": round(cur, 1),
                })
                if abs(gap) > self._threshold:
                    nudges.append({
                        "name": name, "metric": metric,
                        "direction": "提升" if gap > 0 else "降低",
                        "gap": round(gap, 1),
                    })
        ctx.metadata["outline.nudges"] = nudges
        ctx.metadata["outline.arc_report"] = arc_report
        return ctx
