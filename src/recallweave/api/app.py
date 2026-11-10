"""FastAPI application factory + module-level `app` entrypoint for uvicorn."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from .. import APP_VERSION
from ..core.config import Settings, from_env
from ..core.errors import RecallWeaveError
from ..core.logging import configure_logging
from ..db.session import prep_sqlite_path
from ..service.service import RecallWeaveService
from .routes import router


def _error_response(status: int, body: dict) -> JSONResponse:
    return JSONResponse(status_code=status, content=body)


async def _on_recall_weave_error(_request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, RecallWeaveError):  # dispatch pairs this handler; fail loud if violated
        raise exc
    return _error_response(exc.status_code, exc.as_dict())


async def _on_validation_error(_request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, RequestValidationError):
        raise exc
    return _error_response(
        422,
        {
            "code": "invalid_request",
            "message": "request validation failed",
            "detail": {"errors": exc.errors()[:20]},
        },
    )


async def _on_unexpected(_request: Request, exc: Exception) -> JSONResponse:
    # Fail-closed posture: unexpected errors are logged with stack, client sees a
    # generic body (no internals leak).
    logging.getLogger("recallweave").exception("unhandled_error", extra={"kind": type(exc).__name__})
    return _error_response(
        500,
        {"code": "internal_error", "message": "internal error", "detail": {}},
    )


def create_app(settings: Settings | None = None, service: RecallWeaveService | None = None) -> FastAPI:
    """`settings`/`service` are injectable for tests; prod path builds its own.

    When `service` is injected the caller owns the engine lifecycle; otherwise
    the app builds one engine and disposes it on shutdown.
    """
    settings = settings or from_env()
    configure_logging(settings.app_env)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        engine: AsyncEngine | None
        if service is not None:
            svc, engine = service, None
        else:
            prep_sqlite_path(settings.db_url)
            engine = create_async_engine(settings.db_url, future=True)
            svc = RecallWeaveService(settings, engine)
        app.state.settings = settings
        app.state.service = svc
        await svc.bootstrap()
        try:
            yield
        finally:
            if engine is not None:
                await engine.dispose()

    app = FastAPI(
        title="RecallWeave",
        description=(
            "Memory layer + context compiler for long-lived agents. "
            "Every response is labeled with its data `provider`; mock-mode "
            "output must never be presented as measured results."
        ),
        version=APP_VERSION,
        lifespan=lifespan,
    )
    app.include_router(router)
    # Order matters: specific handlers first, then the catch-all.
    app.add_exception_handler(RecallWeaveError, _on_recall_weave_error)
    app.add_exception_handler(RequestValidationError, _on_validation_error)
    app.add_exception_handler(Exception, _on_unexpected)
    return app


app = create_app()
