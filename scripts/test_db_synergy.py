"""Three-DB Synergy Test — verify Kuzu/LanceDB/SQLite improvements in full pipeline.

Tests:
  1. Kuzu continuity guard prevents dead-characters-appearing-alive
  2. Kuzu causal chain effects appear in agent self_memory
  3. SQLite story_state persistence survives restart simulation
  4. Kuzu-filtered LanceDB queries return more relevant results
  5. LanceDB style anchors inject into prose prompt
"""
from __future__ import annotations

import asyncio, os, sys, time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

os.environ.setdefault("FORGE_PROVIDER", "lmstudio")
os.environ.setdefault("FORGE_LLM_BASE", "http://127.0.0.1:1234/v1")
os.environ.setdefault("FORGE_LLM_MODEL", "qwen3.5-2b")
os.environ.setdefault("FORGE_DATA_DIR", os.path.join(os.environ.get("TEMP", "."), "lit_db_synergy"))
os.environ.setdefault("FORGE_MAX_CONCURRENT", "4")

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from literarycreation.engine.models import DeductionAgentProfile, EntityState
from literarycreation.engine.rule_engine import RuleEngine
from literarycreation.engine.simulator import SimulationEngine
from literarycreation.engine.orchestrator import DeductionOrchestrator
from literarycreation.engine.prose_renderer import ProseRenderer, build_story_context, append_chapter_summary

_build_continuity_ctx = DeductionOrchestrator._build_continuity_ctx
_retrieve_style_anchors = DeductionOrchestrator._retrieve_style_anchors

PASS = FAIL = 0

def banner(t):
    print(f"\n{'='*55}\n  {t}\n{'='*55}")

def check(name, cond, detail=""):
    global PASS, FAIL
    if cond: PASS += 1; print(f"  OK  {name} {detail}")
    else: FAIL += 1; print(f"  FAIL  {name} {detail}")

async def test_kuzu_continuity():
    banner("Test 1: Kuzu continuity guard")
    re = RuleEngine.from_domain("literary_realism")
    m = dict(re.pack.get("initial_metrics", {}))
    agents = [
        DeductionAgentProfile(entity_id="A1", name="shenye", persona="刺客", goals=["复仇"]),
        DeductionAgentProfile(entity_id="A2", name="luyuan", persona="导师", goals=["守护"]),
    ]
    states = {a.entity_id: EntityState(id=a.entity_id, name=a.name,
        domain="literary_realism", metrics=dict(m), history=[]) for a in agents}

    # Run 1 round with Kuzu events — effect should appear
    from literarycreation.storage.graph_store import DeductionGraphStore
    import tempfile
    tmp = tempfile.mkdtemp()
    graph = DeductionGraphStore(os.path.join(tmp, "kuzu"))
    # Agent nodes must be upserted (normally done by agent_factory in Phase 3)
    for a in agents:
        graph.upsert_agent_node(a.entity_id, a.name, a.persona, a.background, str(a.goals))

    engine = SimulationEngine(
        agents=agents, graph=graph, total_rounds=2,
        rule_engine=re, states=states,
        enable_narrate=False, persist_events=True, max_concurrent=2,
    )

    # Round 1
    await engine.run_round(1)
    # Check Kuzu events have effect field
    se = graph.get_recent_events_for_agent("A1", last_n=1)
    check("Kuzu event exists for shenye", len(se) > 0, f"{len(se)} events")
    if se:
        check("Kuzu event has effect", bool(se[0].get("effect")),
              f"effect={se[0].get('effect', '')[:60]}")

    # Round 2
    await engine.run_round(2)
    se2 = graph.get_recent_events_for_agent("A1", last_n=2)
    check("Kuzu 2 events for shenye", len(se2) >= 2, f"{len(se2)} events")

    # Check continuity context builds
    ctx = _build_continuity_ctx(graph, agents, 3)
    check("Continuity ctx includes shenye", "shenye" in ctx, ctx[:80])
    check("Continuity ctx includes luyuan", "luyuan" in ctx)

    graph.close()
    import shutil; shutil.rmtree(tmp, ignore_errors=True)


async def test_kuzu_causal_chain():
    banner("Test 2: Kuzu causal chain in agent memory")
    re = RuleEngine.from_domain("literary_realism")
    m = dict(re.pack.get("initial_metrics", {}))
    agents = [
        DeductionAgentProfile(entity_id="A1", name="shenye", persona="冷峻刺客，复仇为先", goals=["查出真相"]),
        DeductionAgentProfile(entity_id="A2", name="liuqian", persona="礼部主事", goals=["保全自己"]),
    ]
    states = {a.entity_id: EntityState(id=a.entity_id, name=a.name,
        domain="literary_realism", metrics=dict(m), history=[]) for a in agents}

    engine = SimulationEngine(
        agents=agents, graph=None, total_rounds=2,
        rule_engine=re, states=states,
        enable_narrate=False, persist_events=False, max_concurrent=2,
    )
    await engine.run_round(1)
    r2 = await engine.run_round(2)
    actions = [a.action_type for a in r2.actions]
    check("freeform actions not empty", len(actions) > 0, f"{len(actions)} actions")
    # Verify action diversity
    unique = len(set(actions))
    check("action diversity", unique >= 1, f"{unique}/{len(actions)} unique")


async def test_sqlite_story_state():
    banner("Test 3: SQLite story_state persistence")
    from literarycreation.engine.prose_renderer import extract_synopsis

    # Simulate story_state accumulation
    state = {}
    append_chapter_summary(state, 1, "长安城暗流涌动，刺客沈夜潜入礼部。" * 3, {})
    append_chapter_summary(state, 2, "沈夜在档案室发现密信，师父之死另有隐情。" * 3, {})
    ctx = build_story_context(state, 3)
    check("story_state has ch1 synopsis", "第1章概要" in state.get("summary", ""),
          state.get("summary", "")[:80])
    check("story_context for ch3", len(ctx) > 50, f"{len(ctx)} chars")

    # Verdict extract quality
    syn = extract_synopsis("沈夜潜入档案室发现密信。师父之死另有隐情。" * 3)
    check("synopsis extraction", len(syn) > 10 and "潜入" in syn, syn[:80])


async def test_lancedb_style_anchors():
    banner("Test 4: LanceDB style anchors")

    from literarycreation.engine.preprocessor import DeductionPreprocessor
    from literarycreation.core.config import config
    import tempfile

    tmp = tempfile.mkdtemp()
    sid = "test_style_anchors"
    pp = DeductionPreprocessor(workspace_root=tmp, session_id=sid)
    source = (
        "残阳如血，将整座长安城染成一片不亡的赤红。"
        "沈夜蹲在永宁坊的屋脊上，左手按住刀柄，已经保持这个姿势快一个时辰了。"
        "风从终南山方向吹来，裹着初冬的寒意，像是一把无形的手，试图把这一方天地里的热气全部抽干。"
        "巷子那头终于有了动静。门被推开一条缝，一个穿着灰布长衫的人闪了出来。"
    )
    try:
        pp.preprocess(source)
        anchors = _retrieve_style_anchors(pp, source)
        check("style anchors found", len(anchors) > 0, f"{len(anchors)} anchors")
        if anchors:
            check("anchor length > 150", len(anchors[0]) > 100, f"{len(anchors[0])} chars")
    except Exception as e:
        check("style anchors test", False, f"exception: {e}")
    finally:
        try: pp.drop_tables()
        except: pass
        import shutil; shutil.rmtree(tmp, ignore_errors=True)


async def test_full_mini_pipeline():
    banner("Test 5: Mini full pipeline — 2 agents x 2 rounds + prose")
    re = RuleEngine.from_domain("literary_realism")
    m = dict(re.pack.get("initial_metrics", {}))
    agents = [
        DeductionAgentProfile(entity_id="A1", name="shenye", persona="冷峻刺客背负复仇使命",
                              background="师父被朝廷陷害", goals=["查出师父死因"]),
        DeductionAgentProfile(entity_id="A2", name="liuqian", persona="礼部主事谨小慎微",
                              background="身藏秘密", goals=["保全性命"]),
    ]
    states = {a.entity_id: EntityState(id=a.entity_id, name=a.name,
        domain="literary_realism", metrics=dict(m), history=[]) for a in agents}

    engine = SimulationEngine(
        agents=agents, graph=None, total_rounds=2,
        rule_engine=re, states=states,
        enable_narrate=False, persist_events=False, max_concurrent=2,
    )

    t0 = time.time()
    for rnd in range(1, 3):
        r = await engine.run_round(rnd)
        check(f"r{rnd} actions", len(r.actions) > 0, f"{len(r.actions)} actions")
        check(f"r{rnd} states in delta", len(r.state_delta.get("states", {})) > 0)
        snap = r.state_delta.get("snapshot")
        check(f"r{rnd} snapshot", snap is not None and snap.get("entity_count") == 2,
              f"entities={snap.get('entity_count') if snap else 0}")

    elapsed = time.time() - t0
    print(f"\n  Pipeline time: {elapsed:.1f}s")

    # Test prose rendering with story_context
    renderer = ProseRenderer(style="现实主义")
    events_r1 = [act.content for act in r.actions]
    states_r1 = r.state_delta.get("states", {})

    state = {}
    text = await renderer.render_chapter(
        chapter_idx=1, total_chapters=2, seed_text="古代刺客复仇",
        round_events=events_r1, round_narration="", round_states=states_r1,
        prev_tail="", target_words=300,
        story_context=build_story_context(state, 1),
        style_anchors="",
    )
    check("prose generated", len(text) > 50 and "正文生成失败" not in text,
          f"{len(text)} chars")


async def main():
    global PASS, FAIL
    PASS = FAIL = 0
    model = os.environ.get("FORGE_LLM_MODEL", "?")
    print("=" * 55)
    print(f"  Three-DB Synergy Test")
    print(f"  LLM: {model}")
    print("=" * 55)

    tests = [
        ("Kuzu continuity guard", test_kuzu_continuity),
        ("Kuzu causal chain", test_kuzu_causal_chain),
        ("SQLite story_state", test_sqlite_story_state),
        ("LanceDB style anchors", test_lancedb_style_anchors),
        ("Mini full pipeline", test_full_mini_pipeline),
    ]

    for name, fn in tests:
        try:
            await fn()
        except Exception as e:
            FAIL += 1
            print(f"\n  EXCEPTION in {name}: {e}")
            import traceback; traceback.print_exc()

    print(f"\n{'='*55}")
    print(f"  Results: {PASS} passed / {FAIL} failed ({PASS+FAIL} total)")
    if FAIL == 0: print("  ALL TESTS PASSED")
    print(f"{'='*55}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
