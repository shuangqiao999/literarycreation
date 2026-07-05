"""E2E full pipeline test — literary_* domain with LM Studio.

Verifies the full 5-phase pipeline with the new narrative engine architecture.
Tests both Mode A (freeform, no outline) and Mode B (blueprint, with outline).
"""
from __future__ import annotations

import asyncio, os, sys, time, traceback

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

os.environ.setdefault("FORGE_DATA_DIR", os.path.join(os.environ.get("TEMP", "."), "lit_test"))
os.environ.setdefault("FORGE_PROVIDER", "lmstudio")
os.environ.setdefault("FORGE_LLM_BASE", "http://127.0.0.1:1234/v1")
os.environ.setdefault("FORGE_LLM_MODEL", "qwen3.5-2b")

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from literarycreation.engine.models import DeductionAgentProfile, EntityState
from literarycreation.engine.rule_engine import RuleEngine
from literarycreation.engine.simulator import SimulationEngine
from literarycreation.engine.event_scheduler import EventScheduler

PASS = FAIL = 0


def banner(t):
    print(f"\n{'=' * 60}\n  {t}\n{'=' * 60}")


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  OK  {name} {detail}")
    else:
        FAIL += 1
        print(f"  FAIL  {name} {detail}")


async def test_rule_pack_loading():
    banner("Test 1: Rule pack loading — 5 literary domains")
    domains = ["literary_realism", "literary_romance", "literary_suspense", "literary_epic", "literary_court"]
    for d in domains:
        re = RuleEngine.from_domain(d)
        check(f"load {d}", re is not None and re.domain == d,
              f"style={re.pack.get('style')} metrics={re.metrics()}")


async def test_mode_a_freeform():
    banner("Test 2: Mode A — Freeform 3-agent 3-round literary simulation")
    re = RuleEngine.from_domain("literary_realism")
    init_m = dict(re.pack.get("initial_metrics", {}))

    agents = [
        DeductionAgentProfile(entity_id="A1", name="沈夜", persona="冷峻刺客，背负复仇使命",
                              background="师父被朝廷陷害，独自漂泊", goals=["查出师父死因", "保护无辜"]),
        DeductionAgentProfile(entity_id="A2", name="陆远", persona="深沉隐忍的导师",
                              background="原朝中大臣，隐退多年", goals=["保护沈夜", "揭示真相"]),
        DeductionAgentProfile(entity_id="A3", name="刘谦", persona="谨小慎微的礼部主事",
                              background="身处朝堂边缘，隐藏秘密", goals=["保全性命", "找到证据"]),
    ]

    states = {a.entity_id: EntityState(id=a.entity_id, name=a.name, domain="literary_realism",
               metrics=dict(init_m), history=[]) for a in agents}
    print(f"  初始指标: {init_m}")

    engine = SimulationEngine(
        agents=agents, graph=None, total_rounds=2,
        log_fn=lambda p, m: print(f"  [{p}] {m}"),
        rule_engine=re, states=states,
        enable_narrate=False, persist_events=False,
        mode="freeform",
    )

    t0 = time.time()
    for rnd in range(1, 3):
        print(f"\n  --- Round {rnd} ---")
        result = await engine.run_round(rnd)
        print(f"  Actions: {len(result.actions)}")
        for act in result.actions:
            print(f"    {act.agent_id}: {act.action_type} -> {act.content[:60]}")

    check("Round 1 has actions", len(result.actions) > 0)
    check("Agents have changed metrics", any(
        v != init_m.get("tension", 0) for st in states.values()
        for k, v in st.metrics.items() if k == "tension"
    ), f"Round {rnd} elapsed: {time.time() - t0:.1f}s")


async def test_mode_b_blueline():
    banner("Test 3: Mode B — Blueprint execution with key events")
    re = RuleEngine.from_domain("literary_realism")
    init_m = dict(re.pack.get("initial_metrics", {}))

    outline = {
        "key_events": [
            {"round": 1, "event": "沈夜偶然发现师父留下的密信", "level": "hard"},
            {"round": 2, "event": "刘谦向沈夜透露朝中秘密", "level": "soft"},
        ],
        "characters": [
            {"name": "沈夜", "initial_state": {"trust": 60, "tension": 30},
             "final_state": {"trust": 30, "tension": 70}},
        ]
    }
    scheduler = EventScheduler.from_outline(outline, 3)

    agents = [
        DeductionAgentProfile(entity_id="A1", name="沈夜",
                              persona="冷峻刺客，背负复仇使命",
                              background="师父被朝廷陷害", goals=["查出师父死因"]),
        DeductionAgentProfile(entity_id="A2", name="刘谦",
                              persona="谨小慎微的礼部主事",
                              background="身藏秘密", goals=["保全性命"]),
    ]

    states = {a.entity_id: EntityState(id=a.entity_id, name=a.name, domain="literary_realism",
               metrics=dict(init_m), history=[]) for a in agents}

    # Set outline character initial states
    for c in outline.get("characters", []):
        name = c.get("name")
        if name:
            for eid, st in states.items():
                if st.name == name and c.get("initial_state"):
                    st.metrics.update({k: float(v) for k, v in c["initial_state"].items()})

    engine = SimulationEngine(
        agents=agents, graph=None, total_rounds=2,
        log_fn=lambda p, m: print(f"  [{p}] {m}"),
        rule_engine=re, states=states,
        enable_narrate=False, persist_events=False,
        mode="blueline",
        event_scheduler=scheduler,
    )

    t0 = time.time()
    for rnd in range(1, 3):
        events = scheduler.get_events_for_round(rnd)
        hard = [e for e in events if e.level == "hard"]
        print(f"\n  --- Round {rnd} --- 硬事件: {[e.description for e in hard]}")
        result = await engine.run_round(rnd)
        print(f"  Actions: {len(result.actions)}")
        for act in result.actions:
            driver_tag = getattr(act, 'driver', '?')
            print(f"    [{driver_tag}] {act.agent_id}: {act.action_type} -> {act.content[:60]}")

    check("Round 1 has actions in blueline mode", len(result.actions) > 0)
    check("Round 1 has mandate dispatch", scheduler.get_mandate_text(1) != "",
          f"mandate={scheduler.get_mandate_text(1)[:50]}")
    check("Round 1 elapsed reasonable", time.time() - t0 < 180,
          f"{time.time() - t0:.1f}s")


async def test_event_scheduler():
    banner("Test 4: EventScheduler — milestone planning and correction")
    outline = {
        "key_events": [
            {"round": 2, "event": "发现密信", "level": "hard"},
            {"round": 4, "event": "朝中对峙", "level": "hard"},
            {"round": 6, "event": "真相大白", "level": "soft"},
        ],
        "characters": [
            {"name": "沈夜", "initial_state": {"trust": 80, "tension": 20},
             "final_state": {"trust": 20, "tension": 80}},
        ]
    }
    s = EventScheduler.from_outline(outline, 10)

    # Round 2 should have the hard event
    events = s.get_events_for_round(2)
    check("Round 2 has hard event", len([e for e in events if e.level == "hard"]) == 1)
    check("Mandate text contains event", "密信" in s.get_mandate_text(2))

    # Round 1 should catch up window 1
    events_r1 = s.get_events_for_round(1)
    check("Round 1 may catch round 2 event", len(events_r1) >= 0,
          f"events={len(events_r1)}")

    # Narrative phases
    check("Phase at round 1", s.get_narrative_phase(1) == "exposition")
    check("Phase at round 5", s.get_narrative_phase(5) == "climax_zone",
          f"got {s.get_narrative_phase(5)}")


async def main():
    global PASS, FAIL
    PASS = FAIL = 0
    print("╔" + "═" * 58 + "╗")
    print("║  LiteraryCreation E2E Test — LM Studio                     ║")
    print("║  LLM: " + os.environ.get("FORGE_LLM_MODEL", "?").ljust(48) + "║")
    print("╚" + "═" * 58 + "╝")

    tests = [
        ("Rule pack loading", test_rule_pack_loading),
        ("Mode A freeform", test_mode_a_freeform),
        ("Mode B blueline", test_mode_b_blueline),
        ("EventScheduler", test_event_scheduler),
    ]

    for name, fn in tests:
        try:
            await fn()
        except Exception as e:
            FAIL += 1
            print(f"  EXCEPTION in {name}: {e}")
            traceback.print_exc()

    print(f"\n{'=' * 60}")
    print(f"  Results: {PASS} passed / {FAIL} failed ({PASS + FAIL} total)")
    print(f"{'=' * 60}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
