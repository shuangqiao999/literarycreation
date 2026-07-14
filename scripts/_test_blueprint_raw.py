"""蓝图原始回复测试 — 直接向 LLM 发请求，查看为何解析失败。"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

os.environ["FORGE_PROVIDER"] = "lmstudio"
os.environ["FORGE_LLM_BASE"] = "http://127.0.0.1:1234/v1"
os.environ["FORGE_LLM_MODEL"] = "qwen/qwen3.5-9b"

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

SEED = """残雪还没化尽，巷口的青石板路被晨雾压得低低的。沈爷把旧棉袍的袖口往上挽了一截，露出缠着暗红绷带的小臂，那上面渗出的血痕已干结如铁锈。
他坐在"听雨阁"最角落的条凳上，面前那只粗瓷碗里的茶面浮着一层油花，早就凉透了。街角的叫卖声由远及近，又渐渐消散，像有人用湿布慢慢擦过耳膜。
刘谦推门进来时，带进一股冷风。他没说话，只把一张折了三折的铜钱轻轻推到沈爷面前。"这是定金，"他的声音低得像桌底的灰尘，"但规矩得改一改——这次不是杀人。"
沈爷没碰那枚铜钱，指尖在碗沿上停了片刻。然后他抬起眼——那眼里的东西让刘谦下意识退了一步。"不杀人的话，你找我做什么。"沈爷的声音很轻，轻到刘谦宁愿他大声吼出来。
"""


async def main():
    from literarycreation.core.llm_client import DeductionLLMClient, Message
    from literarycreation.engine._utils import extract_text

    client = DeductionLLMClient()
    print(f"模型: qwen/qwen3.5-9b | 种子: {len(SEED)} 字")

    # 复刻 generate_blueprint 的完整 prompt
    from literarycreation.engine.blueprint import _PROMPT, _SIMPLE_PROMPT, _get_metrics_hint, _parse_blueprint, normalize_blueprint
    import copy

    domain = "literary_suspense"
    total_rounds = 10

    style_directive = "目标风格：「悬疑」。即使素材原生风格与之不同，也必须按「悬疑」风格构建大纲与写作（后续章节会逐步向该风格迁移）。"
    domain_metrics = _get_metrics_hint(domain)

    prompt = _PROMPT.format(
        style_directive=style_directive,
        chapters=total_rounds,
        target_words="不限",
        domain_metrics=domain_metrics,
        source=SEED[:8000],
    )

    print(f"Prompt 长度: {len(prompt)} 字符")

    resp = await client.chat(
        [Message(role="user", content=prompt)],
        system="你是小说结构编辑，只输出规范 JSON 大纲。",
        temperature=0.4,
        max_tokens=16384,
    )
    raw = extract_text(resp).strip()
    print(f"回复长度: {len(raw)} 字符")
    print(f"以 '}}' 结尾: {raw.endswith('}')}")
    print(f"包含 '```' : {'```' in raw}")
    print(f"包含 'key_events': {'key_events' in raw}")
    print(f"包含 'logline': {'logline' in raw}")

    # 保存原始回复
    from pathlib import Path
    out = Path("data") / "blueprint_raw_test.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(raw, encoding="utf-8")
    print(f"原始回复已保存: {out}")

    # 逐层尝试解析
    from literarycreation.engine.blueprint import _parse_blueprint

    # Step 1: 剥离 markdown
    cleaned_raw = raw
    if "```" in cleaned_raw:
        cleaned_raw = re.sub(r'```(?:json\s*)?', '', cleaned_raw)
        cleaned_raw = cleaned_raw.replace('```', '').strip()
        print("已剥离 markdown 代码块标记")

    # Step 2: JSON 提取
    match = re.search(r"\{[\s\S]*\}", cleaned_raw)
    if not match:
        print("ERROR: 未找到 JSON 对象 ({{ ... }})")
    else:
        json_str = match.group(0)
        print(f"提取到 JSON: {len(json_str)} 字符")

        # Step 3: 尝试解析
        parsed = None
        for attempt, label in [
            (lambda s: json.loads(s), "直接解析"),
            (lambda s: json.loads(re.sub(r",\s*([}\]])", r"\1", s)), "尾逗号修复"),
            (lambda s: json.loads(re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', s)), "控制字符清理"),
        ]:
            try:
                parsed = attempt(json_str)
                print(f"  [OK] {label}")
                break
            except json.JSONDecodeError as e:
                print(f"  [FAIL] {label}: {e}")

        if parsed is None:
            # 最后一个尝试：截断到最后一个完整的 }
            last_brace = json_str.rfind("}")
            truncated = json_str[: last_brace + 1]
            try:
                parsed = json.loads(truncated)
                print(f"  [OK] 截断到最后一个}}}} ({len(json_str) - len(truncated)} 字符被移除)")
            except json.JSONDecodeError as e:
                print(f"  [FAIL] 截断后仍失败: {e}")

        if parsed and isinstance(parsed, dict):
            # 检查关键字段
            print(f"\n解析成功！字段分析:")
            print(f"  logline: {'✓' if parsed.get('logline') else '✗ 缺失'}")
            ke = parsed.get("key_events") or []
            print(f"  key_events: {len(ke)} 条")
            for e in ke[:3]:
                print(f"    - R{e.get('round','?')} {e.get('event', e.get('description','(无描述)'))[:60]}")
            print(f"  characters: {len(parsed.get('characters') or [])} 个")
            print(f"  themes: {len(parsed.get('themes') or [])} 条")
            print(f"  subplots: {len(parsed.get('subplots') or [])} 条")
            print(f"  knowledge_gaps: {len(parsed.get('knowledge_gaps') or [])} 条")

            # 再跑 normalize_blueprint
            from literarycreation.engine.blueprint import normalize_blueprint
            norm = normalize_blueprint(parsed, total_rounds)
            if norm is None:
                print("\n[根因] normalize_blueprint 返回 None → key_events 为空或字段名不匹配")
            else:
                print(f"\nnormalize_blueprint 成功: {len(norm.get('key_events',[]))} 个事件")

    print("\n=== 原始回复前 2000 字 ===")
    print(raw[:2000])

    # ── 简化 prompt 重试 ──
    print("\n\n=== 简化 prompt 重试 ===")
    simple_prompt = _SIMPLE_PROMPT.format(
        style_directive=style_directive,
        chapters=total_rounds,
        source=SEED[:8000],
    )
    print(f"Prompt 长度: {len(simple_prompt)} 字符")
    resp2 = await client.chat(
        [Message(role="user", content=simple_prompt)],
        system="你是小说结构编辑，只输出 JSON。",
        temperature=0.4,
        max_tokens=16384,
    )
    raw2 = extract_text(resp2).strip()
    print(f"回复长度: {len(raw2)} 字符 | 以'}}'结尾: {raw2.endswith('}')} | 含key_events: {'key_events' in raw2}")

    bp = _parse_blueprint(raw2, total_rounds)
    if bp:
        print(f"简化 prompt 成功: {len(bp.get('key_events',[]))} key_events, "
              f"{len(bp.get('characters',[]))} characters, "
              f"logline={bp.get('logline','?')[:60]}")
        for e in bp.get("key_events", [])[:5]:
            print(f"  R{e.get('round','?')} [{e.get('level','?')}] {e.get('event','?')[:60]}")
    else:
        print("简化 prompt 仍失败——查看 data/blueprint_raw_test.txt")
        out2 = Path("data") / "blueprint_simple_raw.txt"
        out2.write_text(raw2, encoding="utf-8")
        print(f"已保存: {out2}")
        print(f"\n前 500 字:\n{raw2[:500]}")


asyncio.run(main())
