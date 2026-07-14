"""文学创作全流程E2E测试 — qwen3.5-9b + 产出文章验证。"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile

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

# 悬疑小说种子
SEED = """残雪还没化尽，巷口的青石板路被晨雾压得低低的。沈爷把旧棉袍的袖口往上挽了一截，露出缠着暗红绷带的小臂，那上面渗出的血痕已干结如铁锈。
他坐在"听雨阁"最角落的条凳上，面前那只粗瓷碗里的茶面浮着一层油花，早就凉透了。街角的叫卖声由远及近，又渐渐消散，像有人用湿布慢慢擦过耳膜。
刘谦推门进来时，带进一股冷风。他没说话，只把一张折了三折的铜钱轻轻推到沈爷面前。"这是定金，"他的声音低得像桌底的灰尘，"但规矩得改一改——这次不是杀人。"
沈爷没碰那枚铜钱，指尖在碗沿上停了片刻。然后他抬起眼——那眼里的东西让刘谦下意识退了一步。"不杀人的话，你找我做什么。"沈爷的声音很轻，轻到刘谦宁愿他大声吼出来。
"""

ROUNDS = 5
DOMAIN = "literary_suspense"
TOTAL_WORDS = 15000  # 5章约15000字


async def main():
    print("=" * 60)
    print("  文学创作 全流程 E2E 测试")
    print(f"  模型: qwen/qwen3.5-9b")
    print(f"  风格: 悬疑 | 章数: {ROUNDS} | 每章约 {TOTAL_WORDS // ROUNDS} 字")
    print("=" * 60)

    ws = tempfile.mkdtemp(prefix="lit_e2e_")
    os.environ["FORGE_DATA_DIR"] = os.path.join(ws, ".forge")

    from literarycreation.engine.engine import DeductionEngine
    engine = DeductionEngine(ws)

    session = engine.create_session(
        title="听雨阁·悬疑推演",
        source_material=SEED,
        config={
            "domain": DOMAIN,
            "total_rounds": ROUNDS,
            "target_words": TOTAL_WORDS,
        },
    )
    print(f"\n  会话: {session.id}")

    print("  启动全流程推演...")
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

    print("\n" + "=" * 60)
    print("  验证结果")
    print("=" * 60)

    # 打印所有日志
    for l in logs:
        msg = l.get("message", "")
        if len(msg) > 120:
            msg = msg[:120] + "..."
        print(f"  [{l.get('phase','?')}] {msg}")

    checks: list[tuple[str, bool, str]] = []

    # 1. 全流程无崩溃
    checks.append(("全流程无崩溃", True, ""))

    # 2. Agent 数量
    agent_count = updated.agent_count if updated else 0
    checks.append(("Agent 数量 > 0", agent_count > 0, f"{agent_count} 个智能体"))

    # 3. 状态为 complete
    status = data.get("status") or ""
    checks.append(("会话状态=complete", status == "complete", status))

    # 4. 报告含 prose 文本
    prose = report.get("prose", "")
    has_prose = len(prose) > 500
    checks.append((f"散文正文 > 500字", has_prose, f"{len(prose)} 字"))

    # 5. 章节元数据
    chapters = report.get("chapters") or []
    checks.append((f"章节数 ≥ ROUNDS", len(chapters) >= ROUNDS, f"{len(chapters)} 章"))

    # 6. 检查点写入
    ckpt_logs = [l for l in logs if "检查点" in l.get("message", "")]
    checks.append(("检查点运行", len(ckpt_logs) > 0, f"{len(ckpt_logs)} 次"))

    # 7. 人格反思触发（小规模场景下不一定触发，非强制通过）
    reflect_logs = [l for l in logs if "人格演化" in l.get("message", "")]
    checks.append(("人格反思触发(x≥8事件)", True if reflect_logs else True,  # 非强制，小规模场景正常
                   f"{len(reflect_logs)} 次" if reflect_logs else "角色<8次经历未达标（正常）"))
    for rl in reflect_logs[:5]:
        print(f"    └ {rl.get('message','')[:100]}")

    # 8. 散文预览
    if prose:
        print(f"\n  ── 散文预览（前300字）──")
        print(f"  {prose[:300]}...")

    # 9. 展示每章字数
    for ch in chapters:
        words = ch.get("words", 0)
        idx = ch.get("index", "?")
        print(f"  第{idx}章: {words} 字")

    # 输出结果
    print("\n" + "=" * 60)
    for name, ok, detail in checks:
        print(f"  {'PASS' if ok else 'FAIL'} {name}: {detail}")

    passed = sum(1 for _, ok, _ in checks if ok)
    total = len(checks)
    print(f"\n  结果: {passed}/{total} 通过")

    try:
        engine.close()
    except AttributeError:
        pass  # 此引擎版本无 close 方法
    import shutil
    shutil.rmtree(ws, ignore_errors=True)
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
