"""Deduction Orchestrator — five-stage pipeline coordinator with pause/resume."""
from __future__ import annotations

import json as _json
import logging
from collections.abc import Callable
from typing import Any

from literarycreation.storage.graph_store import DeductionGraphStore
from literarycreation.storage.session_store import SessionStore
from literarycreation.core.token_counter import (
    _current_session,
    _current_phase,
    _current_round,
)

from .models import (
    DeductionPhase,
    DeductionSession,
    SessionStatus,
    SimulationRound,
)

logger = logging.getLogger(__name__)


class _PhaseCancelledError(Exception):
    """用户取消推演（非错误，应持久化进度为 paused）。"""


class DeductionOrchestrator:

    def __init__(
        self,
        session: DeductionSession,
        graph: DeductionGraphStore,
        session_store: SessionStore,
        logger_fn: Callable[[str, str], None] | None = None,
        cancel_event: Any = None,
        round_callback: Callable[[int, int], None] | None = None,
        resume_start_round: int = 0,
        fsm_override_store: dict | None = None,
    ) -> None:
        self.session = session
        self.graph = graph
        self.store = session_store
        self._log = logger_fn or (lambda p, m: None)
        self._cancel = cancel_event
        self._round_callback = round_callback
        self._resume_start_round = resume_start_round
        self._fsm_override_store = fsm_override_store if fsm_override_store is not None else {}
        from literarycreation.core.config import config as _cfg
        self._max_concurrent = _cfg.deduction_max_concurrent
        # 量化模式状态（rule_engine 非空即量化）
        self._rule_engine: Any = None
        self._states: dict[str, Any] = {}
        self._enable_narrate: bool = True
        self._enable_multi_action: bool = False
        self._max_actions: int = 3
        self._style: str = "现实主义"
        self._outline: dict[str, Any] | None = None
        self._target_words: int = 0

    async def run(self) -> DeductionSession:
        import time as _time

        session_id = self.session.id
        _current_session.set(session_id)
        _total_start = _time.monotonic()
        _phase_times: dict[str, float] = {}

        async def _timed_phase(name: str, fn):
            t0 = _time.monotonic()
            await fn()
            dt = _time.monotonic() - t0
            _phase_times[name] = dt
            self._log("orchestrator", f"阶段 {name} 耗时 {dt:.1f}s")

        try:
            if self._resume_start_round > 0:
                await self._resume_from_pause()
            else:
                for phase_name, phase_fn in [
                    ("ontology", self._phase1_ontology),
                    ("quantify", self._phase1_5_quantify),
                    ("graph", self._phase2_graph),
                    ("agents", self._phase3_agents),
                ]:
                    await _timed_phase(phase_name, phase_fn)
            await _timed_phase("simulation", self._phase4_simulation)
            await _timed_phase("report", self._phase5_report)

            _total = _time.monotonic() - _total_start
            _detail = " | ".join(f"{k}={v:.1f}s" for k, v in _phase_times.items())
            self._log("orchestrator", f"五阶段完成，总耗时 {_total:.1f}s | {_detail}")

            self.store.update(session_id, status=SessionStatus.COMPLETE.value,
                              phase=DeductionPhase.COMPLETE.value)
            self._clear_state_snapshot(session_id)
        except _PhaseCancelledError:
            _total = _time.monotonic() - _total_start
            self._log("orchestrator", f"推演已暂停（运行 {_total:.1f}s），进度已保存")
            self._save_pause_snapshot(session_id)
        except Exception as e:
            _total = _time.monotonic() - _total_start
            logger.exception("[Deduction] Pipeline failed: %s", e)
            self.store.update(session_id, status=SessionStatus.FAILED.value,
                              error=str(e)[:500])
            self._log("orchestrator", f"推演失败（运行 {_total:.1f}s）: {e}")
        return self.session

    def _check_cancel(self) -> None:
        if self._cancel is not None and self._cancel.is_set():
            raise _PhaseCancelledError()

    def _save_pause_snapshot(self, session_id: str) -> None:
        """Serialize in-memory state (EntityState metrics/history/delays) into config_json."""
        snapshot: dict[str, Any] = {}
        states = getattr(self, "_states", None)
        if states:
            snapshot["states"] = {
                eid: {
                    "id": st.id,
                    "name": getattr(st, "name", eid),
                    "domain": getattr(st, "domain", ""),
                    "metrics": dict(getattr(st, "metrics", {})),
                    "history": getattr(st, "history", [])[-100:],
                    "pending_delays": getattr(st, "_pending_delays", []),
                }
                for eid, st in states.items()
            }
        data = self.store.get(session_id)
        cfg = (data or {}).get("config_json", {}) or {}
        if isinstance(cfg, str):
            cfg = _json.loads(cfg)
        cfg["state_snapshot"] = snapshot
        self.store.update(session_id, config_json=_json.dumps(cfg, ensure_ascii=False),
                          status=SessionStatus.PAUSED.value)

    @staticmethod
    def _load_state_snapshot(cfg: dict[str, Any]) -> dict[str, Any] | None:
        return cfg.get("state_snapshot")

    def _clear_state_snapshot(self, session_id: str) -> None:
        data = self.store.get(session_id)
        cfg = (data or {}).get("config_json", {}) or {}
        if isinstance(cfg, str):
            cfg = _json.loads(cfg)
        if "state_snapshot" in cfg:
            del cfg["state_snapshot"]
            self.store.update(session_id, config_json=_json.dumps(cfg, ensure_ascii=False))

    async def _resume_from_pause(self) -> None:
        """从 paused 状态续推：恢复内存态，跳过 Phase 1-3。"""
        _current_phase.set("resume")
        self._log("orchestrator", "从暂停点恢复推演...")
        data = self.store.get(self.session.id)
        cfg = (data or {}).get("config_json", {}) or {}
        if isinstance(cfg, str):
            cfg = _json.loads(cfg)

        # 1. 恢复配置参数
        self._enable_narrate = bool(cfg.get("enable_narrate", True))
        self._enable_multi_action = bool(cfg.get("enable_multi_action", False))
        try:
            self._max_actions = int(cfg.get("max_actions", 3))
        except (TypeError, ValueError):
            self._max_actions = 3
        self._weather = str(cfg.get("weather", "") or "").strip()
        self._terrain = str(cfg.get("terrain", "") or "").strip()
        self._style = str(cfg.get("style", "") or "现实主义").strip()
        self._outline = cfg.get("outline") if isinstance(cfg.get("outline"), dict) else None
        try:
            self._target_words = int(cfg.get("target_words", 0) or 0)
        except (TypeError, ValueError):
            self._target_words = 0

        # 2. 恢复规则包
        domain = (cfg.get("domain") or "").strip()
        if domain:
            from .rule_engine import RuleEngine
            try:
                self._rule_engine = RuleEngine.from_domain(domain)
                self._log("orchestrator",
                          f"恢复规则包: {self._rule_engine.pack.get('display_name', domain)}")
            except Exception as e:
                logger.warning("[Orchestrator] 规则包恢复失败: %s", e)
                self._rule_engine = None

        # 3. 恢复预处理器 (打开已有 LanceDB 表)
        from literarycreation.core.config import config as forge_config
        from .preprocessor import DeductionPreprocessor
        self._preprocessor = DeductionPreprocessor(
            workspace_root=forge_config.project_root,
            session_id=self.session.id,
        )
        self._pre_goals = cfg.get("pre_goals", [])

        # 4. 从 Kuzu 图重建 Agent 列表
        self._log("orchestrator", "从图谱重建智能体...")
        try:
            from .agent_factory import create_agents_from_graph
            agents = await create_agents_from_graph(
                graph=self.graph,
                source_material=self.session.source_material,
                log_fn=self._log,
                preprocessor=self._preprocessor,
            )
            self._agents = agents
            self.session.agent_count = len(agents)
        except Exception as e:
            logger.warning("[Orchestrator] 智能体重建失败: %s", e)

        # 5. 恢复量化状态 (EntityState metrics / history / pending delays)
        snapshot = self._load_state_snapshot(cfg)
        if snapshot and self._rule_engine is not None:
            states_raw = snapshot.get("states", {})
            restored: dict[str, Any] = {}
            for eid, raw in states_raw.items():
                st = self._rule_engine.init_state(
                    raw.get("id", eid),
                    raw.get("name", eid),
                )
                st.metrics = dict(raw.get("metrics", {}))
                st.history = list(raw.get("history", []))
                st._pending_delays = list(raw.get("pending_delays", []))
                restored[eid] = st
            self._states = restored
            self._log("orchestrator",
                      f"恢复量化状态: {len(restored)} 个实体")

        self.store.update(self.session.id,
                          status=SessionStatus.SIMULATING.value,
                          phase=DeductionPhase.SIMULATION.value)
        self._clear_state_snapshot(self.session.id)
        self._log("orchestrator", f"续推就绪，从第 {self._resume_start_round + 1} 轮开始")

    async def _phase1_ontology(self) -> None:
        _current_phase.set("ontology")
        self._check_cancel()
        self._log("ontology", "阶段1: 本体生成开始")
        self.store.update(self.session.id,
                          status=SessionStatus.ONTOLOGY_RUNNING.value,
                          phase=DeductionPhase.ONTOLOGY.value)

        from .ontology import generate_ontology
        ontology = await generate_ontology(self.session.source_material)
        self.session.ontology = ontology

        self._log("ontology", f"本体生成完成: {len(ontology.entities)} 种实体类型, "
                  f"{len(ontology.relations)} 种关系类型")
        self.store.update(self.session.id,
                          status=SessionStatus.GRAPH_RUNNING.value,
                          phase=DeductionPhase.GRAPH.value)

    async def _phase1_5_quantify(self) -> None:
        """阶段1.5：加载文学风格规则包（domain 来自前端配置）。"""
        _current_phase.set("quantify")
        self._check_cancel()

        data = self.store.get(self.session.id)
        cfg = (data or {}).get("config_json", {}) or {}
        if isinstance(cfg, str):
            cfg = _json.loads(cfg)
        self._enable_narrate = bool(cfg.get("enable_narrate", True))
        self._weather = str(cfg.get("weather", "") or "").strip()
        self._terrain = str(cfg.get("terrain", "") or "").strip()
        self._outline = cfg.get("outline") if isinstance(cfg.get("outline"), dict) else None
        try:
            self._target_words = int(cfg.get("target_words", 0) or 0)
        except (TypeError, ValueError):
            self._target_words = 0

        domain = (cfg.get("domain") or "literary_realism").strip()
        from .rule_engine import RuleEngine
        try:
            self._rule_engine = RuleEngine.from_domain(domain)
            self._log("quantify", f"阶段1.5: 使用领域规则包: {self._rule_engine.pack.get('display_name', domain)}")
        except Exception as e:
            logger.warning("[Orchestrator] 规则包加载失败: %s", e)
            self._rule_engine = None
            self._log("quantify", f"规则包加载失败: {e}")

    async def _phase2_graph(self) -> None:
        _current_phase.set("graph")
        self._check_cancel()
        self._log("graph", "阶段2: GraphRAG 知识图谱构建开始")

        # 预处理: 语义分块 + 实体提取 + LanceDB 索引
        self._log("graph", "  预处理: 语义分块 + 实体提取 + LanceDB 索引")
        from literarycreation.core.config import config

        from .preprocessor import DeductionPreprocessor

        preprocessor = DeductionPreprocessor(
            workspace_root=config.project_root,
            session_id=self.session.id,
        )
        preprocessor.preprocess(self.session.source_material)
        self._preprocessor = preprocessor

        from .graph_builder import build_graph
        await build_graph(
            source=self.session.source_material,
            graph=self.graph,
            ontology=self.session.ontology,
            log_fn=self._log,
            preprocessor=preprocessor,
        )

        e_count = self.graph.count_entities()
        r_count = self.graph.count_relations()
        self.session.entity_count = e_count
        self.session.relation_count = r_count

        self._log("graph", f"图谱构建完成: {e_count} 实体, {r_count} 关系")
        self.store.update(self.session.id, entity_count=e_count, relation_count=r_count,
                          status=SessionStatus.AGENTS_RUNNING.value,
                          phase=DeductionPhase.AGENTS.value)

    async def _phase3_agents(self) -> None:
        _current_phase.set("agents")
        self._check_cancel()
        self._log("agents", "阶段3: 智能体工厂开始")

        from .agent_factory import create_agents_from_graph
        cfg_data = self.store.get(self.session.id)
        pre_goals: list[str] = []
        if cfg_data:
            cfg = cfg_data.get("config_json", {}) or {}
            if isinstance(cfg, str):
                cfg = _json.loads(cfg)
            pre_goals = cfg.get("pre_goals", [])
        agents = await create_agents_from_graph(
            graph=self.graph,
            source_material=self.session.source_material,
            log_fn=self._log,
            preprocessor=getattr(self, "_preprocessor", None),
            pre_interventions=pre_goals if pre_goals else None,
        )
        self.session.agent_count = len(agents)
        self._agents = agents
        self._pre_goals = pre_goals

        # 将预目标写入 LanceDB 动态事件表 (immutable_goal, priority=0.9)
        # 确保长期推演中智能体"不忘初心"
        pp = getattr(self, "_preprocessor", None)
        if pp and pre_goals:
            for goal in pre_goals:
                try:
                    pp.add_event_memory(
                        content=goal, agent_id="system_user",
                        round_number=1, event_type="immutable_goal",
                        priority=0.9,
                    )
                except Exception:
                    pass
            self._log("agents", f"已注入 {len(pre_goals)} 个不可变目标到 LanceDB")

        self._log("agents", f"智能体工厂完成: {len(agents)} 个智能体生成")
        self.store.update(self.session.id, agent_count=len(agents),
                          status=SessionStatus.SIMULATING.value,
                          phase=DeductionPhase.SIMULATION.value)

    async def _phase4_simulation(self) -> None:
        _current_phase.set("simulation")
        self._check_cancel()
        total_rounds = self.session.total_rounds
        re_engine = self._rule_engine
        states: dict[str, Any] = {}
        if re_engine is not None:
            for a in self._agents:
                states[a.entity_id] = re_engine.init_state(a.entity_id, a.name)
            # Mode 2 提纲复现：按角色 initial_state 覆盖统一初值
            outline = getattr(self, "_outline", None)
            if outline and outline.get("characters"):
                metric_set = set(re_engine.metrics())
                char_map = {c.get("name"): c for c in outline["characters"] if c.get("name")}
                seeded = 0
                for a in self._agents:
                    spec = char_map.get(a.name)
                    if spec and spec.get("initial_state"):
                        states[a.entity_id].metrics.update({
                            k: float(v) for k, v in spec["initial_state"].items()
                            if k in metric_set
                        })
                        seeded += 1
                if seeded:
                    self._log("simulation", f"提纲复现：按角色初值覆盖 {seeded} 个实体")
            self._states = states
            self._log("simulation",
                       f"阶段4: 文学叙事模拟开始 ({total_rounds} 轮, {len(states)} 个角色, "
                      f"领域={re_engine.domain})")
        else:
            self._log("simulation", "规则包加载失败，跳过模拟阶段")
            self.store.update(self.session.id, status=SessionStatus.COMPLETE.value)
            return

        from .simulator import SimulationEngine

        # 模式判定：有提纲 → 蓝图执行模式（blueline），无提纲 → 自由续写模式（freeform）
        outline = getattr(self, "_outline", None)
        mode = "blueline" if (outline and outline.get("key_events")) else "freeform"
        event_scheduler = None
        if mode == "blueline" and outline:
            from literarycreation.engine.event_scheduler import EventScheduler
            event_scheduler = EventScheduler.from_outline(outline, total_rounds)
            self._event_scheduler = event_scheduler
            self._log("simulation", f"蓝图执行模式已启用: {len(outline.get('key_events', []))} 个关键事件")

        engine = SimulationEngine(
            agents=self._agents,
            graph=self.graph,
            total_rounds=total_rounds,
            log_fn=self._log,
            preprocessor=getattr(self, "_preprocessor", None),
            pre_goals=getattr(self, "_pre_goals", []),
            rule_engine=re_engine,
            states=states if re_engine is not None else None,
            enable_narrate=self._enable_narrate,
            env={"weather": self._weather, "terrain": self._terrain} if (self._weather or self._terrain) else None,
            cancel_event=self._cancel,
            outline=outline,
            fsm_override_store=self._fsm_override_store,
            mode=mode,
            event_scheduler=event_scheduler,
            max_concurrent=self._max_concurrent,
        )

        rounds: list[SimulationRound] = []
        start_rnd = self._resume_start_round + 1
        for rnd in range(start_rnd, total_rounds + 1):
            if self._cancel is not None and self._cancel.is_set():
                self._log("simulation", "推演收到取消信号，提前终止")
                raise _PhaseCancelledError()
            _current_round.set(rnd)
            self._log("simulation", f"  第 {rnd}/{total_rounds} 轮开始")
            result = await engine.run_round(rnd)
            rounds.append(result)
            self.session.current_round = rnd
            self.store.update(self.session.id, current_round=rnd)
            self._log("simulation", f"  第 {rnd} 轮完成: {len(result.actions)} 个动作")
            if self._round_callback:
                snapshot = result.state_delta.get("snapshot") if hasattr(result, "state_delta") else None
                self._round_callback(rnd, total_rounds, snapshot)
            # Persist token stats incrementally (survives pause/interrupt)
            from literarycreation.core.token_counter import accumulator
            stats = accumulator.get_session_stats(self.session.id)
            if stats:
                self.store.update(self.session.id,
                                  token_json=_json.dumps(stats, ensure_ascii=False))

        self._simulation_rounds = rounds
        self._log("simulation", f"模拟完成: {len(rounds)} 轮, "
                  f"{sum(len(r.actions) for r in rounds)} 个总动作")
        self.store.update(self.session.id,
                          status=SessionStatus.REPORTING.value,
                          phase=DeductionPhase.REPORT.value)

    async def _phase5_report(self) -> None:
        _current_phase.set("report")

        # 规则包加载失败 → 无输出可直接结束，不走非文学分支（reporter.py 已删除）
        if self._rule_engine is None:
            self._log("report", "未加载规则包，跳过阶段5")
            self.store.update(self.session.id, status=SessionStatus.COMPLETE.value,
                              report_json='{}')
            return

        is_literary = self._rule_engine.domain.startswith("literary")

        # ── 文学模式：散文渲染为主输出，跳过推演分析报告 ──
        if is_literary:
            self._log("report", "阶段5: 文学正文生成开始")
            report_payload: dict[str, Any] = {
                "is_literary": True,
                "domain": self._rule_engine.domain,
                "final_states": {
                    eid: {"name": st.name, "metrics": st.metrics,
                          "history": st.history[-60:],
                          "alive": self._rule_engine.is_alive(st)}
                    for eid, st in self._states.items()
                },
            }
            try:
                await self._render_prose(report_payload)
                self._log("report", f"文学正文生成完成: {len(report_payload.get('prose', ''))} 字")
            except Exception as e:  # noqa: BLE001
                logger.warning("[Orchestrator] 散文渲染失败: %s", e)
                self._log("report", f"散文渲染失败: {e}")
            self.store.update(self.session.id,
                              report_json=_json.dumps(report_payload, ensure_ascii=False))
            return

        # 降级：无可渲染的状态数据
        self._log("report", "无状态数据，保存空报告")
        self.store.update(self.session.id,
                          report_json=_json.dumps({"is_literary": True, "domain": self._rule_engine.domain, "prose": ""}, ensure_ascii=False))

    @staticmethod
    def _retrieve_style_anchors(pp: Any, source: str) -> list[str]:
        """从 LanceDB 检索原文中最具风格代表性的片段。"""
        try:
            chunks = pp.retrieve_for_entity(source[:200], top_k=5)
        except Exception:
            return []
        # 优先长句（>150 字）含描写的段落
        return [c for c in chunks if len(c) > 150][:3] or chunks[:3]

    @staticmethod
    def _build_continuity_ctx(graph: Any, agents: list, chapter_idx: int) -> str:
        """从 Kuzu 查询角色最后已知行动，构建连续性约束。"""
        if graph is None:
            return ""
        lines = []
        for a in agents:
            try:
                events = graph.get_recent_events_for_agent(a.entity_id, last_n=1)
            except Exception:
                continue
            if events:
                last = events[0]
                lines.append(f"{a.name}：第{last['round']}轮执行了{last['action']}")
        if not lines:
            return ""
        return (
            "【角色连续性约束 — 请确保角色的声称为、行动与其最后已知状态一致，"
            "不要写已死亡的角色以活人身份出现】\n"
            + "\n".join(f"- {l}" for l in lines)
        )

    async def _render_prose(self, report_payload: dict[str, Any]) -> None:
        """文学模式 Phase 5：逐章生成正文并落盘，计算提纲对齐。"""
        import re as _re
        from pathlib import Path

        from literarycreation.core.config import config as _cfg

        from .prose_renderer import ProseRenderer, build_story_context, append_chapter_summary

        rounds = list(getattr(self, "_simulation_rounds", []))
        style = getattr(self, "_style", "现实主义")
        # 优先从规则包读取风格（domain 决定 style），config.style 作为可选的覆盖
        if self._rule_engine is not None and hasattr(self._rule_engine, "pack"):
            pack_style = self._rule_engine.pack.get("style")
            if pack_style:
                style = str(pack_style)
        outline = getattr(self, "_outline", None)
        target_words = int(getattr(self, "_target_words", 0) or 0)

        # 关键事件按轮次索引（Mode 2）
        ev_by_round: dict[int, list[str]] = {}
        if outline and outline.get("key_events"):
            for e in outline["key_events"]:
                try:
                    ev_by_round.setdefault(int(e.get("round", 0)), []).append(str(e.get("event", "")))
                except (TypeError, ValueError):
                    pass

        # 作品输出目录：<data_dir>/作品/<安全标题>/
        raw_title = (self.session.title or "未命名作品").strip() or "未命名作品"
        safe_title = _re.sub(r'[\\/:*?"<>|]', "_", raw_title).strip() or "未命名作品"
        work_dir = Path(_cfg.deduction_data_dir) / "作品" / safe_title
        try:
            work_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:  # noqa: BLE001
            logger.warning("[Orchestrator] 作品目录创建失败: %s", e)

        renderer = ProseRenderer(style=style)
        n = max(1, len(rounds))
        per_ch = (target_words // n) if target_words > 0 else 0

        chapters_meta: list[dict[str, Any]] = []
        full_parts: list[str] = []
        prev_tail = ""

        def _write(fname: str, text: str) -> str:
            try:
                p = work_dir / fname
                p.write_text("\ufeff" + text, encoding="utf-8")
                return str(p)
            except Exception as e:  # noqa: BLE001
                logger.warning("[Orchestrator] 写入 %s 失败: %s", fname, e)
                return ""

        if not rounds:
            # 无推演轮次：退回单次整篇渲染
            persona_map = {a.entity_id: getattr(a, "persona", "") for a in getattr(self, "_agents", [])}
            characters = [{"name": st.name, "persona": persona_map.get(eid, ""), "metrics": dict(st.metrics)}
                          for eid, st in self._states.items()]
            prose = await renderer.render(self.session.source_material,
                                          report_payload.get("final_states", {}), [], characters, outline)
            full_parts.append(prose)
        else:
            story_state = {}  # 累积剧情状态
            for i, rnd in enumerate(rounds, 1):
                self._check_cancel()
                events = [act.content for act in rnd.actions if getattr(act, "content", "")]
                narration = str(rnd.state_delta.get("narration", "") or "")
                states = rnd.state_delta.get("states", {}) or {}
                outline_event = "；".join(x for x in ev_by_round.get(rnd.round_number, []) if x)

                # 构建 ChapterContext 传递给渲染器
                chapter_ctx = None
                if hasattr(self, "_event_scheduler") and self._event_scheduler is not None:
                    try:
                        chapter_ctx = self._event_scheduler.build_chapter_context(
                            rnd.round_number, self._states, [],
                            outline.get("characters", []) if outline else None)
                    except Exception:
                        pass

                # 构建累积剧情上下文
                story_ctx = build_story_context(story_state, i)
                # 构建 Kuzu 连续性约束：阻止死而复生等矛盾
                continuity = self._build_continuity_ctx(self.graph, self._agents, i)
                if continuity:
                    story_ctx = continuity + "\n\n" + story_ctx

                # 构建 LanceDB 风格锚点
                style_anchors = ""
                if self._preprocessor is not None:
                    try:
                        anchors = self._retrieve_style_anchors(
                            self._preprocessor, self.session.source_material)
                        if anchors:
                            style_anchors = "【文笔锚点 — 参考以下原文笔法】\n" + "\n---\n".join(
                                a[:300] for a in anchors[:3])
                    except Exception:
                        pass

                text = await renderer.render_chapter(
                    chapter_idx=i, total_chapters=n,
                    seed_text=self.session.source_material,
                    round_events=events, round_narration=narration,
                    round_states=states, prev_tail=prev_tail,
                    outline_event=outline_event, target_words=per_ch,
                    chapter_context=chapter_ctx,
                    story_context=story_ctx,
                    style_anchors=style_anchors,
                )
                fname = f"{safe_title}_第{i:02d}章.txt"
                path = _write(fname, text)
                chapters_meta.append({"index": i, "title": f"第{i}章", "file": fname, "words": len(text)})

                is_fallback = "正文生成失败" in text[:50]
                if not is_fallback:
                    full_parts.append(f"第{i}章\n\n{text}")
                    prev_tail = text[-600:]
                    # 更新累积剧情状态，供下一章参考
                    append_chapter_summary(story_state, i, text, states)
                    # 持久化故事状态到 SQLite，暂停/重启不丢失
                    try:
                        data = self.store.get(self.session.id)
                        cfg = (data or {}).get("config_json", {}) or {}
                        if isinstance(cfg, str):
                            cfg = _json.loads(cfg)
                        cfg["story_state"] = story_state
                        self.store.update(self.session.id, config_json=_json.dumps(cfg, ensure_ascii=False))
                    except Exception:
                        pass
                else:
                    full_parts.append(f"第{i}章\n\n（正文生成失败，详细摘要见文件 {fname}）")
                self._log("report", f"第{i}/{n}章已生成并保存（{len(text)} 字）→ {fname}")

        prose = "\n\n".join(full_parts)
        # 合本
        combined_name = f"{safe_title}_全本.txt"
        _write(combined_name, prose)

        report_payload["is_literary"] = True
        report_payload["prose"] = prose
        report_payload["style"] = style
        report_payload["chapters"] = chapters_meta
        report_payload["work_dir"] = str(work_dir)
        report_payload["target_words"] = target_words

        # Mode 2 提纲对齐：final_state 达成度 + 关键事件轮次
        if outline and outline.get("characters"):
            name_to_state = {st.name: st for st in self._states.values()}
            arc = []
            for c in outline["characters"]:
                st = name_to_state.get(c.get("name"))
                if st is None or not c.get("final_state"):
                    continue
                res = self._rule_engine.judge(st, {"metrics": c["final_state"]})
                arc.append({"name": c["name"], "win_score": res["win_score"],
                            "final_metrics": {k: round(st.get_metric(k), 1)
                                              for k in c["final_state"]},
                            "target": c["final_state"]})
            report_payload["arc_alignment"] = arc
            report_payload["key_events_plan"] = outline.get("key_events", [])
        self._log("report", f"文学正文完成：{len(chapters_meta)} 章，共 {len(prose)} 字，已保存至 {work_dir}")

    def get_realtime_round(self) -> SimulationRound | None:
        rounds = getattr(self, "_simulation_rounds", None)
        if rounds and self.session.current_round > 0:
            idx = self.session.current_round - 1
            if idx < len(rounds):
                return rounds[idx]
        return None
