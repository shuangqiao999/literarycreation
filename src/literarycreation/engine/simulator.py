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
    ) -> None:
        self.agents = agents
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
        self._enable_narrate = enable_narrate
        self._env = env
        self._outline: dict[str, Any] | None = outline
        self._fsm_override_store: dict = fsm_override_store or {}
        self._mode = mode
        self._scheduler = event_scheduler
        self._round_mandate: str = ""

        from .strategic_reasoner import StrategicReasoner
        self.reasoner = StrategicReasoner(
            chat_fn=chat_fn,
            preprocessor=preprocessor,
            immutable_goals=self._immutable_goals,
        )

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

        alive_agents = self.agents
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
            others = self._build_others_ctx(agent.entity_id, alive_agents)
            dyn, static = await self._retrieve_memory(agent, round_number)
            recent = "\n".join(
                f"- [{e.get('round','?')}] {e.get('agent_name','?')}: {e.get('content','')[:80]}"
                for e in self._event_history[-5:]
            ) or "（无近期事件）"

            dec = await self.reasoner.reason_narrative(
                agent=agent, round_number=round_number, state=st,
                event_mandate=mandate_text, correction_level=correction_level,
                other_context=others, static_knowledge=static, dynamic_memory=dyn,
                recent_events=recent, env_context=self._env_context(),
                cached_action_catalog=self._cached_action_catalog(),
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
            decisions.append(dec)

        return await self._apply_decisions(round_number, decisions, client, sim_round)

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

        alive_agents = self.agents
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
            others = self._build_others_ctx(agent.entity_id, alive_agents)
            dyn, static = await self._retrieve_memory(agent, round_number)
            recent = "\n".join(
                f"- [{e.get('round','?')}] {e.get('agent_name','?')}: {e.get('content','')[:80]}"
                for e in self._event_history[-5:]
            ) or "（无近期事件）"

            dec = await self.reasoner.reason_quantified(
                agent=agent, round_number=round_number, state=st,
                other_context=others, static_knowledge=static, dynamic_memory=dyn,
                recent_events=recent, env_context=self._env_context(),
                cached_action_catalog=self._cached_action_catalog(),
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
            decisions.append(dec)

        return await self._apply_decisions(round_number, decisions, client, sim_round)

    # ── Shared: apply rule engine effects ──

    async def _apply_decisions(
        self, round_number: int, decisions: list[dict], client: Any,
        sim_round: SimulationRound,
    ) -> SimulationRound:
        re_engine = self._rule_engine
        ranges = re_engine.ranges()

        auto_deltas = re_engine.evaluate_auto_effects(self._states)
        for eid, d in auto_deltas.items():
            if eid in self._states:
                self._states[eid].apply_deltas(d, round_number, ranges)

        for eid, st in self._states.items():
            delay_d = st.resolve_delays(round_number)
            if delay_d:
                st.apply_deltas(delay_d, round_number, ranges)

        deltas, interactions = re_engine.resolve_round(
            self._states, decisions, self._name_to_id, self._env, collect_interactions=True)

        if len(self._states) >= 20:
            _bulk_apply_deltas(self._states, deltas, ranges, re_engine.metrics())
        else:
            for eid, d in deltas.items():
                if eid in self._states:
                    self._states[eid].apply_deltas(d, round_number, ranges)

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
                        logger.warning("[Simulator] Event memory write failed: %s", e)

        if self._enable_narrate and hasattr(self, '_chat_fn'):
            narration = await self._narrate_round(client, round_number, decisions, deltas)
            sim_round.state_delta = {"narration": narration}

        return sim_round

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
            lines.append(st.to_prompt_context())
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
        return "、".join(actions) if actions else ""

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
