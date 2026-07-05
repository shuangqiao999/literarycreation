"""Character Consistency — validates agent decisions against established traits.

Checks whether each agent's decision aligns with their profile metrics and action history.
Flags contradictions (e.g., high-trust character performing betray).

Writes consistency flags via ctx.metadata["consistency.flags"].
"""
from __future__ import annotations

from typing import Any

import numpy as np

from .base import AlgorithmModule, ModuleContext


# Trait-action compatibility matrix: action → (metric, min_threshold) constraints
# If metric is below threshold, the action is considered inconsistent
TRAIT_ACTION_CONSTRAINTS: dict[str, list[tuple[str, float]]] = {
    "betray": [("trust", 70.0)],       # betray when trust > 70 is suspicious
    "confess": [("affection", 30.0)],  # confess needs minimum affection
    "protect": [("trust", 20.0)],      # protect needs some trust
    "ally": [("trust", 20.0)],         # ally needs some trust
    "confront": [("tension", 20.0)],   # confront is natural when tension is high
    "manipulate": [("power", 30.0)],   # manipulate needs some power base
}


class CharacterConsistencyModule(AlgorithmModule):
    """Trait-action alignment checker for character behavior.

    Reads: ctx.arrays (metrics), ctx.metadata["consistency.decisions"] (recent actions).
    Config: trait_constraints (override default constraints), warn_threshold.
    Writes: ctx.metadata["consistency.flags"].
    """

    OUTPUT_SIGNALS: list[str] = ["consistency.flags"]

    def __init__(self) -> None:
        self._constraints: dict[str, list[tuple[str, float]]] = {}
        self._warn_threshold: float = 0.7

    @property
    def name(self) -> str:
        return "character_consistency"

    @property
    def description(self) -> str:
        return "角色一致性——检测角色行为与既有指标/历史的矛盾"

    def configure(self, params: dict[str, Any]) -> None:
        user_constraints = params.get("trait_constraints", {})
        self._constraints = {**TRAIT_ACTION_CONSTRAINTS, **user_constraints}
        self._warn_threshold = float(params.get("warn_threshold", 0.7))

    def execute(self, ctx: ModuleContext) -> ModuleContext:
        if not ctx.arrays:
            return ctx

        entity_names: list[str] = ctx.metadata.get("entity_names", []) or []
        entity_ids: list[str] = ctx.metadata.get("entity_ids", []) or []
        decisions: list[dict[str, Any]] = ctx.metadata.get("consistency.decisions", []) or []
        n = len(entity_ids)
        flags: list[dict[str, Any]] = []

        if not decisions or n == 0:
            return ctx

        for dec in decisions:
            actor_id = dec.get("actor_id", dec.get("actor", ""))
            action_type = dec.get("action_type", dec.get("action", ""))
            if not actor_id or not action_type:
                continue

            idx = entity_ids.index(actor_id) if actor_id in entity_ids else -1
            name = entity_names[idx] if 0 <= idx < len(entity_names) else actor_id
            if idx < 0:
                continue

            constraints = self._constraints.get(action_type, [])
            for metric, threshold in constraints:
                arr = ctx.arrays.get(metric)
                if arr is None or idx >= len(arr):
                    continue
                val = float(arr[idx])
                if val > threshold * self._warn_threshold:
                    flags.append({
                        "entity": name,
                        "action": action_type,
                        "metric": metric,
                        "value": round(val, 1),
                        "threshold": threshold,
                        "message": f"{name}执行{action_type}时{metric}={val:.0f}偏高，可能角色不一致",
                    })

        ctx.metadata["consistency.flags"] = flags
        return ctx
