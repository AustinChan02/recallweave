"""JSON-lines logging via structlog. No print() in application code — ever."""

from __future__ import annotations

import logging

import structlog


def configure_logging(app_env: str) -> None:
    level = logging.DEBUG if app_env == "dev" else logging.INFO
    renderer = (
        structlog.dev.ConsoleRenderer(colors=False)
        if app_env == "dev"
        else structlog.processors.JSONRenderer()
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.add_log_level,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=False,
    )


def get_logger(**initial: object):
    return structlog.get_logger("recallweave", **initial)
