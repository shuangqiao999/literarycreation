"""Algorithm modules for LiteraryCreation deduction engine.

Lightweight design: only outline deviation detection remains.
"""
from .base import AlgorithmModule, ModuleContext, arrays_to_states, states_to_arrays
from .outline_control import (
    CorrectionLevel,
    build_correction_prompt,
    compute_deviation,
    resolve_correction,
)

__all__ = [
    "AlgorithmModule",
    "CorrectionLevel",
    "ModuleContext",
    "arrays_to_states",
    "build_correction_prompt",
    "compute_deviation",
    "resolve_correction",
    "states_to_arrays",
]
