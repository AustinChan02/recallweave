"""Auth + service dependency. user_id ALWAYS comes from the bearer token — a
request body can carry a session id, never an identity (spoofs impossible)."""

from __future__ import annotations

import uuid

from fastapi import Request

from ..core.config import Settings
from ..core.errors import AuthError, ConfigError
from ..service.service import RecallWeaveService


def get_service(request: Request) -> RecallWeaveService:
    svc = getattr(request.app.state, "service", None)
    if svc is None:
        raise ConfigError("service not wired on app.state")
    return svc


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def current_user(request: Request) -> uuid.UUID:
    settings: Settings = request.app.state.settings
    header = request.headers.get("authorization", "")
    if not header.startswith("Bearer "):
        if not settings.api_auth_enabled:
            return uuid.UUID(settings.dev_user)
        raise AuthError("missing bearer token")
    token = header[len("Bearer ") :].strip()
    user_id = settings.api_keys().get(token)
    if user_id is None:
        raise AuthError("unknown token")
    try:
        return uuid.UUID(user_id)
    except ValueError as exc:
        raise AuthError("token maps to an invalid user id") from exc
