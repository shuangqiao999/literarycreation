"""Minimal configuration for LiteraryCreation — non-endpoint settings only.

All LLM/embedding endpoint resolution is delegated to core.providers.registry.
Hardcoded addresses and model names are FORBIDDEN here.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def _get_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path.cwd()


def _get_data_dir() -> Path:
    env_data = os.getenv("FORGE_DATA_DIR", "")
    if env_data:
        p = Path(env_data)
        if p.is_absolute():
            return p
        _log_env_once("FORGE_DATA_DIR", env_data, "not-absolute")
    else:
        _log_env_once("FORGE_DATA_DIR", "", "unset")
    return _get_root() / "data"

_data_logged: set = set()

def _log_env_once(key: str, val: str, reason: str):
    if key in _data_logged:
        return
    _data_logged.add(key)
    import logging
    logging.getLogger("literarycreation").info("ENV %s=%s (%s → fallback to %s)",
        key, val[:80] if val else "(empty)", reason, str(_get_root() / "data" if reason != "unset" else "root/data"))


class DeductionConfig:
    """Non-endpoint configuration (rounds, agents, concurrency, data paths)."""

    def __init__(self):
        self.project_root = _get_root()
        self.deduction_data_dir = _get_data_dir()
        self.deduction_default_rounds = int(os.getenv("FORGE_DEFAULT_ROUNDS", "10"))
        self.deduction_candidate_count = int(os.getenv("FORGE_CANDIDATE_COUNT", "3"))
        self.deduction_llm_temperature = float(os.getenv("FORGE_LLM_TEMPERATURE", "0.85"))
        self.deduction_similarity_threshold = float(os.getenv("FORGE_SIMILARITY_THRESHOLD", "0.4"))

    @property
    def deduction_max_agents(self) -> int:
        from literarycreation.core.providers import registry
        return int(registry._data.get("max_agents") or os.getenv("FORGE_MAX_AGENTS", "10000"))

    @property
    def deduction_max_concurrent(self) -> int:
        from literarycreation.core.providers import registry
        return int(registry._data.get("max_concurrent") or os.getenv("FORGE_MAX_CONCURRENT", "2"))

    @property
    def deduction_retrieve_top_k(self) -> int:
        from literarycreation.core.providers import registry
        return int(registry._data.get("retrieve_top_k") or os.getenv("FORGE_RETRIEVE_TOP_K", "5"))

    def __getattr__(self, name: str):
        return None


config = DeductionConfig()
