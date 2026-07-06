"""Prose retry test — verify retry logic eliminates chapter generation failures.

Simulates 10 consecutive chapters with a small LLM model.
Counts how many chapters succeed (vs fallback summary).
"""
from __future__ import annotations

import asyncio, os, sys, time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

os.environ.setdefault("FORGE_PROVIDER", "lmstudio")
os.environ.setdefault("FORGE_LLM_BASE", "http://127.0.0.1:1234/v1")
os.environ.setdefault("FORGE_LLM_MODEL", "qwen3.5-2b")

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from literarycreation.engine.prose_renderer import ProseRenderer

PASS = FAIL = 0
FALLBACKS = SUCCESS = 0

def check(name, cond, detail=""):
    global PASS, FAIL
    if cond: PASS += 1
    else: FAIL += 1; print(f"  FAIL  {name} {detail}")

async def main():
    global PASS, FAIL, FALLBACKS, SUCCESS, SUCCESS

    seed = "长安城暗流涌动，刺客沈夜背负师父遗命调查旧案。"
    events = [
        "沈夜潜入礼部档案室寻找线索", "刘谦暗中观察沈夜行动",
        "陆远之向沈夜传递残玉线索", "诏狱典狱收到密报开始监视",
    ]
    snapshots = {
        "A1": {"name": "沈夜", "metrics": {"trust": 38, "tension": 65, "affection": 47, "power": 35, "mystery": 85, "fatigue": 42}},
        "A2": {"name": "刘谦", "metrics": {"trust": 33, "tension": 58, "affection": 30, "power": 35, "mystery": 78, "fatigue": 40}},
        "A3": {"name": "陆远之", "metrics": {"trust": 47, "tension": 38, "affection": 52, "power": 35, "mystery": 70, "fatigue": 15}},
        "A4": {"name": "诏狱", "metrics": {"trust": 25, "tension": 70, "affection": 15, "power": 60, "mystery": 40, "fatigue": 30}},
    }

    renderer = ProseRenderer(style="现实主义")
    prev_tail = seed

    print("=" * 60)
    print("  Prose Retry Test — 10 consecutive chapters")
    print(f"  LLM: {os.environ['FORGE_LLM_MODEL']}")
    print("=" * 60)

    for i in range(1, 11):
        chapter_events = [f"{e}" for e in events]
        t0 = time.time()
        text = await renderer.render_chapter(
            chapter_idx=i, total_chapters=10,
            seed_text=seed,
            round_events=chapter_events,
            round_narration="",
            round_states=snapshots,
            prev_tail=prev_tail,
            target_words=500,
        )
        elapsed = time.time() - t0

        is_fallback = "正文生成失败" in text[:50]
        if is_fallback:
            FALLBACKS += 1
            print(f"  Ch{i:02d}: FALLBACK ({elapsed:.1f}s) — {text[:80]}...")
        else:
            SUCCESS += 1
            prev_tail = text[-600:]  # same logic as orchestrator
            print(f"  Ch{i:02d}: OK {len(text)} chars ({elapsed:.1f}s) — {text[:60]}...")

    print(f"\n{'='*60}")
    print(f"  Success: {SUCCESS}/10 chapters generated prose")
    print(f"  Fallbacks: {FALLBACKS}/10 chapters used summary")
    print(f"  Success rate: {SUCCESS*10}%")

    check("success rate >= 80%", SUCCESS >= 8, f"{SUCCESS}/10")
    if FAIL == 0:
        print("  ALL PASSED")
    else:
        print(f"  {FAIL} FAILURES")
    print(f"{'='*60}")
    return 0 if FAIL == 0 else 1

if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
