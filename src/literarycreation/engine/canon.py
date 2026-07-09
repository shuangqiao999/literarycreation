"""Canon Fact Ledger — 正典事实台账与逐章一致性守卫。

维护不可违背的既定事实（角色死亡、唯一麦高芬的状态与揭示、已揭露的真相），
并在每章写作前注入约束、写作后做规则校验，阻止：
  - 死而复生：已判定死亡的角色又以活人身份行动；
  - 麦高芬增殖：唯一核心物件被"重复发现/再次取得"；
  - 提前泄底：本应后续章节才揭晓的麦高芬提前被完整取得。

设计为纯规则、确定性实现（不依赖 LLM），可离线单测；随 story_state 持久化。
"""
from __future__ import annotations

import re
from typing import Any

# 表示"活人正在行动"的谓词（用于检测死者复活）
_LIVING_VERBS = (
    "说道", "说", "问道", "问", "答道", "笑道", "喝道", "低声",
    "站起", "起身", "走向", "走进", "走到", "拔出", "拔刀", "推开",
    "冲向", "转身", "抬手", "抬起", "点了点头", "摇头", "伸手", "握住",
    "迈步", "翻身", "策马", "喊道", "沉声", "开口",
)
# 表示"提及死者但非复活"的语境（回忆/尸体/悼念/梦境）
_DEATH_CONTEXT = (
    "尸体", "尸首", "遗体", "坟", "墓", "灵位", "牌位", "回忆", "想起",
    "梦", "遗物", "遗信", "遗书", "生前", "临终", "死前", "曾经", "当年",
    "如果", "假如", "仿佛", "好像", "画像", "遗言", "亡", "已死", "死去",
)
# 判定某段文字宣告死亡
_DEATH_MARKERS = ("死了", "断气", "毙命", "气绝", "殒命", "身亡", "咽了气", "没了气息")
# 首次"发现/取得"物件的谓词
_ACQUIRE_VERBS = ("发现", "找到", "取出", "取得", "拿到", "掘出", "挖出", "起获", "寻获")


class CanonLedger:
    """正典事实台账：登记既定事实并校验章节文本。"""

    def __init__(self) -> None:
        self.dead: dict[str, int] = {}            # 角色名 -> 判定死亡的章号
        self.macguffins: dict[str, dict] = {}     # 物件id -> {reveal_round, acquired, acquired_round}
        self.revealed: list[str] = []             # 已揭露的真相摘要

    # ── 构造 / 持久化 ──

    @classmethod
    def from_state(
        cls, story_state: dict[str, Any] | None, blueprint: dict[str, Any] | None = None
    ) -> CanonLedger:
        """从持久化的 story_state 恢复台账；首次则用 blueprint 播种麦高芬。"""
        ledger = cls()
        raw = (story_state or {}).get("canon")
        if isinstance(raw, dict):
            ledger.dead = {str(k): int(v) for k, v in (raw.get("dead") or {}).items()}
            for mid, m in (raw.get("macguffins") or {}).items():
                if isinstance(m, dict):
                    ledger.macguffins[str(mid)] = {
                        "reveal_round": int(m.get("reveal_round", 0) or 0),
                        "acquired": bool(m.get("acquired", False)),
                        "acquired_round": int(m.get("acquired_round", 0) or 0),
                    }
            ledger.revealed = [str(x) for x in (raw.get("revealed") or [])]
        # 用 blueprint 播种尚未登记的麦高芬
        if blueprint:
            for m in blueprint.get("macguffins") or []:
                mid = str(m.get("id", "") or "").strip()
                if mid and mid not in ledger.macguffins:
                    ledger.macguffins[mid] = {
                        "reveal_round": int(m.get("reveal_round", 0) or 0),
                        "acquired": False,
                        "acquired_round": 0,
                    }
            # 播种"预设既定死亡"（故事开始前已死的角色，如师父）→ chapter=0
            ef = blueprint.get("established_facts") or {}
            for d in (ef.get("deaths") or []):
                nm = str(d.get("name", "") or "").strip()
                if nm and nm not in ledger.dead:
                    ledger.dead[nm] = 0
        return ledger

    def to_dict(self) -> dict[str, Any]:
        return {
            "dead": dict(self.dead),
            "macguffins": {k: dict(v) for k, v in self.macguffins.items()},
            "revealed": list(self.revealed)[-30:],
        }

    def save_into(self, story_state: dict[str, Any]) -> None:
        story_state["canon"] = self.to_dict()

    # ── 写作前：约束注入 ──

    def build_constraint_text(self, current_round: int = 0) -> str:
        """构建注入 prose 提示的正典约束块。"""
        parts: list[str] = []
        if self.dead:
            names = "、".join(sorted(self.dead.keys()))
            parts.append(
                f"以下角色已在前文死亡，本章【不得】让其以活人身份说话或行动"
                f"（仅可出现在回忆、尸体、遗物或他人提及中）：{names}"
            )
            pre_dead = [n for n, r in self.dead.items() if r == 0]
            if pre_dead:
                parts.append(
                    "其中 " + "、".join(sorted(pre_dead)) + " 在故事开始前已死亡；"
                    "其遗言/遗信/遗物只能作为「死前所留」出现，严禁作为当前对话或活人行动"
                )
        for mid, m in self.macguffins.items():
            if m.get("acquired"):
                parts.append(
                    f"核心物件「{mid}」已在第{m.get('acquired_round')}章被取得，"
                    f"本章不得再次'发现/取得'它，只能延续其已在某人手中的既定事实"
                )
            elif m.get("reveal_round") and current_round < int(m["reveal_round"]):
                parts.append(
                    f"核心物件「{mid}」应在第{m['reveal_round']}章才被完整取得，"
                    f"本章只能侧写/接近，不得提前取得或揭晓其全部秘密"
                )
        if not parts:
            return ""
        return "【正典一致性约束（必须遵守）】\n" + "\n".join(f"- {p}" for p in parts)

    # ── 写作后：一致性校验 ──

    def validate(self, text: str, current_round: int = 0) -> list[str]:
        """返回本章与既定事实冲突的清单；空列表表示通过。"""
        conflicts: list[str] = []
        if not text:
            return conflicts
        sentences = re.split(r"[。！？\n]", text)

        # 1) 死者复活检测
        for name, _dead_round in self.dead.items():
            for s in sentences:
                if name not in s:
                    continue
                if any(ctx in s for ctx in _DEATH_CONTEXT):
                    continue  # 回忆/尸体/假设等语境，放行
                # name 之后紧跟活人谓词 → 视为复活
                idx = s.find(name)
                tail = s[idx + len(name): idx + len(name) + 8]
                if any(v in tail for v in _LIVING_VERBS):
                    conflicts.append(
                        f"已死亡角色「{name}」在本章以活人身份行动（“{s.strip()[:40]}”），"
                        f"请改为回忆/遗物/他人转述，或删除该情节"
                    )
                    break

        # 2) 麦高芬重复取得 / 提前取得
        for mid, m in self.macguffins.items():
            acquired_here = False
            for s in sentences:
                if mid not in s:
                    continue
                if any(v in s for v in _ACQUIRE_VERBS):
                    acquired_here = True
                    break
            if not acquired_here:
                continue
            if m.get("acquired"):
                conflicts.append(
                    f"核心物件「{mid}」已在第{m.get('acquired_round')}章被取得，"
                    f"本章不得再次'发现/取得'同一物件（麦高芬增殖）"
                )
            elif m.get("reveal_round") and current_round < int(m["reveal_round"]):
                conflicts.append(
                    f"核心物件「{mid}」被提前取得（计划在第{m['reveal_round']}章），"
                    f"本章应改为侧写/接近而非取得"
                )
        return conflicts

    # ── 写作后：登记既定事实 ──

    def establish_from_chapter(
        self,
        text: str,
        chapter_idx: int,
        snapshots: dict[str, Any] | None = None,
        alive_checker: Any = None,
    ) -> None:
        """从已采纳章节抽取新事实并登记。

        - snapshots + alive_checker：规则引擎判定出局的角色登记为死亡（权威来源）。
        - 文本死亡标记：作为补充启发式。
        - 麦高芬取得：按取得谓词登记 acquired。
        """
        # (a) 规则引擎判定的死亡（最权威）
        if snapshots and alive_checker is not None:
            for _eid, st in snapshots.items():
                name = st.get("name") if isinstance(st, dict) else getattr(st, "name", None)
                if not name or name in self.dead:
                    continue
                try:
                    if not alive_checker(st):
                        self.dead[name] = chapter_idx
                except Exception:
                    pass

        # (b) 文本死亡标记（补充）—— 仅登记已知角色名，避免误伤
        if text and self.macguffins is not None:
            known_names = set(self.dead.keys())
            # 从 snapshots 收集候选角色名
            if snapshots:
                for _eid, st in snapshots.items():
                    nm = st.get("name") if isinstance(st, dict) else getattr(st, "name", None)
                    if nm:
                        known_names.add(nm)
            for s in re.split(r"[。！？\n]", text):
                if not any(mk in s for mk in _DEATH_MARKERS):
                    continue
                if any(ctx in s for ctx in ("如果", "假如", "仿佛", "好像", "梦")):
                    continue
                for nm in known_names:
                    if nm and nm in s and nm not in self.dead:
                        self.dead[nm] = chapter_idx

        # (c) 麦高芬取得登记
        if text:
            for mid, m in self.macguffins.items():
                if m.get("acquired"):
                    continue
                for s in re.split(r"[。！？\n]", text):
                    if mid in s and any(v in s for v in _ACQUIRE_VERBS):
                        m["acquired"] = True
                        m["acquired_round"] = chapter_idx
                        break

    # ── 场景重复检测 ──

    @staticmethod
    def _scene_features(text: str) -> dict[str, Any]:
        """抽取场景特征：开场句指纹 + 动作动词集合 + 4字词集合（用于相似度比对）。"""
        head = (text or "")[:400]
        sentences = [s.strip() for s in re.split(r"[。！？\n]", head) if s.strip()]
        opening = "".join(sentences[:2])
        verbs = {v for v in _LIVING_VERBS + _ACQUIRE_VERBS if v in head}
        # 4字滑窗特征词（取开头段落，代表"场景骨架"）
        grams: set[str] = set()
        for seg in re.split(r"[，。；：、！？\n]", head):
            seg = seg.strip()
            for i in range(max(0, len(seg) - 3)):
                grams.add(seg[i:i + 4])
        return {"opening": opening, "verbs": verbs, "grams": grams}

    @staticmethod
    def _scene_similarity(a: dict[str, Any], b: dict[str, Any]) -> float:
        """两个场景特征的相似度（0-1）：4字词 Jaccard 为主，开场句完全相同则拔高。"""
        ga, gb = set(a.get("grams") or []), set(b.get("grams") or [])
        if not ga or not gb:
            jac = 0.0
        else:
            inter = len(ga & gb)
            union = len(ga | gb)
            jac = inter / union if union else 0.0
        if a.get("opening") and a.get("opening") == b.get("opening"):
            return max(jac, 0.9)
        return jac

    def detect_scene_repetition(self, text: str, chapter_idx: int,
                                story_state: dict[str, Any], threshold: float = 0.7) -> list[str]:
        """检测本章场景是否与历史章节高度相似（>threshold）。返回冲突描述。"""
        if not text:
            return []
        feats = self._scene_features(text)
        conflicts: list[str] = []
        for past in story_state.get("scene_features", []):
            sim = self._scene_similarity(feats, past)
            if sim >= threshold:
                conflicts.append(
                    f"本章场景与第{past.get('chapter')}章高度相似（相似度 {sim:.0%}），"
                    f"请改写为推进剧情的【全新场景】，不要重复开场/地点/动作")
        return conflicts

    def record_scene(self, text: str, chapter_idx: int, story_state: dict[str, Any]) -> None:
        """把本章场景特征存入 story_state（供后续章节比对）。集合转 list 以便 JSON 持久化。"""
        f = self._scene_features(text)
        story_state.setdefault("scene_features", []).append({
            "chapter": chapter_idx,
            "opening": f["opening"],
            "verbs": sorted(f["verbs"]),
            "grams": sorted(f["grams"])[:200],
        })
        # 上限保护
        story_state["scene_features"] = story_state["scene_features"][-40:]
