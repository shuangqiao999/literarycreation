"""读者体验模型 — 每章 LLM 模拟读者阅读体验。"""
from __future__ import annotations

import json
import re
from typing import Any

from ._utils import extract_text

_READER_PROMPT = """你是一位挑剔的读者。刚读完一章小说，如实回答你对本章的感受：

1. [困惑度 0-100] 有什么让你摸不着头脑的吗？（角色动机不明、事件缺因果、信息太多太密）
2. [无聊度 0-100] 有想跳过的段落吗？（重复描写、冗长独白、无推进的对话）
3. [疲劳度 0-100] 需要喘口气吗？（连续高强度章节会让人麻木——这正常）
4. [期待] 你最想看下一章的什么？（一句话——某人某事某真相）

输出 JSON：{"confusion": N, "boredom": N, "fatigue": N, "anticipation": "一句话"}
"""


async def simulate_reader(client, chapter_text: str, chapter_idx: int) -> dict[str, Any] | None:
    """LLM 调用：模拟读者读后感受。只传章尾 2500 字。失败返回 None。"""
    from literarycreation.core.llm_client import Message
    tail = chapter_text[-2500:] if len(chapter_text) > 2500 else chapter_text
    try:
        resp = await client.chat(
            [Message(role="user", content=_READER_PROMPT + f"\n\n【本章内容】\n{tail}")],
            system="你是挑剔的读者，只输出 JSON。",
            temperature=0.2,
            max_tokens=120,
        )
        text = extract_text(resp).strip()
        m = re.search(r"\{[\s\S]*\}", text)
        if m:
            data = json.loads(m.group(0))
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return None


def build_reader_feedback_block(prev_feedback: dict[str, Any] | None) -> str:
    """将上一章读者反馈编译为当前章的写作指导文本。"""
    if not prev_feedback:
        return ""
    parts: list[str] = []
    confusion = int(prev_feedback.get("confusion", 0) or 0)
    boredom = int(prev_feedback.get("boredom", 0) or 0)
    fatigue = int(prev_feedback.get("fatigue", 0) or 0)
    antic = str(prev_feedback.get("anticipation", "") or "").strip()

    if confusion > 60:
        parts.append("【读者反馈】上一章读者反映有些困惑。本章请在合适的地方自然地解释或回顾，帮助读者理清头绪。")
    if boredom > 50:
        parts.append("【读者反馈】上一章节奏偏慢。本章请加速推进——让一个事件紧接着一个事件，减少大段描写。")
    if fatigue > 70:
        parts.append("【读者反馈】读者连续高强度阅读后已显疲劳。本章请降速——一个安静的场景、一次真诚的对话、一个让读者休息的时刻。")
    if confusion > 40 and boredom > 40:
        parts.append("【读者反馈】上一章让读者感到困惑且无聊——这是最危险的信号。本章请用清晰、具体的行动和对话破局。")
    if antic:
        parts.append(f"【读者期待】读者最想看下一章：{antic}。如果本章剧情能触及这一点——优先安排。")

    return "\n".join(parts) if parts else ""
