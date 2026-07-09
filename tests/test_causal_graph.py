"""Smoke test: causal graph assembly from ACTED rows (offline, no Kuzu)."""
from literarycreation.storage.graph_store import DeductionGraphStore


class _FakeGraph(DeductionGraphStore):
    def __init__(self, rows):
        self._rows = rows

    def query(self, cypher, params=None):
        return self._rows


# rows: a.id, a.name, ev.id, ev.description, ev.event_type, r.action,
#       ev.round, ev.driver, ev.effect, ev.target_id
rows = [
    ["ag1", "沈彻", "ev1", "沈彻发现驿丞尸体与半截密信", "investigate", "调查", 1, "blueline", "mystery+18", ""],
    ["ag2", "余光", "ev2", "余光暗中观察", "observe", "观察", 2, "freeform", "", "ag1"],
    ["ag1", "沈彻", "ev3", "", "confront", "对峙", 3, "blueline", "tension+30", "ag2"],
]
g = _FakeGraph(rows)
out = g.get_causal_graph()
nodes, links = out["nodes"], out["links"]

ids = {n["id"] for n in nodes}
assert ids == {"ag1", "ag2", "ev1", "ev2", "ev3"}, ids
kinds = {n["id"]: n["kind"] for n in nodes}
assert kinds["ag1"] == "agent" and kinds["ev1"] == "event"
# 空描述回退 event_type
ev3 = next(n for n in nodes if n["id"] == "ev3")
assert ev3["label"] == "confront", ev3["label"]
# ACTED 边
acted = [e for e in links if e["type"] == "acted"]
assert len(acted) == 3, len(acted)
# 交互边：ev2->ag1, ev3->ag2
target = [(e["source"], e["target"]) for e in links if e["type"] == "target"]
assert ("ev2", "ag1") in target and ("ev3", "ag2") in target, target
# desc 含轮次
ev1 = next(n for n in nodes if n["id"] == "ev1")
assert "第1轮" in ev1["desc"], ev1["desc"]
print("nodes=", len(nodes), "links=", len(links), "target-edges=", len(target))
print("CAUSAL GRAPH ASSEMBLY OK")
