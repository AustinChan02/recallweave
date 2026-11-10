"""HTTP routes (proposal §5). Transport is a thin adapter: auth resolves the
user identity, validation lives in schemas, domain rules in service.

Colon routes (`/v1/memories:extract`) are deliberate — they read as
`resource:verb` and keep query-string conventions free.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request

from .. import APP_VERSION
from .deps import current_user, get_service
from .schemas import CompileRequest, IngestRequest, SearchRequest

router = APIRouter(prefix="/v1", tags=["v1"])


@router.post("/memories:extract")
async def memories_extract(
    body: IngestRequest,
    user: uuid.UUID = Depends(current_user),
    svc=Depends(get_service),
):
    report = await svc.ingest(user, body.session_id, body.plain())
    return report.model_dump()


@router.post("/memories:search")
async def memories_search(
    body: SearchRequest,
    user: uuid.UUID = Depends(current_user),
    svc=Depends(get_service),
):
    return await svc.search(user, body.query, top_k=body.k)


@router.post("/context:compile")
async def context_compile(
    body: CompileRequest,
    user: uuid.UUID = Depends(current_user),
    svc=Depends(get_service),
):
    out = await svc.compile(user, body.session_id, body.plain(), budget=body.budget_tokens)
    return out.model_dump()


@router.get("/memories/{memory_id}")
async def memory_get(
    memory_id: uuid.UUID,
    user: uuid.UUID = Depends(current_user),
    svc=Depends(get_service),
):
    return await svc.get_memory(user, memory_id)


@router.delete("/memories/{memory_id}", status_code=200)
async def memory_delete(
    memory_id: uuid.UUID,
    user: uuid.UUID = Depends(current_user),
    svc=Depends(get_service),
):
    return await svc.delete_memory(user, memory_id)


@router.post("/sessions/{session_id}:summarize")
async def session_summarize(
    session_id: uuid.UUID,
    user: uuid.UUID = Depends(current_user),
    svc=Depends(get_service),
) -> dict:
    """P1.a (§4.4) rolling session summary; explicit trigger on top of the
    post-ingest auto-trigger. The response always carries `provider` and a
    `note` when no summary was produced (threshold / mock / empty output)."""
    return await svc.summarize_session(user, session_id, force=True)


@router.post("/reflect")
async def reflect(
    user: uuid.UUID = Depends(current_user),
    svc=Depends(get_service),
) -> dict:
    """P1.c real reflection: near-duplicate clustering over active memories,
    per-cluster resolution through the ingest conflict pipeline (fail-closed
    coercion), all applied merges in ONE transaction. This replaced an honest
    501 stub on 2026-11-10 (devlog records the 501→real transition)."""
    report = await svc.reflect(user)
    return report.model_dump()


@router.get("/health")
async def health(request: Request) -> dict:
    settings = request.app.state.settings
    svc = getattr(request.app.state, "service", None)
    return {
        "status": "ok",
        "app_version": APP_VERSION,
        "app_env": settings.app_env,
        "db_dialect": svc.dialect if svc else None,
        "llm_provider": settings.llm_provider,
        "embedding_provider": settings.embedding_provider,
        "provider_note": (
            "llm_provider=mock and embedding_provider=hash are offline placeholders; "
            "mock output must never be presented as measured provider results."
        ),
    }


@router.get("/stats")
async def stats(
    user: uuid.UUID = Depends(current_user),
    svc=Depends(get_service),
) -> dict:
    return await svc.stats(user)
