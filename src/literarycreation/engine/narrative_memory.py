"""角色叙事记忆 — 每个角色亲身经历的关键事件的自然语言摘要（FIFO）。

区别于 LanceDB 语义检索（碎片、不可控），这里保存角色"记得的事"，
按发生顺序累积、上限裁剪，注入决策 prompt 提升跨轮/跨章连贯性。
可随 story_state / pause 快照序列化。
"""
from __future__ import annotations

from typing import Any


class NarrativeMemoryStore:
    """Per-agent FIFO 叙事记忆，含情感关系记忆独立队列。"""

    # 情感关系关键词 → 分类标签
    _EMOTIONAL_TAGS: dict[str, str] = {
        "承诺": "promise", "答应": "promise", "保证": "promise", "发誓": "promise",
        "背叛": "betrayal", "出卖": "betrayal", "举报": "betrayal", "告密": "betrayal",
        "救命": "debt", "恩情": "debt", "亏欠": "debt", "欠": "debt", "人情": "debt",
        "爱上": "affection", "心动": "affection", "倾心": "affection",
        "恨": "hatred", "仇恨": "hatred", "报复": "hatred",
    }

    def __init__(self, cap: int = 8) -> None:
        self._cap = max(1, int(cap))
        self._mem: dict[str, list[str]] = {}
        # 情感记忆：tag → agent_id → [summaries]，独立于 FIFO 事件队列，不受 cap 限制
        self._emo_mem: dict[str, dict[str, list[str]]] = {}

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

        # 情感关系记忆：不随 FIFO 淘汰
        for kw, tag in self._EMOTIONAL_TAGS.items():
            if kw in s:
                emo_lst = self._emo_mem.setdefault(tag, {}).setdefault(agent_id, [])
                if not emo_lst or emo_lst[-1] != s:
                    emo_lst.append(s)
                if len(emo_lst) > 5:
                    del emo_lst[0]

    def get(self, agent_id: str) -> list[str]:
        return list(self._mem.get(agent_id, []))

    def inject_prompt(self, agent_id: str) -> str:
        """构建注入决策 prompt 的记忆文本；无记忆返回空串。"""
        parts: list[str] = []
        mems = self.get(agent_id)
        if mems:
            parts.append("\n".join(f"- {m}" for m in mems))
        # 情感记忆单独注入
        emo_parts: list[str] = []
        for tag, label in [("promise","你对他人许下的承诺"),("betrayal","你经历的背叛"),
                           ("debt","你欠下的人情或恩情"),("affection","你产生的情感"),
                           ("hatred","你心中的恨意")]:
            for aid, lst in self._emo_mem.get(tag, {}).items():
                if aid == agent_id and lst:
                    emo_parts.append(f"【{label}】" + "；".join(f"- {m[:60]}" for m in lst[-3:]))
        if emo_parts:
            parts.insert(0, "\n".join(emo_parts))
        return "\n".join(parts) if parts else ""

    # ── 序列化（用于 pause/resume 与 story_state 持久化）──

    def to_dict(self) -> dict[str, Any]:
        return {
            "cap": self._cap,
            "mem": {k: list(v) for k, v in self._mem.items()},
            "emo_mem": {tag: {aid: list(lst) for aid, lst in d.items()}
                        for tag, d in self._emo_mem.items()},
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> NarrativeMemoryStore:
        store = cls(cap=int((data or {}).get("cap", 8)))
        for k, v in ((data or {}).get("mem") or {}).items():
            if isinstance(v, list):
                store._mem[str(k)] = [str(x) for x in v][-store._cap:]
        emo_raw = (data or {}).get("emo_mem") or {}
        for tag, d in emo_raw.items():
            if isinstance(d, dict):
                for aid, lst in d.items():
                    if isinstance(lst, list):
                        store._emo_mem.setdefault(str(tag), {}).setdefault(str(aid), []).extend(
                            str(x) for x in lst[-5:])
        return store
