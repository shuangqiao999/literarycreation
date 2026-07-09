"""角色叙事记忆 — 每个角色亲身经历的关键事件的自然语言摘要（FIFO）。

区别于 LanceDB 语义检索（碎片、不可控），这里保存角色"记得的事"，
按发生顺序累积、上限裁剪，注入决策 prompt 提升跨轮/跨章连贯性。
可随 story_state / pause 快照序列化。
"""
from __future__ import annotations

from typing import Any


class NarrativeMemoryStore:
    """Per-agent FIFO 叙事记忆。"""

    def __init__(self, cap: int = 8) -> None:
        self._cap = max(1, int(cap))
        self._mem: dict[str, list[str]] = {}

    def add(self, agent_id: str, summary: str) -> None:
        s = (summary or "").strip()
        if not agent_id or not s:
            return
        lst = self._mem.setdefault(agent_id, [])
        # 去重相邻重复
        if lst and lst[-1] == s:
            return
        lst.append(s)
        if len(lst) > self._cap:
            del lst[0: len(lst) - self._cap]

    def get(self, agent_id: str) -> list[str]:
        return list(self._mem.get(agent_id, []))

    def inject_prompt(self, agent_id: str) -> str:
        """构建注入决策 prompt 的记忆文本；无记忆返回空串。"""
        mems = self.get(agent_id)
        if not mems:
            return ""
        return "\n".join(f"- {m}" for m in mems)

    # ── 序列化（用于 pause/resume 与 story_state 持久化）──

    def to_dict(self) -> dict[str, Any]:
        return {"cap": self._cap, "mem": {k: list(v) for k, v in self._mem.items()}}

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> NarrativeMemoryStore:
        store = cls(cap=int((data or {}).get("cap", 8)))
        for k, v in ((data or {}).get("mem") or {}).items():
            if isinstance(v, list):
                store._mem[str(k)] = [str(x) for x in v][-store._cap:]
        return store
