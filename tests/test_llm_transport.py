"""OpenAI-compatible adapter transport behavior: retry/backoff, fail-closed."""

from __future__ import annotations

import httpx
import pytest
from pydantic import BaseModel

from recallweave.adapters.llm import (
    MockLLMAdapter,
    OpenAICompatAdapter,
    parse_json_strict,
)
from recallweave.core.errors import ProviderError, ProviderSchemaError


class _Out(BaseModel):
    memories: list[dict] = []


def _adapter(handler) -> OpenAICompatAdapter:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return OpenAICompatAdapter(
        base_url="http://fake/v1", api_key="k", model="fake-model", client=client, max_retries=2
    )


def _chat_response(content: str) -> dict:
    return {"choices": [{"message": {"content": content}}]}


class TestTransport:
    @pytest.mark.asyncio
    async def test_success_json_mode(self):
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            return httpx.Response(200, json=_chat_response('{"ok": true}'))

        adapter = _adapter(handler)
        out = await adapter.chat_json("s", "u", _Out)
        assert out == {"memories": []}
        body = calls[0].read()
        assert b"response_format" in body
        await adapter.aclose()

    @pytest.mark.asyncio
    async def test_retries_on_500_then_succeeds(self):
        attempts = []

        def handler(request: httpx.Request) -> httpx.Response:
            attempts.append(1)
            if len(attempts) == 1:
                return httpx.Response(500, json={"error": "boom"})
            return httpx.Response(200, json=_chat_response('{}'))

        adapter = _adapter(handler)
        out = await adapter.chat_json("s", "u", _Out)
        assert out == {"memories": []}
        assert len(attempts) == 2
        await adapter.aclose()

    @pytest.mark.asyncio
    async def test_429_honors_retry_after_header(self, monkeypatch):
        sleeps = []

        async def fake_sleep(seconds):
            sleeps.append(seconds)

        monkeypatch.setattr("recallweave.adapters.llm.asyncio.sleep", fake_sleep)

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                429, json={"error": "rate limited"}, headers={"retry-after": "7"}
            )

        adapter = _adapter(handler)
        with pytest.raises(ProviderError):
            await adapter.chat_json("s", "u", _Out)
        assert sleeps[-1] == 7.0
        await adapter.aclose()

    @pytest.mark.asyncio
    async def test_4xx_not_retried(self):
        attempts = []

        def handler(request: httpx.Request) -> httpx.Response:
            attempts.append(1)
            return httpx.Response(401, json={"error": "bad key"})

        adapter = _adapter(handler)
        with pytest.raises(ProviderError) as ei:
            await adapter.chat_json("s", "u", _Out)
        assert len(attempts) == 1
        assert ei.value.status_code == 502
        await adapter.aclose()

    @pytest.mark.asyncio
    async def test_retries_exhausted_raises_provider_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503, json={"error": "down"})

        adapter = _adapter(handler)
        with pytest.raises(ProviderError):
            await adapter.chat_json("s", "u", _Out)
        await adapter.aclose()

    @pytest.mark.asyncio
    async def test_schema_error_retries_then_succeeds(self):
        contents = ["not json at all", '{"memories": [{"kind": "fact"}]}']

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_chat_response(contents.pop(0)))

        adapter = _adapter(handler)
        out = await adapter.chat_json("s", "u", _Out)
        assert out == {"memories": [{"kind": "fact"}]}
        await adapter.aclose()

    @pytest.mark.asyncio
    async def test_schema_error_exhausted(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_chat_response("definitely::not::json"))

        adapter = _adapter(handler)
        with pytest.raises(ProviderSchemaError):
            await adapter.chat_json("s", "u", _Out)
        await adapter.aclose()


class TestParseJsonStrict:
    def test_fence_stripped(self):
        assert parse_json_strict("```json\n{\"a\": 1}\n```") == {"a": 1}

    def test_requires_object(self):
        with pytest.raises(ProviderSchemaError):
            parse_json_strict("[1,2,3]")

    def test_invalid(self):
        with pytest.raises(ProviderSchemaError):
            parse_json_strict("{nope")


class TestMockAdapter:
    @pytest.mark.asyncio
    async def test_fifo_and_exhaustion(self):
        m = MockLLMAdapter(["text1", "text2"])
        assert await m.chat_text("s", "u") == "text1"
        assert await m.chat_text("s", "u") == "text2"
        with pytest.raises(ProviderError):
            await m.chat_text("s", "u")

    @pytest.mark.asyncio
    async def test_json_validate_or_raise(self):
        from recallweave.write.extraction import ExtractedBatch

        m = MockLLMAdapter([{"memories": [{"kind": "fact", "content": "xx"}]}])
        out = await m.chat_json("s", "u", ExtractedBatch)
        assert out["memories"][0]["content"] == "xx"
        bad = MockLLMAdapter([{"memories": [{"kind": "gossip", "content": "xx"}]}])
        with pytest.raises(ProviderSchemaError):
            await bad.chat_json("s", "u", ExtractedBatch)

    def test_provider_label(self):
        assert MockLLMAdapter([]).provider_label == "mock"
        custom = MockLLMAdapter([], declare_provider="mock:scripted")
        assert custom.provider_label == "mock:scripted"
