"""HTTP routes (proposal §5). Transport is a thin adapter: auth resolves the
user identity, validation lives in schemas, domain rules in service.

Colon routes (`/v1/memories:extract`) are deliberate — they read as
`resource:verb` and keep query-string conventions free.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse

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


@router.post("/reflect")
async def reflect(user: uuid.UUID = Depends(current_user)) -> Response:
    """P1 honest stub (proposal §4.4/§5): NOT implemented — answer 501, never fake."""
    return JSONResponse(
        status_code=501,
        content={
            "code": "not_implemented",
            "message": (
                "/v1/reflect is a P1 feature (periodic reflection). It is not "
                "implemented in this MVP and thus returns 501 instead of pretending to work."
            ),
            "detail": {"roadmap_item": "P1-reflect"},
        },
        headers={"Allow": "POST"},
    )


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
