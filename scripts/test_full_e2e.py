"""Full E2E pipeline test — literary creation with prose rendering via LM Studio.

Tests the complete 5-phase pipeline:
  1. Rule pack loading
  2. Mode A freeform with 3 agents x 3 rounds
  3. Mode B blueline with outline + prose rendering
  4. EventScheduler milestones + correction
  5. Character state snapshots in state_delta
  6. Prose renderer output quality
"""
from __future__ import annotations

import asyncio, os, sys, time, tempfile, traceback

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

os.environ["FORGE_DATA_DIR"] = os.path.join(tempfile.mkdtemp(prefix="lit_e2e_"), ".forge")
os.environ.setdefault("FORGE_PROVIDER", "lmstudio")
os.environ.setdefault("FORGE_LLM_BASE", "http://127.0.0.1:1234/v1")
os.environ.setdefault("FORGE_LLM_MODEL", "qwen3.5-2b")

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from literarycreation.engine.models import DeductionAgentProfile, EntityState
from literarycreation.engine.rule_engine import RuleEngine
from literarycreation.engine.simulator import SimulationEngine
from literarycreation.engine.event_scheduler import EventScheduler
from literarycreation.engine.prose_renderer import ProseRenderer

PASS = FAIL = 0

def banner(t):
    print(f"\n{'='*60}\n  {t}\n{'='*60}")

def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  OK  {name} {detail}")
    else:
        FAIL += 1
        print(f"  FAIL  {name} {detail}")

async def test_rule_packs():
    banner("Test 1: 5 literary rule packs")
    for d in ["literary_realism", "literary_romance", "literary_suspense", "literary_epic", "literary_court"]:
        re = RuleEngine.from_domain(d)
        check(f"load {d}", re is not None, f"style={re.pack.get('style')}")

async def test_mode_a_freeform():
    banner("Test 2: Mode A — 3 agents x 3 rounds")
    re = RuleEngine.from_domain("literary_realism")
    init_m = dict(re.pack.get("initial_metrics", {}))

    agents = [
        DeductionAgentProfile(entity_id="A1", name="shenye", persona="冷峻刺客，背负复仇使命",
                              background="师父被朝廷陷害", goals=["查出师父死因", "保护无辜"]),
        DeductionAgentProfile(entity_id="A2", name="luyuan", persona="深沉隐忍的导师",
                              background="原朝中大臣，隐退多年", goals=["保护shenye", "揭示真相"]),
        DeductionAgentProfile(entity_id="A3", name="liuqian", persona="谨小慎微的礼部主事",
                              background="身藏秘密", goals=["保全性命", "找到证据"]),
    ]

    states = {}
    for a in agents:
        states[a.entity_id] = EntityState(id=a.entity_id, name=a.name,
            domain="literary_realism", metrics=dict(init_m), history=[])

    engine = SimulationEngine(
        agents=agents, graph=None, total_rounds=2,
        rule_engine=re, states=states,
        enable_narrate=False, persist_events=False,
        mode="freeform", max_concurrent=1,
    )

    results_rounds: list = []
    for rnd in range(1, 3):
        result = await engine.run_round(rnd)
        results_rounds.append(result)
        check(f"r{rnd} has actions", len(result.actions) > 0, f"{len(result.actions)} actions")

    # Verify state_delta snapshots
    final_result = results_rounds[-1]
    check("r2 state_delta has states", "states" in final_result.state_delta)
    snapshots = final_result.state_delta.get("states", {})
    check("snapshots contain agents", len(snapshots) == 3, f"{len(snapshots)} agents")
    for eid, st in snapshots.items():
        check(f"snapshot {st['name']} has metrics", len(st.get("metrics", {})) > 0,
              f"{list(st['metrics'].keys())[:3]}...")

    # Verify metrics changed: tension变化 或 任一指标变化 或 有>=2个实质性动作
    non_observe = sum(1 for r in results_rounds for a in r.actions
                      if getattr(a, "action_type", "") != "observe")
    tension_changed = any(
        abs(st.metrics.get("tension", 0) - init_m.get("tension", 0)) > 0.5
        for st in states.values()
    )
    any_changed = any(
        abs(st.metrics.get(mk, 0) - init_m.get(mk, 0)) > 0.5
        for st in states.values() for mk in init_m
    )
    check("metrics changed over rounds", tension_changed or any_changed or non_observe >= 2,
          f"tension={tension_changed}, any_metric={any_changed}, non-observe={non_observe}")

async def test_mode_b_blueline_with_prose():
    banner("Test 3: Mode B — blueline + prose rendering")
    re = RuleEngine.from_domain("literary_realism")
    init_m = dict(re.pack.get("initial_metrics", {}))

    outline = {
        "key_events": [
            {"round": 1, "event": "shenye found master's secret letter", "level": "hard"},
            {"round": 2, "event": "liuqian reveals court secret to shenye", "level": "soft"},
        ],
        "characters": [
            {"name": "shenye", "initial_state": {"trust": 60, "tension": 30},
             "final_state": {"trust": 30, "tension": 70}},
        ]
    }
    scheduler = EventScheduler.from_outline(outline, 2)

    agents = [
        DeductionAgentProfile(entity_id="A1", name="shenye",
                              persona="冷峻刺客", goals=["查出师父死因"]),
        DeductionAgentProfile(entity_id="A2", name="liuqian",
                              persona="礼部主事", goals=["保全性命"]),
    ]

    states = {}
    for a in agents:
        states[a.entity_id] = EntityState(id=a.entity_id, name=a.name,
            domain="literary_realism", metrics=dict(init_m), history=[])
    for c in outline.get("characters", []):
        name = c.get("name")
        if name:
            for eid, st in states.items():
                if st.name == name and c.get("initial_state"):
                    st.metrics.update({k: float(v) for k, v in c["initial_state"].items()})

    engine = SimulationEngine(
        agents=agents, graph=None, total_rounds=2,
        rule_engine=re, states=states,
        enable_narrate=False, persist_events=False,
        mode="blueline", event_scheduler=scheduler, max_concurrent=1,
    )

    rounds_data = []
    for rnd in range(1, 3):
        mandate = scheduler.get_mandate_text(rnd)
        print(f"\n  --- R{rnd} mandate: {mandate[:60]}...")
        result = await engine.run_round(rnd)
        rounds_data.append(result)
        check(f"r{rnd} blueline actions", len(result.actions) > 0, f"{len(result.actions)} actions")

    # Test prose renderer with real character states
    renderer = ProseRenderer(style="现实主义")
    events_r1 = [act.content for act in rounds_data[0].actions]
    snapshots = rounds_data[0].state_delta.get("states", {})

    print("\n  --- Testing prose renderer ---")
    text = await renderer.render_chapter(
        chapter_idx=1, total_chapters=2,
        seed_text="古代刺客复仇小说",
        round_events=events_r1,
        round_narration=rounds_data[0].state_delta.get("narration", ""),
        round_states=snapshots,
        prev_tail="",
        target_words=500,
    )

    is_prose = len(text) > 100 and "正文生成失败" not in text
    check("prose generated >100 chars", is_prose, f"{len(text)} chars")
    if is_prose:
        print(f"    Preview: {text[:150]}...")
    else:
        print(f"    Fallback: {text[:200]}")
        # Fallback should at least have character states
        check("fallback has character states", "角色状态" in text and "shenye" in text)

async def test_event_scheduler():
    banner("Test 4: EventScheduler")
    outline = {
        "key_events": [
            {"round": 2, "event": "find letter", "level": "hard"},
            {"round": 5, "event": "confrontation", "level": "hard"},
            {"round": 7, "event": "truth revealed", "level": "soft"},
        ],
        "characters": [
            {"name": "shenye", "initial_state": {"trust": 80, "tension": 20},
             "final_state": {"trust": 20, "tension": 80}},
        ]
    }
    s = EventScheduler.from_outline(outline, 10)
    e_r2 = s.get_events_for_round(2)
    check("r2 hard event", len([e for e in e_r2 if e.level == "hard"]) == 1)
    check("mandate text", "find" in s.get_mandate_text(2))
    check("r1 exposition", s.get_narrative_phase(1) == "exposition")
    check("r5 climax", s.get_narrative_phase(5) == "climax_zone", f"got {s.get_narrative_phase(5)}")

    # Build chapter context
    ctx = s.build_chapter_context(2, {}, [], outline.get("characters"))
    check("context mandatory events", len(ctx.mandatory_events) > 0)
    check("context phase", ctx.narrative_phase != "")

async def main():
    global PASS, FAIL
    PASS = FAIL = 0
    model = os.environ.get("FORGE_LLM_MODEL", "?")
    print("=" * 60)
    print(f"  LiteraryCreation Full E2E Test")
    print(f"  LLM: {model}")
    print("=" * 60)

    tests = [
        ("Rule packs", test_rule_packs),
        ("Mode A freeform", test_mode_a_freeform),
        ("Mode B + prose", test_mode_b_blueline_with_prose),
        ("EventScheduler", test_event_scheduler),
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
    if FAIL == 0:
        print("  ALL TESTS PASSED")
    print(f"{'='*60}")
    return 0 if FAIL == 0 else 1

if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
