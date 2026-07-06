"""Temperature comparison test — measure output diversity at 0.5 vs 0.7 vs 0.85 vs 0.95.

Tests 4 agents × 2 rounds at each temperature level.
Measures: unique_actions, action_type diversity, rationale variety.
"""
from __future__ import annotations

import asyncio, os, sys, time, traceback
from collections import Counter

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

os.environ.setdefault("FORGE_PROVIDER", "lmstudio")
os.environ.setdefault("FORGE_LLM_BASE", "http://127.0.0.1:1234/v1")
os.environ.setdefault("FORGE_LLM_MODEL", "qwen3.5-2b")

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from literarycreation.engine.models import DeductionAgentProfile, EntityState
from literarycreation.engine.rule_engine import RuleEngine
from literarycreation.engine.simulator import SimulationEngine

PASS = FAIL = 0

def check(name, cond, detail=""):
    global PASS, FAIL
    if cond: PASS += 1
    else: FAIL += 1; print(f"  FAIL  {name} {detail}")

async def test_temperature(temp, label):
    print(f"\n--- Temperature={temp} ({label}) ---")
    re = RuleEngine.from_domain("literary_realism")
    init_m = dict(re.pack.get("initial_metrics", {}))

    agents = [
        DeductionAgentProfile(entity_id="A1", name="shenye", persona="冷峻刺客，背负复仇使命", background="师父被朝廷陷害", goals=["查出师父死因"]),
        DeductionAgentProfile(entity_id="A2", name="luyuan", persona="深沉导师，暗藏锋芒", background="原朝中大臣", goals=["保护shenye"]),
        DeductionAgentProfile(entity_id="A3", name="liuqian", persona="谨小慎微的官员", background="身藏秘密", goals=["保全性命"]),
        DeductionAgentProfile(entity_id="A4", name="zhaoyu", persona="诏狱典狱，冷酷无情", background="掌管诏狱刑罚", goals=["拷问真相"]),
    ]

    states = {a.entity_id: EntityState(id=a.entity_id, name=a.name,
        domain="literary_realism", metrics=dict(init_m), history=[]) for a in agents}

    # Override the reasoner's temperature by patching Semaphore LLM call
    # We do this by creating the engine with a modified reasoner
    engine = SimulationEngine(
        agents=agents, graph=None, total_rounds=2,
        rule_engine=re, states=states,
        enable_narrate=False, persist_events=False,
        max_concurrent=4, temperature=temp,
    )
    # Directly set temperature on the reasoner's _call_llm
    original_async_call = engine.reasoner._call_llm

    async def _call_with_temp(prompt, system):
        from literarycreation.core.llm_client import DeductionLLMClient, Message
        client = DeductionLLMClient()
        resp = await client.chat([Message(role="user", content=prompt)],
                                 system=system, temperature=temp)
        import re, json
        raw = resp.text if hasattr(resp, 'text') else str(resp)
        match = re.search(r"\{[\s\S]*\}", raw)
        if not match:
            return {}
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return {}

    engine.reasoner._call_llm = _call_with_temp

    t0 = time.time()
    all_action_types = []
    all_rationales = []
    for rnd in range(1, 3):
        result = await engine.run_round(rnd)
        for act in result.actions:
            all_action_types.append(act.action_type)
            all_rationales.append(act.content)
    elapsed = time.time() - t0

    # Restore original
    engine.reasoner._call_llm = original_async_call

    # Analysis
    unique_actions = len(set(all_action_types))
    observe_count = sum(1 for a in all_action_types if a in ("observe", "观察", "观望"))
    counts = Counter(all_action_types)

    print(f"  Time: {elapsed:.1f}s")
    print(f"  Unique actions: {unique_actions}/{len(all_action_types)}")
    print(f"  Observe count: {observe_count}/{len(all_action_types)}")
    print(f"  Top actions: {counts.most_common(3)}")
    rationales_short = [r[:40] for r in all_rationales if r]
    print(f"  Samples: {rationales_short[:3]}")

    return {
        "temp": temp, "label": label,
        "unique": unique_actions, "total": len(all_action_types),
        "observe": observe_count, "elapsed": elapsed,
        "top": counts.most_common(3),
    }


async def main():
    global PASS, FAIL
    PASS = FAIL = 0
    print("=" * 60)
    print("  Temperature Comparison Test")
    print("=" * 60)

    results = []
    for temp, label in [(0.5, "low"), (0.7, "mid"), (0.85, "high"), (0.95, "very_high")]:
        try:
            r = await test_temperature(temp, label)
            results.append(r)
        except Exception as e:
            FAIL += 1
            print(f"  EXCEPTION at {temp}: {e}")
            traceback.print_exc()

    print(f"\n{'='*60}")
    print("  Comparison Summary")
    print(f"{'='*60}")
    print(f"  {'Temp':>6} {'Unique':>7} {'Total':>6} {'Obs':>4} {'Time':>6}s")
    print(f"  {'-'*35}")
    for r in results:
        print(f"  {r['temp']:>5.2f}  {r['unique']:>6}/{r['total']:<4}  {r['observe']:>4}  {r['elapsed']:>5.1f}")
    print(f"{'='*60}")

    # Check: higher temp should produce more unique actions
    if len(results) >= 3:
        low_unique = results[0]["unique"]
        high_unique = results[2]["unique"]
        check("high temp >= low temp diversity", high_unique >= low_unique,
              f"{high_unique} vs {low_unique} unique actions")

    if FAIL == 0:
        print("  ALL PASSED")
    else:
        print(f"  {FAIL} FAILURES")
    print(f"{'='*60}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
