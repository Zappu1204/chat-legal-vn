"""OpenAI-compatible LLM adapter (used for MIMO).

Uses the official `openai` Python SDK in async mode. The MIMO endpoint is
OpenAI-compatible so we can talk to it with the same client.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from typing import Any

from openai import AsyncOpenAI
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from ...domain.ports import LLMAdapter
from ...infra.config import Settings
from ...infra.logging import get_logger

log = get_logger(__name__)


_JSON_FENCE = re.compile(r"```(?:json)?\s*([\s\S]+?)\s*```", re.IGNORECASE)


def _extract_json(text: str) -> dict[str, Any]:
    """Best-effort JSON extraction: try direct, then code-fence, then bracket scan."""
    text = text.strip()
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass
    if m := _JSON_FENCE.search(text):
        try:
            obj = json.loads(m.group(1))
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass
    # Greedy bracket scan
    start = text.find("{")
    end = text.rfind("}")
    if 0 <= start < end:
        try:
            obj = json.loads(text[start : end + 1])
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass
    msg = "LLM did not return parseable JSON"
    raise ValueError(msg)


class OpenAICompatLLM(LLMAdapter):
    """OpenAI-compatible chat-completion adapter."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = AsyncOpenAI(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key.get_secret_value(),
            timeout=settings.llm_timeout_seconds,
            max_retries=0,  # tenacity handles retries
        )

    @property
    def model_name(self) -> str:
        return self._settings.llm_model_name

    async def complete(
        self,
        *,
        system: str,
        user: str,
        temperature: float | None = None,
        max_tokens: int | None = None,
        stop: Sequence[str] | None = None,
    ) -> str:
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(self._settings.llm_max_retries),
            wait=wait_exponential(multiplier=1, min=1, max=10),
            retry=retry_if_exception_type(Exception),
            reraise=True,
        ):
            with attempt:
                resp = await self._client.chat.completions.create(
                    model=self._settings.llm_model_name,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    temperature=(
                        temperature if temperature is not None else self._settings.llm_temperature
                    ),
                    max_tokens=max_tokens or self._settings.llm_max_tokens,
                    stop=list(stop) if stop else None,
                )
                content = resp.choices[0].message.content or ""
                return content
        return ""

    async def complete_json(
        self,
        *,
        system: str,
        user: str,
        schema: dict[str, Any] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        # Try response_format=json_object first; fall back to text + extraction.
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(self._settings.llm_max_retries),
            wait=wait_exponential(multiplier=1, min=1, max=10),
            retry=retry_if_exception_type(Exception),
            reraise=True,
        ):
            with attempt:
                try:
                    resp = await self._client.chat.completions.create(
                        model=self._settings.llm_model_name,
                        messages=[
                            {"role": "system", "content": system},
                            {"role": "user", "content": user},
                        ],
                        temperature=(
                            temperature if temperature is not None else 0.0
                        ),
                        max_tokens=max_tokens or self._settings.llm_max_tokens,
                        response_format={"type": "json_object"},
                    )
                except Exception:
                    # Server may not support response_format
                    resp = await self._client.chat.completions.create(
                        model=self._settings.llm_model_name,
                        messages=[
                            {"role": "system", "content": system},
                            {"role": "user", "content": user},
                        ],
                        temperature=(
                            temperature if temperature is not None else 0.0
                        ),
                        max_tokens=max_tokens or self._settings.llm_max_tokens,
                    )
                content = resp.choices[0].message.content or ""
                return _extract_json(content)
        return {}
