"""Conflict Progression — tracks narrative tension arc across rounds.

Monitors the tension metric trajectory and validates it against expected narrative
structure (rising action → climax → falling action). Detects:
  - Premature climax (tension peaks too early then drops)
  - Flat arc (tension never rises significantly)
  - Early resolution (tension drops below threshold while rounds remain)

Outputs arc phase and warnings via ctx.metadata.
"""
from __future__ import annotations

from typing import Any

import numpy as np

from .base import AlgorithmModule, ModuleContext


class ConflictProgressionModule(AlgorithmModule):
    """Tension arc tracker for narrative conflict structure.

    Reads: ctx.arrays["tension"], ctx.metadata["conflict.tension_history"].
    Config: total_rounds, climax_min_tension, early_drop_threshold.
    Writes: ctx.metadata["conflict.arc_phase"], ctx.metadata["conflict.warnings"].
    """

    OUTPUT_SIGNALS: list[str] = ["conflict.arc_phase", "conflict.warnings"]

    def __init__(self) -> None:
        self._total_rounds: int = 10
        self._climax_min_tension: float = 65.0
        self._early_drop_threshold: float = 30.0
        self._tension_history: list[float] = []

    @property
    def name(self) -> str:
        return "conflict_progression"

    @property
    def description(self) -> str:
        return "冲突递进追踪——监测张力弧线，验证叙事冲突上升-高潮-回落结构"

    def configure(self, params: dict[str, Any]) -> None:
        self._total_rounds = int(params.get("total_rounds", 10))
        self._climax_min_tension = float(params.get("climax_min_tension", 65.0))
        self._early_drop_threshold = float(params.get("early_drop_threshold", 30.0))

    def execute(self, ctx: ModuleContext) -> ModuleContext:
        tension_arr = ctx.arrays.get("tension")
        if tension_arr is None or len(tension_arr) == 0:
            return ctx

        avg_tension = float(np.mean(tension_arr))
        total = max(1, self._total_rounds)
        round_num = ctx.round_number
        self._tension_history.append(avg_tension)

        # Restore history from metadata (for resume)
        saved: list = ctx.metadata.get("conflict.tension_history", [])
        if saved and len(saved) > len(self._tension_history):
            self._tension_history = saved
        ctx.metadata["conflict.tension_history"] = self._tension_history

        history = self._tension_history
        warnings: list[dict[str, Any]] = []

        # --- Arc phase determination ---
        if len(history) <= 1:
            ctx.metadata["conflict.arc_phase"] = "exposition"
            return ctx

        first_t = history[0]
        max_t = max(history)
        max_idx = history.index(max_t) + 1  # 1-indexed round
        fraction = round_num / total

        if fraction < 0.3:
            phase = "rising_action"
        elif fraction < 0.55:
            phase = "rising_action" if avg_tension < max_t * 0.9 else "climax_zone"
        elif fraction < 0.75:
            phase = "climax_zone"
        else:
            phase = "falling_action"

        ctx.metadata["conflict.arc_phase"] = phase

        # --- Warnings ---

        # Premature climax: peak tension occurred in first 30% of rounds
        if max_idx <= total * 0.3 and max_t > self._climax_min_tension:
            warnings.append({
                "type": "premature_climax",
                "peak_round": max_idx,
                "peak_tension": round(max_t, 1),
                "message": f"高潮过早：张力在第{max_idx}轮达到峰值{max_t:.0f}（仅占全剧{round(max_idx/total*100)}%），后续缺乏上升空间",
            })

        # Flat arc: tension never rose significantly
        if len(history) >= 3 and (max_t - first_t) < 20:
            warnings.append({
                "type": "flat_arc",
                "rise": round(max_t - first_t, 1),
                "message": f"冲突弧线平坦：从首轮{first_t:.0f}到峰值{max_t:.0f}仅上升{max_t-first_t:.0f}，缺乏戏剧张力递进",
            })

        # Early resolution: tension below threshold with many rounds remaining
        remaining = total - round_num
        if remaining >= 3 and avg_tension < self._early_drop_threshold and max_t > self._climax_min_tension:
            warnings.append({
                "type": "early_resolution",
                "current_tension": round(avg_tension, 1),
                "remaining_rounds": remaining,
                "message": f"冲突过早平息：第{round_num}轮张力仅{avg_tension:.0f}，但仍有{remaining}轮，建议制造新冲突",
            })

        ctx.metadata["conflict.warnings"] = warnings
        ctx.metadata["conflict.tension_max_round"] = max_idx
        ctx.metadata["conflict.tension_peak"] = round(max_t, 1)

        return ctx
