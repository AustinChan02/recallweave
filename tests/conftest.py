"""Shared fixtures: tmp-file sqlite settings + scripted MockLLM service.

Every test run is fully offline (mock LLM + hash embeddings): mocks are
STATELESS-deterministic and the FIFo contract means tests declare every LLM
call — a mock can never silently "answer" an undeclared provider call.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from recallweave.adapters.llm import MockLLMAdapter
from recallweave.core.config import make_settings
from recallweave.core.errors import ProviderError
from recallweave.db.session import make_engine, prep_sqlite_path
from recallweave.service.service import RecallWeaveService

TEST_DIM = 64  # hash embeddings: dim is free, smaller = faster
DEV_KEY = "dev-key-1"


def make_tmp_settings(tmp_path: Path, **overrides):
    db_url = overrides.pop("db_url", None) or f"sqlite+aiosqlite:///{tmp_path / 'rw.sqlite'}"
    prep_sqlite_path(db_url)
    defaults = dict(
        app_env="test",
        db_url=db_url,
        llm_provider="mock",
        llm_api_key="",
        embedding_provider="hash",
        embedding_dim=TEST_DIM,
        context_budget_default=800,
        extract_recent_turns=8,
        working_turns=12,
        api_auth_enabled=True,
        api_keys_json='{"dev-key-1": "11111111-1111-1111-1111-111111111111"}',
        env_file="",  # tests must NOT read a stray .env in the repo cwd
    )
    defaults.update(overrides)
    return make_settings(**defaults)


def extract_script(memories: list[dict]) -> dict:
    return {"memories": memories}


def make_service(tmp_path: Path, llm_outputs: list | None = None, **overrides):
    settings = make_tmp_settings(tmp_path, **overrides)
    engine = make_engine(settings.db_url)
    llm = MockLLMAdapter(list(llm_outputs or []))
    svc = RecallWeaveService(settings, engine, llm=llm)
    return svc, llm, settings


@pytest.fixture
async def service(tmp_path):
    svc, _llm, _settings = make_service(tmp_path)
    await svc.bootstrap()
    try:
        yield svc
    finally:
        await svc.aclose()


@pytest.fixture
def user_id() -> uuid.UUID:
    return uuid.UUID("11111111-1111-1111-1111-111111111111")


@pytest.fixture
def dev_user_uuid() -> str:
    return "11111111-1111-1111-1111-111111111111"


def push_extract(llm: MockLLMAdapter, memories: list[dict]) -> None:
    llm.push(extract_script(memories))


def provider_error_from(msg="boom") -> ProviderError:
    return ProviderError(msg)


CAND_SIMPLE = {
    "kind": "fact",
    "content": "The user lives in Beijing, Haidian district.",
    "importance": 0.6,
}
