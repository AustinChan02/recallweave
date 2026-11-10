"""P1.b §4.5 forgetting job: archive stale low-importance memories.

Honesty invariants, stated as code and echoed in the output:
  * 遗忘 = 分层存储, NOT deletion: this script sets `status='archived'`
    (fully recoverable via MemoryRepo.restore); it NEVER deletes anything.
    Physical removal only ever happens through an explicit user DELETE.
  * defaults come from settings (forget_tau_days=30, forget_importance_lt=0.3);
    overrides are explicit CLI flags — a job that secretly changed its own
    thresholds would be exactly the kind of silent drift this repo forbids.
  * `--dry-run` writes nothing and reports what WOULD be archived.

Usage:
  uv run python scripts/forget.py --dry-run
  uv run python scripts/forget.py [--tau-days 30] [--importance-lt 0.3]
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
for _p in (str(_ROOT / "src"),):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from recallweave.core.config import from_env  # noqa: E402
from recallweave.db.repos import MemoryRepo  # noqa: E402
from recallweave.db.session import ensure_schema, make_engine, prep_sqlite_path  # noqa: E402


async def _run(engine, tau: float, imp: float, dry_run: bool) -> None:
    await ensure_schema(engine)
    repo = MemoryRepo(engine)
    now = datetime.now(UTC)
    offenders = await repo.stale_candidates(now=now, tau_days=tau, importance_lt=imp)
    print(f"forget.py: tau={tau}d importance<{imp} | active-but-stale candidates: {len(offenders)}")
    for m in offenders[:5]:
        print(f"  would archive: {m.id} [{m.kind}] importance={m.importance} :: {m.content[:60]!r}")
    if len(offenders) > 5:
        print(f"  … and {len(offenders) - 5} more")
    if dry_run:
        print("forget.py: dry-run — nothing written (by design).")
        return

    archived = await repo.archive_stale(now=now, tau_days=tau, importance_lt=imp)
    print(f"forget.py: archived {len(archived)} memories (rows kept, status='archived', recoverable).")


def main() -> int:
    ap = argparse.ArgumentParser(description="Archive stale low-importance memories (recoverable)")
    ap.add_argument("--tau-days", type=float, default=None, help="default: settings.forget_tau_days")
    ap.add_argument(
        "--importance-lt", type=float, default=None, help="default: settings.forget_importance_lt"
    )
    ap.add_argument("--dry-run", action="store_true", help="report candidates, write nothing")
    args = ap.parse_args()

    settings = from_env()
    tau = settings.forget_tau_days if args.tau_days is None else args.tau_days
    imp = settings.forget_importance_lt if args.importance_lt is None else args.importance_lt
    db_url = settings.db_url
    if db_url.startswith("sqlite"):
        prep_sqlite_path(db_url)
    engine = make_engine(db_url)
    try:
        asyncio.run(_run(engine, tau, imp, args.dry_run))
    finally:
        asyncio.run(engine.dispose())  # AsyncEngine: dispose, not aclose
    return 0


if __name__ == "__main__":
    sys.exit(main())
