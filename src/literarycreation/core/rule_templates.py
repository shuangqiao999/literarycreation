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

# ??rules.json ??/??????????????????
_FALLBACK_RULES: dict[str, dict[str, Any]] = {
    "literary_realism": {
        "name": "📖 现实主义", "domain": "literary_realism", "display_name": "现实主义",
        "style": "现实主义",
        "metrics": ["trust", "tension", "affection", "power", "mystery", "fatigue"],
        "initial_metrics": {"trust": 50, "tension": 20, "affection": 40, "power": 40, "mystery": 30, "fatigue": 10},
        "metric_ranges": {m: [0, 100] for m in ["trust", "tension", "affection", "power", "mystery", "fatigue"]},
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
        "modules": {"outline_control": {"deviation_threshold": 12.0, "catch_up_window": 2}},
    },
    "literary_romance": {
        "name": "💕 浪漫主义", "domain": "literary_romance", "display_name": "浪漫主义",
        "style": "浪漫主义",
        "metrics": ["trust", "tension", "affection", "power", "mystery", "fatigue"],
        "initial_metrics": {"trust": 45, "tension": 15, "affection": 55, "power": 35, "mystery": 25, "fatigue": 10},
        "metric_ranges": {m: [0, 100] for m in ["trust", "tension", "affection", "power", "mystery", "fatigue"]},
        "thresholds": {},
        "actions": ["confront", "confess", "ally", "betray", "investigate", "protect", "manipulate", "observe"],
        "self_effects": {
            "confront": {"trust": -15, "tension": 20, "affection": -10, "fatigue": 8},
            "confess": {"trust": 20, "tension": -15, "affection": 30, "fatigue": 3},
            "ally": {"trust": 18, "tension": -12, "power": 5, "fatigue": -5},
            "betray": {"trust": -45, "tension": 25, "affection": -35, "power": 8, "fatigue": 5},
            "investigate": {"mystery": 8, "tension": 3, "fatigue": 6},
            "protect": {"affection": 15, "trust": 8, "fatigue": -5},
            "manipulate": {"power": 5, "trust": -8, "tension": 8, "fatigue": 4},
            "observe": {"fatigue": -3, "mystery": 2},
        },
        "target_effects": {
            "confront": {"trust": -10, "tension": 12, "affection": -8, "fatigue": 4},
            "betray": {"trust": -35, "tension": 18, "affection": -25, "fatigue": 6},
            "confess": {"trust": 15, "affection": 25},
            "manipulate": {"trust": -6, "tension": 6, "fatigue": 2},
            "ally": {"trust": 12, "affection": 8},
            "protect": {"affection": 10, "trust": 8},
        },
        "auto_effects": {
            "tension_decay": {"condition": "tension > 35", "effects": {"tension": -2, "trust": 1}},
            "affection_growth": {"condition": "affection > 60", "effects": {"affection": 2, "trust": 1}},
        },
        "modules": {"outline_control": {"deviation_threshold": 15.0, "catch_up_window": 2}},
    },
    "literary_suspense": {
        "name": "🔍 悬疑", "domain": "literary_suspense", "display_name": "悬疑",
        "style": "悬疑",
        "metrics": ["trust", "tension", "affection", "power", "mystery", "fatigue"],
        "initial_metrics": {"trust": 30, "tension": 50, "affection": 30, "power": 35, "mystery": 70, "fatigue": 15},
        "metric_ranges": {m: [0, 100] for m in ["trust", "tension", "affection", "power", "mystery", "fatigue"]},
        "thresholds": {},
        "actions": ["confront", "confess", "ally", "betray", "investigate", "protect", "manipulate", "observe"],
        "self_effects": {
            "confront": {"trust": -25, "tension": 30, "affection": -15, "fatigue": 12},
            "confess": {"trust": 10, "tension": -5, "affection": 15, "fatigue": 8},
            "ally": {"trust": 12, "tension": -10, "power": 5, "fatigue": -3},
            "betray": {"trust": -40, "tension": 35, "affection": -25, "power": 12, "fatigue": 8},
            "investigate": {"mystery": 18, "tension": 8, "fatigue": 10},
            "protect": {"affection": 8, "trust": 5, "fatigue": -5},
            "manipulate": {"power": 10, "trust": -12, "tension": 12, "fatigue": 6},
            "observe": {"fatigue": -3, "mystery": 3},
        },
        "target_effects": {
            "confront": {"trust": -18, "tension": 18, "affection": -12, "fatigue": 6},
            "betray": {"trust": -35, "tension": 25, "affection": -20, "fatigue": 10},
            "confess": {"trust": 10, "affection": 12},
            "manipulate": {"trust": -10, "tension": 10, "fatigue": 4},
            "ally": {"trust": 8, "affection": 5},
            "protect": {"affection": 6, "trust": 5},
        },
        "auto_effects": {
            "tension_decay": {"condition": "tension > 40", "effects": {"tension": -4, "trust": 1}},
            "mystery_fade": {"condition": "mystery > 70", "effects": {"mystery": -3}},
        },
        "modules": {"outline_control": {"deviation_threshold": 10.0, "catch_up_window": 1}},
    },
    "literary_epic": {
        "name": "⚔️ 史诗", "domain": "literary_epic", "display_name": "史诗",
        "style": "史诗",
        "metrics": ["trust", "tension", "affection", "power", "mystery", "fatigue"],
        "initial_metrics": {"trust": 55, "tension": 30, "affection": 35, "power": 50, "mystery": 20, "fatigue": 15},
        "metric_ranges": {m: [0, 100] for m in ["trust", "tension", "affection", "power", "mystery", "fatigue"]},
        "thresholds": {},
        "actions": ["confront", "confess", "ally", "betray", "investigate", "protect", "manipulate", "observe"],
        "self_effects": {
            "confront": {"trust": -25, "tension": 30, "affection": -20, "power": 5, "fatigue": 12},
            "confess": {"trust": 18, "tension": -10, "affection": 25, "fatigue": 8},
            "ally": {"trust": 20, "tension": -20, "power": 10, "fatigue": -8},
            "betray": {"trust": -50, "tension": 35, "affection": -35, "power": 15, "fatigue": 8},
            "investigate": {"mystery": 10, "tension": 5, "fatigue": 8},
            "protect": {"affection": 12, "trust": 8, "power": 3, "fatigue": -5},
            "manipulate": {"power": 10, "trust": -12, "tension": 12, "fatigue": 6},
            "observe": {"fatigue": -5, "mystery": 2},
        },
        "target_effects": {
            "confront": {"trust": -20, "tension": 20, "affection": -15, "fatigue": 8},
            "betray": {"trust": -40, "tension": 25, "affection": -25, "fatigue": 10},
            "confess": {"trust": 15, "affection": 20},
            "manipulate": {"trust": -10, "tension": 10, "fatigue": 5},
            "ally": {"trust": 15, "affection": 8},
            "protect": {"affection": 10, "trust": 8},
        },
        "auto_effects": {
            "tension_decay": {"condition": "tension > 45", "effects": {"tension": -3, "trust": 1}},
            "fatigue_recovery": {"condition": "fatigue > 50", "effects": {"fatigue": -5}},
        },
        "modules": {"outline_control": {"deviation_threshold": 15.0, "catch_up_window": 3}},
    },
    "literary_court": {
        "name": "👑 宫廷剧", "domain": "literary_court", "display_name": "宫廷剧",
        "style": "宫廷剧",
        "metrics": ["trust", "tension", "affection", "power", "mystery", "fatigue"],
        "initial_metrics": {"trust": 30, "tension": 25, "affection": 35, "power": 50, "mystery": 40, "fatigue": 15},
        "metric_ranges": {m: [0, 100] for m in ["trust", "tension", "affection", "power", "mystery", "fatigue"]},
        "thresholds": {},
        "actions": ["confront", "confess", "ally", "betray", "investigate", "protect", "manipulate", "observe"],
        "self_effects": {
            "confront": {"trust": -20, "tension": 25, "affection": -15, "power": 3, "fatigue": 10},
            "confess": {"trust": 12, "tension": -8, "affection": 25, "fatigue": 6},
            "ally": {"trust": 18, "tension": -12, "power": 12, "fatigue": -3},
            "betray": {"trust": -45, "tension": 30, "affection": -30, "power": 15, "fatigue": 5},
            "investigate": {"mystery": 12, "tension": 5, "power": 3, "fatigue": 8},
            "protect": {"affection": 12, "trust": 6, "power": -2, "fatigue": -3},
            "manipulate": {"power": 12, "trust": -15, "tension": 12, "fatigue": 5},
            "observe": {"fatigue": -3, "mystery": 3},
        },
        "target_effects": {
            "confront": {"trust": -15, "tension": 15, "affection": -10, "power": -3, "fatigue": 5},
            "betray": {"trust": -35, "tension": 20, "affection": -22, "power": -8, "fatigue": 8},
            "confess": {"trust": 10, "affection": 20},
            "manipulate": {"trust": -10, "power": -3, "tension": 10, "fatigue": 3},
            "ally": {"trust": 12, "affection": 5, "power": 5},
            "protect": {"affection": 8, "trust": 6},
        },
        "auto_effects": {
            "tension_decay": {"condition": "tension > 40", "effects": {"tension": -2, "trust": 1}},
            "power_consolidation": {"condition": "power > 60 and trust < 40", "effects": {"power": 2, "trust": -1}},
        },
        "modules": {"outline_control": {"deviation_threshold": 10.0, "catch_up_window": 2}},
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
