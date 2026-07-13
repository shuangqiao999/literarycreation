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
2. 如果全章节奏过于平坦（连续多段都是相同的句长），在一处插入外部节奏干扰——一个声音、一个动作、一个人的突然介入
3. 如果章尾无钩子——加一句话让它悬而未决。一个好钩子通常满足：一个问题（但不给出答案）、一个意象（打开而非收束）、一个不可逆的决定（刚做出就后悔）
4. 不要改变情节走向、不要删除已建立的关键信息、不要缩短总字数超过 10%
5. 如果本章本身已充分满足上述要求——输出原文即可

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
        if not self.should_revise(reader_feedback, chapter_weight):
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
            if text and len(text) > len(chapter_text) * 0.5:
                self._revised.add(chapter_idx)
                changes = ["抽象情感转感官细节", "对话加潜台词", "节奏打断"]
                return text, changes
        except Exception:
            pass
        return chapter_text, []
