"""Reasoner — LLM决策推理，叙事导向。

角色基于人格、记忆、弧光自然推理行动，无预定义动作约束。
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


def _metrics_to_narrative(state: Any) -> str:
    """将数值指标转换为叙事语言，优先合成复合情感，未命中回退单指标变体。"""
    import random as _rnd
    m = state.metrics if hasattr(state, "metrics") else {}
    lines = [f"{state.name if hasattr(state, 'name') else '角色'}当前的内心状态："]

    # 优先尝试复合情感合成
    from .emotional_engine import EmotionalComposer
    compound = EmotionalComposer().compose(m)
    if compound:
        lines.append(f"- 【深层的矛盾】{compound}")
        return "\n".join(lines)

    trust = m.get("trust", 50)
    if trust < 20:
        lines.append(_rnd.choice([
            "- 你对身边的人几乎完全失去了信任——不是愤怒，是疲惫的失望",
            "- 你发现自己在计算每一句话的代价——信任已经变成一种你买不起的奢侈品",
            "- 你不再期待任何人的真诚。这让你安全，但你也知道——你正在钝化",
        ]))
    elif trust < 40:
        lines.append(_rnd.choice([
            "- 你对大多数人心存戒备。你会在对话后反复推敲对方的弦外之音",
            "- 信任对你来说是一张反复皱折的纸——还能写字，但已经不平了",
            "- 你学会了把重要的事留在心里。但有时候，心里太满了，你找不到出口",
        ]))
    elif trust > 80:
        lines.append(_rnd.choice([
            "- 你对周围的人抱有深厚的信任——这既是力量，也是软肋",
            "- 你相信身边的人不会辜负你。这份确信让你敢于做危险的决定",
            "- 你感受到了久违的信任感——但内心深处有个声音在问：这会不会是再次失望的前奏？",
        ]))

    tension = m.get("tension", 20)
    if tension > 70:
        lines.append(_rnd.choice([
            "- 局势已到燃点。你能感觉到——下一次对视、下一个字——就会引爆一切",
            "- 空气中的紧张像拉满的弓弦。你每做一个动作都在想：这一次会不会崩？",
            "- 所有人都在等一个信号。而你隐约觉得——发出这个信号的可能是你",
        ]))
    elif tension > 40:
        lines.append(_rnd.choice([
            "- 空气中弥漫着不安。人们在微笑，但眼睛不笑",
            "- 你感到一种深水下的涌流。表面上风平浪静，暗处有东西在动",
            "- 紧张感像低频率的耳鸣——不是无法忍受，但让你无法真正放松",
        ]))
    elif tension < 15:
        lines.append(_rnd.choice([
            "- 眼下局势相对平静，但你知道——暴风雨前最危险的不是风，是寂静",
            "- 一切似乎回到了轨道上。但越是平静，你越觉得有什么被忽略了",
            "- 这是难得的喘息时刻。你几乎不敢享受它——怕一放松就会错过什么",
        ]))

    affection = m.get("affection", 40)
    if affection > 70:
        lines.append(_rnd.choice([
            "- 你心里有个人的影子挥之不去。你试图把它压回原处——但它总是回来",
            "- 情感在你体内积累到无法忽视的程度。你在想——如果不说出口，会不会后悔？",
            "- 你发现自己在做一些没有逻辑的事：绕路经过某个位置、在嘈杂中分辨某个声音",
        ]))
    elif affection < 25:
        lines.append(_rnd.choice([
            "- 你的心正在降温。曾经让你温暖的东西——现在只是灰烬",
            "- 你发现自己开始用理智代替情感——不是冷酷，是自我保护",
            "- 你与周围的人之间隔着一层冰——不是恨，是无力再投入",
        ]))

    power = m.get("power", 40)
    if power > 70:
        lines.append(_rnd.choice([
            "- 你手中握有相当的影响力。但你知道——权力是借来的，总有归还的一天",
            "- 你的每一个决定都在改变他人的命运。这份重量，你从未真正习惯",
            "- 你正在变成你曾经不信任的那种人——有权有势、也有代价",
        ]))
    elif power < 25:
        lines.append(_rnd.choice([
            "- 你感到自己的力量正在从指缝间流失——不是一下子，是慢慢地",
            "- 当你的名字不再被提起的时候，你意识到——曾经拥有的一切，只是暂时的保管",
            "- 你正在被边缘化。不是突然的斩断，更像是一种缓慢的窒息",
        ]))

    mystery = m.get("mystery", 30)
    if mystery > 70:
        lines.append(_rnd.choice([
            "- 真相藏在多层谎言的最深处——每当你以为自己接近了，就会发现还有一层",
            "- 你收集的线索越来越多，但它们像拼图——碎片的形状一直在变",
            "- 你隐约感到有人在掩盖什么。但奇怪的是——越掩盖，痕迹越明显",
        ]))
    elif mystery < 20:
        lines.append(_rnd.choice([
            "- 碎片已拼成完整的图案。你终于看见了真相的全貌——它比你想的更大、更冷",
            "- 一切开始说得通了。这种「终于懂了」的感觉，既是解脱，也是新的负担",
            "- 你握住了最后一枚钥匙。现在的问题不是「真相是什么」，而是「要不要打开那扇门」",
        ]))

    fatigue = m.get("fatigue", 10)
    if fatigue > 70:
        lines.append(_rnd.choice([
            "- 你已经筋疲力尽。不是身体的疲惫——是灵魂的重量超过了承载力",
            "- 每一步都像在水底行走。你还能动，但不知道为了什么",
            "- 你的身体还在运转，但你的心已经关掉了一些东西——为了活下来",
        ]))
    elif fatigue > 40:
        lines.append(_rnd.choice([
            "- 疲惫开始侵蚀你的判断力。你发现自己在做决定之前需要更长的停顿",
            "- 你渴望一个可以不用思考的夜晚——一觉到天亮，什么都不用想",
            "- 你能感觉到体力的消耗在累积——不是致命伤，但每一刀都在削弱你",
        ]))
    elif fatigue < 15:
        lines.append(_rnd.choice([
            "- 你感到精神饱满。这种状态珍贵而短暂——你知道不能浪费",
            "- 今天你感觉不同——像是久雨后第一个晴日。你准备好了",
            "- 你醒过来的时候发现：身体没有抗议。这是一天中最好的礼物",
        ]))

    return "\n".join(lines)


class StrategicReasoner:
    """LLM-based decision maker for literary creation.

    Single entry point reason(mode=...):
      - mode="freeform": Free writing — agents decide freely
      - mode="blueline": Blueprint execution — agents follow scheduled events
    """

    def __init__(
        self,
        chat_fn: Any = None,
        preprocessor: Any = None,
        immutable_goals: list[str] | None = None,
    ) -> None:
        self._chat_fn = chat_fn
        self._preprocessor = preprocessor
        self._immutable_goals = immutable_goals or []

    async def reason(
        self,
        *,
        agent: Any,
        round_number: int,
        state: Any,
        mode: str = "freeform",
        event_mandate: str = "",
        correction_level: str = "none",
        other_context: str = "",
        relationship_context: str = "",
        static_knowledge: str = "",
        dynamic_memory: str = "",
        recent_events: str = "",
        env_context: str = "",
        user_cmd: str = "",
        self_memory: str = "",
        narrative_memory: str = "",
        knowledge_gaps: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """统一决策入口：角色基于人格、记忆、弧光自然推理行动。

        mode="freeform" 自由创作；mode="blueline" 蓝图执行（注入本轮必须推动的事件与偏离校正）。
        """
        goals = agent.goals if hasattr(agent, "goals") else []
        goals_txt = "\n".join(f"- {g}" for g in goals) if goals else "（无具体目标）"
        narrative_state = _metrics_to_narrative(state)

        if mode == "blueline":
            header = (
                f"你是「{agent.name}」，这是一个文学创作中的关键场景。\n\n"
                f"## 你的人格\n{agent.persona or '（无）'}\n\n"
            )
            extra = getattr(agent, "system_prompt_extra", "")
            if extra:
                header += f"## 你的行为准则（从经历中习得）\n{extra}\n\n"
            header += (
                f"## 你的目标\n{goals_txt}\n\n"
                f"## 你当前的状态\n{narrative_state}\n"
            )
        else:
            imm = "；".join(self._immutable_goals) if self._immutable_goals else "无"
            header = (
                f"你是「{agent.name}」。\n\n"
                f"## 你的人格\n{agent.persona or '（无）'}\n\n"
            )
            extra = getattr(agent, "system_prompt_extra", "")
            if extra:
                header += f"## 你的行为准则（从经历中习得）\n{extra}\n\n"
            header += (
                f"## 你的目标\n{goals_txt}\n"
                f"（不可动摇的初衷：{imm}）\n\n"
                f"## 你当前的状态\n{narrative_state}\n"
            )

        # 叙事记忆（亲身经历，优先于碎片检索）
        if narrative_memory:
            header += f"\n## 你的亲身经历（叙事记忆）\n{narrative_memory}\n"
        if self_memory:
            header += f"\n## 你最近做过的事\n{self_memory}\n"

        # blueline 专属：本轮必须推动的剧情 + 偏离校正
        if mode == "blueline":
            if event_mandate:
                header += f"\n## 【本轮必须推动的剧情】\n{event_mandate}\n"
            correction_directive = ""
            if correction_level == "soft":
                correction_directive = ("【内心之声】你隐约感到自己正在偏离预设的路径。"
                                       "继续向前——还是回头？这道选择题本身，就是你的故事。")
            elif correction_level == "strong":
                correction_directive = ("【内心之声（强）】走得越远，回去越难。"
                                       "但如果偏离本身就是命运的安排——你愿意付出什么代价来坚持自己的选择？")
            elif correction_level == "event_inject":
                correction_directive = (
                    "【命运的齿轮】不是所有偏离都能回头。新的力量已经介入，"
                    "无论你是否准备好——世界已经改变了。请对此做出反应。\n"
                    f"外部事件：{event_mandate or '重大事件发生'}"
                )
            if correction_directive:
                header += f"\n## {correction_directive}\n"
        elif user_cmd:
            header += f"\n## 外部干预指令（最高优先级）\n{user_cmd}\n"

        if other_context:
            header += f"\n## 其他角色状态\n{other_context}\n"
        if relationship_context:
            header += f"\n## 你的关系网络\n{relationship_context}\n"
        if dynamic_memory:
            header += f"\n## 相关的过往事件\n{dynamic_memory}\n"
        if recent_events and mode != "blueline":
            header += f"\n## 近期局势\n{recent_events}\n"
        if static_knowledge:
            header += f"\n## 原著背景参考\n{static_knowledge}\n"
        if env_context:
            header += f"\n## 环境\n{env_context}\n"

        # 信息差：角色当前相信但可能是错的（戏剧反讽）
        if knowledge_gaps:
            for gap in knowledge_gaps:
                if (isinstance(gap, dict) and
                    gap.get("character") == agent.name and
                    round_number < int(gap.get("before_round", 999))):
                    believes = gap.get("believes", "")
                    if believes:
                        header += (
                            f"\n## 你目前的认知\n"
                            f"你相信：{believes}。\n"
                            f"但细节之间似乎有些许裂缝——你隐约感到事情可能不是表面那样。\n"
                        )
                    break  # 只注入一条信息差

        if mode == "blueline":
            header += (
                f"\n请以{agent.name}的身份，自由演绎本轮的剧情。"
                "你可以做任何符合角色性格的选择，不需要局限于预定义的动作类型。\n\n"
                + _FREE_SPEC
            )
            system = "你是文学创作中的角色。基于你的人格、记忆和处境，自由演绎剧情。只输出 JSON。"
        else:
            header += (
                f"\n现在，请以{agent.name}的身份，基于以上所有信息，决定你下一步的行动。\n"
                "你可以做任何符合你性格和处境的行动，不需要局限于预定义的类型。\n"
                "冲突和意外是故事的燃料——不要害怕做出大胆的选择。\n\n"
                + _FREE_SPEC
            )
            system = "你是一位文学创作中的角色。基于你的人格、记忆和处境，自由决定下一步行动。只输出 JSON。"

        return await self._call_llm(header, system=system)

    async def _call_llm(self, prompt: str, system: str) -> dict[str, Any]:
        from literarycreation.core.llm_client import DeductionLLMClient, Message

        if self._chat_fn is not None:
            try:
                resp = await self._chat_fn(
                    messages=[Message(role="user", content=prompt)],
                    system=system, temperature=0.85,
                )
                return _parse_json(resp.text)
            except Exception as e:
                logger.warning(f"[Reasoner] LLM failed: {e}")
                return {}

        client = DeductionLLMClient()
        resp = await client.chat(
            [Message(role="user", content=prompt)],
            system=system, temperature=0.7,
        )
        return _parse_json(resp.text)


def _parse_json(raw: str) -> dict[str, Any]:
    match = re.search(r"\{[\s\S]*\}", raw)
    if not match:
        return {}
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return {}


_FREE_SPEC = """## 输出 JSON（仅 JSON，无解释）
```json
{
  "action_type": "你的具体行动描述（自由文本，如'潜入档案室寻找证据'、'向陆远坦白自己对师父之死的怀疑'）",
  "target": "行动的涉及对象或留空",
  "intensity": 0.0到1.0,
  "rationale": "20-50字理由",
  "inner_monologue": "30-80字内心独白，以角色第一人称写出当下最真实、最隐秘的想法",
  "scene_moment": "50-120字叙事片段，用第三人称写出本行动的关键瞬间（动作+对白+氛围）"
}
```"""
