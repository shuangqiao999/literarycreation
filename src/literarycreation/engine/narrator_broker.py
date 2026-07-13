"""叙述者声音代理 — 一次性生成叙述者人格，持续注入每章 prompt。"""
from __future__ import annotations

from typing import Any

from ._utils import extract_text

_STYLE_DEFAULTS: dict[str, dict[str, Any]] = {
    "悬疑": {"距离": "中距——比主角远半步，能看到她的动作但不说破她的心思",
             "节奏": "短促——多用句号，段落不超过3句",
             "手法": "重客观描写、轻内心独白；善用环境中的反常细节暗示危险",
             "禁忌词": "突然、显然、原来如此、果然"},
    "现实主义": {"距离": "贴身——站在主角肩膀后两步，时近时远",
                "节奏": "自然——长短句交替，跟随角色的呼吸节奏",
                "手法": "重日常生活细节、轻宏大修辞；用具体物件和动作传达情感",
                "禁忌词": "非常、极其、无比、难以言喻"},
    "史诗": {"距离": "远景——站在时代的高度俯视，偶尔拉近到个人体验",
             "节奏": "绵长——多用排比和长句，营造命运的重量感",
             "手法": "重时代氛围和历史纵深；把个人命运放在更大的背景中映照",
             "禁忌词": "有点、稍微、大概、差不多"},
    "浪漫主义": {"距离": "热忱——叙述者对人物的情感抱有同情和共鸣",
                "节奏": "情绪化——高潮处可以加速、用短句制造心跳感",
                "手法": "重情感投射和意象渲染；自然描写与人物的心境共振",
                "禁忌词": "客观地说、从数据来看"},
    "宫廷剧": {"距离": "冷眼——站在权力博弈的外部，偶尔透过角色的微表情泄露内心",
              "节奏": "克制——对话之间有停顿，动作之间有潜台词",
              "手法": "重权力关系和等级差异；每一句话背后的政治意味重于字面意义",
              "禁忌词": "真诚地、坦率地说、毫不掩饰"},
}


class NarratorRegistry:
    """叙述者声音一次性生成，持久化注入每章 prompt。"""

    def __init__(self, style: str = "现实主义"):
        self._style = style
        self._voice: dict[str, Any] = {}

    async def generate(self, client, seed_text: str) -> None:
        """用小型 LLM 调用从种子文本提取叙述者特征。失败回退风格默认值。"""
        from literarycreation.core.llm_client import Message
        prompt = (
            f"分析以下小说片段的叙述者声音特征。\n\n"
            f"【片段】\n{seed_text[:1500]}\n\n"
            f"输出JSON：\n"
            f'{{"距离":"贴身/中距/远景/冷眼/热忱","节奏":"短促/自然/绵长/情绪化/克制",'
            f'"手法":"叙述者最常用的技巧","禁忌词":"叙述者永远不会用的词"}}'
        )
        try:
            resp = await client.chat(
                [Message(role="user", content=prompt)],
                system="你是文学批评家，分析叙述者声音。只输出 JSON。",
                temperature=0.2,
                max_tokens=120,
            )
            import json, re
            text = extract_text(resp).strip()
            m = re.search(r"\{[\s\S]*\}", text)
            if m:
                parsed = json.loads(m.group(0))
                if isinstance(parsed, dict):
                    self._voice = parsed
                    return
        except Exception:
            pass
        # 回退风格默认值
        self._voice = dict(_STYLE_DEFAULTS.get(self._style, _STYLE_DEFAULTS["现实主义"]))

    def build_voice_block(self) -> str:
        """构建注入每章 CHAPTER_PROMPT 的叙述者约束块。"""
        if not self._voice:
            self._voice = dict(_STYLE_DEFAULTS.get(self._style, _STYLE_DEFAULTS["现实主义"]))
        v = self._voice
        parts = ["【叙述者人格 — 非角色声音，是讲故事的人的固定风格，全章不变】"]
        if v.get("距离"):
            parts.append(f"- 视角距离：{v['距离']}")
        if v.get("节奏"):
            parts.append(f"- 句子节奏：{v['节奏']}")
        if v.get("手法"):
            parts.append(f"- 惯用手法：{v['手法']}")
        if v.get("禁忌词"):
            parts.append(f"- 禁用词汇：{v['禁忌词']}")
        return "\n".join(parts)
