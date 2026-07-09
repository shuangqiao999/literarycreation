"""字数硬约束检查器 — 生成后检查是否达标，不足则触发扩写重试。

与 prose_renderer._retry_prose 正交：后者处理"生成失败/退化"，本器处理"成功但太短"。
中文按 len(text) 字符数≈字数目标。
"""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)


class WordCountEnforcer:
    def __init__(self, min_ratio: float = 0.75) -> None:
        self.min_ratio = max(0.1, min(1.0, min_ratio))

    def check(self, text: str, target: int) -> tuple[bool, str]:
        if not target or target <= 0:
            return True, ""
        actual = len(text or "")
        need = int(target * self.min_ratio)
        if actual < need:
            return False, f"字数不足：实际 {actual} 字，目标 {target} 字（至少需 {need} 字）"
        return True, ""

    def build_expansion_prompt(self, text: str, target: int, current: int) -> str:
        shortage = max(200, target - current)
        per = max(100, shortage // 4)
        return (
            f"以下是已完成的本章正文，当前约 {current} 字，目标 {target} 字，还差约 {shortage} 字。\n"
            f"请在【完整保留原有情节、人物、对话与顺序】的前提下扩写加长，"
            f"不得改变已发生的事实、不得让已死角色复活、不得新增与主线无关的情节。\n"
            f"从以下维度补充细节以自然增加篇幅：\n"
            f"1. 环境与场景描写（约 {per} 字）\n"
            f"2. 角色内心活动与心理刻画（约 {per} 字）\n"
            f"3. 对话的潜台词与神态动作（约 {per} 字）\n"
            f"4. 动作过程的细节铺陈（约 {per} 字）\n"
            f"直接输出【扩写后的完整本章正文】，不要输出说明或标题。\n\n"
            f"【原正文】\n{text}"
        )

    async def enforce(
        self, text: str, target: int,
        expand_fn: Callable[[str], Awaitable[str]],
        max_retries: int = 2,
        log_fn: Callable[[str, str], None] | None = None,
    ) -> str:
        """不达标则用 expand_fn(expansion_prompt) 扩写，最多 max_retries 次；始终返回最长稿。"""
        best = text or ""
        for attempt in range(1, max_retries + 1):
            ok, _msg = self.check(best, target)
            if ok:
                return best
            prompt = self.build_expansion_prompt(best, target, len(best))
            try:
                expanded = (await expand_fn(prompt)) or ""
            except Exception as e:  # noqa: BLE001
                logger.warning("[WordCount] 扩写失败(%d): %s", attempt, e)
                break
            if log_fn:
                import contextlib
                with contextlib.suppress(Exception):
                    log_fn("report", f"字数扩写第{attempt}次：{len(best)}→{len(expanded)} 字（目标{target}）")
            # 只接受更长的稿，防止扩写反而变短
            if len(expanded) > len(best):
                best = expanded
        return best
