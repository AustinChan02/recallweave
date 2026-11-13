"""Regression: the pg migration loader must not leak comment prose into SQL.

Caught by the repo's first public CI run: `_load_pg_migrations` used to split
on ';' BEFORE stripping `--` comments, so a semicolon inside a comment
(002: "...create_all; this file makes...") split the statement stream and fed
Postgres the comment tail as SQL. These tests are offline (no PG needed):
they guard the loader itself, which is exactly where the bug lived.
"""

from __future__ import annotations

from recallweave.db.session import _load_pg_migrations

_FIRST_TOKENS_OK = {"CREATE", "DROP", "ALTER", "INSERT", "UPDATE", "DELETE", "SELECT"}


def test_statements_are_pure_ddl() -> None:
    stmts = _load_pg_migrations()
    assert stmts, "migrations produced no statements"
    for s in stmts:
        first = s.split(None, 1)[0].upper()
        assert first in _FIRST_TOKENS_OK, f"statement does not start with DDL keyword: {s[:80]!r}"
        for ln in s.splitlines():
            assert not ln.strip().startswith("--"), f"comment leaked into statement: {ln!r}"


def test_002_comment_semicolon_regression() -> None:
    stmts = _load_pg_migrations()
    assert not any("this file makes" in s for s in stmts), "comment prose leaked into SQL"
    assert any(s.startswith("CREATE TABLE IF NOT EXISTS session_turn_stats") for s in stmts)
    assert any(s.startswith("CREATE TABLE IF NOT EXISTS session_summaries") for s in stmts)
