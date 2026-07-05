"""Algorithm module base — lightweight compute units for the deduction engine.

All modules operate on generic numeric data via ModuleContext.
No spatial/physics simulation in this layer.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import numpy as np


@dataclass
class ModuleContext:
    """Shared data context passed through the algorithm module chain.

    --- Generic numeric layer ---
    arrays: dict[str, np.ndarray]
        Keyed by metric name, each value is shape (N,) float64 array.
        Modules can read/modify/add arrays freely.

    --- Control ---
    dt: float
        Integration step size in seconds (default 1.0 for one round).
    round_number: int
        Current simulation round.

    --- Passthrough ---
    metadata: dict
        Arbitrary key-value store for module-to-module communication.
    """

    arrays: dict[str, np.ndarray] = field(default_factory=dict)
    dt: float = 1.0
    round_number: int = 1
    metadata: dict[str, Any] = field(default_factory=dict)

    def get(self, key: str, default: Any = None) -> Any:
        return self.metadata.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self.metadata[key] = value


class AlgorithmModule(ABC):
    """Abstract base for all algorithm modules — domain-agnostic.

    Subclasses may optionally define:
      - REQUIRED_SIGNALS: list[str] — metadata keys that must exist in ctx before execute.
      - OUTPUT_SIGNALS: list[str] — metadata keys this module writes to ctx.
      - IS_FINALIZER: bool — True if this module writes to ctx.arrays directly.
        Finalizers are automatically sorted to the end of the pipeline.
    """

    REQUIRED_SIGNALS: list[str] = []
    OUTPUT_SIGNALS: list[str] = []
    IS_FINALIZER: bool = False

    @property
    @abstractmethod
    def name(self) -> str:
        ...

    @property
    @abstractmethod
    def description(self) -> str:
        ...

    def configure(self, params: dict[str, Any]) -> None:
        pass

    def _validate(self, ctx: ModuleContext) -> bool:
        for sig in self.REQUIRED_SIGNALS:
            if sig not in ctx.metadata:
                return False
        return True

    @abstractmethod
    def execute(self, ctx: ModuleContext) -> ModuleContext:
        ...

    def shutdown(self) -> None:
        pass


# ── bridge: EntityState ↔ ModuleContext ──


def states_to_arrays(
    states: dict[str, Any],
    metric_names: list[str],
    entity_ids: list[str],
) -> dict[str, np.ndarray]:
    """Convert EntityState metrics dicts → ModuleContext.arrays."""
    result: dict[str, np.ndarray] = {}
    for metric in metric_names:
        arr = np.zeros(len(entity_ids), dtype=np.float64)
        for i, eid in enumerate(entity_ids):
            st = states.get(eid)
            if st is not None:
                val = st.metrics.get(metric)
                if val is not None and not np.isnan(float(val)):
                    arr[i] = float(val)
        result[metric] = arr
    return result


def arrays_to_states(
    ctx: ModuleContext,
    states: dict[str, Any],
    entity_ids: list[str],
    metric_ranges: dict[str, tuple[float, float]] | None = None,
) -> None:
    """Write ctx.arrays back into EntityState.metrics, clamping to ranges."""
    ranges = metric_ranges or {}
    for metric, arr in ctx.arrays.items():
        lo, hi = ranges.get(metric, (-1e12, 1e12))
        for i, eid in enumerate(entity_ids):
            val = float(arr[i])
            if np.isnan(val):
                continue
            val = max(lo, min(hi, val))
            st = states.get(eid)
            if st is not None:
                st.metrics[metric] = val
