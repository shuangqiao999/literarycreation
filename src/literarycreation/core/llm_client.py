"""Thin LLM adapter for the deduction engine — OpenAI-compatible API only.

Replaces the ~2800-line openakita.llm.client.LLMClient with a ~70-line wrapper.
Only implements what the deduction engine actually uses: chat().
"""
from __future__ import annotations

import json as _json
import logging
import time
from dataclasses import dataclass

import httpx

from .token_counter import TokenStats

logger = logging.getLogger(__name__)


@dataclass
class Message:
    """Minimal Message dataclass (replaces openakita.llm.types.Message)."""
    role: str
    content: str


@dataclass
class TextBlock:
    """Minimal TextBlock (replaces openakita.llm.types.TextBlock)."""
    text: str


class DeductionLLMResponse:
    """LLM response wrapper, compatible with the three parsing paths in _utils.extract_text()."""

    def __init__(self, content: str, token_stats: TokenStats | None = None):
        self.text = content
        self.content = content  # string path (simulator.py custom extract path)
        self.choices: list = []  # dict path
        self.token_stats = token_stats or TokenStats()

    def get(self, key: str, default=None):
        return getattr(self, key, default)


class DeductionLLMClient:
    """Lightweight LLM client for LiteraryCreation deduction engine."""

    def __init__(
        self,
        api_base: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
    ):
        from literarycreation.core.providers import registry

        resolved = registry.resolve_for_llm_client()
        self.api_base = (api_base or resolved.get("api_base", "")).rstrip("/")
        self.api_key = api_key or resolved.get("api_key", "")
        self.model = model or resolved.get("model", "")
        self._http: httpx.AsyncClient | None = None
        # 模型档位自适应
        from .prompt_adapter import detect_tier, simplify_prompt, reduce_max_tokens
        self._tier = detect_tier(self.model)
        self._simplify = simplify_prompt
        self._reduce_tokens = reduce_max_tokens

    async def _ensure_client(self):
        if self._http is None:
            headers = {"Content-Type": "application/json"}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
            self._http = httpx.AsyncClient(
                timeout=httpx.Timeout(None, connect=30.0),
                headers=headers,
            )

    async def chat(
        self,
        messages: list[dict] | list[Message],
        system: str = "",
        tools=None,
        max_tokens: int = 0,
        temperature: float = 1.0,
        **kwargs,
    ) -> DeductionLLMResponse:
        await self._ensure_client()

        full_messages: list[dict] = []
        if system:
            system = self._simplify(system, self._tier)
            full_messages.append({"role": "system", "content": system})
        for m in messages:
            if isinstance(m, Message):
                content = self._simplify(m.content, self._tier)
                full_messages.append({"role": m.role, "content": content})
            elif isinstance(m, dict):
                d = dict(m)
                d["content"] = self._simplify(d.get("content", ""), self._tier)
                full_messages.append(d)
            else:
                content = self._simplify(str(m), self._tier)
                full_messages.append({"role": "user", "content": content})

        payload: dict = {
            "model": self.model,
            "messages": full_messages,
            "temperature": temperature,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if max_tokens:
            payload["max_tokens"] = self._reduce_tokens(max_tokens, self._tier)
        payload.update({k: v for k, v in kwargs.items() if v is not None})

        t0 = time.monotonic()
        try:
            content_parts: list[str] = []
            usage: dict = {}
            # 事件驱动：逐块接收 SSE，直至 [DONE]；无生成总超时
            async with self._http.stream(
                "POST", f"{self.api_base}/chat/completions", json=payload
            ) as resp:
                resp.raise_for_status()
                async for raw_line in resp.aiter_lines():
                    if not raw_line:
                        continue
                    line = raw_line.strip()
                    if not line.startswith("data:"):
                        continue
                    data_str = line[5:].strip()
                    if not data_str:
                        continue
                    if data_str == "[DONE]":
                        break
                    try:
                        chunk = _json.loads(data_str)
                    except ValueError:
                        continue
                    choices = chunk.get("choices") or []
                    if choices:
                        delta = choices[0].get("delta") or {}
                        piece = delta.get("content")
                        if piece:
                            content_parts.append(piece)
                    if chunk.get("usage"):
                        usage = chunk["usage"]
            content = "".join(content_parts)
            elapsed_ms = int((time.monotonic() - t0) * 1000)
            if not usage:
                # 兜底：流式响应未返回 usage 时按字数估算（中文约 1.6 tokens/字）
                est = int(len(content) * 1.6)
                usage = {"completion_tokens": est, "total_tokens": est}
            stats = TokenStats(
                prompt_tokens=usage.get("prompt_tokens", 0),
                completion_tokens=usage.get("completion_tokens", 0),
                total_tokens=usage.get("total_tokens", 0),
                model=self.model,
                duration_ms=elapsed_ms,
            )
            # Auto-accumulate if context is set
            from .token_counter import _current_phase, _current_round, _current_session, accumulator
            sid = _current_session.get()
            if sid:
                accumulator.record(sid, _current_phase.get(), _current_round.get(), stats)
            else:
                logger.warning("[Token] session context not set, skipping accumulation (phase=%s tokens=%d)",
                             _current_phase.get(), stats.total_tokens)
            return DeductionLLMResponse(content, token_stats=stats)
        except Exception as e:
            logger.error("[LLM] Chat request failed: %s", e)
            raise

    async def close(self):
        if self._http:
            await self._http.aclose()
            self._http = None
