"""Deduction Orchestrator — five-stage pipeline coordinator with pause/resume."""
from __future__ import annotations

import asyncio
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


def _build_atmosphere_text(thermometer: dict[str, float]) -> str:
    """将社会温度计读数编译为叙事氛围指导文本，注入散文渲染 prompt。"""
    if not thermometer:
        return ""
    parts: list[str] = []
    if thermometer.get("faction_polarity", 40) > 60:
        parts.append("社会氛围是分裂与猜疑。每个人都在选边站，每条走廊里都有窃窃私语。对话应有潜台词，每个微笑背后都藏着一笔计算。")
    if thermometer.get("rumor_intensity", 30) > 50:
        parts.append("流言已成为不可忽视的力量。角色的一举一动都被放大、扭曲、传播。即使是私下对话，也要带上'可能被人听到'的紧张感。")
    if thermometer.get("intimate_pressure", 20) > 40:
        parts.append("私人情感已变成公开的赌注。每一次表白、每一次拒绝，都不再只是两个人的事——它们在改变权力天平。")
    if thermometer.get("external_threat", 10) > 30:
        parts.append("外部威胁正在迫近。所有人的行动都应带上紧迫感——对话要简短，动作要迅速，没有时间犹豫。")
    if not parts:
        return ""
    return "【本章氛围指导 — 根据当前故事世界的社会状态，本章应体现以下氛围】\n" + "\n".join(parts)


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
        self._canon_retries: int = 2
        self._scene_retries: int = 2
        self._quality_warnings: list[str] = []  # 章节级质量警告，供API报告和重跑反馈
        self._states_lock = asyncio.Lock()  # 守卫 _states 的跨协程读写
        self._auto_blueprint: bool = True
        self._last_canon_conflicts: list[str] = []
        self._style_mode: str = "manual"
        self._base_domain: str = "literary_realism"
        self._selected_style: str = ""

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
                    ("blueprint", self._phase1_6_blueprint),
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
        # 追加 agent 演化数据（speech_style + 行为准则）
        agents = getattr(self, "_agents", None)
        if agents:
            snapshot["agents"] = {}
            for a in agents:
                snapshot["agents"][a.entity_id] = {
                    "name": a.name,
                    "speech_style": getattr(a, "speech_style", ""),
                    "system_prompt_extra": getattr(a, "system_prompt_extra", ""),
                }
        # 追加情感投资数据
        sim = getattr(self, "_simulation_engine", None)
        if sim and hasattr(sim, "_emotional_investment"):
            snapshot["emotional_investment"] = sim._emotional_investment.to_dict()
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
        self._auto_blueprint = bool(cfg.get("auto_blueprint", True))
        try:
            self._canon_retries = max(0, int(cfg.get("canon_retries", 2)))
        except (TypeError, ValueError):
            self._canon_retries = 2

        # 2. 恢复规则包
        raw_domain = (cfg.get("domain") or "").strip()
        if raw_domain == "auto":
            self._style_mode = "auto"
            self._base_domain = "literary_realism"
            self._selected_style = ""
        else:
            self._style_mode = "manual"
            self._base_domain = raw_domain or "literary_realism"
        domain = self._base_domain
        if domain:
            from .rule_engine import RuleEngine
            try:
                self._rule_engine = RuleEngine.from_domain(domain)
                if self._style_mode == "manual":
                    self._selected_style = str(self._rule_engine.pack.get("style", "") or "")
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
            # 恢复 agent 演化数据（speech_style + 行为准则）
            saved_agents = (snapshot or {}).get("agents") or {}
            for a in self._agents:
                sa = saved_agents.get(a.entity_id)
                if sa and isinstance(sa, dict):
                    if sa.get("speech_style"):
                        a.speech_style = sa["speech_style"]
                    if sa.get("system_prompt_extra"):
                        a.system_prompt_extra = sa["system_prompt_extra"]
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

        # 恢复情感投资数据
        self._saved_investment_data = (snapshot or {}).get("emotional_investment")
        if self._saved_investment_data:
            self._log("orchestrator", "恢复情感投资追踪数据")

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
        self._auto_blueprint = bool(cfg.get("auto_blueprint", True))
        try:
            self._canon_retries = max(0, int(cfg.get("canon_retries", 2)))
        except (TypeError, ValueError):
            self._canon_retries = 2

        raw_domain = (cfg.get("domain") or "literary_realism").strip()
        if raw_domain == "auto":
            self._style_mode = "auto"
            self._base_domain = "literary_realism"
            self._selected_style = ""
        else:
            self._style_mode = "manual"
            self._base_domain = raw_domain
        domain = self._base_domain
        from .rule_engine import RuleEngine
        try:
            self._rule_engine = RuleEngine.from_domain(domain)
            if self._style_mode == "manual":
                self._selected_style = str(self._rule_engine.pack.get("style", "") or "")
            self._log("quantify", f"阶段1.5: 使用领域规则包: {self._rule_engine.pack.get('display_name', domain)}"
                      + (f" (风格模式: {'自动' if self._style_mode == 'auto' else '手动/' + self._selected_style})"))
        except Exception as e:
            logger.warning("[Orchestrator] 规则包加载失败: %s", e)
            self._rule_engine = None
            self._log("quantify", f"规则包加载失败: {e}")

    async def _phase1_6_blueprint(self) -> None:
        """阶段1.6：故事蓝图生成。

        无 outline（或缺 key_events）且 auto_blueprint 开启时，用 LLM 生成结构化大纲
        并写回 config_json['outline']；已有大纲则尊重人工输入；生成失败安全降级 freeform。
        """
        _current_phase.set("blueprint")
        self._check_cancel()
        self.store.update(self.session.id,
                          status=SessionStatus.BLUEPRINT_RUNNING.value,
                          phase=DeductionPhase.BLUEPRINT.value)

        existing = self._outline
        if existing and existing.get("key_events"):
            self._log("blueprint", "阶段1.6: 已提供人工大纲，跳过自动生成")
            return
        if not self._auto_blueprint:
            self._log("blueprint", "阶段1.6: 自动大纲已禁用，进入自由续写模式")
            return

        data = self.store.get(self.session.id)
        cfg = (data or {}).get("config_json", {}) or {}
        if isinstance(cfg, str):
            cfg = _json.loads(cfg)

        from .blueprint import generate_blueprint
        self._log("blueprint", "阶段1.6: 故事蓝图生成开始")
        blueprint = await generate_blueprint(
            self.session.source_material,
            domain=getattr(self, "_base_domain", "literary_realism"),
            total_rounds=self.session.total_rounds,
            target_words=self._target_words,
            style_mode=getattr(self, "_style_mode", "manual"),
            target_style=getattr(self, "_selected_style", ""),
            log_fn=self._log,
        )
        if not blueprint:
            self._log("blueprint", "阶段1.6: 未生成有效大纲，降级为自由续写模式")
            return

        self._outline = blueprint
        cfg["outline"] = blueprint
        self.store.update(self.session.id,
                          config_json=_json.dumps(cfg, ensure_ascii=False))
        # 蓝图完整性校验
        from .health_validator import validate_blueprint
        missing = validate_blueprint(blueprint)
        if missing:
            self._log("blueprint", f"蓝图完整性警告: 缺失 {'; '.join(missing)}")
        self._log("blueprint", "阶段1.6: 故事蓝图已写入会话配置，将按蓝图执行写作")

    async def _phase2_graph(self) -> None:
        _current_phase.set("graph")
        self._check_cancel()
        # 嵌入模型快速校验
        from literarycreation.core.providers import ProviderRegistry
        from .health_validator import validate_embedding_model
        registry = ProviderRegistry()  # None → 自动解析数据目录
        emb = registry.resolve_for_embedding()
        if emb.get("api_base") and emb.get("model_name"):
            validate_embedding_model(emb["api_base"], emb["model_name"], log_fn=self._log)
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
                        for k, v in spec["initial_state"].items():
                            if k not in metric_set:
                                continue
                            try:
                                states[a.entity_id].metrics[k] = float(v)
                            except (ValueError, TypeError):
                                logger.warning(
                                    "[Orchestrator] 角色 %s 的 %s 值非数字，跳过: %s",
                                    a.name, k, v,
                                )
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
            investment_data=getattr(self, "_saved_investment_data", None),
        )
        self._simulation_engine = engine  # 供散文渲染阶段读取社会温度

        rounds: list[SimulationRound] = []
        start_rnd = self._resume_start_round + 1
        # 注入前次运行的质量警告作为本轮行为强化信号
        if self._quality_warnings and start_rnd <= 3:
            for w in self._quality_warnings[-3:]:
                self._log("simulation",
                          f"[质量反馈] 前次运行警告：{w[:80]}。本轮角色应做出更果断的选择。")
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

            # 每 3 轮写一次检查点（防崩溃丢失全部进度）
            if rnd % 3 == 0 and rnd < total_rounds:
                self._save_pause_snapshot(self.session.id)
                self._log("simulation", f"  已写入第 {rnd} 轮检查点")

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

    def _resolve_effective_style(self, outline: dict[str, Any] | None) -> tuple[str, bool]:
        """解析生效写作风格：自动=素材检测；手选=所选风格（冲突时启用迁移）。

        Returns (style, migrate_required).
        Sets self._migrate_style / self._detected_style as side effects.
        """
        style_mode = getattr(self, "_style_mode", "manual")
        detected_style = str((outline or {}).get("detected_style", "") or "").strip()
        selected_style = getattr(self, "_selected_style", "") or ""
        if not selected_style and self._rule_engine is not None and hasattr(self._rule_engine, "pack"):
            selected_style = str(self._rule_engine.pack.get("style", "") or "")
        if style_mode == "auto":
            style = detected_style or selected_style or "现实主义"
            migrate = False
        else:
            style = selected_style or "现实主义"
            migrate = bool(detected_style and detected_style != style)
        self._migrate_style = migrate
        self._detected_style = detected_style
        self._log("report",
                  f"生效写作风格: {style}（模式={'自动' if style_mode == 'auto' else '手动'}"
                  + (f"，素材风格={detected_style}，逐章向目标迁移" if migrate else "") + "）")
        return style, migrate

    def _assemble_story_ctx(
        self,
        *,
        i: int, n: int,
        rnd: Any,
        outline: dict[str, Any] | None,
        style: str,
        story_state: dict[str, Any],
        canon: Any,
        ev_by_round: dict[int, list[str]],
    ) -> dict[str, Any]:
        """为第 i 章组装所有上下文注入：连续性/防重复/人格/阶段/风格迁移/正典/POV/揭示/场景种子/高潮推进/短语/锚点。

        Returns {"story_ctx","chapter_ctx","outline_event","events","narration","states","style_anchors"}。
        注入顺序原样保留（共 9 层）。
        """
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
        from .prose_renderer import build_story_context

        story_ctx = build_story_context(story_state, i)
        # Kuzu 连续性约束
        continuity = self._build_continuity_ctx(self.graph, self._agents, i)
        if continuity:
            story_ctx = continuity + "\n\n" + story_ctx
        # 防重复上下文
        from .prose_renderer import build_anti_repeat_context

        anti_repeat = build_anti_repeat_context(story_state)
        if anti_repeat:
            story_ctx = anti_repeat + "\n\n" + story_ctx
        # 上一章的读者反馈 → 本章的行为修正
        prev_feedbacks = story_state.get("reader_feedback") or []
        if prev_feedbacks:
            from .reader_model import build_reader_feedback_block
            reader_ctx = build_reader_feedback_block(prev_feedbacks[-1])
            if reader_ctx:
                story_ctx = reader_ctx + "\n\n" + story_ctx
        # 社会氛围（从模拟引擎温度计编译为叙事氛围文本）
        sim = getattr(self, "_simulation_engine", None)
        if sim is not None:
            atmosphere = _build_atmosphere_text(getattr(sim, "_social_thermometer", {}))
            if atmosphere:
                story_ctx = atmosphere + "\n\n" + story_ctx
        # 叙述者声音（全书一致，内部已生成）
        if hasattr(self, "_narrator") and self._narrator:
            voice_block = self._narrator.build_voice_block()
            if voice_block:
                story_ctx = voice_block + "\n\n" + story_ctx
        # 场景权重分配
        if hasattr(self, "_scene_allocator") and self._scene_allocator:
            per_ch = getattr(self, "_per_ch", 0)
            scene_alloc = self._scene_allocator.allocate(
                events, outline, chapter_ctx, per_ch)
            if scene_alloc:
                story_ctx = scene_alloc + "\n\n" + story_ctx
        # 母题回声
        if hasattr(self, "_motif_tracker") and self._motif_tracker:
            motif_hint = self._motif_tracker.inject_prompt(i)
            if motif_hint:
                story_ctx = motif_hint + "\n\n" + story_ctx
        # 角色行为准则（从反思机制产出，应在散文的行文中自然体现）
        reflect_lines = []
        for a in (self._agents or []):
            extra = getattr(a, "system_prompt_extra", "")
            if extra:
                reflect_lines.append(f"- {a.name}：{extra}")
        if reflect_lines:
            story_ctx = ("【角色当前行为准则 — 这些是角色在经历中习得的经验，"
                         "应在本章的行动和对话中自然体现，而非直接说教】\n"
                         + "\n".join(reflect_lines) + "\n\n" + story_ctx)
        # 角色人格
        persona_lines = []
        for a in (self._agents or []):
            ss = getattr(a, "speech_style", "")
            ss_hint = f" | 说话风格：{ss}" if ss else ""
            persona_lines.append(f"- {a.name}（{a.persona[:60]}{ss_hint}）")
        if persona_lines:
            story_ctx = ("【角色语言特征 — 每个角色说话应符合其人格与对话风格】\n"
                         + "\n".join(persona_lines) + "\n\n" + story_ctx)
        # 叙事阶段 + 技巧
        from .prose_renderer import (
            build_phrase_hint,
            build_pov_text,
            build_reveal_text,
            build_style_migration,
            get_technique,
            pov_allows_switch,
        )

        technique = get_technique(i, n, allow_pov_switch=pov_allows_switch(outline))
        if technique:
            story_ctx = f"【本章叙事技巧指导】{technique}\n\n" + story_ctx
        # 风格迁移
        if getattr(self, "_migrate_style", False):
            mig = build_style_migration(getattr(self, "_detected_style", ""), style, i, n)
            if mig:
                story_ctx = mig + "\n\n" + story_ctx
        # 正典约束 + POV + 揭示层级
        canon_ctx = canon.build_constraint_text(current_round=i)
        if canon_ctx:
            story_ctx = canon_ctx + "\n\n" + story_ctx
        pov_ctx = build_pov_text(outline)
        if pov_ctx:
            story_ctx = pov_ctx + "\n\n" + story_ctx
        reveal_ctx = build_reveal_text(outline, i)
        if reveal_ctx:
            story_ctx = reveal_ctx + "\n\n" + story_ctx
        # 场景种子
        from .prose_renderer import build_scene_seeds_text

        seeds_ctx = build_scene_seeds_text(outline, i)
        if seeds_ctx:
            story_ctx = seeds_ctx + "\n\n" + story_ctx

        # 角色轨迹：登场/退场提醒
        traj_lines: list[str] = []
        for c in (outline.get("characters") if outline else []) or []:
            if not isinstance(c, dict):
                continue
            nm = c.get("name", "")
            if c.get("first_appearance") == i:
                traj_lines.append(f"本章引入角色「{nm}」，需交代其身份与来意")
            if c.get("last_appearance") == i:
                ex = c.get("exit", "") or "完成其使命"
                traj_lines.append(f"角色「{nm}」将于本章完成弧光并有意义地退场（{ex}），不要让其无声消失")
        if traj_lines:
            story_ctx = "【角色轨迹】\n" + "\n".join(f"- {t}" for t in traj_lines) + "\n\n" + story_ctx
        # 高潮推进（仅在本章无强制事件时注入）
        if self._climax_driver is not None and not outline_event:
            climax_ctx = self._climax_driver.build_text(i, canon, story_state)
            if climax_ctx:
                story_ctx = climax_ctx + "\n\n" + story_ctx
        # 结局章专属约束（最后3章）
        if i >= max(1, n - 2) and i > 1:
            ending_block = _build_ending_block(i, n)
            if ending_block:
                story_ctx = ending_block + "\n\n" + story_ctx
        # 短语避重
        phrase_hint = build_phrase_hint(story_state)
        if phrase_hint:
            story_ctx = phrase_hint + "\n\n" + story_ctx
        # 风格锚点
        style_anchors = ""
        if self._preprocessor is not None:
            try:
                anchors = self._retrieve_style_anchors(
                    self._preprocessor, self.session.source_material)
                if anchors:
                    style_anchors = ("【文笔锚点 — 参考以下原文笔法】\n"
                                     + "\n---\n".join(a[:300] for a in anchors[:3]))
            except Exception:
                pass

        return {
            "story_ctx": story_ctx,
            "chapter_ctx": chapter_ctx,
            "outline_event": outline_event,
            "events": events,
            "narration": narration,
            "states": states,
            "style_anchors": style_anchors,
        }

    async def _render_and_validate_chapter(
        self,
        *,
        renderer: Any,
        i: int, n: int,
        ci: dict[str, Any],
        story_ctx: str,
        prev_tail: str,
        per_ch: int,
        canon: Any,
        enforcer: Any,
        story_state: dict[str, Any],
    ) -> str:
        """渲染一章 → 正典校验重写 → 场景去重重写 → 字数扩写。返回最终 text。"""
        text = await renderer.render_chapter(
            chapter_idx=i, total_chapters=n,
            seed_text=self.session.source_material,
            round_events=ci["events"], round_narration=ci["narration"],
            round_states=ci["states"], prev_tail=prev_tail,
            outline_event=ci["outline_event"], target_words=per_ch,
            chapter_context=ci["chapter_ctx"],
            story_context=story_ctx,
            style_anchors=ci["style_anchors"],
        )

        async def _rerender(fix_ctx: str) -> str:
            return await renderer.render_chapter(
                chapter_idx=i, total_chapters=n,
                seed_text=self.session.source_material,
                round_events=ci["events"], round_narration=ci["narration"],
                round_states=ci["states"], prev_tail=prev_tail,
                outline_event=ci["outline_event"], target_words=per_ch,
                chapter_context=ci["chapter_ctx"],
                story_context=fix_ctx,
                style_anchors=ci["style_anchors"],
            )

        is_fallback = "正文生成失败" in text[:50]
        ccu: list[str] = []
        # 正典一致性校验 → 冲突则自动重写
        if not is_fallback and self._canon_retries > 0:
            attempt = 0
            conflicts = canon.validate(text, current_round=i)
            while conflicts and attempt < self._canon_retries:
                attempt += 1
                self._log("report",
                          f"第{i}章检测到 {len(conflicts)} 处正典冲突，第 {attempt} 次自动重写")
                fix_ctx = ("【一致性修正 — 上一稿存在下列事实冲突，请重写本章以彻底消除，"
                           "不得保留矛盾情节】\n"
                           + "\n".join(f"- {c}" for c in conflicts)
                           + "\n\n" + story_ctx)
                text = await _rerender(fix_ctx)
                is_fallback = "正文生成失败" in text[:50]
                if is_fallback:
                    break
                conflicts = canon.validate(text, current_round=i)
            if conflicts:
                # 强提示 + 替代式后处理赎战（无额外 LLM 调用）
                self._log("report",
                          f"第{i}章仍存在 {len(conflicts)} 处正典冲突（已达重写上限），执行替换式后处理")
                logger.warning("[Orchestrator] 第%d章未消除的正典冲突: %s", i, conflicts)
                text, rep = canon.postprocess_resurrections(text)
                if rep > 0:
                    self._log("report", f"第{i}章替换式后处理：修正 {rep} 处复活表述")
                    # 后处理后再次校验
                    conflicts = canon.validate(text, current_round=i)
                    if conflicts:
                        self._log("report",
                                  f"第{i}章后处理后仍有 {len(conflicts)} 处冲突，记录")
                if conflicts:
                    ccu = list(conflicts)
            if ccu:
                self._quality_warnings.append(
                    f"第{i}章：正典冲突未完全消除 — {'；'.join(c[:1] for c in ccu[:2])}")

        # 场景去重（独立于正典的重写预算）
        is_fallback = "正文生成失败" in text[:50]
        if not is_fallback and self._scene_retries > 0:
            s_attempt = 0
            scene_conflicts = canon.detect_scene_repetition(text, i, story_state)
            while scene_conflicts and s_attempt < self._scene_retries:
                s_attempt += 1
                self._log("report",
                          f"第{i}章场景与历史章节雷同，第 {s_attempt} 次改写")
                fix_ctx = ("【场景去重 — 本章与已写章节场景高度雷同，请改写为推进剧情的全新场景，"
                           "不要重复开场/地点/动作】\n"
                           + "\n".join(f"- {c}" for c in scene_conflicts)
                           + "\n\n" + story_ctx)
                text = await _rerender(fix_ctx)
                is_fallback = "正文生成失败" in text[:50]
                if is_fallback:
                    break
                scene_conflicts = canon.detect_scene_repetition(text, i, story_state)
            if scene_conflicts:
                self._log("report",
                           f"第{i}章场景重复未消除（已达改写上限），保留当前稿")
                logger.warning("[Orchestrator] 第%d章场景重复: %s", i, scene_conflicts)
                self._quality_warnings.append(
                    f"第{i}章：场景与前章雷同未消除")

        # 字数硬约束
        is_fallback = "正文生成失败" in text[:50]
        if not is_fallback and per_ch > 0:
            passed, _msg = enforcer.check(text, per_ch)
            if not passed:
                from literarycreation.core.llm_client import DeductionLLMClient, Message
                from ._utils import extract_text as _extract_text
                _exp_client = DeductionLLMClient()

                async def _expand(prompt: str, _c=_exp_client, _pc=per_ch) -> str:
                    resp = await _c.chat(
                        [Message(role="user", content=prompt)],
                        system="你是文学作家，在完整保留原情节、人物、对话与顺序的前提下扩写加长本章正文。只输出正文。",
                        temperature=0.8, max_tokens=int(_pc * 2.4) if _pc else 0)
                    return _extract_text(resp).strip()

                text = await enforcer.enforce(text, per_ch, _expand, max_retries=2, log_fn=self._log)
                post = canon.validate(text, current_round=i)
                if post:
                    logger.warning("[Orchestrator] 第%d章扩写后仍存正典风险: %s", i, post)
                if post:
                    ccu = list(post)

        self._last_canon_conflicts = ccu
        return text

    def _record_chapter(
        self,
        *,
        i: int,
        text: str,
        story_state: dict[str, Any],
        canon: Any,
        states: dict[str, Any],
        alive_checker: Any,
    ) -> None:
        """更新剧情状态、登记正典、记录指纹/短语、持久化 story_state。"""
        from .prose_renderer import append_chapter_summary

        append_chapter_summary(story_state, i, text, states)
        try:
            canon.establish_from_chapter(text, i, self._states, alive_checker)
            canon.record_scene(text, i, story_state)
            canon.save_into(story_state)
        except Exception:
            pass
        try:
            from .prose_renderer import fingerprint_text
            fps = fingerprint_text(text)
            story_state.setdefault("used_fingerprints", []).extend(fps)
            syn = text[:120].replace("\n", " ")
            story_state.setdefault("used_scenes", "")
            story_state["used_scenes"] = story_state["used_scenes"][:1500] + f"\n第{i}章已写场景：{syn}"
            from .prose_renderer import track_repeated_phrases
            new_phrases = track_repeated_phrases(text)
            existing = set(story_state.get("tracked_phrases", []))
            existing.update(new_phrases)
            story_state["tracked_phrases"] = list(existing)[:8]
            # 跨章整句/箴言重复追踪
            from .prose_renderer import update_repeated_sentences
            update_repeated_sentences(story_state, text)
        except Exception:
            pass
        try:
            data = self.store.get(self.session.id)
            cfg = (data or {}).get("config_json", {}) or {}
            if isinstance(cfg, str):
                cfg = _json.loads(cfg)
            cfg["story_state"] = story_state
            self.store.update(self.session.id, config_json=_json.dumps(cfg, ensure_ascii=False))
        except Exception:
            pass

    async def _render_prose(self, report_payload: dict[str, Any]) -> None:
        """文学模式 Phase 5：逐章生成正文并落盘，计算提纲对齐。"""
        import re as _re
        from pathlib import Path

        from literarycreation.core.config import config as _cfg

        from .canon import CanonLedger
        from .prose_renderer import ProseRenderer

        rounds = list(getattr(self, "_simulation_rounds", []))
        outline = getattr(self, "_outline", None)

        style, _migrate = self._resolve_effective_style(outline)
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

        # 初始化叙述者声音代理（检测风格切换时重新生成）
        from .narrator_broker import NarratorRegistry
        current_style = getattr(self, "_selected_style", style) or style
        if (not hasattr(self, "_narrator_style") or
                self._narrator_style != current_style or
                not hasattr(self, "_narrator")):
            self._narrator_style = current_style
            self._narrator = NarratorRegistry(style=current_style)
            try:
                await self._narrator.generate(DeductionLLMClient(), self.session.source_material)
            except Exception:
                pass

        # 初始化技艺守卫
        from .craft_guard import SceneAllocator, MotifTracker
        self._scene_allocator = SceneAllocator()
        self._motif_tracker = MotifTracker()
        self._per_ch = per_ch

        # 情感投资数据（从模拟器传出，存 story_state 供高潮驱动用）
        sim = getattr(self, "_simulation_engine", None)
        if sim and hasattr(sim, "_emotional_investment"):
            inv_data = sim._emotional_investment.to_dict()

        chapters_meta: list[dict[str, Any]] = []
        full_parts: list[str] = []
        prev_tail = ""
        # 读者反馈循环
        reader_feedback_list: list[dict] = []
        # 多POV模式检测
        pov_chars = None
        if outline and outline.get("pov", {}).get("mode") == "multi":
            pov_chars = [c.get("name", "") for c in outline.get("characters", [])
                         if c.get("name")][:3] or None

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
            story_state: dict[str, Any] = {}  # 累积剧情状态
            if outline and outline.get("themes"):
                story_state["themes"] = outline["themes"]
                story_state["theme_appearances"] = {}
            if outline and outline.get("subplots"):
                story_state["subplots"] = outline["subplots"]
            canon = CanonLedger.from_state(story_state, blueprint=outline)
            alive_checker = (lambda st: self._rule_engine.is_alive(st)) if self._rule_engine else None
            from .climax_driver import ClimaxDriver
            from .word_count_enforcer import WordCountEnforcer
            self._climax_driver = ClimaxDriver(n)
            enforcer = WordCountEnforcer(min_ratio=0.75)
            for i, rnd in enumerate(rounds, 1):
                self._check_cancel()
                self._log("report", f"正在渲染第{i}/{n}章...")
                ci = self._assemble_story_ctx(
                    i=i, n=n, rnd=rnd, outline=outline, style=style,
                    story_state=story_state, canon=canon, ev_by_round=ev_by_round)
                text = await self._render_and_validate_chapter(
                    renderer=renderer, i=i, n=n, ci=ci, story_ctx=ci["story_ctx"],
                    prev_tail=prev_tail, per_ch=per_ch, canon=canon, enforcer=enforcer,
                    story_state=story_state)

                # 对话风格事后检测
                from .prose_renderer import _check_dialogue_style
                d_violations = _check_dialogue_style(text, self._agents, i)
                for dv in d_violations:
                    self._log("report", dv)
                    self._quality_warnings.append(dv)

                fname = f"{safe_title}_第{i:02d}章.txt"
                _write(fname, text)
                chapters_meta.append({"index": i, "title": f"第{i}章", "file": fname,
                                       "words": len(text),
                                       "canon_conflicts": self._last_canon_conflicts})

                is_fallback = "正文生成失败" in text[:50]
                if not is_fallback:
                    full_parts.append(f"第{i}章\n\n{text}")
                    prev_tail = text[-600:]

                    # 文学质检：钩子/节奏/重量
                    from .prose_renderer import (
                        _check_chapter_hook, _check_opening_hook,
                        _analyze_rhythm, _compute_chapter_weight)
                    hook = _check_chapter_hook(text)
                    if hook:
                        story_state["next_chapter_hint"] = hook
                    ohook = _check_opening_hook(text)
                    if ohook:
                        story_state.setdefault("opening_hook_warnings", []).append(
                            f"第{i}章: {ohook}")
                    rhythm = _analyze_rhythm(text)
                    ra = story_state.setdefault("rhythm_history", [])
                    ra.append(rhythm)
                    if len(ra) >= 2:
                        if (ra[-1]["action_ratio"] > 0.3 and ra[-2]["action_ratio"] > 0.3):
                            story_state["rhythm_hint"] = "前两章节奏偏重动作。本章请减速——给角色一个停下来思考的场景。"
                        elif (ra[-1]["reflect_ratio"] > 0.4 and ra[-2]["reflect_ratio"] > 0.4):
                            story_state["rhythm_hint"] = "前两章节奏偏重内心反思。本章请加速——引入一个外部事件打破沉思。"
                    weight = _compute_chapter_weight(text)
                    cw = story_state.setdefault("chapter_weights", [])
                    cw.append(weight)
                    if len(cw) >= 3 and max(1e-6, cw[-2]) > 2 * max(1e-6, cw[-1]):
                        story_state["weight_hint"] = "前一章的情节重量显著低于前两章。本章请加大情节密度——一个重要的揭示、一个不可逆的选择、或一次关键的对峙。"

                    # 主题出现追踪
                    themes = story_state.get("themes") or []
                    if themes:
                        ta = story_state.setdefault("theme_appearances", {})
                        for t in themes:
                            name = t.get("name", "")
                            desc = t.get("description", "")
                            # 简单词袋匹配：如果章节文本包含主题名或其描述中的关键词
                            kw = name[:4] if len(name) >= 4 else name
                            dw = desc[:6] if len(desc) >= 6 else desc
                            cnt = text.count(kw) + text.count(dw)
                            ta[f"{i}_{name}"] = cnt

                    # 母题观察
                    self._motif_tracker.observe_chapter(text, i)

                    # 读者体验模拟
                    from .reader_model import simulate_reader, build_reader_feedback_block
                    reader_fb = await simulate_reader(DeductionLLMClient(), text, i)
                    if reader_fb:
                        reader_feedback_list.append(reader_fb)
                        story_state["reader_feedback"] = reader_feedback_list

                    self._record_chapter(i=i, text=text, story_state=story_state,
                                         canon=canon, states=ci["states"], alive_checker=alive_checker)
                else:
                    full_parts.append(f"第{i}章\n\n（正文生成失败，详细摘要见文件 {fname}）")
                self._log("report", f"第{i}/{n}章已生成并保存（{len(text)} 字）→ {fname}")

        # 修订流水线：对读者反馈不佳的章节做编辑增强
        from .revision_pipeline import RevisionPipeline
        rev_pipe = RevisionPipeline(self._narrator.build_voice_block())
        for idx, ch_text in enumerate(full_parts, 1):
            rfb = reader_feedback_list[idx - 1] if idx <= len(reader_feedback_list) else None
            cw_data = story_state.get("chapter_weights", [])
            cw = cw_data[idx - 1] if idx <= len(cw_data) else 0.0
            try:
                if idx == n:
                    revised, changes = await rev_pipe.revise_final(
                        DeductionLLMClient(), idx, ch_text)
                else:
                    revised, changes = await rev_pipe.revise(
                        DeductionLLMClient(), idx, ch_text, rfb, cw)
                if changes:
                    full_parts[idx - 1] = revised
                    self._log("report", f"第{idx}章经编辑修订（{';'.join(changes[:3])}）")
            except Exception:
                pass

        # 全局质检：角色出场 + 宏观节奏 + 意象
        from .imagery_tracker import ImageryTracker
        imagery = ImageryTracker()
        for idx, ch_text in enumerate(full_parts, 1):
            imagery.scan_chapter(ch_text, idx)
        imagery_warnings = imagery.analyze_trajectories()
        if imagery_warnings:
            report_payload.setdefault("imagery_warnings", []).extend(imagery_warnings)

        # 宏观节奏分析
        rh = story_state.get("rhythm_history") or []
        macro_warnings = _analyze_macro_rhythm(rh, n)
        if macro_warnings:
            report_payload.setdefault("macro_rhythm_warnings", []).extend(macro_warnings)

        # 角色出场验证
        appearance_warnings = _validate_character_appearances(
            full_parts, outline, getattr(self, "_agents", []))
        if appearance_warnings:
            report_payload.setdefault("character_appearance_warnings", []).extend(
                appearance_warnings)

        # 章首钩子汇总
        oh_warnings = story_state.get("opening_hook_warnings") or []
        if oh_warnings:
            report_payload.setdefault("opening_hook_warnings", []).extend(oh_warnings)

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
                try:
                    res = self._rule_engine.judge(st, {"metrics": c["final_state"]})
                    arc.append({"name": c["name"], "win_score": res["win_score"],
                                "final_metrics": {k: round(st.get_metric(k), 1)
                                                  for k in c["final_state"]},
                                "target": c["final_state"]})
                except (ValueError, TypeError) as e:
                    logger.warning("[Orchestrator] 弧光对齐跳过 %s（final_state 值非法）: %s",
                                   c.get("name"), e)
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


def _build_ending_block(chapter_idx: int, total_chapters: int) -> str:
    """结局章专属约束文本。"""
    if chapter_idx == total_chapters:
        return (
            "【最终章 — 以下约束优先级最高】\n"
            "1. 至少一个角色必须做出不可逆的决定。这个决定在逻辑上必然，在情感上意外。\n"
            "2. 不要在结局解释一切。留一个未回答的问题——不是悬念，是余韵。\n"
            "3. 最后一句话的重量等于整本书。不要用'从此' '故事就这样' 类收束词——用意象、用动作、用一个无法忘记的细节。\n"
            "4. 如果前文有麦高芬——它必须被完整揭示或不可逆地改变。不能含糊过去。\n"
            "5. 读者读完后应该感到的不是'结束了'，而是'这个故事的震动还在继续'。"
        )
    if chapter_idx == total_chapters - 1:
        return (
            "【倒数第二章 — 为结局做最后的蓄力】\n"
            "1. 本章应在结尾处把所有的势能汇聚到一点——让读者清楚地感觉到'下一章就是结局'。\n"
            "2. 最后一个配角在此表明最终立场。站队完成——下一章不再有新的立场变化。\n"
            "3. 本章的结尾是一个不可逆的动作——而非对话或心理活动——指向最后一章。"
        )
    return ""


def _analyze_macro_rhythm(rhythm_history: list[dict], total_chapters: int) -> list[str]:
    """全局节奏分析。"""
    if len(rhythm_history) < 3:
        return []
    warnings: list[str] = []
    action_ratios = [r.get("action_ratio", 0.3) for r in rhythm_history]
    for i in range(len(action_ratios) - 2):
        if all(a < 0.15 for a in action_ratios[i:i + 3]):
            warnings.append(
                f"第{i+1}至{i+3}章连续 3 章动作密度偏低——读者可能失去耐心。")
            break
    for i in range(len(action_ratios) - 2):
        if all(a > 0.35 for a in action_ratios[i:i + 3]):
            warnings.append(
                f"第{i+1}至{i+3}章连续 3 章高强度——读者可能情感疲劳。")
            break
    if len(action_ratios) >= 5:
        import statistics
        std = statistics.stdev(action_ratios)
        if std < 0.06:
            warnings.append("全书节奏过于均匀（标准差<0.06）。好小说的节奏应有起伏——暴风雨和宁静交替。")
    return warnings


def _validate_character_appearances(prose_chapters: list[str],
                                     outline: dict | None,
                                     agents: list) -> list[str]:
    """校验角色出场次数是否符合蓝图。"""
    if not outline or not outline.get("characters"):
        return []
    import re as _re
    warnings: list[str] = []
    for c in outline["characters"]:
        name = c.get("name", "").strip()
        if not name:
            continue
        last = int(c.get("last_appearance", 999) or 999)
        appearances: list[int] = []
        for idx, text in enumerate(prose_chapters, 1):
            if name in text and _re.search(
                f'{name}(道|说|问道|走|站|坐|看|指|拿|放|推|拉|笑|叹|摇|点)',
                text
            ):
                appearances.append(idx)
        if len(appearances) < 2:
            continue
        for i in range(len(appearances) - 1):
            gap = appearances[i + 1] - appearances[i]
            if gap > 3:
                warnings.append(
                    f"「{name}」在第{appearances[i]}章后消失 {gap} 章，"
                    f"于第{appearances[i+1]}章重新出现。读者可能已忘记这个角色。")
                break
        if last < 999 and appearances and max(appearances) < last - 1:
            warnings.append(
                f"蓝图设定「{name}」last_appearance={last}，"
                f"但实际最后出现是第{max(appearances)}章。")
    return warnings
