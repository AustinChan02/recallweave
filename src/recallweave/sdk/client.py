"""Sync client (httpx). Thin: no retries of its own — the server already
fail-closes internally; double-retrying a 502 would duplicate writes' side
effects on the extraction LLM bill. Errors surface as RecallWeaveAPIError."""

from __future__ import annotations

from typing import Any

import httpx


class RecallWeaveAPIError(Exception):
    def __init__(self, status: int, code: str, message: str, detail: dict | None = None) -> None:
        super().__init__(f"[{status}] {code}: {message}")
        self.status = status
        self.code = code
        self.message = message
        self.detail = detail or {}


class RecallWeaveClient:
    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8100",
        api_key: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._client = httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout, headers=headers)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> RecallWeaveClient:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ----------------------------- internals -----------------------------

    def _request(self, method: str, path: str, json_body: dict | None = None) -> Any:
        resp = self._client.request(method, path, json=json_body)
        try:
            payload = resp.json()
        except ValueError:
            payload = {"raw": resp.text}
        if resp.status_code >= 400:
            raise RecallWeaveAPIError(
                status=resp.status_code,
                code=str(payload.get("code", "http_error")),
                message=str(payload.get("message", resp.reason_phrase)),
                detail=payload.get("detail") if isinstance(payload.get("detail"), dict) else None,
            )
        return payload

    # ----------------------------- public API ----------------------------

    def health(self) -> dict:
        return self._request("GET", "/v1/health")

    def ingest(self, messages: list[dict], session_id: str | None = None) -> dict:
        """`messages`: [{"role": "user"|"assistant"|"system", "content": str}]."""
        body: dict[str, Any] = {"messages": messages}
        if session_id:
            body["session_id"] = session_id
        return self._request("POST", "/v1/memories:extract", body)

    def search(self, query: str, k: int = 5) -> dict:
        return self._request("POST", "/v1/memories:search", {"query": query, "k": k})

    def compile(self, messages: list[dict], budget_tokens: int | None = None,
                session_id: str | None = None) -> dict:
        body: dict[str, Any] = {"messages": messages}
        if budget_tokens is not None:
            body["budget_tokens"] = budget_tokens
        if session_id:
            body["session_id"] = session_id
        return self._request("POST", "/v1/context:compile", body)

    def get_memory(self, memory_id: str) -> dict:
        return self._request("GET", f"/v1/memories/{memory_id}")

    def delete_memory(self, memory_id: str) -> dict:
        return self._request("DELETE", f"/v1/memories/{memory_id}")

    def stats(self) -> dict:
        return self._request("GET", "/v1/stats")
