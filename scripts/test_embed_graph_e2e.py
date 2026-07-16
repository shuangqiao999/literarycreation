"""专项测试：嵌入模型配置 → 图谱实体提取 → 全流程端到端（本地 LM Studio）。"""
from __future__ import annotations

import asyncio, json, os, sys, tempfile
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

os.environ["FORGE_PROVIDER"] = "lmstudio"
os.environ["FORGE_LLM_BASE"] = "http://127.0.0.1:1234/v1"
os.environ["FORGE_LLM_MODEL"] = "qwen/qwen3.5-9b"
os.environ["FORGE_EMBED_BASE"] = "http://127.0.0.1:1234/v1"
os.environ["FORGE_EMBED_MODEL"] = "text-embedding-embeddinggemma-300m-qat"
os.environ["FORGE_MAX_CONCURRENT"] = "1"

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

SEED = """残雪还没化尽，巷口的青石板路被晨雾压得低低的。沈爷把旧棉袍的袖口往上挽了一截，露出缠着暗红绷带的小臂。
刘谦推门进来时，带进一股冷风。他没说话，只把一张折了三折的铜钱轻轻推到沈爷面前。
"这是定金，"他的声音低得像桌底的灰尘，"但规矩得改一改——这次不是杀人。"
沈爷没碰那枚铜钱，指尖在碗沿上停了片刻。然后他抬起眼——那眼里的东西让刘谦下意识退了一步。
"""

ROUNDS = 3
DOMAIN = "literary_suspense"
TOTAL_WORDS = 6000


async def main():
    print("=" * 60)
    print("  嵌入模型→图谱提取 专项验证")
    print(f"  LLM: qwen/qwen3.5-9b | Embed: text-embedding-embeddinggemma-300m-qat")
    print(f"  章数: {ROUNDS} | 目标: {TOTAL_WORDS} 字")
    print("=" * 60)

    ws = tempfile.mkdtemp(prefix="lit_embed_")
    os.environ["FORGE_DATA_DIR"] = os.path.join(ws, ".forge")

    from literarycreation.engine.engine import DeductionEngine
    engine = DeductionEngine(ws)

    session = engine.create_session(
        title="听雨阁·嵌入校验",
        source_material=SEED,
        config={"domain": DOMAIN, "total_rounds": ROUNDS, "target_words": TOTAL_WORDS},
    )
    print(f"\n  会话: {session.id}")

    print("  启动全流程...")
    try:
        updated = await engine.start(session.id)
    except Exception as e:
        print(f"\n  !!! 崩溃: {e}")
        import traceback
        traceback.print_exc()
        engine.close()
        import shutil
        shutil.rmtree(ws, ignore_errors=True)
        return 1

    data = engine.session_store.get(session.id)
    report = data.get("report_json", {}) if data else {}
    if isinstance(report, str):
        report = json.loads(report)
    logs = engine.get_logs(session.id, limit=1000)

    # 关键验证
    failures = 0

    # 1. 图谱实体数 > 0
    e_count = data.get("entity_count", 0)
    if e_count > 0:
        print(f"\n  PASS 图谱实体: {e_count} 个")
    else:
        failures += 1
        print(f"\n  FAIL 图谱实体: 0 个 ── 嵌入模型可能未生效")

    # 2. Agent 数 > 0
    agent_count = updated.agent_count if updated else 0
    if agent_count > 0:
        print(f"  PASS Agent: {agent_count} 个")
    else:
        failures += 1
        print(f"  FAIL Agent: 0 个")

    # 3. 模拟动作 > 0
    sim_logs = [l for l in logs if "个动作" in l.get("message", "")]
    total_actions = sum(1 for _ in sim_logs)  # approximate
    print(f"  INFO 模拟动作日志: {len(sim_logs)} 条")

    # 4. Prose > 0
    prose = report.get("prose", "")
    if len(prose) > 100:
        print(f"  PASS 散文: {len(prose)} 字")
    else:
        failures += 1
        print(f"  FAIL 散文: {len(prose)} 字")

    # 5. 状态
    status = data.get("status", "?")
    print(f"  INFO 状态: {status}")

    # 日志摘要
    print("\n  ── 关键日志 ──")
    for l in logs:
        msg = l.get("message", "")
        if any(k in msg for k in ("嵌入", "embed", "实体", "图谱", "agent", "Agent", "模拟完成", "散文完成")):
            print(f"    [{l.get('phase','?')}] {msg[:130]}")

    engine.close()
    import shutil
    shutil.rmtree(ws, ignore_errors=True)

    if failures:
        print(f"\n  RESULT: {failures} 项失败")
        return 1
    print("\n  RESULT: 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
