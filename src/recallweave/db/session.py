"""Engine/session factory + schema bootstrap.

Single config path rule: dialect-specific DDL is exactly one of:
- Postgres  : executes migrations/001_init_vector.sql (pgvector + HNSW)
- SQLite    : raw `memory_embeddings` table (json-blob vectors, dev/demo path)
Both are idempotent and safe to run concurrently on startup.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from ..core.errors import ConfigError
from .models import Base

_MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "migrations"


def make_engine(db_url: str) -> AsyncEngine:
    kwargs: dict = {"echo": False, "future": True}
    if db_url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    return create_async_engine(db_url, **kwargs)


def prep_sqlite_path(db_url: str) -> None:
    """Create the parent directory of a file-backed sqlite URL (idempotent)."""
    if not db_url.startswith("sqlite"):
        return
    raw = db_url.split(":///", 1)[-1] if ":///" in db_url else ""
    if not raw or raw == ":memory:" or ":memory:" in raw:
        return
    path = Path(raw.split("?", 1)[0])
    if path.parent != path:
        path.parent.mkdir(parents=True, exist_ok=True)


def _load_pg_migrations() -> list[str]:
    """Every migrations/*.sql in name order (001_init_vector, 002_summaries, ...)."""
    paths = sorted(_MIGRATIONS_DIR.glob("*.sql"))
    if not any(p.name == "001_init_vector.sql" for p in paths):
        raise ConfigError("pg migration missing: migrations/001_init_vector.sql")
    stmts: list[str] = []
    for path in paths:
        raw = path.read_text(encoding="utf-8")
        # Strip `--` comment lines BEFORE splitting on ';': a semicolon inside
        # a comment (002: "...create_all; this file makes...") would otherwise
        # split the statement stream and feed Postgres the comment tail as
        # SQL. (Caught by the repo's first public CI pg-integration run.)
        code = "\n".join(ln for ln in raw.splitlines() if not ln.strip().startswith("--"))
        for chunk in code.split(";"):
            stripped = chunk.strip()
            if stripped:
                stmts.append(stripped)
    return stmts


async def ensure_schema(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        if engine.dialect.name == "postgresql":
            for stmt in _load_pg_migrations():
                await conn.execute(text(stmt))
        else:
            await conn.execute(
                text(
                    "CREATE TABLE IF NOT EXISTS memory_embeddings ("
                    "memory_id TEXT PRIMARY KEY, embedding_json TEXT NOT NULL)"
                )
            )


class Database:
    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine
        self.session_factory = async_sessionmaker(engine, expire_on_commit=False)

    def session(self) -> AsyncSession:
        return self.session_factory()

    @property
    def dialect(self) -> str:
        return self.engine.dialect.name
