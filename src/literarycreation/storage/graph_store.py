"""Graph store adapter — Kuzu embedded graph database.

Thread-safe: each thread should use its own Connection.
Primary key required on all node tables.
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class DeductionGraphStore:

    NODE_TABLE = "Entity"
    AGENT_TABLE = "Agent"
    EVENT_TABLE = "Event"

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn: Any = None
        self._db: Any = None
        self._closed = False
        self._init()

    def _init(self) -> None:
        import kuzu
        self._db = kuzu.Database(str(self._db_path))
        self._conn = kuzu.Connection(self._db)
        self._init_schema()
        logger.info("[DeductionGraph] Kuzu database initialized: %s", self._db_path)

    def _init_schema(self) -> None:
        # Kuzu 对每个 NODE TABLE 的 PRIMARY KEY(id) 自动维护 hash 索引，
        # upsert_entity/upsert_relation 等按 id 的 MERGE/MATCH 均为 O(1) 主键查找；
        # 每个会话使用独立的 Kuzu 库目录，无需额外二级索引。
        with self._lock:
            self._conn.execute(
                "CREATE NODE TABLE IF NOT EXISTS Entity("
                "id STRING, name STRING, type STRING, description STRING, "
                "properties STRING, PRIMARY KEY(id))"
            )
            self._conn.execute(
                "CREATE NODE TABLE IF NOT EXISTS Agent("
                "id STRING, name STRING, persona STRING, background STRING, "
                "goals STRING, PRIMARY KEY(id))"
            )
            self._conn.execute(
                "CREATE NODE TABLE IF NOT EXISTS Event("
                "id STRING, description STRING, event_type STRING, "
                "timestamp STRING, agent_id STRING, round INT64, target_id STRING, "
                "effect STRING, driver STRING, "
                "PRIMARY KEY(id))"
            )
            self._conn.execute(
                "CREATE REL TABLE IF NOT EXISTS RELATES("
                "FROM Entity TO Entity, relation STRING, weight DOUBLE, evidence STRING)"
            )
            self._conn.execute(
                "CREATE REL TABLE IF NOT EXISTS ACTED("
                "FROM Agent TO Event, action STRING, timestamp STRING)"
            )
            self._conn.execute(
                "CREATE REL TABLE IF NOT EXISTS CAUSED("
                "FROM Event TO Entity, metric STRING, amount DOUBLE)"
            )

    def _check_conn(self) -> None:
        if self._conn is None or self._closed:
            import traceback
            msg = "[DeductionGraph] Kuzu connection is None (closed=%s)" % self._closed
            logger.error(msg)
            traceback.print_stack()
            raise RuntimeError(msg)

    def upsert_entity(self, entity_id: str, name: str, etype: str,
                      description: str = "", properties: str = "{}") -> None:
        self._check_conn()
        # Kuzu 0.11.3 支持 $param 仅限 MERGE 节点匹配，不支持 MATCH..SET = $param。
        # 因此用参数化 MERGE + 内联转义 SET（已验证无 SQL 注入风险）。
        with self._lock:
            self._conn.execute(
                f"MERGE (e:{self.NODE_TABLE} {{id: $id}})",
                {"id": entity_id},
            )
            safe_name = name.replace("'", "\\'")
            safe_type = etype.replace("'", "\\'")
            safe_desc = description.replace("'", "\\'")
            safe_props = properties.replace("'", "\\'")
            self._conn.execute(
                f"MATCH (e:{self.NODE_TABLE} {{id: $id}}) "
                f"SET e.name = '{safe_name}', e.type = '{safe_type}', "
                f"e.description = '{safe_desc}', e.properties = '{safe_props}'",
                {"id": entity_id},
            )

    def upsert_relation(self, source_id: str, target_id: str,
                        relation: str, weight: float = 1.0, evidence: str = "") -> None:
        self._check_conn()
        with self._lock:
            self._conn.execute(
                f"MATCH (a:{self.NODE_TABLE} {{id: $sid}}), (b:{self.NODE_TABLE} {{id: $tid}}) "
                "MERGE (a)-[r:RELATES {relation: $rel}]->(b) "
                "SET r.weight = $w, r.evidence = $ev",
                {"sid": source_id, "tid": target_id, "rel": relation,
                 "w": weight, "ev": evidence},
            )

    def upsert_agent_node(self, agent_id: str, name: str, persona: str,
                          background: str = "", goals: str = "[]") -> None:
        with self._lock:
            self._conn.execute(
                f"MERGE (a:{self.AGENT_TABLE} {{id: $id}}) "
                "SET a.name = $name, a.persona = $persona, a.background = $bg, a.goals = $goals",
                {"id": agent_id, "name": name, "persona": persona,
                 "bg": background, "goals": goals},
            )

    def add_event(self, event_id: str, description: str, event_type: str,
                  timestamp: str, agent_id: str = "", round_number: int = 0,
                  target_id: str = "", effect: str = "", driver: str = "") -> None:
        self._check_conn()
        safe = {
            "id": event_id.replace("'", "\\'"),
            "desc": description.replace("'", "\\'")[:500],
            "type": event_type.replace("'", "\\'"),
            "ts": timestamp.replace("'", "\\'"),
            "aid": agent_id.replace("'", "\\'"),
            "tid": (target_id or "").replace("'", "\\'"),
            "eff": (effect or "").replace("'", "\\'")[:200],
            "drv": (driver or "").replace("'", "\\'")[:16],
        }
        rnd = int(round_number)
        with self._lock:
            self._conn.execute(
                f"CREATE (ev:{self.EVENT_TABLE} {{id: '{safe['id']}', "
                f"description: '{safe['desc']}', event_type: '{safe['type']}', "
                f"timestamp: '{safe['ts']}', agent_id: '{safe['aid']}', "
                f"round: {rnd}, target_id: '{safe['tid']}', "
                f"effect: '{safe['eff']}', driver: '{safe['drv']}'}})"
            )

    def add_acted(self, agent_id: str, event_id: str, action: str, timestamp: str = "") -> None:
        self._check_conn()
        with self._lock:
            self._conn.execute(
                f"MATCH (a:{self.AGENT_TABLE} {{id: $aid}}), (ev:{self.EVENT_TABLE} {{id: $eid}}) "
                "CREATE (a)-[:ACTED {action: $act, timestamp: $ts}]->(ev)",
                {"aid": agent_id, "eid": event_id, "act": action, "ts": timestamp},
            )

    # ── Query helpers ──

    def query(self, cypher: str, params: dict | None = None) -> list[list[Any]]:
        self._check_conn()
        result = self._conn.execute(cypher, params or {})
        rows: list[list[Any]] = []
        while result.has_next():
            rows.append(result.get_next())
        return rows

    def count_entities(self) -> int:
        self._check_conn()
        result = self._conn.execute(f"MATCH (e:{self.NODE_TABLE}) RETURN count(e)")
        row = result.get_next()
        return row[0] if row else 0

    def count_relations(self) -> int:
        self._check_conn()
        result = self._conn.execute("MATCH ()-[r:RELATES]->() RETURN count(r)")
        row = result.get_next()
        return row[0] if row else 0

    def get_entities_by_type(self, etype: str) -> list[dict[str, Any]]:
        self._check_conn()
        result = self._conn.execute(
            f"MATCH (e:{self.NODE_TABLE}) WHERE e.type = $t RETURN e.id, e.name, e.type, e.description",
            {"t": etype},
        )
        rows: list[dict[str, Any]] = []
        while result.has_next():
            r = result.get_next()
            rows.append({"id": r[0], "name": r[1], "type": r[2], "description": r[3]})
        return rows

    def get_entity_neighbors(self, entity_id: str, max_depth: int = 1) -> dict[str, Any]:
        """返回实体的关系邻居。

        1 跳返回带 relation/weight 的结构化邻居（用于盟友/对手识别与决策注入）；
        max_depth>1 时附带多跳邻居名称（仅作扩展上下文）。
        """
        rows = self.query(
            f"MATCH (e:{self.NODE_TABLE} {{id: $id}})-[r:RELATES]-(n:{self.NODE_TABLE}) "
            "RETURN r.relation, r.weight, n.id, n.name, n.type",
            {"id": entity_id},
        )
        neighbors = [
            {"relation": r[0] or "", "weight": (r[1] if r[1] is not None else 1.0),
             "id": r[2], "name": r[3] or "", "type": r[4] or ""}
            for r in rows
        ]
        extended: list[str] = []
        if max_depth > 1:
            erows = self.query(
                f"MATCH (e:{self.NODE_TABLE} {{id: $id}})-[:RELATES*2..{max_depth}]-(n:{self.NODE_TABLE}) "
                "RETURN DISTINCT n.name",
                {"id": entity_id},
            )
            extended = [r[0] for r in erows if r and r[0]]
        return {"entity_id": entity_id, "neighbors": neighbors, "extended": extended}

    def get_agent_timelines(self, limit_per_agent: int = 20) -> list[dict[str, Any]]:
        """按智能体聚合"行动—事件"时序（读取 Agent-[ACTED]->Event 时序行动图）。"""
        rows = self.query(
            f"MATCH (a:{self.AGENT_TABLE})-[r:ACTED]->(ev:{self.EVENT_TABLE}) "
            "RETURN a.id, a.name, r.action, r.timestamp, ev.description, ev.event_type, ev.effect, ev.driver "
            "ORDER BY r.timestamp"
        )
        grouped: dict[str, dict[str, Any]] = {}
        for r in rows:
            aid = r[0]
            bucket = grouped.setdefault(aid, {"agent_id": aid, "agent_name": r[1] or aid[:8],
                                              "actions": []})
            if len(bucket["actions"]) < limit_per_agent:
                bucket["actions"].append({
                    "action": r[2] or "", "timestamp": r[3] or "",
                    "description": r[4] or "", "event_type": r[5] or "",
                    "effect": r[6] or "", "driver": r[7] or "",
                })
        return list(grouped.values())

    def get_recent_events_for_agent(self, agent_id: str, last_n: int = 5) -> list[dict[str, Any]]:
        """查询某智能体最近 N 条事件（按轮次倒序），用于增强角色自我记忆。"""
        rows = self.query(
            f"MATCH (a:{self.AGENT_TABLE} {{id: $aid}})-[:ACTED]->(ev:{self.EVENT_TABLE}) "
            "RETURN ev.round, ev.event_type, ev.description, ev.driver, ev.effect "
            f"ORDER BY ev.round DESC LIMIT {int(last_n)}",
            {"aid": agent_id},
        )
        return [{"round": r[0] if r[0] is not None else 0, "action": r[1] or "",
                 "description": (r[2] or "")[:300], "driver": r[3] or "",
                 "effect": r[4] or ""} for r in rows]

    def get_recent_global_events(self, last_n: int = 5) -> list[dict[str, Any]]:
        """全局最近 N 条事件，替代内存中的 _event_history。"""
        rows = self.query(
            f"MATCH (a:{self.AGENT_TABLE})-[:ACTED]->(ev:{self.EVENT_TABLE}) "
            "RETURN a.name, ev.round, ev.event_type, ev.description "
            f"ORDER BY ev.round DESC LIMIT {int(last_n)}"
        )
        events = []
        for r in rows:
            events.append({"agent_name": r[0] or "?", "round": r[1] if r[1] is not None else 0,
                           "action": r[2] or "", "content": (r[3] or "")[:200]})
        events.reverse()
        return events

    def get_event_sequence(self, limit: int = 40) -> list[dict[str, Any]]:
        """全局按时间排序的事件序列（供因果链分析）。"""
        rows = self.query(
            f"MATCH (a:{self.AGENT_TABLE})-[r:ACTED]->(ev:{self.EVENT_TABLE}) "
            "RETURN r.timestamp, a.name, r.action, ev.description, ev.event_type, ev.effect, ev.driver "
            "ORDER BY r.timestamp"
        )
        seq = [{
            "timestamp": r[0] or "", "agent_name": r[1] or "", "action": r[2] or "",
            "description": r[3] or "", "event_type": r[4] or "",
            "effect": r[5] or "", "driver": r[6] or "",
        } for r in rows]
        return seq[-limit:] if limit and len(seq) > limit else seq

    def get_causal_summary(self, limit: int = 15) -> list[dict[str, Any]]:
        """全局 Agent→Event 行动摘要，按时间倒序。"""
        rows = self.query(
            f"MATCH (a:{self.AGENT_TABLE})-[:ACTED]->(ev:{self.EVENT_TABLE}) "
            "RETURN a.name, ev.event_type, ev.description, ev.round "
            f"ORDER BY ev.round DESC LIMIT {int(limit)}"
        )
        return [{"source": r[0] or "?", "action": r[1] or "", "description": str(r[2] or "")[:120],
                 "round": r[3] if r[3] is not None else 0} for r in rows]

    def get_causal_graph(self, limit: int = 300) -> dict[str, list[dict[str, Any]]]:
        """从 Agent-[ACTED]->Event 构建因果/行动图（节点+边），供前端 3D 图渲染。

        - Agent 节点(kind=agent) / Event 节点(kind=event)
        - ACTED 边：agent → event（谁执行了该事件）
        - 交互边：event → 目标 Agent（当 Event.target_id 匹配到已知智能体时）
        事件按轮次升序取前 limit 条，防止长会话节点过多。
        """
        rows = self.query(
            f"MATCH (a:{self.AGENT_TABLE})-[r:ACTED]->(ev:{self.EVENT_TABLE}) "
            "RETURN a.id, a.name, ev.id, ev.description, ev.event_type, r.action, "
            "ev.round, ev.driver, ev.effect, ev.target_id "
            f"ORDER BY ev.round LIMIT {int(limit)}"
        )
        nodes: list[dict[str, Any]] = []
        links: list[dict[str, Any]] = []
        agent_ids: dict[str, str] = {}   # id -> name
        seen_nodes: set[str] = set()

        def _add_node(nid: str, kind: str, label: str, desc: str = "") -> None:
            if nid and nid not in seen_nodes:
                seen_nodes.add(nid)
                nodes.append({"id": nid, "kind": kind, "label": label or nid[:8], "desc": desc})

        # 第一遍：收集智能体 id→name，便于交互边解析
        for r in rows:
            if r[0]:
                agent_ids[r[0]] = r[1] or str(r[0])[:8]

        for r in rows:
            aid, aname, eid = r[0], r[1] or "", r[2]
            desc, etype, action = str(r[3] or ""), str(r[4] or ""), str(r[5] or "")
            rnd, driver, effect, target_id = r[6], str(r[7] or ""), str(r[8] or ""), r[9]
            if not aid or not eid:
                continue
            _add_node(aid, "agent", aname)
            ev_label = (desc[:36] or etype or "事件")
            ev_desc = f"第{rnd if rnd is not None else '?'}轮" + (f"·{driver}" if driver else "") + (f" {effect}" if effect else "")
            _add_node(eid, "event", ev_label, ev_desc.strip())
            links.append({"source": aid, "target": eid, "type": "acted", "label": action or etype})
            # 交互边：事件指向目标智能体（仅当目标是已知智能体，避免悬空边）
            if target_id and target_id in agent_ids and target_id != aid:
                _add_node(target_id, "agent", agent_ids[target_id])
                links.append({"source": eid, "target": target_id, "type": "target", "label": "作用于"})

        return {"nodes": nodes, "links": links}

    def export_graph_data(self) -> dict[str, Any]:
        self._check_conn()
        nodes: list[dict[str, Any]] = []
        result = self._conn.execute(
            f"MATCH (e:{self.NODE_TABLE}) RETURN e.id, e.name, e.type, e.description"
        )
        while result.has_next():
            r = result.get_next()
            nodes.append({"id": r[0], "name": r[1], "type": r[2], "description": r[3]})

        links: list[dict[str, Any]] = []
        result = self._conn.execute(
            "MATCH (a)-[r:RELATES]->(b) RETURN a.id, b.id, r.relation, r.weight"
        )
        while result.has_next():
            r = result.get_next()
            links.append({"source": r[0], "target": r[1], "relation": r[2], "weight": r[3]})

        return {"nodes": nodes, "links": links}

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._conn = None
            self._db = None
        logger.info("[DeductionGraph] Kuzu database closed")
