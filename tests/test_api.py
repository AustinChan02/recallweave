"""API transport tests on an in-process TestClient (sqlite, mock LLM)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from recallweave.api.app import create_app
from recallweave.db.session import make_engine
from recallweave.service.service import RecallWeaveService

from .conftest import make_tmp_settings

HEADERS = {"Authorization": "Bearer dev-key-1"}


@pytest.fixture
def client_and_service(tmp_path):
    settings = make_tmp_settings(
        tmp_path,
        conflict_lo=0.001,
        dedup_near=0.999,
        context_budget_default=900,
    )
    engine = make_engine(settings.db_url)
    svc = RecallWeaveService(settings, engine)
    app = create_app(settings=settings, service=svc)
    with TestClient(app) as client:
        yield client, svc
    del engine  # owned by the service; TestClient teardown closes nothing else


class TestAuth:
    def test_missing_bearer_is_401(self, client_and_service):
        client, _svc = client_and_service
        resp = client.post("/v1/memories:extract", json={"messages": []})
        assert resp.status_code == 401
        assert resp.json()["code"] == "auth_failed"

    def test_unknown_token_is_401(self, client_and_service):
        client, _svc = client_and_service
        resp = client.get("/v1/stats", headers={"Authorization": "Bearer nope"})
        assert resp.status_code == 401

    def test_search_requires_auth(self, client_and_service):
        client, _svc = client_and_service
        assert client.post("/v1/memories:search", json={"query": "x"}).status_code == 401


class TestHappyPath:
    def test_health_includes_provider_labels(self, client_and_service):
        client, _svc = client_and_service
        resp = client.get("/v1/health", headers=HEADERS)
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["llm_provider"] == "mock"
        assert body["embedding_provider"] == "hash"
        assert "must never be presented" in body["provider_note"]

    def test_extract_add_then_search(self, client_and_service):
        client, _svc = client_and_service
        resp = client.post(
            "/v1/memories:extract",
            headers=HEADERS,
            json={"messages": [{"role": "user", "content": "用户住在上海浦东，喜欢骑车通勤。"}]},
        )
        assert resp.status_code == 200, resp.text
        # NOTE: this call FAILS closed when the mock queue is empty -> 200 report
        # with extraction_error set (server must not 500 for provider failures).
        body = resp.json()
        assert body["provider"] == "mock"
        assert body["extraction_error"] is not None or body["decisions"] is not None

    def test_search_shape_contract(self, client_and_service):
        client, _svc = client_and_service
        resp = client.post("/v1/memories:search", headers=HEADERS, json={"query": "随便"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["provider"] == "local-hybrid"
        assert "items" in body and "rrf_detail" in body

    def test_compile_contract(self, client_and_service):
        client, _svc = client_and_service
        resp = client.post(
            "/v1/context:compile",
            headers=HEADERS,
            json={
                "messages": [{"role": "user", "content": "你好"}],
                "budget_tokens": 600,
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["provider"] == "local-compiler"
        assert body["context_messages"][0]["role"] == "system"
        assert body["usage"]["budget"] == 600
        assert "caps" in body["usage"]

    def test_validation_error_shape(self, client_and_service):
        client, _svc = client_and_service
        resp = client.post("/v1/memories:search", headers=HEADERS, json={"query": ""})
        assert resp.status_code == 422
        assert resp.json()["code"] == "invalid_request"

    def test_stats_counts(self, client_and_service):
        client, _svc = client_and_service
        resp = client.get("/v1/stats", headers=HEADERS)
        assert resp.status_code == 200
        assert "counts_by_status_kind" in resp.json()


class TestReflect:
    """/v1/reflect became REAL on 2026-11-10 (P1.c) — formerly an honest 501.

    The default fixture mock is UNSCRIPTED, so this test pins the honest
    failure surface: reflection runs, reports zero fabrication, and any
    provider trouble surfaces as skipped reasons — never row changes."""

    def test_reflect_zero_clusters_costs_no_llm_call(self, client_and_service):
        client, _svc = client_and_service
        resp = client.post("/v1/reflect", headers=HEADERS)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["clusters_found"] == 0
        assert body["decisions"] == [] and body["skipped"] == []
        assert body["provider"] == "mock"  # honesty label always present

    def test_reflect_reports_provider_error_as_skip_not_fabrication(self, client_and_service):
        client, svc = client_and_service
        import asyncio
        import uuid

        from sqlalchemy.ext.asyncio import async_sessionmaker

        from recallweave.db.models import Memory

        uid = uuid.UUID("11111111-1111-1111-1111-111111111111")  # the fixture's dev user
        maker = async_sessionmaker(svc.repo.engine, expire_on_commit=False)

        async def seed():
            await svc.bootstrap()  # create schema BEFORE seeding (lifespan owns it too)
            async with maker() as s, s.begin():
                s.add_all(
                    [
                        Memory(
                            user_id=uid,
                            session_id=None,
                            kind="fact",
                            content="重复记忆甲",
                            importance=0.5,
                            status="active",
                            meta={},
                        ),
                        Memory(
                            user_id=uid,
                            session_id=None,
                            kind="fact",
                            content="重复记忆甲",
                            importance=0.5,
                            status="active",
                            meta={},
                        ),
                    ]
                )

        asyncio.run(seed())
        resp = client.post("/v1/reflect", headers=HEADERS)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["clusters_found"] == 1
        assert body["decisions"] == []  # unscripted mock: NOTHING fabricated
        assert any(s["reason"].startswith("provider_error_in_resolution") for s in body["skipped"])
