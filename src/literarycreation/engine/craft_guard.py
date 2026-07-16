"""技艺守卫 — 场景权重分配 + 母题观察与培养。"""
from __future__ import annotations

from typing import Any


class SceneAllocator:
    """事件分级 → 字数预算分配。"""

    def allocate(self, round_events: list[str], outline: dict | None,
                 chapter_context: Any, total_words: int) -> str:
        if total_words < 500:
            return ""
        events = [str(e)[:160] for e in (round_events or []) if e]
        if len(events) < 2:
            return ""

        scores: list[float] = []
        for e in events:
            s = 1.0
            # 关键事件加权
            if outline and outline.get("key_events"):
                for ke in outline["key_events"]:
                    if isinstance(ke, dict) and (ke.get("event") or "")[:40] in e:
                        if ke.get("level") == "hard":
                            s = 3.0
                        elif ke.get("level") == "soft":
                            s = 2.0
                        break
            # 场景片段加权
            if "[场景]" in e:
                s = max(s, 1.5)
            scores.append(s)

        total = sum(scores) or 1.0
        alloc = [max(200, int(total_words * (sc / total))) for sc in scores]
        # 重归一化防溢出
        ratio = total_words / sum(alloc)
        alloc = [int(a * ratio) for a in alloc]

        parts = ["【场景权重 — 按以下权重分配笔墨（字数仅为参考比例，非硬性要求；以情节紧凑为先）】"]
        for j, (ev, w) in enumerate(zip(events, alloc), 1):
            snippet = ev[:50].replace("\n", " ")
            label = "核心转折" if scores[j - 1] >= 3 else ("推进" if scores[j - 1] >= 2 else "过渡")
            parts.append(f"  场景{j}（{label}，~{w}字）：{snippet}...")
        return "\n".join(parts)


class MotifTracker:
    """母题培养：蓄意重复 vs 意外重复。"""

    def __init__(self):
        self._observations: dict[str, list[dict]] = {}
        self._motifs: list[dict] = []

    def observe_chapter(self, text: str, chapter_idx: int) -> None:
        head = text[:150].replace("\n", "")
        tail = text[-150:].replace("\n", "")
        for chunk in (head, tail):
            for length in range(6, 16):
                for i in range(len(chunk) - length + 1):
                    phrase = chunk[i:i + length]
                    # 只有汉字占比 > 60% 才算有意短语
                    han = sum(1 for c in phrase if '\u4e00' <= c <= '\u9fff')
                    if han < length * 0.6:
                        continue
                    self._observations.setdefault(phrase, []).append({
                        "chapter": chapter_idx, "len": length,
                    })
        # 清理多余记录：每个 phrase 保最近 8 章
        for p in self._observations:
            if len(self._observations[p]) > 8:
                self._observations[p] = self._observations[p][-5:]

    def promote_motifs(self) -> list[dict]:
        self._motifs = []
        for phrase, occurrences in self._observations.items():
            if len(occurrences) < 3:
                continue
            chs = sorted(set(o["chapter"] for o in occurrences))
            if len(chs) < 3:
                continue
            # 检查是否在章首或章尾位置
            self._motifs.append({
                "phrase": phrase,
                "chapters": chs,
                "count": len(occurrences),
            })
        # 最多保留 3 个母题
        self._motifs.sort(key=lambda m: -m["count"])
        self._motifs = self._motifs[:3]
        return self._motifs

    def inject_prompt(self, chapter_idx: int) -> str:
        self.promote_motifs()
        if not self._motifs:
            return ""
        items = []
        for m in self._motifs:
            items.append(f"「{m['phrase']}」（已在 {len(m['chapters'])} 章出现）")
        return ("【母题回声 — 以下意象已在全书中自然生长，"
                "请在本章合适处用一个有微妙变化的新版本重新召唤它】\n" +
                "\n".join(f"- {i}" for i in items))

    def to_dict(self) -> dict[str, Any]:
        return {"observations": {k: list(v) for k, v in self._observations.items()},
                "motifs": list(self._motifs)}

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> MotifTracker:
        mt = cls()
        for k, v in ((data or {}).get("observations") or {}).items():
            if isinstance(v, list):
                mt._observations[str(k)] = [dict(o) for o in v if isinstance(o, dict)]
        mt._motifs = list((data or {}).get("motifs") or [])
        return mt
