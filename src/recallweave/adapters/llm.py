"""LLM adapters.

Contract: `chat_json` ALWAYS returns a dict validated against the pydantic schema
or raises ProviderError/ProviderSchemaError after bounded retries (ADR: fail-closed).
Transport failures (connect/timeout/429/5xx) retry with exponential backoff +
Retry-After; schema failures retry too (providers drift), same bounded budget —
afterwards the CALLER decides (write pipeline skips the batch and logs).
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Protocol, runtime_checkable

import httpx
from pydantic import BaseModel, ValidationError

from ..core.errors import ProviderError, ProviderSchemaError

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def _extract_content(payload: dict) -> str:
    try:
        content = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ProviderSchemaError(f"unexpected provider payload: {exc}") from exc
    if not isinstance(content, str):
        raise ProviderSchemaError("provider content is not a string")
    return content


def parse_json_strict(content: str) -> dict:
    raw = _FENCE_RE.sub("", content).strip()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ProviderSchemaError(f"content is not JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ProviderSchemaError("content must decode to an object")
    return parsed


def validate_one(schema: type[BaseModel], obj: dict) -> dict:
    try:
        return schema.model_validate(obj).model_dump(mode="json")
    except ValidationError as exc:
        raise ProviderSchemaError(f"schema mismatch: {exc}") from exc


@runtime_checkable
class LLMAdapter(Protocol):
    async def chat_json(self, system: str, user: str, schema: type[BaseModel]) -> dict: ...
    async def chat_text(self, system: str, user: str) -> str: ...


class OpenAICompatAdapter:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        temperature: float = 0.0,
        timeout_s: float = 60.0,
        max_retries: int = 2,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._model = model
        self._temperature = temperature
        self._max_retries = max(0, max_retries)
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_s),
            headers={"Authorization": f"Bearer {api_key}"},
        )
        self._url = base_url.rstrip("/") + "/chat/completions"
        self.provider_label = f"openai_compat:{model}"
        # provider usage of the LATEST successful call (prompt/completion tokens);
        # consumers (eval harness cost accounting) read it right after each call.
        self.last_usage: dict = {}

    async def aclose(self) -> None:  # pragma: no cover - resource path
        await self._client.aclose()

    async def chat_json(self, system: str, user: str, schema: type[BaseModel]) -> dict:
        body = self._body(system, user, json_mode=True)

        async def once() -> dict:
            content, _usage = await self._send_once(body)
            parsed = parse_json_strict(content)
            return validate_one(schema, parsed)  # schema drift IS retryable

        return await self._with_retries(once, "chat_json")

    async def chat_text(self, system: str, user: str) -> str:
        body = self._body(system, user, json_mode=False)

        async def once() -> str:
            content, _usage = await self._send_once(body)
            return content.strip()

        return await self._with_retries(once, "chat_text")

    def _body(self, system: str, user: str, *, json_mode: bool) -> dict:
        body: dict = {
            "model": self._model,
            "temperature": self._temperature,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        return body

    async def _send_once(self, body: dict) -> str:
        """One provider round-trip. Raises: _Retryable (429/5xx), ProviderError
        (4xx — permanent), TransportError/Timeout (retryable)."""
        try:
            resp = await self._client.post(self._url, json=body)
        except (httpx.TimeoutException, httpx.TransportError):
            raise
        if resp.status_code == 429 or resp.status_code >= 500:
            retry_after = resp.headers.get("retry-after")
            wait = float(retry_after) if retry_after else 0.5
            raise _Retryable(status=resp.status_code, wait_s=wait)
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:  # 4xx: never retried
            raise ProviderError(
                f"provider {exc.response.status_code}: {exc.response.text[:200]}",
                detail={"status": exc.response.status_code},
            ) from exc
        payload = resp.json()
        usage = payload.get("usage") or {}
        self.last_usage = usage  # only SUCCESSFUL attempts ever reach here
        return _extract_content(payload), self.last_usage

    async def _with_retries(self, factory, label: str):
        """Bounded retries over ONE attempt factory: transport failures AND
        schema failures share the budget (ADR: fail-closed after max_retries)."""
        from ..core.errors import ProviderError

        last_exc: Exception | None = None
        for attempt in range(self._max_retries + 1):
            try:
                return await factory()
            except ProviderSchemaError as exc:
                # schema drift IS retryable — it subclasses ProviderError, so it
                # MUST be caught before the permanent-ProviderError branch.
                last_exc = exc
            except ProviderError:
                raise  # permanent (e.g. 4xx) — never duplicated by this loop
            except _Retryable as exc:
                last_exc = exc
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_exc = exc
            if attempt >= self._max_retries:
                break
            await asyncio.sleep(getattr(last_exc, "wait_s", None) or 0.5 * (2**attempt))
        if isinstance(last_exc, ProviderSchemaError):
            raise last_exc
        raise ProviderError(f"provider exhausted retries ({label}): {last_exc!r}") from last_exc


class _Retryable(Exception):
    def __init__(self, status: int, wait_s: float) -> None:
        super().__init__(f"retryable status {status}")
        self.wait_s = wait_s


class MockLLMAdapter:
    """Deterministic FIFO of outputs: dict | already-valid json str | Exception.

    Queue exhaustion raises — tests must declare every call explicitly, which is
    the honesty design: a mock can never silently "answer for" the real provider.
    Optional `sticky` relaxes THAT for long-running harnesses: when the queue is
    empty AND sticky is set, the sticky payload is validated and returned every
    time. sticky is a DECLARATION (always labeled in eval metadata), not a
    silent default: default construction keeps the strict fail-closed semantics.
    """

    def __init__(self, outputs: list, declare_provider: str = "mock", sticky: dict | None = None) -> None:
        self._outputs = list(outputs)
        self._sticky = sticky
        self.provider_label = declare_provider  # tests may set e.g. "mock:scripted-eval"

    def push(self, item) -> None:
        self._outputs.append(item)

    async def chat_json(self, system: str, user: str, schema: type[BaseModel]) -> dict:
        item = self._next()
        if isinstance(item, Exception):
            raise item
        obj = json.loads(item) if isinstance(item, str) else item
        return validate_one(schema, obj)

    async def chat_text(self, system: str, user: str) -> str:
        return str(self._next())

    def _next(self):
        if self._outputs:
            return self._outputs.pop(0)
        if self._sticky is not None:
            return self._sticky
        raise ProviderError("mock llm: script queue exhausted")


def make_llm_adapter(settings) -> LLMAdapter:  # single wiring point (no parallel config path)
    if settings.llm_provider == "mock":
        return MockLLMAdapter([])
    if not settings.llm_api_key:
        raise ProviderError("openai_compat provider requires RECALLWEAVE_LLM_API_KEY")
    return OpenAICompatAdapter(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        model=settings.llm_model,
        temperature=settings.llm_temperature,
        timeout_s=settings.llm_timeout_s,
        max_retries=settings.llm_max_retries,
    )
