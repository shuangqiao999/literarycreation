"""Algorithm modules for LiteraryCreation deduction engine.

Module registry: all available algorithm modules keyed by their name property.
New modules: import and register here to make them available in rules.json pipeline.
"""
from .base import AlgorithmModule, ModuleContext, arrays_to_states, states_to_arrays
from .fsm_module import FiniteStateMachineModule
from .outline_control import OutlineControlModule
from .pacing_analyzer import PacingAnalyzerModule
from .character_consistency import CharacterConsistencyModule
from .conflict_progression import ConflictProgressionModule
from .pipeline_engine import PipelineEngine
from .module_utils import apply_context_results, build_context, build_pipeline

# Module registry: {module_name: module_class}
MODULE_REGISTRY: dict[str, type[AlgorithmModule]] = {
    "finite_state_machine": FiniteStateMachineModule,
    "outline_control": OutlineControlModule,
    "pacing_analyzer": PacingAnalyzerModule,
    "character_consistency": CharacterConsistencyModule,
    "conflict_progression": ConflictProgressionModule,
}

__all__ = [
    "AlgorithmModule",
    "CharacterConsistencyModule",
    "ConflictProgressionModule",
    "FiniteStateMachineModule",
    "MODULE_REGISTRY",
    "ModuleContext",
    "OutlineControlModule",
    "PacingAnalyzerModule",
    "PipelineEngine",
    "apply_context_results",
    "arrays_to_states",
    "build_context",
    "build_pipeline",
    "states_to_arrays",
]
