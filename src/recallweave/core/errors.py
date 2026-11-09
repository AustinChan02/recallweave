"""Domain errors. Each carries an HTTP status so transport stays a thin adapter."""

from __future__ import annotations


class RecallWeaveError(Exception):
    status_code = 500
    code = "internal_error"

    def __init__(self, message: str, *, detail: dict | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail or {}

    def as_dict(self) -> dict:
        return {"code": self.code, "message": self.message, "detail": self.detail}


class AuthError(RecallWeaveError):
    status_code = 401
    code = "auth_failed"


class NotFoundError(RecallWeaveError):
    status_code = 404
    code = "not_found"


class ValidationError(RecallWeaveError):
    status_code = 422
    code = "invalid_request"


class ProviderError(RecallWeaveError):
    """Upstream LLM failed after retries (transport or exhausted retry budget)."""

    status_code = 502
    code = "provider_error"


class ProviderSchemaError(ProviderError):
    """Provider answered 200 but payload could not satisfy the required schema."""

    code = "provider_schema_error"


class ConfigError(RecallWeaveError):
    status_code = 500
    code = "config_error"
