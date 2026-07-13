"""情感引擎 — 交叉指标合成复合情感 + 角色投入追踪 + 高潮回报校验。"""
from __future__ import annotations

import random as _rnd
from typing import Any


class EmotionalComposer:
    """交叉指标合成复合情感。纯规则，零 LLM 调用。"""

    # 冲突对表：(metric_a, metric_b, pattern) → [文学变体]
    # pattern: "high_low" = a高b低, "high_high" = a高b高, "low_low" = a低b低
    _CONFLICT_PAIRS: dict[tuple, list[str]] = {
        ("affection", "trust", "high_low"): [
            "你心里装着一个人，但每次靠近都像踩薄冰——你害怕下一个表情就是背叛的证据",
            "你渴望他的触碰，但你的手指在碰到他之前会先缩回来。不是他的问题——是你的",
            "你想对他说一句话，但它在你喉咙里卡了三天还是没出来。不是因为不自信——是因为有太多理由不该相信他",
        ],
        ("affection", "trust", "low_high"): [
            "你信任这个人——完完全全地信任。但信任和心跳不是同一件事，你隐隐知道这个区别",
            "他是你最可靠的盟友，但你开始厌恶「可靠」这两个字。你想从他身上得到超出同盟的东西——而你不敢开口",
        ],
        ("power", "affection", "high_low"): [
            "你拥有改变命运的力量，但你的手在发抖——不是恐惧，是因为你在想：如果我赢了，代价是什么",
            "你可以毁掉他。但你知道——毁掉他的同时，你心里的一部分也会变成灰",
        ],
        ("power", "affection", "low_high"): [
            "你深爱着的人正在离开你的控制范围。你拥有的力量不够留住她——而你现在才意识到这一点",
            "你在权力的游戏中节节败退，但她却越来越靠近你。有时候你分不清——这是拯救还是另一种蚕食",
        ],
        ("tension", "fatigue", "high_high"): [
            "局势在升温，但你的身体在说：停下来。你在这两者间被反复撕扯，不知道哪一种疼痛更真实",
            "世界在加速，而你已经在减速。这种时间上的错位比任何一次冲突都更让你恐惧——你在想：会不会我才是那个拖后腿的人",
        ],
        ("tension", "fatigue", "high_low"): [
            "局势紧张但你的精力充沛——这种状态比疲惫更危险，因为它让你以为自己可以承受一切",
        ],
        ("mystery", "trust", "high_low"): [
            "真相碎片在你手里越积越多，但没有人可以信任、没有人可以商量——你不知道自己在揭开真相还是在自毁",
            "你独自承载着比任何人都多的信息。有时候你羡慕那些一无所知的人——他们的世界简单得多",
        ],
        ("mystery", "power", "high_low"): [
            "你知道得太多了——多到足以改变局面，但少到无法说服任何人。这种中间状态是最大的消耗",
        ],
    }

    def compose(self, metrics: dict[str, float]) -> str | None:
        """尝试合成复合情感文本。命中返回描述，未命中返回 None。"""
        m = metrics
        high_thresh = 70.0
        low_thresh = 30.0

        high_keys = [k for k, v in m.items() if v > high_thresh]
        low_keys = [k for k, v in m.items() if v < low_thresh]

        candidates: list[str] = []
        for a in high_keys:
            for b in low_keys:
                key = (a, b, "high_low")
                if key in self._CONFLICT_PAIRS:
                    candidates.extend(self._CONFLICT_PAIRS[key])
        for a in high_keys:
            for b in high_keys:
                if a >= b:
                    continue
                key = (a, b, "high_high")
                if key in self._CONFLICT_PAIRS:
                    candidates.extend(self._CONFLICT_PAIRS[key])
        if not candidates:
            return None
        return _rnd.choice(candidates)


class EmotionalInvestment:
    """追踪角色情感投入额，校验高潮回报比例。"""

    _WEIGHTS: dict[str, float] = {
        "betray": 5, "confess": 4, "confront": 3,
        "protect": 3, "ally": 2, "manipulate": 2,
        "investigate": 1, "observe": 0,
    }

    def __init__(self):
        self._ledger: dict[str, float] = {}

    def record(self, char_id: str, action_type: str, intensity: float) -> None:
        wt = self._WEIGHTS.get(action_type, 1.0)
        self._ledger[char_id] = self._ledger.get(char_id, 0.0) + wt * max(0.0, min(1.0, intensity))

    def require_climax_words(self, char_id: str, base_per_ch: int) -> int:
        points = self._ledger.get(char_id, 0.0)
        needed = int(points * 8)
        return max(base_per_ch // 4, min(needed, base_per_ch * 3))

    def check_payoff(self, chapter_idx: int, total_chapters: int,
                     char_words: dict[str, int]) -> list[str]:
        """在高潮章（后 20%）校验回报比例。返回警告列表。"""
        if chapter_idx < int(total_chapters * 0.8) or not self._ledger:
            return []
        warnings: list[str] = []
        for cid, points in self._ledger.items():
            if points < 10:
                continue
            actual = char_words.get(cid, 0)
            needed = int(points * 5)
            if actual < needed * 0.6:
                warnings.append(
                    f"角色 {cid[:8]} 的情感投资 ({points:.0f}点) 回报不足："
                    f"{actual} 字 < 需 {needed} 字。请在本章为之安排一个有分量的场景。")
        return warnings

    def to_dict(self) -> dict[str, Any]:
        return {"ledger": dict(self._ledger)}

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> EmotionalInvestment:
        inv = cls()
        for k, v in ((data or {}).get("ledger") or {}).items():
            try:
                inv._ledger[str(k)] = float(v)
            except (TypeError, ValueError):
                pass
        return inv
