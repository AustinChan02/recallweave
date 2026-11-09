"""Transport schemas (pydantic). Validation lives here; domain rules in service."""

from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, Field


class Message(BaseModel):
    role: Literal["user", "assistant", "system"]
    content: str = Field(min_length=1, max_length=8000)


def _to_plain_messages(messages: list[Message]) -> list[dict]:
    return [{"role": m.role, "content": m.content} for m in messages]


class IngestRequest(BaseModel):
    session_id: uuid.UUID | None = None
    messages: list[Message] = Field(min_length=1, max_length=200)

    def plain(self) -> list[dict]:
        return _to_plain_messages(self.messages)


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=400)
    k: int = Field(default=5, ge=1, le=20)


class CompileRequest(BaseModel):
    session_id: uuid.UUID | None = None
    messages: list[Message] = Field(min_length=1, max_length=200)
    budget_tokens: int | None = Field(default=None, ge=200, le=8000)

    def plain(self) -> list[dict]:
        return _to_plain_messages(self.messages)
