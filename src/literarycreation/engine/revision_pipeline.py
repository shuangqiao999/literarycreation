"""修订流水线 — 全章生成完毕后，对被读者标记的章节做编辑增强。"""
from __future__ import annotations

import re as _re
from typing import Any

from ._utils import extract_text

_REVISE_PROMPT = """你是一位文学编辑。下面是小说第{idx}章的初稿。进行编辑润色，不要重写：

1. 修复三类缺陷：
   - 把 "他感到愤怒""她很悲伤""他非常紧张" 类抽象情感陈述改为具体的感官细节和动作
   - 把平淡的对话改为有潜台词的对话——角色说的和角色想的不是同一件事
   - 把超过 150 字的连续内心独白精简到约 80 字，保留核心冲突，删掉重复的自问自答
2. 删除注水：重复出现的场景回放、逐字或近似重复的段落/独白、同义反复的修饰，只保留首次最有力的一处。允许因此明显缩短篇幅——凝练优于长度
3. 如果全章节奏过于平坦（连续多段都是相同的句长），在一处插入外部节奏干扰——一个声音、一个动作、一个人的突然介入
4. 如果章尾无钩子——加一句话让它悬而未决。一个好钩子通常满足：一个问题（但不给出答案）、一个意象（打开而非收束）、一个不可逆的决定（刚做出就后悔）
5. 不要改变情节走向、不要删除已建立的关键信息
6. 如果本章本身已充分满足上述要求——输出原文即可

【初稿】
{original}

【修订版 — 只输出全文，不要解释你改了什么】"""


# 终章修订提示词
_REVISE_FINAL_PROMPT = """你是一位文学编辑。这是小说的第{idx}章（最终章）的初稿。进行编辑润色，不要重写：

1. 修复三类缺陷：
   - 抽象情感陈述 → 具体感官细节
   - 平淡对话 → 有潜台词
   - 过长内心独白 → 精简
2. 最终章专属：检查最后一句是否有余韵——它应该让读者停在那一行。如果最后一句是功能性的（"他关上门离开了"），改为意象性收尾（"门外，雪已经开始下了。他没有回头。"）
3. 最终章专属：确保至少一个角色的弧光在本章完成闭环——不是"从此幸福"，而是"变成了最初的那个自己的另一个版本"
4. 最终章专属：至少留一处"未说的"——一个角色想到了某个问题但没有说出来。这是读者合上书后还会继续想的事情
5. 不要改变情节走向、不要删除已建立的关键信息
6. 如果本章本身已充分满足上述要求——输出原文即可

【初稿】
{original}

【修订版 — 只输出全文，不要解释你改了什么】"""


class RevisionPipeline:
    """编辑增强流水线。第二章生成后不再碰第一章。修订在全部首轮渲染完成后批量执行。"""

    def __init__(self, narrator_voice_block: str = ""):
        self._voice = narrator_voice_block
        self._revised: set[int] = set()

    def should_revise(self, reader_feedback: dict[str, Any] | None,
                      chapter_weight: float = 0.0) -> bool:
        """判断本章是否值得修订。"""
        if not reader_feedback:
            return chapter_weight < 0.05  # 极低情节重量应修订
        confusion = int(reader_feedback.get("confusion", 0) or 0)
        boredom = int(reader_feedback.get("boredom", 0) or 0)
        # 任何一项 > 40 表示读者体验不佳
        return confusion > 40 or boredom > 40 or chapter_weight < 0.05

    async def revise(self, client, chapter_idx: int, chapter_text: str,
                     reader_feedback: dict[str, Any] | None,
                     chapter_weight: float = 0.0) -> tuple[str, list[str]]:
        """修订一章。返回 (revised_text, change_log)。"""
        if chapter_idx in self._revised:
            return chapter_text, []
        from .prose_renderer import _detect_repetition
        has_repetition = _detect_repetition(chapter_text)
        if not has_repetition and not self.should_revise(reader_feedback, chapter_weight):
            return chapter_text, []

        from literarycreation.core.llm_client import Message
        prompt = _REVISE_PROMPT.format(idx=chapter_idx, original=chapter_text[:8000])
        try:
            kwargs = {"max_tokens": max(2048, len(chapter_text) * 2)}
            resp = await client.chat(
                [Message(role="user", content=prompt)],
                system="你是文学编辑，润色小说章节。只输出修订后的全文。",
                temperature=0.4, **kwargs,
            )
            text = extract_text(resp).strip()
            # 允许因删除注水而明显缩短，但过短视为截断失败
            if text and len(text) > len(chapter_text) * 0.3:
                self._revised.add(chapter_idx)
                changes = ["删除注水与重复", "抽象情感转感官细节", "对话加潜台词"]
                return text, changes
        except Exception:
            pass
        return chapter_text, []

    async def revise_final(self, client, chapter_idx: int,
                            chapter_text: str) -> tuple[str, list[str]]:
        """最终章强制修订——即使读者反馈无问题也要做。"""
        if chapter_idx in self._revised:
            return chapter_text, []
        from literarycreation.core.llm_client import Message
        prompt = _REVISE_FINAL_PROMPT.format(idx=chapter_idx, original=chapter_text[:8000])
        try:
            kwargs = {"max_tokens": max(2048, len(chapter_text) * 2)}
            resp = await client.chat(
                [Message(role="user", content=prompt)],
                system="你是文学编辑，润色最终章。只输出修订后的全文。",
                temperature=0.4, **kwargs,
            )
            text = extract_text(resp).strip()
            if text and len(text) > len(chapter_text) * 0.5:
                self._revised.add(chapter_idx)
                return text, ["最终章专属润色：余韵收尾、弧光闭环、未言之语"]
        except Exception:
            pass
        return chapter_text, []
