"""Smoke test: 蓝图规范化 + 正典守卫 + POV/揭示提示（离线、无需 LLM）。"""
from literarycreation.engine.blueprint import normalize_blueprint
from literarycreation.engine.canon import CanonLedger
from literarycreation.engine.prose_renderer import (
    build_pov_text,
    build_reveal_text,
    get_technique,
    pov_allows_switch,
)

# ── 1. blueprint 规范化 ──
raw = {
    "logline": "捕快追查师父之死",
    "pov": {"mode": "single", "anchor": "沈彻"},
    "macguffins": [
        {"id": "账册", "desc": "记录罪证", "reveal_round": 9, "states": ["下落不明", "被取得"]},
        {"id": "多余物件", "reveal_round": 3},  # 应被裁剪（至多 1）
    ],
    "reveal_schedule": [
        {"round": 3, "reveals": "手法"},
        {"round": 1, "reveals": "是什么"},
    ],
    "characters": [{"name": "沈彻", "persona": "冷静", "arc": "从被动到主动",
                    "initial_state": {"trust": 50}, "final_state": {"trust": 80}}],
    "chapters": [{"round": 1, "goal": "接手案子", "solves": "", "hook": "遗信"}],
    "key_events": [
        {"round": 99, "event": "高潮对决", "level": "hard"},   # round 越界 → 裁剪到 10
        {"round": 1, "event": "发现遗信", "level": "soft"},
    ],
}
bp = normalize_blueprint(raw, total_rounds=10)
assert bp is not None
assert bp["pov"] == {"mode": "single", "anchor": "沈彻"}
assert len(bp["macguffins"]) == 1 and bp["macguffins"][0]["id"] == "账册"
assert [e["round"] for e in bp["reveal_schedule"]] == [1, 3], "reveal 应按轮次排序"
assert [e["round"] for e in bp["key_events"]] == [1, 10], "key_events 排序+越界裁剪"
print("blueprint normalize OK")

# 无 key_events → 非法 → None
assert normalize_blueprint({"logline": "x"}, 10) is None
print("blueprint reject-empty OK")


# ── 2. 正典守卫 ──
story_state: dict = {}
canon = CanonLedger.from_state(story_state, blueprint=bp)
assert "账册" in canon.macguffins and canon.macguffins["账册"]["reveal_round"] == 9
print("canon seed macguffin OK")

# 提前取得麦高芬（第 5 章取得，计划第 9 章）→ 冲突
premature = "沈彻在密室里找到了账册，翻开一看。"
conf = canon.validate(premature, current_round=5)
assert any("账册" in c for c in conf), f"应检测提前取得: {conf}"
print("canon premature-acquire OK")

# 登记死亡（规则引擎判定出局）
snapshots = {"e1": {"name": "韩传令使", "metrics": {"life": 0}}}
def alive_checker(st):
    return st.get("metrics", {}).get("life", 100) > 0
canon.establish_from_chapter("韩传令使中了一刀。", 3, snapshots, alive_checker)
assert "韩传令使" in canon.dead, "应登记死亡"
print("canon establish-death OK")

# 死者复活 → 冲突
resurrect = "韩传令使站起身，冷冷说道：你们都得死。"
conf2 = canon.validate(resurrect, current_round=4)
assert any("韩传令使" in c for c in conf2), f"应检测死者复活: {conf2}"
print("canon resurrection-detect OK")

# 回忆语境不误报
memory = "沈彻想起韩传令使生前说道的那句话。"
conf3 = canon.validate(memory, current_round=4)
assert not any("韩传令使" in c for c in conf3), f"回忆不应误报: {conf3}"
print("canon memory-context OK")

# 麦高芬取得后再次发现 → 冲突
canon.establish_from_chapter("沈彻终于取得了账册。", 9, {}, None)
assert canon.macguffins["账册"]["acquired"] is True
conf4 = canon.validate("他又一次发现了账册。", current_round=10)
assert any("账册" in c for c in conf4), f"应检测重复取得: {conf4}"
print("canon re-acquire OK")

# 约束文本包含死者与麦高芬
ctext = canon.build_constraint_text(current_round=10)
assert "韩传令使" in ctext and "账册" in ctext
canon.save_into(story_state)
assert "canon" in story_state
# 往返持久化
canon2 = CanonLedger.from_state(story_state, blueprint=bp)
assert "韩传令使" in canon2.dead and canon2.macguffins["账册"]["acquired"] is True
print("canon persist round-trip OK")


# ── 3. POV / 揭示 / 技巧 ──
assert "沈彻" in build_pov_text(bp)
assert pov_allows_switch(bp) is False, "单视角默认不允许切换"
assert pov_allows_switch({"pov": {"mode": "multi"}}) is True
assert "手法" in build_reveal_text(bp, 3)
assert build_reveal_text(bp, 7) == ""  # 无该轮计划
tech = get_technique(3, 10, allow_pov_switch=False)
assert "视角约束" in tech, "单视角应注入视角约束"
tech_multi = get_technique(3, 20, allow_pov_switch=True)
assert "视角约束" not in tech_multi
print("pov/reveal/technique OK")

print("\nALL BLUEPRINT+CANON SMOKE TESTS PASSED")
