"""Vectorized batch operations using pure numpy — no external JIT dependency."""
from __future__ import annotations

import numpy as np


def batch_apply_deltas(
    metrics_arr: np.ndarray,
    deltas_arr: np.ndarray,
    lo_arr: np.ndarray,
    hi_arr: np.ndarray,
) -> None:
    """Apply delta values to metric arrays with range clamping, in-place."""
    metrics_arr[:] = np.clip(metrics_arr + deltas_arr, lo_arr[np.newaxis, :], hi_arr[np.newaxis, :])


def batch_eval_conditions(
    metric_arrays: dict[str, np.ndarray],
    conditions: list[tuple[str, str, float]],
) -> np.ndarray:
    """Evaluate condition expressions against metric arrays. Returns (N,) bool mask."""
    if not conditions:
        return np.ones(1, dtype=bool)
    mask = np.ones(len(next(iter(metric_arrays.values()))), dtype=bool)
    for metric, op, threshold in conditions:
        if metric not in metric_arrays:
            return np.zeros_like(mask)
        vals = metric_arrays[metric]
        if op == "<":
            mask &= vals < threshold
        elif op == ">":
            mask &= vals > threshold
        elif op == "<=":
            mask &= vals <= threshold
        elif op == ">=":
            mask &= vals >= threshold
        elif op == "==":
            mask &= np.abs(vals - threshold) < 1e-9
        elif op == "!=":
            mask &= np.abs(vals - threshold) >= 1e-9
    return mask
