"""Pacing Analyzer — tracks narrative pacing across rounds.

Monitors event density and metric change velocity to detect:
  - Stalling (multiple rounds with low activity)
  - Rushing (sudden large metric shifts in a single round)
  - Plateau (prolonged stagnation in key metrics)

Outputs pacing score (0-100) and warnings via ctx.metadata.
"""
from __future__ import annotations

from typing import Any

import numpy as np

from .base import AlgorithmModule, ModuleContext


class PacingAnalyzerModule(AlgorithmModule):
    """Tracks per-round event density and metric velocity for pacing analysis.

    Reads: ctx.metadata["round_metrics_snapshot"] — serialized metric snapshots
           for velocity computation.
    Config: stall_threshold (rounds), rush_threshold (delta), plateau_rounds.
    Writes: ctx.metadata["pacing.score"], ctx.metadata["pacing.warnings"].
    """

    OUTPUT_SIGNALS: list[str] = ["pacing.score", "pacing.warnings"]

    def __init__(self) -> None:
        self._stall_threshold: int = 3
        self._rush_threshold: float = 30.0
        self._plateau_rounds: int = 4
        self._history: list[dict[str, float]] = []

    @property
    def name(self) -> str:
        return "pacing_analyzer"

    @property
    def description(self) -> str:
        return "节奏分析——追踪事件密度与指标变化速度，检测过慢/过快/停滞"

    def configure(self, params: dict[str, Any]) -> None:
        self._stall_threshold = int(params.get("stall_threshold", 3))
        self._rush_threshold = float(params.get("rush_threshold", 30.0))
        self._plateau_rounds = int(params.get("plateau_rounds", 4))

    def execute(self, ctx: ModuleContext) -> ModuleContext:
        if not ctx.arrays:
            return ctx

        n = len(next(iter(ctx.arrays.values()))) if ctx.arrays else 0
        if n == 0:
            return ctx

        round_num = ctx.round_number
        warnings: list[dict[str, Any]] = []

        # Build current round summary
        current: dict[str, float] = {}
        for metric, arr in ctx.arrays.items():
            if len(arr) > 0:
                current[metric] = float(np.mean(arr))
        current["_round"] = float(round_num)
        self._history.append(current)

        if len(self._history) < 2:
            return ctx

        # --- Metric velocity ---
        velocity_summary: dict[str, float] = {}
        prev = self._history[-2]
        for metric, val in current.items():
            if metric.startswith("_"):
                continue
            pv = prev.get(metric, val)
            delta = abs(val - pv)
            velocity_summary[metric] = delta
            if delta > self._rush_threshold:
                warnings.append({
                    "type": "rush",
                    "metric": metric,
                    "delta": round(delta, 1),
                    "message": f"第{round_num}轮节奏过快：{metric}单轮变化{delta:.0f}",
                })

        # --- Stall detection ---
        stall_count = 0
        for entry in reversed(self._history[-self._stall_threshold - 1:]):
            total_movement = sum(
                abs(entry.get(m, 0) - self._history[0].get(m, 0))
                for m in current if not m.startswith("_")
            )
            if total_movement < 5.0:
                stall_count += 1
            else:
                break
        if stall_count >= self._stall_threshold:
            warnings.append({
                "type": "stall",
                "rounds": stall_count,
                "message": f"叙事停滞：连续{stall_count}轮无显著指标变化",
            })

        # --- Plateau detection ---
        if len(self._history) >= self._plateau_rounds:
            window = self._history[-self._plateau_rounds:]
            tensions = [w.get("tension", 0) for w in window if not w.get("_round", 0) == 0]
            if len(tensions) >= self._plateau_rounds:
                t_range = max(tensions) - min(tensions)
                t_avg = sum(tensions) / len(tensions)
                if t_range < 8.0 and t_avg > 30:
                    warnings.append({
                        "type": "plateau",
                        "metric": "tension",
                        "range": round(t_range, 1),
                        "message": f"张力指标连续{self._plateau_rounds}轮高位平高（{t_avg:.0f}），建议推高冲突",
                    })

        # --- Pacing score (0=stalled, 50=ideal, 100=chaotic) ---
        score = 50.0
        if velocity_summary:
            avg_velocity = sum(velocity_summary.values()) / len(velocity_summary)
            if avg_velocity < 2.0:
                score = max(10.0, 50.0 - avg_velocity * 20)
            elif avg_velocity > 15.0:
                score = min(90.0, 50.0 + avg_velocity * 2)
            else:
                score = 50.0

        ctx.metadata["pacing.score"] = round(score, 1)
        ctx.metadata["pacing.warnings"] = warnings
        ctx.metadata["pacing.velocity"] = velocity_summary

        return ctx
