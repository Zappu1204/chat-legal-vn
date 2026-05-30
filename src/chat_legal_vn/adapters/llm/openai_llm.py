"""OpenAI-compatible LLM adapter (used for MIMO).

Production resilience strategy
==============================
MIMO is OpenAI-compatible at the wire level but its rate-limiting envelope
is non-standard: when the cluster router queue is full it returns

    HTTP 400 — {"error": {"code": "429",
                          "type": "router_queue_limitation",
                          "message": "Cluster rate limit exceeded, ..."}}

i.e. the **HTTP status is 400** but the **body code is 429**. The OpenAI
SDK therefore raises ``BadRequestError`` instead of ``RateLimitError``,
which a naive ``retry_if_exception_type(RateLimitError)`` would miss.

This adapter implements the production retry pattern recommended by
Anthropic [1], OpenAI [2] and the AWS exponential-backoff guide [3]:

* Detect the rate-limit signal at body level (vendor-agnostic),
  treating it as transient regardless of HTTP status.
* Retry with **jittered exponential backoff** (``wait_random_exponential``)
  to avoid thundering-herd effects when many clients retry together.
* Retry transient connection / 5xx / 408 / 425 / 429 errors, but **not**
  validation errors (4xx other than rate-limit/timeout).
* Cap concurrency with an asyncio semaphore so we do not flood the
  router queue from inside our own pipeline (planner + HyDE + answer
  engine + reranker pre-checks all share one client).

[1] https://docs.anthropic.com/en/api/handling-stop-reasons (retry guidance)
[2] https://github.com/openai/openai-python#retries
[3] https://docs.aws.amazon.com/general/latest/gr/api-retries.html
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Sequence
from typing import Any

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
    RateLimitError,
)
from tenacity import (
    AsyncRetrying,
    retry_if_exception,
    stop_after_attempt,
    wait_random_exponential,
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


# ---------------------------------------------------------------------------
# Retryability classification
# ---------------------------------------------------------------------------

# HTTP status codes that are safe to retry per the AWS / RFC 7231 guidance:
# - 408 Request Timeout
# - 425 Too Early
# - 429 Too Many Requests
# - 500 / 502 / 503 / 504 Server-side faults
_RETRYABLE_HTTP_STATUSES: frozenset[int] = frozenset({408, 425, 429, 500, 502, 503, 504})

# Body-level rate-limit signals that some OpenAI-compatible vendors
# (notably MIMO) wrap inside an HTTP 4xx response.
_RATE_LIMIT_BODY_TOKENS: tuple[str, ...] = (
    "rate limit",
    "rate_limit",
    "router_queue_limitation",
    "queue is full",
    "too many requests",
    "tpm",
    "rpm",
)


def _looks_like_rate_limit_body(exc: APIStatusError) -> bool:
    """Inspect the response body for a rate-limit / queue-full signal."""
    payload: Any = None
    try:
        payload = exc.response.json()  # type: ignore[union-attr]
    except Exception:  # noqa: BLE001
        try:
            payload = exc.response.text  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001
            payload = None

    if payload is None:
        return False

    # Look for `error.code == "429"` regardless of the HTTP status.
    if isinstance(payload, dict):
        err = payload.get("error")
        if isinstance(err, dict):
            code = str(err.get("code") or "").strip()
            etype = str(err.get("type") or "").lower()
            message = str(err.get("message") or "").lower()
            if code == "429":
                return True
            if any(tok in etype for tok in _RATE_LIMIT_BODY_TOKENS):
                return True
            if any(tok in message for tok in _RATE_LIMIT_BODY_TOKENS):
                return True
        text_blob = str(payload).lower()
    else:
        text_blob = str(payload).lower()
    return any(tok in text_blob for tok in _RATE_LIMIT_BODY_TOKENS)


def _is_retryable(exc: BaseException) -> bool:
    """Decide if an exception from the OpenAI SDK should be retried.

    Network-layer flakes are always retried. Server-status errors are
    retried only when the status is in the well-known retryable set or
    when the body indicates a rate-limit / queue-full condition.
    """
    if isinstance(exc, RateLimitError | APIConnectionError | APITimeoutError):
        return True
    if isinstance(exc, APIStatusError):
        if exc.status_code in _RETRYABLE_HTTP_STATUSES:
            return True
        # Vendor-specific: MIMO 400 with body code=429
        return _looks_like_rate_limit_body(exc)
    if isinstance(exc, asyncio.TimeoutError):
        return True
    return False


class OpenAICompatLLM(LLMAdapter):
    """OpenAI-compatible chat-completion adapter."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = AsyncOpenAI(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key.get_secret_value(),
            timeout=settings.llm_timeout_seconds,
            max_retries=0,  # tenacity handles retries with our own policy
        )
        # Bounded concurrency to keep us under the upstream queue limit.
        self._semaphore = asyncio.Semaphore(
            max(1, int(settings.llm_max_concurrency))
        )

    @property
    def model_name(self) -> str:
        return self._settings.llm_model_name

    # ---- internal request loop --------------------------------------------

    async def _retrying_call(
        self,
        *,
        op: str,
        kwargs: dict[str, Any],
        prefer_json_mode: bool = False,
    ) -> Any:
        """Execute ``chat.completions.create(**kwargs)`` with retries.

        When ``prefer_json_mode`` is True the first call adds
        ``response_format={"type": "json_object"}``; if the upstream
        rejects it as unsupported (a hard 4xx, *not* a rate-limit), we
        drop that field and retry once **without** consuming a retry slot.
        """

        max_attempts = max(1, int(self._settings.llm_max_retries))
        wait_strategy = wait_random_exponential(
            multiplier=self._settings.llm_retry_min_seconds,
            max=self._settings.llm_retry_max_seconds,
        )

        async def _do_call(local_kwargs: dict[str, Any]) -> Any:
            async with self._semaphore:
                return await self._client.chat.completions.create(**local_kwargs)

        # Optional one-shot fallback for vendors that don't support
        # response_format=json_object: we attempt the first call with
        # the flag set; if it raises a non-retryable BadRequestError
        # whose body says the field is unsupported, we strip the field
        # and recurse exactly once before entering the retry loop.
        async def _attempt_with_optional_json_fallback() -> Any:
            local_kwargs = dict(kwargs)
            if prefer_json_mode:
                local_kwargs.setdefault(
                    "response_format", {"type": "json_object"}
                )
            try:
                return await _do_call(local_kwargs)
            except APIStatusError as exc:
                # If body says response_format is unsupported, drop it once.
                if (
                    prefer_json_mode
                    and "response_format" in local_kwargs
                    and not _is_retryable(exc)
                ):
                    body_blob = ""
                    try:
                        body_blob = str(exc.response.json()).lower()  # type: ignore[union-attr]
                    except Exception:  # noqa: BLE001
                        body_blob = ""
                    if "response_format" in body_blob or "json_object" in body_blob:
                        local_kwargs.pop("response_format", None)
                        return await _do_call(local_kwargs)
                raise

        last_exc: BaseException | None = None
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(max_attempts),
            wait=wait_strategy,
            retry=retry_if_exception(_is_retryable),
            reraise=True,
            before_sleep=lambda rs: log.warning(
                "llm.retrying",
                op=op,
                attempt=rs.attempt_number,
                next_sleep_seconds=round(rs.next_action.sleep, 2)
                if rs.next_action
                else None,
                error=str(rs.outcome.exception()) if rs.outcome else None,
            ),
        ):
            with attempt:
                try:
                    return await _attempt_with_optional_json_fallback()
                except BaseException as exc:  # noqa: BLE001
                    last_exc = exc
                    raise

        if last_exc is not None:
            raise last_exc
        msg = "llm: retry loop exited without producing a result"
        raise RuntimeError(msg)

    # ---- public ports -----------------------------------------------------

    async def complete(
        self,
        *,
        system: str,
        user: str,
        temperature: float | None = None,
        max_tokens: int | None = None,
        stop: Sequence[str] | None = None,
    ) -> str:
        kwargs: dict[str, Any] = {
            "model": self._settings.llm_model_name,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": (
                temperature
                if temperature is not None
                else self._settings.llm_temperature
            ),
            "max_tokens": max_tokens or self._settings.llm_max_tokens,
        }
        if stop:
            kwargs["stop"] = list(stop)
        resp = await self._retrying_call(op="complete", kwargs=kwargs)
        return resp.choices[0].message.content or ""

    async def complete_json(
        self,
        *,
        system: str,
        user: str,
        schema: dict[str, Any] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": self._settings.llm_model_name,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature if temperature is not None else 0.0,
            "max_tokens": max_tokens or self._settings.llm_max_tokens,
        }
        resp = await self._retrying_call(
            op="complete_json", kwargs=kwargs, prefer_json_mode=True
        )
        content = resp.choices[0].message.content or ""
        return _extract_json(content)
