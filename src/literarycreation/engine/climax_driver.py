"""高潮推进器 — 对 CanonLedger"否定性检查"的补充，做"肯定性引导"。

在故事中后段（默认 60% 之后）检查核心矛盾是否在推进：麦高芬是否按计划揭示、
反派是否已登场/施压、未解悬念是否在收束而非增殖。返回引导指令注入写作 prompt。
为避免与 EventScheduler 的硬事件双重驱动，仅在本章无强制事件时由调用方注入。
"""
from __future__ import annotations

from typing import Any


class ClimaxDriver:
    def __init__(self, total_chapters: int) -> None:
        self.total_chapters = max(1, int(total_chapters))
        self.climax_start = max(1, int(self.total_chapters * 0.60))

    def check(self, chapter_idx: int, canon: Any, story_state: dict[str, Any] | None) -> list[str]:
        if chapter_idx < self.climax_start:
            return []
        story_state = story_state or {}
        guidance: list[str] = []

        # 1) 麦高芬是否按计划揭示/取得
        macguffins = getattr(canon, "macguffins", {}) or {}
        for mid, m in macguffins.items():
            reveal_round = int(m.get("reveal_round", 0) or 0)
            if not m.get("acquired") and reveal_round and chapter_idx >= reveal_round:
                guidance.append(f"核心线索「{mid}」应已揭示/取得却仍悬空，本章必须实质推动它")

        # 2) 未解悬念应收束而非增殖
        open_threads = story_state.get("open_threads") or []
        if len(open_threads) > 4:
            guidance.append(f"当前有 {len(open_threads)} 条未解悬念，本章应至少收束 1-2 条，不要再抛新钩子")

        # 3) 临近结尾仍无任何死亡/重大代价 → 提示冲突升级
        if chapter_idx >= int(self.total_chapters * 0.8):
            dead = getattr(canon, "dead", {}) or {}
            if not dead:
                guidance.append("已临近结局却无任何重大代价或牺牲，本章应升级冲突、让抉择产生不可逆后果")

        # 4) 情感投资回报校验
        inv_data = story_state.get("emotional_investment")
        if inv_data and chapter_idx >= int(self.total_chapters * 0.8):
            from .emotional_engine import EmotionalInvestment
            inv = EmotionalInvestment.from_dict(inv_data)
            # 用梗概方式检查：如果角色有大量未偿还投资
            ledger = inv._ledger
            heavy_chars = [c for c, pts in ledger.items() if pts > 15]
            if heavy_chars:
                guidance.append(
                    f"角色 {','.join(c[:6] for c in heavy_chars[:3])} "
                    f"已累计大量情感铺垫，本章请优先安排他们收束情感弧光——"
                    f"不能只是提及，要用一个'场景'来偿还读者 20 章的等待。")

        if not guidance:
            return []
        return ["【高潮推进】" + g for g in guidance]

    def build_text(self, chapter_idx: int, canon: Any, story_state: dict[str, Any] | None) -> str:
        items = self.check(chapter_idx, canon, story_state)
        return "\n".join(items) if items else ""
