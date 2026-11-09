"""Integration tests: real Postgres+pgvector path (gated on env).

Run: make db-up
     RECALLWEAVE_TEST_PG_URL=postgresql+asyncpg://recallweave:recallweave@localhost:5432/recallweave \
     uv run pytest -m integration

These verify the dialect-specific surface that sqlite cannot: HNSW search via
pgvector literal binding, upsert ON CONFLICT, and the superseded chain against
a server-side DB. They share one database and reset via DROP/CREATE between
cases (test DB only).
"""

from __future__ import annotations

import os
import uuid

import pytest

from recallweave.db.models import Memory
from recallweave.db.repos import MemoryRepo
from recallweave.db.session import ensure_schema, make_engine
from recallweave.db.vector_index import VectorIndex

PG_URL_ENV = "RECALLWEAVE_TEST_PG_URL"

pytestmark = pytest.mark.integration


def pg_url():
    return os.environ.get(PG_URL_ENV)


@pytest.fixture
async def pg_engine():
    url = pg_url()
    if not url:
        pytest.skip(f"set {PG_URL_ENV} to run integration tests (make db-up first)")
    engine = make_engine(url)
    await ensure_schema(engine)
    yield engine
    await engine.dispose()


@pytest.mark.asyncio
async def test_pg_vector_search_and_supersede(pg_engine):
    user = uuid.uuid4()
    repo = MemoryRepo(pg_engine)
    # Column is declared vector(512) in migrations/001_init_vector.sql: pgvector
    # ENFORCES the declared width ("expected 512 dimensions, not 16") — the
    # tests must speak the production shape, not an arbitrary one.
    vector = VectorIndex(pg_engine, 512)

    async with repo.new_session() as session:
        async with session.begin():
            m1 = Memory(user_id=user, kind="fact", content="pg 集成测试记忆一", importance=0.5)
            m2 = Memory(user_id=user, kind="fact", content="pg 集成测试记忆二", importance=0.5)
            q = [1.0] + [0.0] * 511
            n1 = [0.9] + [0.1] + [0.0] * 510
            n2 = [0.0] + [1.0] + [0.0] * 510
            await MemoryRepo.insert_with_embedding(session, m1, n1, vector)
            await MemoryRepo.insert_with_embedding(session, m2, n2, vector)

    hits = await vector.search(user_id=user, query_embedding=q, k=2)
    assert hits, "HNSW search returned no rows"
    assert hits[0][0] == m1.id, "nearest must be the parallel unit vector n1 (cos≈0.9)"
    assert hits[1][0] == m2.id

    # supersede chain against the server
    async with repo.new_session() as session:
        async with session.begin():
            fresh = await session.get(Memory, m1.id)
            await MemoryRepo.mark_superseded(session, fresh, new_id=m2.id, reason="test")
    gone = (await vector.search(user_id=user, query_embedding=q, k=5))
    assert m1.id not in [h[0] for h in gone]
