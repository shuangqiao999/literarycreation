"""Outline Deviation Detection — pure metric comparison for arc tracking.

Computes the gap between current character metrics and their target trajectory
(linear interpolation from initial_state → final_state).
Returns correction level: none, soft, strong, event_inject.
"""
from __future__ import annotations

from typing import Any, Literal


CorrectionLevel = Literal["none", "soft", "strong", "event_inject"]


def compute_deviation(
    metric: str,
    current_value: float,
    initial_value: float,
    final_value: float,
    round_number: int,
    total_rounds: int,
    tolerance: float = 10.0,
) -> float:
    """Compute the signed gap between actual and target for a single metric.

    Returns positive gap if current is below target (needs to rise),
    negative gap if current is above target (needs to drop).
    """
    frac = min(1.0, max(0.0, round_number / max(1, total_rounds)))
    target = initial_value + (final_value - initial_value) * frac
    return target - current_value


def resolve_correction(
    gaps: dict[str, float],
    tolerance: float = 10.0,
) -> CorrectionLevel:
    """Determine correction strength from a set of metric gaps.

    Args:
        gaps: {metric_name: gap_value} where gap = target - actual
        tolerance: max allowed gap before correction triggers

    Returns the strongest correction level needed.
    """
    max_gap = max(abs(g) for g in gaps.values()) if gaps else 0.0
    if max_gap < tolerance:
        return "none"
    if max_gap < tolerance * 2:
        return "soft"
    if max_gap < tolerance * 3:
        return "strong"
    return "event_inject"


def build_correction_prompt(
    gaps: dict[str, float],
    level: CorrectionLevel,
    entity_names: dict[int, str],
) -> str:
    """Build prompt text for correction injection.

    Args:
        gaps: {entity_index: {metric: gap}}
        level: correction strength
        entity_names: {index: name} mapping
    """
    if level == "none" or not gaps:
        return ""

    hints: list[str] = []
    for idx, mgaps in gaps.items():
        name = entity_names.get(idx, f"角色{idx}")
        for metric, gap in mgaps.items():
            direction = "提升" if gap > 0 else "降低"
            hints.append(f"{name}的{metric}需{direction}{abs(gap):.0f}")

    if not hints:
        return ""

    detail = "；".join(hints)
    if level == "soft":
        return f"[弧光提醒] {detail}"
    if level == "strong":
        return f"[弧光强制] 本轮必须优先推进以下指标：{detail}"
    if level == "event_inject":
        return (
            f"[强剧情推力] 系统检测到重大偏离。以下指标的偏离已超过容许范围：{detail}\n"
            "请引入外部事件迫使剧情转向目标方向。"
        )
    return ""
