"""Sync Python client for the RecallWeave REST API (httpx)."""

from .client import RecallWeaveAPIError, RecallWeaveClient

__all__ = ["RecallWeaveClient", "RecallWeaveAPIError"]
