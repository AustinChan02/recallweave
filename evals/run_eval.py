"""Eval CLI (proposal §7.3): write-once result files + derived summary.

Usage:
  uv run -m recallweave.evals.run_eval --dataset evals/fixtures/mini_eval.jsonl \
      --conditions C0,C3 --judge mock --budget 600

Protocol invariants enforced HERE, as code, not convention:
  * results are named eval-<date>-<condition>-run<N>.json and are write-once:
    if the target file already exists the run FAILS (covering it would let a
    later run hide a worse one — the legitimate cheat §7.3 closes).
  * unjudged items are excluded from `accuracy` and reported separately;
  * all files carry provider labels; mock results say "smoke-only".
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path

# evals/ lives at repo top level (proposal §9 layout); bootstrap sys.path so it
# works as `python evals/run_eval.py` without installing the repo tree itself.
_ROOT = Path(__file__).resolve().parents[1]
for _p in (str(_ROOT / "src"), str(_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from evals import conditions  # noqa: E402
from evals.judge import make_judge  # noqa: E402
from recallweave.core.config import from_env  # noqa: E402

PROVIDER_NOTE_MOCK = (
    "ALL outputs are mock (scripted extraction, hash embeddings, keyword judge). "
    "Metrics describe pipeline mechanics ONLY and must not be quoted as measured "
    "LLM quality. Real-provider runs require API keys (pending work)."
)


def _next_run_number(results_dir: Path, date: str, condition: str) -> int:
    existing = [
        int(p.stem.rsplit("run", 1)[-1])
        for p in results_dir.glob(f"eval-{date}-{condition}-run*.json")
        if p.stem.rsplit("run", 1)[-1].isdigit()
    ]
    return max(existing, default=0) + 1


def _write_once(path: Path, payload: dict) -> None:
    if path.exists():
        raise SystemExit(f"WRITE-ONCE VIOLATION: refusing to overwrite existing result {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_once_text(path: Path, text: str) -> None:
    if path.exists():
        raise SystemExit(f"WRITE-ONCE VIOLATION: refusing to overwrite existing result {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _result_filename(results_dir: Path, date: str, condition: str) -> Path:
    n = _next_run_number(results_dir, date, condition)
    return results_dir / f"eval-{date}-{condition}-run{n}.json"


def _summary_markdown(manifest: dict, per_condition_rows: list[dict]) -> str:
    lines = [
        f"# Eval summary — run {manifest['run_id']}",
        f"- date: `{manifest['date']}` (UTC)",
        f"- dataset: `{manifest['dataset']}`",
        f"- protocol: `{manifest['protocol']}`",
        f"- judge: `{manifest['judge_label']}`",
        f"- budget: `{manifest['budget_tokens']}` tokens",
        "",
        "| condition | accuracy | judged/total | unjudged | tokens(est) |",
        "|---|---|---|---|---|",
    ]
    for row in per_condition_rows:
        acc = row["accuracy"] if row["accuracy"] is not None else "n/a"
        lines.append(
            f"| {row['condition']} | {acc} | {row['judged']}/{row['total']} "
            f"| {row['unjudged']} | {row['tokens_total_estimate']} |"
        )
    lines += [
        "",
        "> accuracy 语义: context-evidence recall（离线代理指标）。",
        "> `cost_yuan` / `ttft_ms` 需要＋真实供应商数据，离线不可计算，一律不填。",
        "",
        f"*generated {datetime.now(UTC).isoformat()} — derived from the raw",
        "write-once files in this directory; raw files are never edited.*",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description="RecallWeave eval runner (write-once)")
    ap.add_argument("--dataset", required=True, type=Path)
    ap.add_argument("--conditions", default="C0,C3")
    ap.add_argument("--judge", default="mock", choices=["mock"])
    ap.add_argument("--budget", type=int, default=600)
    ap.add_argument(
        "--results-dir",
        type=Path,
        default=from_env().eval_results_dir,
        help="default: settings.eval_results_dir (evals/results)",
    )
    args = ap.parse_args()

    conditions_list = [c.strip().upper() for c in args.conditions.split(",") if c.strip()]
    for c in conditions_list:
        if c not in {"C0", "C1", "C2", "C3"}:
            raise SystemExit(f"unknown condition {c} (choose from C0,C1,C2,C3)")
    if not args.dataset.exists():
        raise SystemExit(f"dataset not found: {args.dataset}")

    date = datetime.now(UTC).strftime("%Y-%m-%d")
    run_id = uuid.uuid4().hex[:12]
    settings = from_env()
    from recallweave.core.logging import configure_logging

    configure_logging("test")  # keep run output readable; files carry the details
    judge = make_judge(args.judge)

    async def run() -> list[tuple[conditions.ConditionRun, Path]]:
        runs: list[tuple[conditions.ConditionRun, Path]] = []
        with tempfile.TemporaryDirectory(prefix="rw-eval-") as td:
            tmp_root = Path(td)
            harness_runs = await conditions.run_harness(
                args.dataset, conditions_list, judge, budget=args.budget, tmp_root=tmp_root
            )
            for cond, run in zip(conditions_list, harness_runs, strict=True):
                out_path = _result_filename(args.results_dir, date, cond)
                payload = {
                    "schema": "rw-eval-result/1",
                    "run_id": run_id,
                    "date": date,
                    "condition": cond,
                    "dataset": str(args.dataset),
                    "protocol": "context-evidence-recall (offline proxy)",
                    "budget_tokens": args.budget,
                    "provider": {
                        "llm": settings.llm_provider,
                        "embedding": settings.embedding_provider,
                        "judge": judge.judge_label,
                        "note": PROVIDER_NOTE_MOCK,
                    },
                    "items": [r.__dict__ for r in run.records],
                    "metrics": run.metrics(),
                    "uncomputed": {
                        "cost_yuan": None,
                        "ttft_ms": None,
                        "cache_hit_tokens": None,
                        "note": (
                            "provider-usage metrics need a REAL provider; "
                            "always null under mock — never fabricated."
                        ),
                    },
                }
                _write_once(out_path, payload)
                runs.append((run, out_path))
        return runs

    results = asyncio.run(run())

    # derived summary (also write-once): consumed by humans, raw files stay raw
    summary_rows = [
        run.metrics() | {"condition": cond} for cond, (run, _p) in zip(conditions_list, results, strict=True)
    ]
    summary_path = args.results_dir / f"summary-{date}-{run_id}.md"
    _write_once_text(
        summary_path,
        _summary_markdown(
            {
                "run_id": run_id,
                "date": date,
                "dataset": str(args.dataset),
                "protocol": "context-evidence-recall (offline proxy)",
                "judge_label": judge.judge_label,
                "budget_tokens": args.budget,
            },
            summary_rows,
        ),
    )
    print(summary_path.read_text(encoding="utf-8"))
    for _cond, (_run, path) in zip(conditions_list, results, strict=True):
        print(f"result file: {path}")


if __name__ == "__main__":
    main()
