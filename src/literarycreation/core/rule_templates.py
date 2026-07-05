"""领域规则包 — JSON 外部化 + 动态加载 + 缓存。

规则包以 JSON 文件存放在 data/rule/ 目录下：
  data/rule/rules.json           — 内置默认规则包（所有领域在一份文件）
  data/rule/custom/*.json        — 用户自定义规则包（上传 / 手动放置）

启动时自动加载；无文件时回退到 FALLBACK_RULES（与旧版 RULE_TEMPLATES 一致，保证向后兼容）。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from .config import _get_data_dir

logger = logging.getLogger(__name__)

DEFAULT_RANGE = [0.0, 100.0]

# ?????????????rules.json ??/??????????????????
_FALLBACK_RULES: dict[str, dict[str, Any]] = {
    "literary": {
        "name": "📖 文学叙事",
        "domain": "literary",
        "display_name": "文学叙事",
        "metrics": ["trust", "tension", "affection", "power", "mystery", "fatigue"],
        "initial_metrics": {"trust": 50, "tension": 20, "affection": 40, "power": 40, "mystery": 30, "fatigue": 10},
        "metric_ranges": {"trust": [0, 100], "tension": [0, 100], "affection": [0, 100], "power": [0, 100], "mystery": [0, 100], "fatigue": [0, 100]},
        "thresholds": {},
        "actions": ["confront", "confess", "ally", "betray", "investigate", "protect", "manipulate", "observe"],
        "self_effects": {
            "confront": {"trust": -20, "tension": 25, "affection": -15, "fatigue": 10},
            "confess": {"trust": 15, "tension": -10, "affection": 20, "fatigue": 5},
            "ally": {"trust": 15, "tension": -15, "power": 8, "fatigue": -5},
            "betray": {"trust": -40, "tension": 30, "affection": -30, "power": 10, "fatigue": 5},
            "investigate": {"mystery": 10, "tension": 5, "fatigue": 8},
            "protect": {"affection": 10, "trust": 5, "fatigue": -5},
            "manipulate": {"power": 8, "trust": -10, "tension": 10, "fatigue": 5},
            "observe": {"fatigue": -3, "mystery": 2},
        },
        "target_effects": {
            "confront": {"trust": -15, "tension": 15, "affection": -10, "fatigue": 5},
            "betray": {"trust": -30, "tension": 20, "affection": -20, "fatigue": 8},
            "confess": {"trust": 12, "affection": 18},
            "manipulate": {"trust": -8, "tension": 8, "fatigue": 3},
            "ally": {"trust": 10, "affection": 5},
            "protect": {"affection": 8, "trust": 6},
        },
        "auto_effects": {
            "tension_decay": {"condition": "tension > 40", "effects": {"tension": -3, "trust": 1}},
            "trust_erosion": {"condition": "trust < 30", "effects": {"trust": -2, "tension": 3}},
        },
        "modules": {
            "pipeline": {"order": ["outline_control", "pacing_analyzer", "character_consistency", "conflict_progression", "finite_state_machine"], "conditionals": {}},
            "outline_control": {"deviation_threshold": 12.0, "catch_up_window": 2},
            "pacing_analyzer": {"stall_threshold": 3, "rush_threshold": 30.0, "plateau_rounds": 4},
            "character_consistency": {"warn_threshold": 0.7},
            "conflict_progression": {"climax_min_tension": 65.0, "early_drop_threshold": 30.0},
            "finite_state_machine": {
                "default_state": "neutral",
                "command_states": ["crisis"],
                "transition_rules": [
                    {"from": "neutral", "to": "crisis", "condition": {"tension": [">", 70]}},
                    {"from": "neutral", "to": "intimate", "condition": {"affection": [">", 70], "trust": [">", 50]}},
                    {"from": "intimate", "to": "betrayal", "condition": {"trust": ["<", 30], "affection": ["<", 40]}},
                    {"from": "crisis", "to": "neutral", "condition": {"tension": ["<", 40]}},
                ],
                "action_map": {
                    "neutral": {"action_type": "observe", "intensity": 0.3, "target": ""},
                    "crisis": None,
                    "intimate": {"action_type": "confess", "intensity": 0.7, "target": ""},
                    "betrayal": {"action_type": "betray", "intensity": 0.9, "target": ""},
                },
            },
        },
    },
}

# ── 动态加载缓存 ──
_RULE_CACHE: dict[str, dict[str, Any]] = {}
_rules_loaded_from_file: bool = False


def _load_json_file(path: Path) -> dict[str, dict[str, Any]] | None:
    """加载单个 JSON 文件，返回 domain→rule 映射。"""
    try:
        data = json.loads(path.read_text("utf-8"))
    except Exception:
        logger.debug("[rule_templates] 无法加载 %s，跳过", path)
        return None
    # 支持两份格式：{"domain1":{...}, "domain2":{...}} 或 {"domain":"...", ...}
    if all(isinstance(v, dict) and ("domain" in v or "metrics" in v) for v in data.values()):
        rules: dict[str, dict[str, Any]] = {}
        for k, v in data.items():
            dom = v.get("domain", k)
            if "name" not in v:
                v["name"] = v.get("display_name", dom)
            rules[dom] = v
        return rules
    if isinstance(data, dict) and "domain" in data:
        dom = data.get("domain", "")
        if dom:
            if "name" not in data:
                data["name"] = data.get("display_name", dom)
            return {dom: data}
    return None


def reload_rules() -> None:
    """扫描内置与自定义规则包。

    打包环境：
      FORGE_RULE_DIR  → 内置规则（安装目录，只读，随安装包更新）
      FORGE_DATA_DIR  → 用户自定义规则 (data/rule/custom/)，持久化、卸载不丢
    开发环境（FORGE_RULE_DIR 未设置）：回退到单一 data/rule/ 目录。
    """
    global _RULE_CACHE, _rules_loaded_from_file
    try:
        data_dir = _get_data_dir()
    except Exception:
        logger.warning("[rule_templates] 无法确定数据目录，使用兜底规则")
        _RULE_CACHE = dict(_FALLBACK_RULES)
        _rules_loaded_from_file = False
        return

    import os
    rule_root = os.getenv("FORGE_RULE_DIR", "")
    loaded: dict[str, dict[str, Any]] = {}
    if rule_root:
        bundle_dir = Path(rule_root)
        if bundle_dir.is_dir():
            # 1) 内置规则包（安装包提供）
            default_file = bundle_dir / "rules.json"
            if default_file.exists():
                rules = _load_json_file(default_file)
                if rules:
                    loaded.update(rules)
                    logger.info("[rule_templates] 加载内置规则(FORGE_RULE_DIR): %d 个领域", len(rules))
    else:
        # 开发模式：回退到 data/rule/
        bundle_dir = data_dir / "rule"
        if bundle_dir.is_dir():
            default_file = bundle_dir / "rules.json"
            if default_file.exists():
                rules = _load_json_file(default_file)
                if rules:
                    loaded.update(rules)
                    logger.info("[rule_templates] 加载内置规则(data/rule): %d 个领域", len(rules))
    # 2) 用户自定义规则（持久化目录，卸载不丢）
    custom_dir = data_dir / "rule" / "custom"
    if custom_dir.is_dir():
        for f in sorted(custom_dir.glob("*.json")):
            rules = _load_json_file(f)
            if rules:
                loaded.update(rules)
                logger.info("[rule_templates] 加载自定义规则: %s", f.name)

    if loaded:
        _RULE_CACHE = loaded
        _rules_loaded_from_file = True
    else:
        _RULE_CACHE = dict(_FALLBACK_RULES)
        _rules_loaded_from_file = False


def get_template(domain: str) -> dict[str, Any] | None:
    return _RULE_CACHE.get(domain)


def list_domains() -> list[dict[str, str]]:
    """供前端下拉使用的领域清单（仅返回从 JSON 文件加载成功的规则包）。

    _rules_loaded_from_file 为 False 时返回空列表——前端如实显示"无规则包"。
    """
    if not _rules_loaded_from_file:
        return []
    def _safe(s: str) -> str:
        return "".join(ch if ord(ch) < 0xD800 or ord(ch) > 0xDFFF else "?" for ch in s)
    return [{"domain": k, "name": _safe(v.get("name", v.get("display_name", k)))}
            for k, v in _RULE_CACHE.items()]


# ── 模块加载即初始化 ──
reload_rules()
