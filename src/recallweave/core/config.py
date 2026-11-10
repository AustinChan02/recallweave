"""Twelve-factor-ish configuration. THIS MODULE IS THE ONE AND ONLY CONFIG PATH.

Two-host trap (borrowed from a related repo's culture): if any code needs a
setting, it must go through here — a second "config from raw os.environ" copy
will eventually disagree with this one and ship.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEV_USER_UUID = "11111111-1111-1111-1111-111111111111"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="RECALLWEAVE_", env_file=".env", extra="ignore")

    app_env: Literal["dev", "test", "prod"] = "dev"
    db_url: str = "sqlite+aiosqlite:///./data/recallweave.db"

    # Auth: bearer token -> user uuid (JSON map). Empty map + auth_enabled=False => dev user.
    api_auth_enabled: bool = True
    api_keys_json: str = json.dumps({"dev-key-1": DEV_USER_UUID})
    dev_user: str = DEV_USER_UUID

    # LLM adapter (OpenAI-compatible; "mock" runs fully offline)
    llm_provider: Literal["mock", "openai_compat"] = "mock"
    llm_base_url: str = "https://api.deepseek.com/v1"
    llm_api_key: str = ""
    llm_model: str = "deepseek-chat"
    llm_temperature: float = 0.0
    llm_timeout_s: float = 60.0
    llm_max_retries: int = 2

    # Embeddings: "hash" = deterministic, dev/test only; "bge" = local BAAI/bge-small-zh-v1.5
    embedding_provider: Literal["hash", "bge"] = "hash"
    embedding_dim: int = 512
    # pgvector runtime knob (proposal §4.2 "ef_search 可配")
    hnsw_ef_search: int = 40

    # Dedup / conflict window (fetching a proposal's exposure: these are tuned hyperparameters)
    dedup_near: float = 0.93
    conflict_lo: float = 0.80

    # Fusion
    rrf_k: int = 60
    mmr_lambda: float = 0.7
    decay_tau_days: float = 21.0
    w_fact: float = 1.0
    w_preference: float = 1.1
    w_instruction: float = 1.2
    w_event: float = 0.9
    importance_lo: float = 0.7  # importance multiplier = importance_lo + importance * importance_span
    importance_span: float = 0.6

    # Write pipeline
    extract_recent_turns: int = 8
    max_ingest_messages: int = 200
    neighbor_top_n: int = 8

    # Read pipeline lanes
    vector_top_m: int = 32
    lexical_top_m: int = 32
    lexical_scan_limit: int = 2000
    recency_top_m: int = 8

    # Context compiler budget split of the TOTAL budget (proposal §4.3, defaults 10/30/40/20)
    context_budget_default: int = 1500
    budget_system_pct: float = 0.10
    budget_profile_pct: float = 0.30
    budget_episodic_pct: float = 0.40
    budget_working_pct: float = 0.20
    working_turns: int = 12
    # P1.a §4.4 session-summarization trigger: every N turns per session.
    summarize_every_turns: int = 20
    summary_recent_limit: int = 8
    # P1.b §4.5 forgetting = archival: un-accessed for tau AND importance below
    # cut → status='archived' (recoverable), NEVER deleted; deletion only via
    # explicit user DELETE.
    forget_tau_days: float = 30.0
    forget_importance_lt: float = 0.3
    # Cache-stable rule#2: profile/episodic items must never embed wall-clock relative times.
    persona_line: str = (
        "You are an assistant with durable long-term memory of the user. Ground your answers "
        "in the PROFILE and RECENT EVENTS sections when they apply."
    )

    data_dir: Path = Path("data")
    eval_results_dir: Path = Path("evals/results")

    @model_validator(mode="after")
    def _check_window(self) -> Settings:
        if not (0 < self.conflict_lo < self.dedup_near <= 1.0):
            raise ValueError(f"conflict_lo({self.conflict_lo}) must be < dedup_near({self.dedup_near})")
        total = (
            self.budget_system_pct
            + self.budget_profile_pct
            + self.budget_episodic_pct
            + self.budget_working_pct
        )
        if abs(total - 1.0) > 0.02:
            raise ValueError(f"budget pcts must sum to 1.0, got {total}")
        if self.budget_working_pct <= 0 or self.budget_profile_pct <= 0:
            raise ValueError("budget pcts must be positive")
        return self

    def api_keys(self) -> dict[str, str]:
        try:
            parsed = json.loads(self.api_keys_json)
        except json.JSONDecodeError as exc:  # fail-closed: bad config must not half-apply
            raise ValueError(f"api_keys_json is not valid JSON: {exc}") from exc
        if not isinstance(parsed, dict):
            raise ValueError("api_keys_json must map token -> user uuid")
        return {str(k): str(v) for k, v in parsed.items()}

    def kind_weight(self, kind: str) -> float:
        return {
            "fact": self.w_fact,
            "preference": self.w_preference,
            "instruction": self.w_instruction,
            "event": self.w_event,
        }.get(kind, 1.0)


def make_settings(**overrides: Any) -> Settings:
    """Build a fresh Settings (tests inject overrides directly)."""
    return Settings(**overrides)


def from_env() -> Settings:
    """Cached process-wide settings for the running server."""
    global _CACHED
    try:
        return _CACHED  # type: ignore[name-defined]
    except NameError:
        _CACHED = Settings()
        return _CACHED
