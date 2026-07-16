"""Prompt 自适应 — 根据模型参数量级简化 prompt，防止小模型 400 错误。"""
from __future__ import annotations

import re as _re

_MODEL_TIERS: dict[str, str] = {}
# 自动注册已知模型
for sz, tier in [("1b","tiny"),("2b","tiny"),("3b","tiny"),
                 ("7b","small"),("8b","small"),("9b","small"),
                 ("13b","medium"),("14b","medium"),("20b","medium"),
                 ("32b","large"),("70b","large"),("72b","large")]:
    _MODEL_TIERS[sz] = tier


def detect_tier(model_name: str) -> str:
    """从模型名推断能力档位。"""
    lower = model_name.lower()
    for sz, tier in sorted(_MODEL_TIERS.items(), key=lambda x: -len(x[0])):
        if sz in lower:
            return tier
    return "medium"  # 未知模型保守假设中档


_BULLET_RE = _re.compile(r"^(?:[-*]|\d+\.)\s")


def simplify_prompt(prompt: str, tier: str) -> str:
    """根据模型档位压缩 prompt。

    仅对 tiny 档做保守压缩（限制要点行数量），绝不删除输出格式、
    JSON schema、围栏代码块或「## 文本」等任务关键内容——
    误删待处理文本会导致模型收到空任务而返回空结果。
    """
    if tier != "tiny":
        return prompt
    final: list[str] = []
    count = 0
    for line in prompt.split("\n"):
        if _BULLET_RE.match(line.strip()):
            count += 1
            if count > 15:
                continue
        final.append(line)
    return "\n".join(final)


def reduce_max_tokens(base: int, tier: str) -> int:
    """按档位限制 max_tokens，所有模型上限 16384。

    下限不能低于 8192：蓝图/图谱抽取的 JSON 输出可能较长，
    过低的上限会截断 JSON 导致解析失败。
    """
    result = base
    if tier in ("tiny", "small"):
        result = min(base, 8192)
    return min(result, 16384) if result > 0 else 0
