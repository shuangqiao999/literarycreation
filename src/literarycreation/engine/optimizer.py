"""策略优化器 — 蒙特卡洛多方案对比，带 SSE 进度流。"""
from __future__ import annotations

import copy
import logging
from typing import Any

logger = logging.getLogger(__name__)


class ScenarioResult:
    """单个方案的优化结果。"""
    name: str = ""
    win_score: float = 0.0
    cost: float = 0.0
    success: bool = False
    alive: bool = True
    final_metrics: dict[str, float] = {}
    rounds: int = 0


class StrategyOptimizer:
    """蒙特卡洛多方案场景对比优化器。"""

    def __init__(self, engine: Any = None):
        self._engine = engine

    def _judge_quantified(self, rule_engine: Any, states: dict[str, Any],
                           scenario: dict[str, Any]) -> ScenarioResult:
        """量化判定：对每个方案评分。"""
        result = ScenarioResult()
        result.name = scenario.get("name", "?")
        wt = scenario.get("win_target") or {}
        entity_ref = str(wt.get("entity_ref", "") or "").strip()
        if entity_ref and entity_ref in states:
            st = states[entity_ref]
            judged = rule_engine.judge(st, wt)
            result.win_score = float(judged.get("win_score", 0.0))
            result.cost = float(judged.get("cost", 0.0))
            result.success = bool(judged.get("success", False))
            result.alive = bool(judged.get("alive", True))
            result.final_metrics = dict(getattr(st, "metrics", {}))
        else:
            # 未指定 entity_ref，取最高分实体
            best = 0.0
            for st in states.values():
                judged = rule_engine.judge(st, wt)
                ws = float(judged.get("win_score", 0.0))
                if ws > best:
                    best = ws
                    result.win_score = ws
                    result.cost = float(judged.get("cost", 0.0))
                    result.success = bool(judged.get("success", False))
                    result.alive = bool(judged.get("alive", True))
                    result.final_metrics = dict(getattr(st, "metrics", {}))
        return result

    async def run(
        self,
        *,
        session_id: str,
        scenarios: list[dict[str, Any]],
        agents: list[Any],
        rule_engine: Any,
        states: dict[str, Any],
        total_rounds: int,
        log_fn: Any,
        progress_fn: Any | None = None,
        graph: Any = None,
        preprocessor: Any = None,
    ) -> tuple[list[ScenarioResult], dict[str, Any] | None]:
        """运行优化：遍历方案链 → 隔离模拟 → 评分。

        返回 (results, recommendation)。
        """
        from .simulator import SimulationEngine

        results: list[ScenarioResult] = []
        total = len(scenarios)
        for idx, scenario in enumerate(scenarios, 1):
            log_fn("optimizer", f"方案 [{idx}/{total}] {scenario.get('name','?')} 开始...")

            # 克隆初始状态（隔离各方案互不污染）
            cloned_states = {
                eid: copy.deepcopy(st) for eid, st in states.items()
            } if states else {}

            # 注入方案指令到角色 goal
            directive = str(scenario.get("directive", "") or "")
            sim_agents = list(agents)
            if directive:
                for a in sim_agents:
                    if hasattr(a, "goals") and isinstance(a.goals, list):
                        if "方案指令" not in str(a.goals):
                            a.goals = list(a.goals) + [f"[方案指令] {directive}"]

            engine = SimulationEngine(
                agents=sim_agents,
                graph=graph,
                total_rounds=total_rounds,
                log_fn=log_fn,
                preprocessor=preprocessor,
                rule_engine=rule_engine,
                states=cloned_states,
                persist_events=False,
                mode="freeform",
            )

            for rnd in range(1, total_rounds + 1):
                await engine.run_round(rnd)

            result = self._judge_quantified(rule_engine, cloned_states, scenario)
            result.rounds = total_rounds
            results.append(result)

            log_fn("optimizer",
                    f"方案 [{idx}/{total}] {result.name}: "
                    f"win_score={result.win_score:.2f} "
                    f"cost={result.cost:.2f} "
                    f"success={result.success}")

            if progress_fn:
                progress_fn(idx, total, {
                    "scenario": result.name,
                    "win_score": round(result.win_score, 3),
                    "cost": round(result.cost, 1),
                    "success": result.success,
                })

        # 推荐：最高 win_score + 最小 cost tiebreaker
        recommendation = None
        if results:
            scored = sorted(results, key=lambda r: (r.win_score, -r.cost, r.success), reverse=True)
            best = scored[0]
            recommendation = {
                "name": best.name,
                "win_score": round(best.win_score, 3),
                "cost": round(best.cost, 1),
                "success": best.success,
                "rationale": (
                    f"最高综合得分 {best.win_score:.2f}，"
                    f"成本 {best.cost:.1f}。"
                    f"推荐采用此方案。"
                ),
            }

        return results, recommendation
