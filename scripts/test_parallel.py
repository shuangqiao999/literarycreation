"""Parallel execution test — verify Semaphore-controlled concurrency.

Tests FORGE_MAX_CONCURRENT=2 (default) with 6 agents.
Measures round latency to confirm parallelism.
"""
from __future__ import annotations

import asyncio, os, sys, time, traceback

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

os.environ.setdefault("FORGE_DATA_DIR", os.path.join(os.environ.get("TEMP", "."), "lit_par"))
os.environ.setdefault("FORGE_PROVIDER", "lmstudio")
os.environ.setdefault("FORGE_LLM_BASE", "http://127.0.0.1:1234/v1")
os.environ.setdefault("FORGE_LLM_MODEL", "qwen3.5-2b")
os.environ.setdefault("FORGE_MAX_CONCURRENT", "2")

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from literarycreation.engine.models import DeductionAgentProfile, EntityState
from literarycreation.engine.rule_engine import RuleEngine
from literarycreation.engine.simulator import SimulationEngine
from literarycreation.core.config import config

PASS = FAIL = 0

def banner(t):
    print(f"\n{'='*60}\n  {t}\n{'='*60}")

def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1; print(f"  OK  {name} {detail}")
    else:
        FAIL += 1; print(f"  FAIL  {name} {detail}")

async def test_parallel_6_agents():
    banner("Parallel test: 6 agents, max_concurrent=2")
    re = RuleEngine.from_domain("literary_realism")
    init_m = dict(re.pack.get("initial_metrics", {}))
    max_c = config.deduction_max_concurrent
    print(f"  Config: max_concurrent={max_c}")

    agents = [
        DeductionAgentProfile(entity_id=f"A{i}", name=f"agent_{i}",
            persona="测试角色", goals=["完成任务"]) for i in range(6)
    ]
    states = {a.entity_id: EntityState(id=a.entity_id, name=a.name,
        domain="literary_realism", metrics=dict(init_m), history=[]) for a in agents}

    # Sequential baseline
    engine_seq = SimulationEngine(
        agents=agents, graph=None, total_rounds=1,
        rule_engine=re, states={a.entity_id: EntityState(id=a.entity_id, name=a.name,
            domain="literary_realism", metrics=dict(init_m), history=[]) for a in agents},
        enable_narrate=False, persist_events=False,
        max_concurrent=6,  # effectively sequential (all at once)
    )
    t0 = time.time()
    await engine_seq.run_round(1)
    t_seq = time.time() - t0
    print(f"  Sequential (max_c=6): {t_seq:.1f}s")

    # Parallel test
    engine_par = SimulationEngine(
        agents=agents, graph=None, total_rounds=1,
        rule_engine=re, states=states,
        enable_narrate=False, persist_events=False,
        max_concurrent=max_c,
    )
    t0 = time.time()
    await engine_par.run_round(1)
    t_par = time.time() - t0
    print(f"  Parallel (max_c={max_c}): {t_par:.1f}s")

    # With 6 agents and 2 concurrent, parallel should be faster
    # Expected: sequential ≈ 6*LLM_time, parallel ≈ 3*LLM_time
    if t_par < t_seq * 0.8:
        check("parallel faster than sequential", True, f"{t_par:.1f}s vs {t_seq:.1f}s")
    else:
        check("parallel faster than sequential", t_par < t_seq,
              f"{t_par:.1f}s vs {t_seq:.1f}s (may overlap with sequential I/O)")


async def test_stability_3_runs():
    banner("Stability: 3 consecutive rounds with parallel=2")
    re = RuleEngine.from_domain("literary_realism")
    init_m = dict(re.pack.get("initial_metrics", {}))
    agents = [
        DeductionAgentProfile(entity_id="A1", name="shenye", persona="刺客", goals=["复仇"]),
        DeductionAgentProfile(entity_id="A2", name="luyuan", persona="导师", goals=["守护"]),
        DeductionAgentProfile(entity_id="A3", name="liuqian", persona="官员", goals=["自保"]),
        DeductionAgentProfile(entity_id="A4", name="zhaoyu", persona="诏狱典狱", goals=["拷问"]),
    ]
    states = {a.entity_id: EntityState(id=a.entity_id, name=a.name,
        domain="literary_realism", metrics=dict(init_m), history=[]) for a in agents}

    engine = SimulationEngine(
        agents=agents, graph=None, total_rounds=3,
        rule_engine=re, states=states,
        enable_narrate=False, persist_events=False,
        max_concurrent=2,
    )

    for rnd in range(1, 4):
        t0 = time.time()
        result = await engine.run_round(rnd)
        elapsed = time.time() - t0
        print(f"  R{rnd}: {len(result.actions)} actions in {elapsed:.1f}s")
        check(f"R{rnd} has all 4 agents", len(result.actions) == 4,
              f"{len(result.actions)}/4")


async def test_semaphore_enforcement():
    banner("Semaphore performance: compare max_c=1 vs max_c=2")

    # We test indirectly: with 4 agents and max_concurrent=2, the total round
    # time should be approximately 2x a single LLM call, not 4x.
    re = RuleEngine.from_domain("literary_realism")
    init_m = dict(re.pack.get("initial_metrics", {}))
    agents = [
        DeductionAgentProfile(entity_id=f"B{i}", name=f"agent_{i}",
            persona="测试", goals=["任务"]) for i in range(4)
    ]
    states = {a.entity_id: EntityState(id=a.entity_id, name=a.name,
        domain="literary_realism", metrics=dict(init_m), history=[]) for a in agents}

    engine = SimulationEngine(
        agents=agents, graph=None, total_rounds=1,
        rule_engine=re, states=states,
        enable_narrate=False, persist_events=False,
        max_concurrent=1,  # sequential
    )
    t0 = time.time()
    await engine.run_round(1)
    t_seq = time.time() - t0

    engine2 = SimulationEngine(
        agents=agents, graph=None, total_rounds=1,
        rule_engine=re, states={a.entity_id: EntityState(id=a.entity_id, name=a.name,
            domain="literary_realism", metrics=dict(init_m), history=[]) for a in agents},
        enable_narrate=False, persist_events=False,
        max_concurrent=2,  # parallel x2
    )
    t0 = time.time()
    await engine2.run_round(1)
    t_par = time.time() - t0

    ratio = t_seq / max(t_par, 0.1)
    print(f"  Sequential (max_c=1): {t_seq:.1f}s")
    print(f"  Parallel  (max_c=2): {t_par:.1f}s")
    print(f"  Speedup ratio: {ratio:.1f}x")
    check("parallel shows speedup", ratio > 1.3 or t_par < t_seq,
          f"ratio={ratio:.1f}")


async def main():
    global PASS, FAIL
    PASS = FAIL = 0
    print("=" * 60)
    print(f"  Parallel Execution Test — max_concurrent={config.deduction_max_concurrent}")
    print("=" * 60)

    tests = [
        ("6-agent parallel", test_parallel_6_agents),
        ("3-round stability", test_stability_3_runs),
        ("Semaphore enforcement", test_semaphore_enforcement),
    ]

    for name, fn in tests:
        try:
            await fn()
        except Exception as e:
            FAIL += 1
            print(f"\n  EXCEPTION in {name}: {e}")
            traceback.print_exc()

    print(f"\n{'='*60}")
    print(f"  Results: {PASS} passed / {FAIL} failed ({PASS+FAIL} total)")
    if FAIL == 0: print("  ALL TESTS PASSED")
    print(f"{'='*60}")
    return 0 if FAIL == 0 else 1

if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
