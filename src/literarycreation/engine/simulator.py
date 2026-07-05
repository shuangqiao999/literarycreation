"""Phase 4: Simulation — dual-path LanceDB memory recall, dual-mode decisions.

Modes:
  - blueline: Blueprint execution — key events are enforced, LLM dramatizes
  - freeform: Free writing — agents decide freely, no forced events
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import uuid
from collections.abc import Callable
from string import Template
from typing import Any, Literal

import numpy as np

from literarycreation.storage.graph_store import DeductionGraphStore

from ._utils import extract_text
from .event_scheduler import EventScheduler
from .models import DeductionAgentProfile, SimulationAction, SimulationRound
from .orchestrator import _PhaseCancelledError
from .preprocessor import DeductionPreprocessor

logger = logging.getLogger(__name__)


_ACTION_PROMPT = """你是一个推演模拟中的智能体。根据你的角色设定和当前世界状态，决定你的下一步行动。

## 你的固定人格（基于原文）
$persona

## 你的背景
$background

## 你的目标
$goals

## 当前轮次
第 $round_number 轮

## 近期模拟动态事件（重要！以下是其他角色刚刚做过的事）
$dynamic_memory

## 你的原著背景参考（仅供参考）
$static_knowledge

## 近期世界缓存
$recent_events

## 输出 JSON — 选择一种行动
```json
{
  "action": "post|reply|interact|observe",
  "target": "目标实体名或留空",
  "content": "行动内容 (30-100字)"
}
```

只返回 JSON，不要解释。"""

_REL_ALLY_KW = ("盟", "同盟", "结盟", "联盟", "支持", "合作", "友", "部下", "下属",
                "效忠", "追随", "ally", "allied", "support", "friend", "cooperat",
                "subordinate", "loyal")
_REL_FOE_KW = ("敌", "对立", "对抗", "对手", "竞争", "冲突", "背叛", "仇", "攻击",
               "威胁", "rival", "enemy", "hostil", "oppos", "compet", "conflict",
               "betray", "threat")


class SimulationEngine:
    """Multi-agent simulation engine with LanceDB dual-path memory and dual-mode decision."""

    def __init__(
        self,
        agents: list[DeductionAgentProfile],
        graph: DeductionGraphStore,
        total_rounds: int = 10,
        log_fn: Callable[[str, str], None] | None = None,
        preprocessor: DeductionPreprocessor | None = None,
        chat_fn: Any = None,
        pre_goals: list[str] | None = None,
        *,
        seed: int | None = None,
        temperature: float = 0.7,
        persist_events: bool = True,
        max_concurrent: int | None = None,
        rule_engine: Any = None,
        states: dict[str, Any] | None = None,
        enable_narrate: bool = True,
        env: dict[str, str] | None = None,
        enable_multi_action: bool = False,
        max_actions: int = 3,
        cancel_event: Any = None,
        algorithm_modules: list | None = None,
        outline: dict[str, Any] | None = None,
        fsm_override_store: dict | None = None,
        mode: Literal["freeform", "blueline"] = "freeform",
        event_scheduler: EventScheduler | None = None,
    ) -> None:
        self.agents = agents
        self.graph = graph
        self._name_to_id: dict[str, str] = {a.name: a.entity_id for a in agents}
        self.total_rounds = total_rounds
        self._log = log_fn or (lambda p, m: None)
        self._event_history: list[dict[str, Any]] = []
        self._preprocessor = preprocessor
        self._chat_fn = chat_fn
        self._immutable_goals: list[str] = list(pre_goals or [])
        self._cancel = cancel_event
        self._persist_events = persist_events
        self._temperature = temperature
        self._rng = random.Random(seed)
        self._rule_engine = rule_engine
        self._states: dict[str, Any] = states or {}
        self._quantified = rule_engine is not None
        self._enable_narrate = enable_narrate
        self._env = env
        self._enable_multi_action = enable_multi_action
        self._max_actions = max(1, int(max_actions))
        self._outline: dict[str, Any] | None = outline
        self._fsm_override_store: dict = fsm_override_store or {}
        self._mode = mode
        self._scheduler = event_scheduler
        self._round_mandate: str = ""
        self._last_outline_nudges: list[dict[str, Any]] = []
        from literarycreation.core.config import config
        self._max_concurrent = (
            max_concurrent if max_concurrent is not None
            else config.deduction_max_concurrent
        )

        from .strategic_reasoner import StrategicReasoner
        self.reasoner = StrategicReasoner(
            chat_fn=chat_fn,
            preprocessor=preprocessor,
            candidate_count=config.deduction_candidate_count,
            immutable_goals=self._immutable_goals,
        )
        self._rel_context: dict[str, dict] = {}
        self._build_relationship_context()

    @staticmethod
    def _classify_relation(relation: str) -> str:
        r = (relation or "").lower()
        if any(k in r for k in _REL_FOE_KW):
            return "foe"
        if any(k in r for k in _REL_ALLY_KW):
            return "ally"
        return "neutral"

    def _build_relationship_context(self) -> None:
        if self.graph is None or not self.agents:
            return
        for a in self.agents:
            allies: list[str] = []
            foes: list[str] = []
            try:
                data = self.graph.get_entity_neighbors(a.entity_id, max_depth=1)
            except Exception as e:
                logger.debug("[Simulator] 关系预取失败 %s: %s", a.name, e)
                continue
            for nb in data.get("neighbors", []):
                nm = nb.get("name", "")
                if not nm or nm == a.name:
                    continue
                kind = self._classify_relation(nb.get("relation", ""))
                if kind == "ally" and nm not in allies:
                    allies.append(nm)
                elif kind == "foe" and nm not in foes:
                    foes.append(nm)
            parts = []
            if allies:
                parts.append("盟友: " + "、".join(allies[:6]))
            if foes:
                parts.append("对手: " + "、".join(foes[:6]))
            self._rel_context[a.entity_id] = {"allies": allies, "opponents": foes, "summary": "；".join(parts)}
            if allies or foes:
                self.reasoner.seed_trust(a.entity_id, allies, foes)

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
                        + (f" → {ov.get('target')}" if ov.get("target") else ""),
        }

    async def run_round(self, round_number: int) -> SimulationRound:
        if not self._quantified:
            return await self._run_nonquantified_round(round_number)
        if self._mode == "blueline" and self._scheduler is not None:
            return await self._run_round_blueline(round_number)
        return await self._run_round_freeform(round_number)

    # ── Mode B: Blueprint execution ──

    async def _run_round_blueline(self, round_number: int) -> SimulationRound:
        from literarycreation.core.llm_client import DeductionLLMClient as LLMClient
        re_engine = self._rule_engine

        ordered = list(self.agents)
        self._rng.shuffle(ordered)
        client = LLMClient()
        sim_round = SimulationRound(round_number=round_number)

        # 1. Get scheduled events for this round
        scheduler = self._scheduler
        if scheduler is None:
            return sim_round
        events = scheduler.get_events_for_round(round_number)
        mandate_text = scheduler.get_mandate_text(round_number)
        correction_level = scheduler.check_correction(round_number, self._states)

        if mandate_text:
            self._log("simulation", f"第{round_number}轮蓝图事件: {mandate_text[:80]}...")

        # 2. Process each agent
        alive_agents = [a for a in self.agents if not hasattr(a, 'dead') or not a.dead]
        decisions: list[dict[str, Any]] = []

        for agent in alive_agents:
            if self._cancel is not None and self._cancel.is_set():
                raise _PhaseCancelledError()

            ov = self._pop_override(agent)
            if ov is not None:
                ov["actor_id"] = agent.entity_id
                ov["driver"] = "forced"
                decisions.append(ov)
                continue

            # Build context
            st = self._states.get(agent.entity_id)
            if st is None:
                continue
            rel_ctx = self._rel_context.get(agent.entity_id, {}).get("summary", "")
            others = self._build_others_ctx(agent.entity_id, alive_agents)
            dyn, static = await self._retrieve_memory(agent, round_number)
            recent = "\n".join(
                f"- [{e.get('round','?')}] {e.get('agent_name','?')}: {e.get('content','')[:80]}"
                for e in self._event_history[-5:]
            ) or "（无近期事件）"

            dec = await self.reasoner.reason_narrative(
                agent=agent, round_number=round_number, state=st,
                event_mandate=mandate_text,
                correction_level=correction_level,
                other_context=others,
                relationship_context=rel_ctx,
                static_knowledge=static,
                dynamic_memory=dyn,
                recent_events=recent,
                env_context=self._env_context(),
                cached_action_catalog=self._cached_action_catalog(),
            )
            if dec:
                dec["actor_id"] = agent.entity_id
                dec["driver"] = "blueline"
                dec.setdefault("action_type", "observe")
                dec.setdefault("intensity", 0.3)
                dec.setdefault("target", "")
            else:
                dec = {"actor_id": agent.entity_id, "action_type": "observe", "intensity": 0.3,
                       "target": "", "rationale": "[蓝图模式] 默认观察", "driver": "blueline"}
            decisions.append(dec)

        # 3. Apply effects via rule engine
        return await self._apply_decisions(round_number, decisions, client, sim_round, ordered)

    # ── Mode A: Freeform writing ──

    async def _run_round_freeform(self, round_number: int) -> SimulationRound:
        from literarycreation.core.llm_client import DeductionLLMClient as LLMClient
        re_engine = self._rule_engine

        ordered = list(self.agents)
        self._rng.shuffle(ordered)
        client = LLMClient()
        sim_round = SimulationRound(round_number=round_number)

        # Build mandate if outline exists (advisory only in freeform mode)
        self._round_mandate = ""
        if self._scheduler is not None:
            mandate_text = self._scheduler.get_mandate_text(round_number)
            soft_text = self._scheduler.get_soft_goals_text(round_number)
            parts = []
            if mandate_text:
                parts.append(f"[蓝图参考] {mandate_text}")
            if soft_text:
                parts.append(f"[剧情建议] {soft_text}")
            self._round_mandate = "；".join(parts)

        alive_agents = [a for a in self.agents if not hasattr(a, 'dead') or not a.dead]
        decisions: list[dict[str, Any]] = []

        for agent in alive_agents:
            if self._cancel is not None and self._cancel.is_set():
                raise _PhaseCancelledError()

            ov = self._pop_override(agent)
            if ov is not None:
                ov["actor_id"] = agent.entity_id
                ov["driver"] = "forced"
                decisions.append(ov)
                continue

            st = self._states.get(agent.entity_id)
            if st is None:
                continue
            rel_ctx = self._rel_context.get(agent.entity_id, {}).get("summary", "")
            others = self._build_others_ctx(agent.entity_id, alive_agents)
            dyn, static = await self._retrieve_memory(agent, round_number)
            recent = "\n".join(
                f"- [{e.get('round','?')}] {e.get('agent_name','?')}: {e.get('content','')[:80]}"
                for e in self._event_history[-5:]
            ) or "（无近期事件）"

            dec = await self.reasoner.reason_quantified(
                agent=agent, round_number=round_number, state=st,
                other_context=others,
                relationship_context=rel_ctx,
                static_knowledge=static,
                dynamic_memory=dyn,
                recent_events=recent,
                spatial_context="",
                env_context=self._env_context(),
                cached_action_catalog=self._cached_action_catalog(),
                enable_multi_action=self._enable_multi_action,
            )
            if dec:
                dec["actor_id"] = agent.entity_id
                dec["driver"] = "freeform"
                dec.setdefault("action_type", "observe")
                dec.setdefault("intensity", 0.3)
                dec.setdefault("target", "")
            else:
                dec = {"actor_id": agent.entity_id, "action_type": "observe", "intensity": 0.3,
                       "target": "", "rationale": "默认观察", "driver": "freeform"}
            decisions.append(dec)

        return await self._apply_decisions(round_number, decisions, client, sim_round, ordered)

    # ── Shared: apply rule engine effects ──

    async def _apply_decisions(
        self, round_number: int, decisions: list[dict], client: Any,
        sim_round: SimulationRound, ordered: list,
    ) -> SimulationRound:
        re_engine = self._rule_engine

        # Auto effects
        ranges = re_engine.ranges()
        auto_deltas = re_engine.evaluate_auto_effects(self._states)
        for eid, d in auto_deltas.items():
            if eid in self._states:
                self._states[eid].apply_deltas(d, round_number, ranges)

        # Delay effects
        for eid, st in self._states.items():
            delay_d = st.resolve_delays(round_number)
            if delay_d:
                st.apply_deltas(delay_d, round_number, ranges)

        # Resolve interactions
        deltas, interactions = re_engine.resolve_round(
            self._states, decisions, self._name_to_id, self._env, collect_interactions=True)

        # Bulk JIT delta application
        if len(self._states) >= 20:
            _bulk_apply_deltas(self._states, deltas, ranges, re_engine.metrics())
        else:
            for eid, d in deltas.items():
                if eid in self._states:
                    self._states[eid].apply_deltas(d, round_number, ranges)

        # Build outcomes
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

        # Schedule delay effects
        for dec in decisions:
            for action, sub_intensity, _target in re_engine._iter_subactions(dec):
                delay_cfg = re_engine.pack.get("delay_effects", {}).get(action)
                if delay_cfg and sub_intensity > 0:
                    dr = int(delay_cfg.get("delay", 1))
                    eff = {k: v * sub_intensity for k, v in delay_cfg.get("effects", {}).items()}
                    self._states[dec.get("actor_id", "")].schedule_delays(round_number, dr, eff)

        # Build SimulationRound actions
        for dec in decisions:
            actor = dec.get("actor_id", "")
            agent = next((a for a in self.agents if a.entity_id == actor), None)
            name = agent.name if agent else actor[:8]
            content = dec.get("rationale", dec.get("content", ""))[:200]
            sim_round.actions.append(SimulationAction(
                agent_id=actor,
                action_type=dec.get("action_type", "observe"),
                content=content,
                driver=dec.get("driver", "freeform"),
            ))
            self._event_history.append({
                "agent": actor, "agent_name": name,
                "action": dec.get("action_type", "?"), "content": content,
                "round": round_number,
            })

        if len(self._event_history) > 200:
            self._event_history = self._event_history[-200:]

        # Persist
        if self._persist_events:
            for action in sim_round.actions:
                event_id = f"evt-{uuid.uuid4().hex[:8]}"
                self.graph.add_event(event_id, action.content[:200], action.action_type,
                                     action.timestamp, action.agent_id)
                self.graph.add_acted(action.agent_id, event_id, action.action_type, action.timestamp)
                if self._preprocessor is not None:
                    try:
                        self._preprocessor.add_event_memory(
                            content=action.content, agent_id=action.agent_id,
                            round_number=round_number, event_type=action.action_type)
                    except Exception as e:
                        logger.warning("[Simulator] Event memory write failed for %s: %s",
                                     action.agent_id, e)

        # Narration
        if self._enable_narrate and hasattr(self, '_chat_fn'):
            narration = await self._narrate_round(client, round_number, decisions, deltas)
            sim_round.state_delta = {"narration": narration}

        return sim_round

    # ── Non-quantified round ──

    async def _run_nonquantified_round(self, round_number: int) -> SimulationRound:
        from literarycreation.core.llm_client import DeductionLLMClient as LLMClient
        sim_round = SimulationRound(round_number=round_number)
        client = LLMClient()
        ordered = list(self.agents)
        self._rng.shuffle(ordered)
        sem = asyncio.Semaphore(self._max_concurrent)

        async def process_agent(agent: DeductionAgentProfile) -> SimulationAction | None:
            async with sem:
                return await self._agent_decide_nonquant(client, agent, round_number)

        for agent in ordered:
            if self._cancel is not None and self._cancel.is_set():
                raise _PhaseCancelledError()
            action = await process_agent(agent)
            if action is not None:
                sim_round.actions.append(action)
                self._event_history.append({
                    "agent": action.agent_id,
                    "agent_name": getattr(next((a for a in self.agents if a.entity_id == action.agent_id), None), "name", action.agent_id[:8]),
                    "action": action.action_type, "content": action.content,
                    "round": round_number, "timestamp": action.timestamp,
                })
        if len(self._event_history) > 200:
            self._event_history = self._event_history[-200:]
        if self._persist_events:
            for action in sim_round.actions:
                event_id = f"evt-{uuid.uuid4().hex[:8]}"
                self.graph.add_event(event_id, action.content[:200], action.action_type, action.timestamp, action.agent_id)
                self.graph.add_acted(action.agent_id, event_id, action.action_type, action.timestamp)
                if self._preprocessor is not None:
                    try:
                        self._preprocessor.add_event_memory(
                            content=action.content, agent_id=action.agent_id,
                            round_number=round_number, event_type=action.action_type)
                    except Exception as e:
                        logger.warning("[Simulator] Event memory write failed: %s", e)
        return sim_round

    async def _agent_decide_nonquant(self, client: Any, agent: DeductionAgentProfile, round_number: int) -> SimulationAction | None:
        st = self._states.get(agent.entity_id) if self._states else None
        st_ctx = st.to_prompt_context() if st else ""
        dyn, static = await self._retrieve_memory(agent, round_number)
        recent = "\n".join(
            f"- [{e.get('round','?')}] {e.get('agent_name','?')}: {e.get('content','')[:80]}"
            for e in self._event_history[-5:]
        ) or "（无近期事件）"
        prompt = Template(_ACTION_PROMPT).substitute(
            persona=agent.persona or "无",
            background=agent.background or "无",
            goals="\n".join(f"- {g}" for g in agent.goals) if agent.goals else "无",
            round_number=round_number,
            dynamic_memory=dyn or "无",
            static_knowledge=static or "无",
            recent_events=recent,
        )
        if st_ctx:
            prompt += f"\n\n## 当前量化状态\n{st_ctx}"
        try:
            from literarycreation.core.llm_client import Message
            resp = await client.chat(
                [Message(role="user", content=prompt)],
                system="你是推演模拟中的角色，根据角色设定和历史事件做出合理的下一步行动。只输出 JSON。",
                temperature=0.7,
            )
            raw = extract_text(resp)
            parsed = _parse_action_json(raw)
            if not parsed:
                return None
            return SimulationAction(
                agent_id=agent.entity_id,
                action_type=parsed.get("action", "observe"),
                content=str(parsed.get("content", str(parsed)))[:300],
            )
        except Exception as e:
            logger.warning(f"[Simulator] Non-quantified decide failed for {agent.name}: {e}")
            return None

    # ── Helpers ──

    async def _retrieve_memory(self, agent: Any, round_number: int) -> tuple[str, str]:
        dyn, static = "", ""
        if self._preprocessor is not None:
            query = getattr(agent, "persona", "") + " " + getattr(agent, "name", "")
            try:
                static = self._preprocessor.retrieve_for_entity(query, agent.entity_id, top_k=2)
            except Exception:
                pass
            try:
                dyn = self._preprocessor.retrieve_dynamic_events(query, top_k=3)
            except Exception:
                pass
        return (dyn or "（无近期动态事件）"), (static or "（无原著参考）")

    def _build_others_ctx(self, self_id: str, alive_agents: list) -> str:
        lines = []
        for a in alive_agents:
            if a.entity_id == self_id:
                continue
            st = self._states.get(a.entity_id)
            if st is None:
                continue
            line = st.to_prompt_context()
            lines.append(line)
        return "\n".join(lines) or "（无其他参与方）"

    def _env_context(self) -> str:
        if not self._env:
            return ""
        parts = []
        weather = self._env.get("weather", "").strip()
        terrain = self._env.get("terrain", "").strip()
        if weather:
            parts.append(f"天气: {weather}")
        if terrain:
            parts.append(f"地形: {terrain}")
        return "； ".join(parts) if parts else ""

    def _cached_action_catalog(self) -> str:
        if not self._rule_engine:
            return ""
        actions = self._rule_engine.pack.get("actions", [])
        if not actions:
            return ""
        return "、".join(actions)

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
            f"将第 {round_number} 轮量化推演结果改写为一段生动简洁的叙事（100 字以内）。\n\n"
            "## 本轮各方行动与数值变化\n" + "\n".join(lines) + "\n\n只输出叙事段落，不要解释或列表。"
        )
        resp = await client.chat([Message(role="user", content=prompt)],
                                 system="你是推演解说员，把数值变化翻译成简洁叙事。", temperature=0.5)
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


def _build_state_snapshot(states: dict, thresholds: dict, event_history: list,
                          round_num: int, re_engine: Any) -> dict:
    metrics_list = re_engine.metrics() if re_engine else []
    alerts = []
    for st in states.values():
        if not hasattr(st, 'name'):
            continue
        for metric, threshold in thresholds.items():
            val = st.metrics.get(metric, 0)
            if val <= threshold * 1.2:
                severity = "critical" if val <= threshold else "warning"
                alerts.append({"entity": getattr(st, 'name', '?'), "metric": metric,
                               "value": round(val, 1), "threshold": threshold, "severity": severity})
    alerts.sort(key=lambda a: a["value"] - a["threshold"])
    groups = {}
    for st in states.values():
        domain = getattr(st, "domain", "generic")
        if domain not in groups:
            groups[domain] = {"names": [], "metrics": {m: [] for m in metrics_list}}
        groups[domain]["names"].append(getattr(st, 'name', '?'))
        for m in metrics_list:
            groups[domain]["metrics"][m].append(st.metrics.get(m, 0))
    group_stats = {}
    for domain, data in groups.items():
        group_stats[domain] = {"count": len(data["names"]),
                               "metrics": {m: round(np.mean(vals), 1) for m, vals in data["metrics"].items() if vals}}
    recent = []
    for e in event_history[-3:]:
        recent.append({"agent": e.get("agent_name", "?"), "action": e.get("action", ""),
                        "content": (e.get("content", "") or "")[:80], "round": e.get("round", round_num)})
    return {"alerts": alerts[:5], "groups": group_stats, "recent": recent,
            "round": round_num, "entity_count": len(states)}


def _parse_action_json(raw: str) -> dict[str, Any]:
    match = re.search(r"\{[\s\S]*\}", raw)
    if not match:
        return {}
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return {}
