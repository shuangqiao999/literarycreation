"""Smoke test: narrative_memory + word_count_enforcer + climax_driver（离线）。"""
import asyncio

from literarycreation.engine.climax_driver import ClimaxDriver
from literarycreation.engine.narrative_memory import NarrativeMemoryStore
from literarycreation.engine.word_count_enforcer import WordCountEnforcer

# ── narrative_memory：FIFO + 上限 + 注入 + 序列化 ──
nm = NarrativeMemoryStore(cap=3)
for k in range(5):
    nm.add("a1", f"第{k}轮：调查")
mem = nm.get("a1")
assert len(mem) == 3 and mem[0] == "第2轮：调查", mem  # FIFO 裁剪
nm.add("a1", "第2轮：调查")  # 与末尾不同（末尾是第4轮）→ 应加入
assert "亲身经历" not in nm.inject_prompt("a1")  # inject 只含条目
assert nm.inject_prompt("a1").startswith("- ")
rt = NarrativeMemoryStore.from_dict(nm.to_dict())
assert rt.get("a1") == nm.get("a1")
assert nm.inject_prompt("nobody") == ""
print("narrative_memory OK")


# ── word_count_enforcer ──
enf = WordCountEnforcer(min_ratio=0.75)
ok, _ = enf.check("x" * 800, 1000)
assert ok  # 800 >= 750
ok2, msg = enf.check("x" * 500, 1000)
assert not ok2 and "字数不足" in msg
assert enf.check("anything", 0)[0] is True  # target=0 视为不限


async def _run_enforce():
    calls = {"n": 0}

    async def expand(prompt):
        calls["n"] += 1
        return "y" * 900  # 扩写到 900

    out = await enf.enforce("z" * 400, 1000, expand, max_retries=2)
    assert len(out) == 900 and calls["n"] >= 1, (len(out), calls)

    # 扩写反而更短 → 保留原稿
    async def shrink(prompt):
        return "s" * 100
    out2 = await enf.enforce("z" * 400, 1000, shrink, max_retries=2)
    assert len(out2) == 400, len(out2)

asyncio.run(_run_enforce())
print("word_count_enforcer OK")


# ── climax_driver ──
class _Canon:
    def __init__(self, macguffins=None, dead=None):
        self.macguffins = macguffins or {}
        self.dead = dead or {}

cd = ClimaxDriver(total_chapters=10)  # climax_start=6
assert cd.check(3, _Canon(), {}) == []  # 高潮期前不触发
# 麦高芬计划第5章揭示但第7章仍未取得 → 触发
canon = _Canon(macguffins={"账册": {"reveal_round": 5, "acquired": False}})
g = cd.check(7, canon, {})
assert any("账册" in x for x in g), g
assert all(x.startswith("【高潮推进】") for x in g)
# 悬念过多 → 收束提示
g2 = cd.check(7, _Canon(), {"open_threads": ["a", "b", "c", "d", "e"]})
assert any("收束" in x for x in g2), g2
# 临近结局(>=8)无死亡 → 升级冲突
g3 = cd.check(9, _Canon(), {})
assert any("代价" in x or "冲突" in x for x in g3), g3
print("climax_driver OK")

print("\nALL NARRATIVE-MODULE SMOKE TESTS PASSED")
