"""Prompt 自适应 — 根据模型参数量级简化 prompt，防止小模型 400 错误。"""
from __future__ import annotations

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


def simplify_prompt(prompt: str, tier: str) -> str:
    """根据模型档位压缩 prompt。"""
    if tier == "large":
        return prompt
    if tier == "medium":
        return prompt
    # small/tiny: 去掉大段示例，压缩规则为精简指令
    lines = prompt.split("\n")
    result: list[str] = []
    in_example = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("示例") or stripped.startswith("例") or stripped.startswith("如") or "```" in stripped:
            in_example = not in_example
            continue
        if in_example:
            continue
        result.append(line)
    simplified = "\n".join(result)
    if tier == "tiny":
        # 进一步压缩：每段不超过3个要点
        final: list[str] = []
        count = 0
        for line in simplified.split("\n"):
            if line.strip().startswith("-") or line.strip().startswith("*") or line.strip().startswith("1."):
                count += 1
                if count > 15:
                    continue
            final.append(line)
        simplified = "\n".join(final)
    return simplified


def reduce_max_tokens(base: int, tier: str) -> int:
    """小模型需降低 max_tokens 防 400 错误。"""
    if tier == "tiny":
        return min(base, 1024)
    if tier == "small":
        return min(base, 4096)
    return base
