"""Phase 4: Simulation — dual-path LanceDB memory, dual-mode literary decisions.

Modes:
  - freeform: Free writing — agents decide freely, no forced events
  - blueline: Blueprint execution — key events are enforced, LLM dramatizes
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import uuid
from collections.abc import Callable
from typing import Any, Literal

import numpy as np

from .event_scheduler import EventScheduler
from .models import DeductionAgentProfile, SimulationAction, SimulationRound
from .orchestrator import _PhaseCancelledError
from .preprocessor import DeductionPreprocessor

logger = logging.getLogger(__name__)


class SimulationEngine:
    """Multi-agent literary simulation engine with LanceDB dual-path memory."""

    def __init__(
        self,
        agents: list[DeductionAgentProfile],
        graph: Any = None,
        total_rounds: int = 10,
        log_fn: Callable[[str, str], None] | None = None,
        preprocessor: DeductionPreprocessor | None = None,
        chat_fn: Any = None,
        pre_goals: list[str] | None = None,
        *,
        seed: int | None = None,
        temperature: float = 0.7,
        persist_events: bool = True,
        rule_engine: Any = None,
        states: dict[str, Any] | None = None,
        enable_narrate: bool = True,
        env: dict[str, str] | None = None,
        cancel_event: Any = None,
        outline: dict[str, Any] | None = None,
        fsm_override_store: dict | None = None,
        mode: Literal["freeform", "blueline"] = "freeform",
        event_scheduler: EventScheduler | None = None,
        max_concurrent: int = 2,
    ) -> None:
        self.agents = agents
        self.graph = graph
        self._name_to_id: dict[str, str] = {a.name: a.entity_id for a in agents}
        self.total_rounds = total_rounds
        self._log = log_fn or (lambda p, m: None)
        self._preprocessor = preprocessor
        self._chat_fn = chat_fn
        self._immutable_goals: list[str] = list(pre_goals or [])
        self._cancel = cancel_event
        self._persist_events = persist_events
        self._temperature = temperature
        self._rng = random.Random(seed)
        self._rule_engine = rule_engine
        self._states: dict[str, Any] = states or {}
        self._enable_narrate = enable_narrate
        self._env = env
        self._outline: dict[str, Any] | None = outline
        self._fsm_override_store: dict = fsm_override_store or {}
        self._mode = mode
        self._scheduler = event_scheduler
        self._round_mandate: str = ""
        self._max_concurrent = max(1, max_concurrent)
        self._sem = asyncio.Semaphore(self._max_concurrent)

        # 社会温度计：5个维度感知社会氛围（舆论/流言/派系/亲密压力/外部威胁）
        self._social_thermometer: dict[str, float] = {
            "public_opinion": 50.0,
            "rumor_intensity": 30.0,
            "faction_polarity": 40.0,
            "intimate_pressure": 20.0,
            "external_threat": 10.0,
        }
        # 行动类型 → 社会温度变化
        self._SOCIAL_HEAT_MAP: dict[str, dict[str, float]] = {
            "confront": {"faction_polarity": 8, "rumor_intensity": 5},
            "betray": {"faction_polarity": 12, "intimate_pressure": 15, "rumor_intensity": 10},
            "ally": {"faction_polarity": -5, "rumor_intensity": 3},
            "confess": {"intimate_pressure": 10, "rumor_intensity": 5},
            "investigate": {"rumor_intensity": 3, "external_threat": 2},
            "protect": {"public_opinion": 5, "intimate_pressure": 5},
            "manipulate": {"faction_polarity": 6, "rumor_intensity": 8},
            "observe": {},
        }
        # 人格反思计数器：per-agent 事件累积数 + 上次反思轮次
        self._reflect_counters: dict[str, int] = {}
        self._last_reflect_round: dict[str, int] = {}
        # 上次反思时的指标快照（用于检测剧变）
        self._last_reflect_snapshot: dict[str, dict[str, float]] = {}
        # 内存事件历史（供反思时查询）
        self._event_history: list[dict[str, Any]] = []

        from .narrative_memory import NarrativeMemoryStore
        self.narrative_memory = NarrativeMemoryStore(cap=8)

        from .strategic_reasoner import StrategicReasoner
        self.reasoner = StrategicReasoner(
            chat_fn=chat_fn,
            preprocessor=preprocessor,
            immutable_goals=self._immutable_goals,
        )

    def _narrative_memory_text(self, agent_id: str) -> str:
        try:
            return self.narrative_memory.inject_prompt(agent_id)
        except Exception:
            return ""

    def _pop_override(self, agent: Any) -> dict | None:
        store = self._fsm_override_store
        if not store:
            return None
        key = None
        for k in (agent.name, agent.entity_id):
            if k in store:
                key = k
                break
        if key is None:
            return None
        ov = store[key]
        try:
            remaining = int(ov.get("remaining", 1))
        except (TypeError, ValueError):
            remaining = 1
        remaining -= 1
        if remaining <= 0:
            store.pop(key, None)
        else:
            ov["remaining"] = remaining
        return {
            "action_type": str(ov.get("action_type", "observe")),
            "intensity": float(ov.get("intensity", 0.6)),
            "target": str(ov.get("target", "") or ""),
            "rationale": f"[用户强制] {ov.get('action_type', 'observe')}"
                        + (f" -> {ov.get('target')}" if ov.get("target") else ""),
        }

    async def run_round(self, round_number: int) -> SimulationRound:
        if self._mode == "blueline" and self._scheduler is not None:
            return await self._run_round_blueline(round_number)
        return await self._run_round_freeform(round_number)

    # ── Mode B: Blueprint execution ──

    async def _run_round_blueline(self, round_number: int) -> SimulationRound:
        from literarycreation.core.llm_client import DeductionLLMClient as LLMClient
        re_engine = self._rule_engine
        client = LLMClient()
        sim_round = SimulationRound(round_number=round_number)

        scheduler = self._scheduler
        if scheduler is None:
            return sim_round
        events = scheduler.get_events_for_round(round_number)
        mandate_text = scheduler.get_mandate_text(round_number)
        correction_level = scheduler.check_correction(round_number, self._states)

        if mandate_text:
            self._log("simulation", f"第{round_number}轮蓝图事件: {mandate_text[:80]}...")

        # 每轮清除 LanceDB 动态检索缓存，确保新事件可被检索
        if self._preprocessor is not None:
            self._preprocessor.clear_round_cache()

        alive_agents = self.agents

        async def _decide(agent):
            if self._cancel is not None and self._cancel.is_set():
                raise _PhaseCancelledError()
            ov = self._pop_override(agent)
            if ov is not None:
                ov["actor_id"] = agent.entity_id
                ov["driver"] = "forced"
                return ov
            st = self._states.get(agent.entity_id)
            if st is None:
                return None
            others = self._build_others_ctx(agent.entity_id, alive_agents)
            dyn, static, kuzu_self = await self._retrieve_memory(agent, round_number)
            rel_ctx = self._build_relation_context(agent.entity_id)
            recent = self._build_recent_context_for_agent(agent.entity_id)

            async with self._sem:
                dec = await self.reasoner.reason(
                    agent=agent, round_number=round_number, state=st, mode="blueline",
                    event_mandate=mandate_text, correction_level=correction_level,
                    other_context=others, relationship_context=rel_ctx,
                    static_knowledge=static, dynamic_memory=dyn,
                    recent_events=recent, env_context=self._env_context(),
                    self_memory=kuzu_self,
                    narrative_memory=self._narrative_memory_text(agent.entity_id),
                )
            if dec:
                dec["actor_id"] = agent.entity_id
                dec["driver"] = "blueline"
            else:
                dec = {"actor_id": agent.entity_id, "action_type": "observe", "intensity": 0.3,
                       "target": "", "rationale": "[蓝图模式] 默认观察", "driver": "blueline"}
            dec.setdefault("action_type", "observe")
            dec.setdefault("intensity", 0.3)
            dec.setdefault("target", "")
            return dec

        ranges = re_engine.ranges()
        decisions = []
        self_seen: dict[str, dict[str, float]] = {}
        for j, agent in enumerate(alive_agents):
            dec = await _decide(agent)
            if dec is None:
                continue
            decisions.append(dec)
            # 事件流：非最后一个角色，立即结算其自身效应，令下一个角色基于变化后的世界决策
            if j < len(alive_agents) - 1 and agent.entity_id in self._states:
                sd = re_engine.compute_self_deltas(dec, state=self._states.get(agent.entity_id), env=self._env)
                if sd:
                    self_seen[agent.entity_id] = sd
                    self._states[agent.entity_id].apply_deltas(sd, round_number, ranges)

        return await self._finalize_round(round_number, decisions, client, sim_round, self_seen)

    # ── Mode A: Freeform writing ──

    async def _run_round_freeform(self, round_number: int) -> SimulationRound:
        from literarycreation.core.llm_client import DeductionLLMClient as LLMClient
        re_engine = self._rule_engine
        client = LLMClient()
        sim_round = SimulationRound(round_number=round_number)

        self._round_mandate = ""
        if self._scheduler is not None:
            mt = self._scheduler.get_mandate_text(round_number)
            st = self._scheduler.get_soft_goals_text(round_number)
            parts = []
            if mt:
                parts.append(f"[蓝图参考] {mt}")
            if st:
                parts.append(f"[剧情建议] {st}")
            self._round_mandate = "；".join(parts)

        if self._preprocessor is not None:
            self._preprocessor.clear_round_cache()

        alive_agents = self.agents

        async def _decide(agent):
            if self._cancel is not None and self._cancel.is_set():
                raise _PhaseCancelledError()
            ov = self._pop_override(agent)
            if ov is not None:
                ov["actor_id"] = agent.entity_id
                ov["driver"] = "forced"
                return ov
            st = self._states.get(agent.entity_id)
            if st is None:
                return None
            others = self._build_others_ctx(agent.entity_id, alive_agents)
            dyn, static, kuzu_self = await self._retrieve_memory(agent, round_number)
            rel_ctx = self._build_relation_context(agent.entity_id)
            recent = self._build_recent_context_for_agent(agent.entity_id)

            async with self._sem:
                dec = await self.reasoner.reason(
                    agent=agent, round_number=round_number, state=st, mode="freeform",
                    other_context=others, relationship_context=rel_ctx,
                    static_knowledge=static, dynamic_memory=dyn,
                    recent_events=recent, env_context=self._env_context(),
                    self_memory=kuzu_self,
                    narrative_memory=self._narrative_memory_text(agent.entity_id),
                )
            if dec:
                dec["actor_id"] = agent.entity_id
                dec["driver"] = "freeform"
            else:
                dec = {"actor_id": agent.entity_id, "action_type": "observe", "intensity": 0.3,
                       "target": "", "rationale": "默认观察", "driver": "freeform"}
            dec.setdefault("action_type", "observe")
            dec.setdefault("intensity", 0.3)
            dec.setdefault("target", "")
            return dec

        ranges = re_engine.ranges()
        decisions = []
        self_seen: dict[str, dict[str, float]] = {}
        for j, agent in enumerate(alive_agents):
            dec = await _decide(agent)
            if dec is None:
                continue
            decisions.append(dec)
            # 事件流：非最后一个角色，立即结算其自身效应，令下一个角色基于变化后的世界决策
            if j < len(alive_agents) - 1 and agent.entity_id in self._states:
                sd = re_engine.compute_self_deltas(dec, state=self._states.get(agent.entity_id), env=self._env)
                if sd:
                    self_seen[agent.entity_id] = sd
                    self._states[agent.entity_id].apply_deltas(sd, round_number, ranges)

        return await self._finalize_round(round_number, decisions, client, sim_round, self_seen)

    # ── Shared: apply rule engine effects ──

    async def _finalize_round(
        self, round_number: int, decisions: list[dict], client: Any,
        sim_round: SimulationRound, self_deltas: dict[str, dict[str, float]] | None = None,
    ) -> SimulationRound:
        re_engine = self._rule_engine
        ranges = re_engine.ranges()
        self_deltas = self_deltas or {}

        auto_deltas = re_engine.evaluate_auto_effects(self._states)
        for eid, d in auto_deltas.items():
            if eid in self._states:
                self._states[eid].apply_deltas(d, round_number, ranges)

        for eid, st in self._states.items():
            delay_d = st.resolve_delays(round_number)
            if delay_d:
                st.apply_deltas(delay_d, round_number, ranges)

        # 自身效应已在事件流循环即时结算；此处仅结算跨角色"目标"效应
        target_deltas = re_engine.resolve_targets(
            self._states, decisions, self._name_to_id, self._env)

        if len(self._states) >= 20:
            _bulk_apply_deltas(self._states, target_deltas, ranges, re_engine.metrics())
        else:
            for eid, d in target_deltas.items():
                if eid in self._states:
                    self._states[eid].apply_deltas(d, round_number, ranges)

        # 合并 自身+目标 增量，供 outcome 反馈与因果 effect 记录
        deltas: dict[str, dict[str, float]] = {a: dict(d) for a, d in self_deltas.items()}
        for eid, d in target_deltas.items():
            bucket = deltas.setdefault(eid, {})
            for k, v in d.items():
                bucket[k] = bucket.get(k, 0.0) + v

        self._last_round_outcomes: dict[str, list[dict]] = {}
        for dec in decisions:
            actor = dec.get("actor_id")
            if actor not in self._states:
                continue
            my_deltas = deltas.get(actor, {})
            if my_deltas:
                summary = ", ".join(f"{k}{v:+.1f}" for k, v in my_deltas.items())
                target = dec.get("target", "")
                action = dec.get("action_type", "?")
                target_name = target if target else "自身"
                self._last_round_outcomes.setdefault(actor, []).append(
                    f"你的 {action} 对 {target_name} 造成: {summary}" if target else
                    f"你的 {action} 自身效应: {summary}"
                )

        # 更新社会温度计（根据本回合发生的所有公开行动）
        for dec in decisions:
            at = dec.get("action_type", "")
            if at in self._SOCIAL_HEAT_MAP:
                for k, v in self._SOCIAL_HEAT_MAP[at].items():
                    self._social_thermometer[k] = max(0.0, min(100.0,
                        self._social_thermometer.get(k, 50.0)
                        + v * float(dec.get("intensity", 0.5))))
            # 无匹配动作：自然衰减
            for k in ("rumor_intensity", "faction_polarity"):
                self._social_thermometer[k] = max(0.0, self._social_thermometer.get(k, 50.0) - 1.0)

        for dec in decisions:
            for action, sub_intensity, _target in re_engine._iter_subactions(dec):
                delay_cfg = re_engine.pack.get("delay_effects", {}).get(action)
                if delay_cfg and sub_intensity > 0:
                    dr = int(delay_cfg.get("delay", 1))
                    eff = {k: v * sub_intensity for k, v in delay_cfg.get("effects", {}).items()}
                    self._states[dec.get("actor_id", "")].schedule_delays(round_number, dr, eff)

        for dec in decisions:
            actor = dec.get("actor_id", "")
            agent = next((a for a in self.agents if a.entity_id == actor), None)
            name = agent.name if agent else actor[:8]
            content = dec.get("rationale", dec.get("content", ""))[:200]
            scene = dec.get("scene_moment", "")
            inner = dec.get("inner_monologue", "")
            if inner:
                content += f"\n[内心] {inner}"
            if scene:
                content += f"\n[场景] {scene}"
            sim_round.actions.append(SimulationAction(
                agent_id=actor,
                action_type=dec.get("action_type", "observe"),
                content=content,
                driver=dec.get("driver", "freeform"),
            ))

        if self._persist_events:
            for action in sim_round.actions:
                event_id = f"evt-{uuid.uuid4().hex[:8]}"
                # 构建因果摘要：记录该行动对各指标的影响
                actor = action.agent_id
                eff = deltas.get(actor, {})
                effect_txt = "，".join(f"{k}{v:+.0f}" for k, v in eff.items()) if eff else ""
                self.graph.add_event(event_id, action.content[:200], action.action_type,
                                     action.timestamp, action.agent_id, effect=effect_txt)
                self.graph.add_acted(action.agent_id, event_id, action.action_type, action.timestamp)
                # 写入内存事件历史（供反思时快速检索）
                self._event_history.append({
                    "round": round_number, "agent": action.agent_id,
                    "content": action.content, "action": action.action_type,
                    "effect": effect_txt,
                })
                if len(self._event_history) > 200:
                    self._event_history = self._event_history[-100:]
                if self._preprocessor is not None:
                    try:
                        self._preprocessor.add_event_memory(
                            content=action.content, agent_id=action.agent_id,
                            round_number=round_number, event_type=action.action_type)
                    except Exception as e:
                        logger.warning("[Simulator] Event memory write failed: %s", e)

        # 更新角色叙事记忆（亲身经历，供后续决策连贯参考）
        for action in sim_round.actions:
            # 优先用场景片段作为记忆摘要，回退到动作描述
            scene_text = ""
            if action.content:
                if "[场景]" in action.content:
                    scene_text = action.content.split("[场景]", 1)[-1].strip()[:80]
                elif "[内心]" in action.content:
                    scene_text = action.content.split("[内心]", 1)[-1].strip()[:60]
            summary = scene_text if scene_text else f"第{round_number}轮：{action.action_type}"
            self.narrative_memory.add(action.agent_id, summary)

        # 角色人格反思（事件驱动：经历累积/关系剧变/状态突变时触发）
        for agent in self.agents:
            await self._reflect_character(agent, round_number, client, deltas)

        # 写入 narrator 文本供 prose renderer
        narration = ""
        if self._enable_narrate and hasattr(self, '_chat_fn'):
            narration = await self._narrate_round(client, round_number, decisions, deltas)

        # 提供角色状态快照供 prose 渲染器使用
        snapshots = {}
        for eid, st in self._states.items():
            snapshots[eid] = {"name": st.name, "metrics": dict(st.metrics)}
        sim_round.state_delta = {"narration": narration, "states": snapshots, "snapshot": self._build_state_snapshot(round_number)}

        return sim_round

    # ── 角色人格动态反思 ──

    async def _reflect_character(self, agent: DeductionAgentProfile,
                                  round_number: int, client: Any,
                                  deltas: dict[str, dict[str, float]]) -> None:
        """事件驱动人格反思：角色从经历中成长，人格不再是静态设定。"""
        # 触发条件：事件累积 > 8 或 重大指标变化 > 25 点累计 或 空闲 > 6 轮
        eid = agent.entity_id
        self._reflect_counters[eid] = self._reflect_counters.get(eid, 0) + 1
        last_r = self._last_reflect_round.get(eid, 0)

        should_reflect = False
        reason = ""
        if self._reflect_counters[eid] >= 8:
            should_reflect = True
            reason = "经历积累"
        if not should_reflect and (round_number - last_r) > 6:
            should_reflect = True
            reason = "长期缺乏反思"
        if not should_reflect:
            # 检测指标剧变
            cur = self._states.get(eid)
            if cur is not None:
                prev = self._last_reflect_snapshot.get(eid, {})
                total_change = 0.0
                for mk, mv in cur.metrics.items():
                    total_change += abs(mv - prev.get(mk, mv))
                if total_change > 25.0:
                    should_reflect = True
                    reason = f"指标剧变 (+{total_change:.0f})"
        if not should_reflect:
            return

        from literarycreation.core.llm_client import Message
        from ._utils import extract_text as _extract

        # 取角色的近期事件
        my_events = [e for e in self._event_history[-20:]
                     if e.get("agent") == eid or e.get("agent_name") == agent.name] if hasattr(self, "_event_history") else []
        if self.graph is not None:
            try:
                my_events = self.graph.get_recent_events_for_agent(eid, last_n=8)
            except Exception:
                pass
        events_text = "\n".join(
            f"- [R{e.get('round','?')}] {e.get('description', e.get('content',''))[:80]}"
            for e in my_events[-8:]
        ) or "（无近期事件）"

        prompt = (
            f"你是「{agent.name}」的潜意识。回顾你的经历，判断你的性格是否需要微调。\n\n"
            f"## 你的核心人格（不可动摇）\n{agent.persona or '（无）'}\n\n"
            f"## 你已有的行为准则\n{agent.system_prompt_extra or '（无，完全依据核心人格）'}\n\n"
            f"## 近期经历\n{events_text}\n\n"
            f"## 触发原因\n{reason}\n\n"
            f"## 任务\n"
            f"根据经历，判断是否需要在核心人格之上添加一条新的行为准则（或修正旧准则）。\n"
            f"核心人格不可动摇，新准则只能是对核心人格的策略性微调——角色经历了什么，"
            f"所以变得怎样。\n"
            f"- 输出格式：一行简短中文准则（20字以内），直接陈述。\n"
            f"- 如果当前人格已足够应对，输出\"无需调整\"。\n"
            f"- 准则上限3条，超限时替换最旧的一条。\n"
            f"- 示例：\"遭受背叛后更谨慎选择盟友\" \"危急时刻敢于孤注一掷\"\n"
            f"\n只输出准则本身或\"无需调整\"，不要解释。"
        )
        try:
            resp = await client.chat(
                [Message(role="user", content=prompt)],
                system="你是潜意识分析师，输出简短行为准则或'无需调整'。",
                temperature=0.3,
                max_tokens=60,
            )
            text = _extract(resp).strip()
            if not text or "无需调整" in text or len(text) < 2:
                return
            old_extra = agent.system_prompt_extra
            if old_extra and text not in old_extra:
                parts = [p.strip() for p in old_extra.split("；") if p.strip()]
                if len(parts) >= 3:
                    parts = parts[1:]
                parts.append(text)
                agent.system_prompt_extra = "；".join(parts)
            elif not old_extra:
                agent.system_prompt_extra = text
            else:
                return
            self._log("simulation",
                       f"[人格演化] {agent.name} 新增准则: {text} (R{round_number}, {reason})")
        except Exception as e:
            logger.debug("[Simulator] 角色反思失败: %s", e)

        # 重置计数器 + 保存快照
        self._reflect_counters[eid] = 0
        self._last_reflect_round[eid] = round_number
        cur = self._states.get(eid)
        if cur is not None:
            self._last_reflect_snapshot[eid] = dict(cur.metrics)

    # ── Helpers ──

    def _build_state_snapshot(self, round_number: int) -> dict:
        """态势简报数据，供前端 dashboard 面板。"""
        re = self._rule_engine
        metrics_list = re.metrics() if re else []
        entities: list[dict] = []
        for st in self._states.values():
            entities.append({
                "name": getattr(st, "name", "?"),
                "metrics": {k: round(float(v), 1) for k, v in st.metrics.items()},
            })
        averages: dict[str, float] = {}
        if metrics_list and entities:
            for m in metrics_list:
                vals = [e["metrics"].get(m, 0) for e in entities]
                averages[m] = round(sum(vals) / len(vals), 1)
        # 结构化近期事件
        recent = self._build_recent_context_global()
        recent_structured: list[dict] = []
        if self.graph is not None:
            try:
                for e in self.graph.get_recent_global_events(last_n=8):
                    recent_structured.append(e)
            except Exception:
                pass
        return {
            "round": round_number, "entity_count": len(self._states),
            "entities": entities, "averages": averages,
            "recent": recent, "recent_structured": recent_structured,
        }

    async def _retrieve_memory(self, agent: Any, round_number: int) -> tuple[str, str, str]:
        """Retrieve memory: LanceDB static + dynamic + Kuzu self-events."""
        dyn, static, kuzu_self = "", "", ""
        if self._preprocessor is not None:
            query = getattr(agent, "persona", "") + " " + getattr(agent, "name", "")
            # Kuzu 关系邻居增强 LanceDB 检索相关性
            if self.graph is not None:
                try:
                    nb = self.graph.get_entity_neighbors(agent.entity_id)
                    names = [n["name"] for n in nb.get("neighbors", []) if n.get("name")][:3]
                    if names:
                        query += " " + " ".join(names)
                except Exception:
                    pass
            try:
                static = self._preprocessor.retrieve_for_entity(query, agent.entity_id, top_k=2)
            except Exception:
                pass
            try:
                dyn = self._preprocessor.retrieve_dynamic_events(query, top_k=3)
            except Exception:
                pass
        # Kuzu self-events: precise timeline of own actions
        if self.graph is not None:
            try:
                events = self.graph.get_recent_events_for_agent(
                    getattr(agent, "entity_id", agent.name), last_n=5)
                if events:
                    parts = []
                    for e in events:
                        base = f"[R{e['round']}] {e['action']}: {e['description'][:100]}"
                        if e.get("effect"):
                            base += f" → 造成影响：{e['effect']}"
                        parts.append(base)
                    kuzu_self = "；\n".join(parts)
            except Exception:
                pass
        return (dyn or "（无近期动态事件）"), (static or "（无原著参考）"), kuzu_self

    def _build_recent_context_for_agent(self, agent_id: str) -> str:
        """近期事件 — 仅角色自身事件 + 公开事件，过滤他人隐私。"""
        if self.graph is None:
            return "（无近期事件）"
        try:
            own = self.graph.get_recent_events_for_agent(agent_id, last_n=3)
            global_ev = self.graph.get_recent_global_events(last_n=3)
        except Exception:
            return "（无近期事件）"
        parts = []
        if own:
            parts.append("【你最近做的事】")
            for e in own:
                base = f"[R{e['round']}] {e['action']}: {e['description'][:100]}"
                if e.get("effect"):
                    base += f" → {e['effect']}"
                parts.append(base)
        public = [e for e in global_ev if any(kw in (e.get("content","") or "") for kw in ("死","杀","被捕","公开","通缉","诏狱","朝堂"))]
        if public:
            parts.append("【公开消息】")
            for e in public:
                parts.append(f"[R{e.get('round',0)}] {e.get('agent_name','?')}: {e.get('content','')[:100]}")
        return "\n".join(parts) if parts else "（无近期事件）"

    def _build_recent_context_global(self) -> str:
        """全局近期事件（供 dashboard snapshot 等全局视图使用）。"""
        if self.graph is None:
            return "（无近期事件）"
        try:
            events = self.graph.get_recent_global_events(last_n=8)
        except Exception:
            return "（无近期事件）"
        return "\n".join(
            f"- [{e['round']}] {e['agent_name']}: {e['content'][:80]}"
            for e in events
        ) or "（无近期事件）"

    def _build_others_ctx(self, self_id: str, alive_agents: list) -> str:
        """角色感知到的其他角色状态 — 基于 Kuzu RELATES 过滤。

        有关系边的角色展示详细信息，无关系的仅展示模糊感知。
        """
        from .strategic_reasoner import _metrics_to_narrative
        known_ids: set[str] = set()
        if self.graph is not None:
            try:
                nb = self.graph.get_entity_neighbors(self_id)
                known_ids = {n.get("id", "") for n in nb.get("neighbors", []) if n.get("id")}
            except Exception:
                pass
        lines = []
        for a in alive_agents:
            if a.entity_id == self_id:
                continue
            st = self._states.get(a.entity_id)
            if st is None:
                continue
            if a.entity_id in known_ids:
                lines.append(_metrics_to_narrative(st))
            else:
                name = st.name if hasattr(st, "name") else a.entity_id[:8]
                lines.append(f"{name}：你对此人了解有限，只能从公开行动中推测其状态。")
        return "\n".join(lines) or "（无其他参与方）"

    def _build_relation_context(self, entity_id: str) -> str:
        """从 Kuzu 图谱查询实体的关系邻居，构建自然语言关系摘要。"""
        if self.graph is None:
            return ""
        try:
            nb = self.graph.get_entity_neighbors(entity_id)
        except Exception:
            return ""
        neighbors = nb.get("neighbors", [])
        if not neighbors:
            return ""
        allies: list[str] = []
        foes: list[str] = []
        others: list[str] = []
        for n in neighbors:
            rel = n.get("relation", "") or ""
            name = n.get("name", "")
            if not name:
                continue
            if any(k in rel for k in ("盟", "友", "支持", "合作", "部下", "下属", "效忠", "追随", "ally", "support", "friend", "loyal")):
                allies.append(name)
            elif any(k in rel for k in ("敌", "对立", "对抗", "对手", "竞争", "冲突", "背叛", "仇", "攻击", "威胁", "rival", "enemy", "hostil", "oppos", "betray")):
                foes.append(name)
            else:
                others.append(name)
        parts = []
        if allies:
            parts.append(f"盟友：{'、'.join(allies[:5])}")
        if foes:
            parts.append(f"对手：{'、'.join(foes[:5])}")
        if others:
            parts.append(f"关联：{'、'.join(others[:5])}")
        return " · ".join(parts) if parts else ""

    def _env_context(self) -> str:
        parts: list[str] = []
        st = self._social_thermometer
        if st.get("faction_polarity", 40) > 60:
            parts.append("阵营之间的裂痕已肉眼可见，空气中弥漫着猜疑。")
        if st.get("rumor_intensity", 30) > 50:
            parts.append("流言如野火蔓延，每个人都在私下谈论同一件事。")
        if st.get("intimate_pressure", 20) > 40:
            parts.append("私人关系已成为公开的筹码，每一次对视都有代价。")
        if st.get("external_threat", 10) > 30:
            parts.append("外部威胁正在迫近，所有人心中都有一根弦在绷紧。")
        if self._env:
            weather = self._env.get("weather", "").strip()
            terrain = self._env.get("terrain", "").strip()
            if weather:
                parts.append(f"天气: {weather}")
            if terrain:
                parts.append(f"地形: {terrain}")
        return "； ".join(parts) if parts else ""

    async def _narrate_round(self, client: Any, round_number: int,
                             decisions: list[dict], deltas: dict) -> str:
        from literarycreation.core.llm_client import Message
        lines = []
        for dec in decisions:
            actor = dec.get("actor_id", "")
            agent = next((a for a in self.agents if a.entity_id == actor), None)
            nm = agent.name if agent else actor[:8]
            d = deltas.get(actor, {})
            chg = ", ".join(f"{k}{v:+.1f}" for k, v in d.items()) or "无显著变化"
            act_txt = f"采取 {dec.get('action_type', 'observe')}(强度{dec.get('intensity', 0.5):.1f}) 目标:{dec.get('target') or '—'}"
            lines.append(f"{nm} {act_txt}，数值变化: {chg}")
        prompt = (
            f"将第 {round_number} 轮文学创作结果改写为一段生动简洁的叙事（100 字以内）。\n\n"
            "## 本轮各方行动与数值变化\n" + "\n".join(lines) + "\n\n只输出叙事段落，不要解释或列表。"
        )
        resp = await client.chat([Message(role="user", content=prompt)],
                                 system="你是文学创作解说员，把数值变化翻译成简洁叙事。", temperature=0.5)
        return extract_text(resp).strip()[:300]


def _bulk_apply_deltas(
    states: dict[str, Any], deltas: dict[str, dict[str, float]],
    ranges: dict[str, Any], metric_names: list[str],
) -> None:
    from literarycreation.engine._jit_utils import batch_apply_deltas
    entity_ids = list(states.keys())
    if not entity_ids:
        return
    N = len(entity_ids)
    M = len(metric_names)
    metrics_arr = np.zeros((N, M), dtype=np.float64)
    deltas_arr = np.zeros((N, M), dtype=np.float64)
    lo_arr = np.full(M, -1e12, dtype=np.float64)
    hi_arr = np.full(M, 1e12, dtype=np.float64)
    for i, eid in enumerate(entity_ids):
        st = states[eid]
        for m, name in enumerate(metric_names):
            metrics_arr[i, m] = float(st.metrics.get(name, 0.0))
            d = deltas.get(eid, {}).get(name, 0.0)
            deltas_arr[i, m] = float(d) if d is not None else 0.0
    for m, name in enumerate(metric_names):
        rng = ranges.get(name, [0.0, 100.0])
        if rng and len(rng) >= 2:
            lo_arr[m] = float(rng[0])
            hi_arr[m] = float(rng[1])
    batch_apply_deltas(metrics_arr, deltas_arr, lo_arr, hi_arr)
    for i, eid in enumerate(entity_ids):
        st = states[eid]
        for m, name in enumerate(metric_names):
            st.metrics[name] = float(metrics_arr[i, m])


def _parse_action_json(raw: str) -> dict[str, Any]:
    match = re.search(r"\{[\s\S]*\}", raw)
    if not match:
        return {}
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return {}


def extract_text(resp: Any) -> str:
    if isinstance(resp, str):
        return resp
    if hasattr(resp, "text"):
        return resp.text
    if hasattr(resp, "content"):
        return resp.content
    if hasattr(resp, "choices") and resp.choices:
        return resp.choices[0].message.content or ""
    return str(resp)
