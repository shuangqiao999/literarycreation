"""Module chain factory — creates and configures algorithm modules from rule pack data."""
from __future__ import annotations

import logging
from typing import Any

from .base import AlgorithmModule, ModuleContext, arrays_to_states, states_to_arrays
from .fsm_module import FiniteStateMachineModule
from .outline_control import OutlineControlModule
from .pacing_analyzer import PacingAnalyzerModule
from .character_consistency import CharacterConsistencyModule
from .conflict_progression import ConflictProgressionModule
from .pipeline_engine import PipelineEngine

logger = logging.getLogger(__name__)


class ConfigValidationError(ValueError):
    """Raised when rules.json configuration is invalid."""


# ── Module class registry (maps config keys to concrete classes) ──
_MODULE_CLASSES: dict[str, type[AlgorithmModule]] = {
    "finite_state_machine": FiniteStateMachineModule,
    "outline_control": OutlineControlModule,
    "pacing_analyzer": PacingAnalyzerModule,
    "character_consistency": CharacterConsistencyModule,
    "conflict_progression": ConflictProgressionModule,
}


def build_pipeline(rule_engine: Any) -> PipelineEngine:
    """Build a PipelineEngine with all modules configured from rule pack."""
    pack: dict[str, Any] = getattr(rule_engine, "pack", {})
    pack_modules: dict[str, Any] = pack.get("modules", {})
    pipeline_cfg: dict[str, Any] = pack_modules.get("pipeline", {})
    metrics: list[str] = pack.get("metrics", [])

    engine = PipelineEngine()

    order = pipeline_cfg.get("order", [
        "outline_control",
        "pacing_analyzer",
        "character_consistency",
        "conflict_progression",
        "finite_state_machine",
    ])

    for name in order:
        cls = _MODULE_CLASSES.get(name)
        if cls is None:
            continue
        cfg = dict(pack_modules.get(name, {}))

        module = cls()
        module.configure(cfg)

        if name == "finite_state_machine":
            _validate_fsm_config(cfg, metrics)

        engine.register(module)

    return engine


def _validate_fsm_config(cfg: dict, metrics: list[str]) -> None:
    metrics_set = set(metrics)
    for rule in cfg.get("transition_rules", []):
        condition = rule.get("condition", {})
        for metric in condition:
            if metric not in metrics_set:
                raise ConfigValidationError(
                    f"FSM transition '{rule.get('from','?')} → {rule.get('to','?')}' "
                    f"references metric '{metric}' which is not in the rule pack metrics "
                    f"{metrics}. Check rules.json transition_rules."
                )


def build_module_chain(rule_engine: Any) -> list[AlgorithmModule]:
    """Backward-compat: returns module list from build_pipeline."""
    engine = build_pipeline(rule_engine)
    result: list[AlgorithmModule] = []
    pack: dict[str, Any] = getattr(rule_engine, "pack", {})
    order = pack.get("modules", {}).get("pipeline", {}).get("order", [
        "outline_control",
        "pacing_analyzer",
        "character_consistency",
        "conflict_progression",
        "finite_state_machine",
    ])
    for name in order:
        mod = engine.get(name)
        if mod is not None:
            result.append(mod)
    return result


def build_context(
    states: dict[str, Any],
    rule_engine: Any,
    entity_ids: list[str],
    round_number: int,
) -> ModuleContext:
    """Build a ModuleContext from current EntityState dicts and rule pack config."""
    pack: dict[str, Any] = getattr(rule_engine, "pack", {})
    metric_names: list[str] = pack.get("metrics", [])

    ctx = ModuleContext(round_number=round_number)
    ctx.arrays = states_to_arrays(states, metric_names, entity_ids)

    return ctx


def apply_context_results(
    ctx: ModuleContext,
    states: dict[str, Any],
    entity_ids: list[str],
    rule_engine: Any,
) -> None:
    """Write module outputs back into EntityState objects."""
    pack: dict[str, Any] = getattr(rule_engine, "pack", {})
    metric_ranges_raw: dict = pack.get("metric_ranges", {})
    metric_ranges: dict[str, tuple[float, float]] = {}
    for k, v in metric_ranges_raw.items():
        if isinstance(v, (list, tuple)) and len(v) >= 2:
            metric_ranges[k] = (float(v[0]), float(v[1]))
    arrays_to_states(ctx, states, entity_ids, metric_ranges)
