"""Eval CLI (proposal §7.3): write-once result files + derived summary.

Usage:
  offline proxy (echo answers, keyword judge — pipeline mechanics only):
    uv run python evals/run_eval.py --dataset evals/fixtures/mini_eval.jsonl \
        --conditions C0,C3 --judge mock --budget 600
  real provider (real answers, real LLM judge, cost-guarded):
    uv run python evals/run_eval.py --dataset ... --answer-mode provider \
        --judge llm --max-cost-yuan 2 --judge-model deepseek-flash

Protocol invariants enforced HERE, as code, not convention:
  * results are named eval-<date>-<condition>-run<N>.json and are write-once:
    if the target file already exists the run FAILS (overwriting would let a
    later run hide a worse one — the legitimate cheat §7.3 closes);
  * unjudged items are excluded from `accuracy` and reported separately;
  * all files carry provider labels; echo/mock results say "smoke-only";
  * FAIL-CLOSED refusals before ANY network call or file write: provider mode
    requires a real `openai_compat` provider AND the LLM judge; mock judges
    never grade real answers; over-budget runs abort BEFORE writing anything.
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
from evals.conditions import ANSWER_SYSTEM  # noqa: E402
from evals.judge import JudgeVerdict, make_judge  # noqa: E402
from evals.pricing import CostGuard, load_price_table  # noqa: E402
from recallweave.core.config import from_env  # noqa: E402

PROVIDER_NOTE_MOCK = (
    "ALL outputs are mock (scripted extraction, hash embeddings, keyword judge). "
    "Metrics describe pipeline mechanics ONLY and must not be quoted as measured "
    "LLM quality. Real-provider runs need --answer-mode provider."
)
PROVIDER_NOTE_REAL = (
    "REAL-provider run: answers and judgments come from the labeled models; "
    "answer/judge token counts are provider-reported facts. cost_yuan_upper_est "
    "uses CONSERVATIVE UPPER-BOUND placeholder prices (configs/prices.toml) until "
    "console-checked; console-priced cost_yuan stays null. Resume grade: the "
    "30-question human review is the required companion of any quoted accuracy."
)
PROTOCOL_ECHO = "context-evidence-recall (offline proxy)"
PROTOCOL_REAL = "qa-accuracy (real provider, LLM-judged)"


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


def _summary_markdown(manifest: dict, rows: list[dict]) -> str:
    real = manifest["protocol"] == PROTOCOL_REAL
    lines = [
        f"# Eval summary — run {manifest['run_id']}",
        f"- date: `{manifest['date']}` (UTC)",
        f"- dataset: `{manifest['dataset']}`",
        f"- protocol: `{manifest['protocol']}`",
        f"- judge: `{manifest['judge_label']}`",
        f"- answered_by: `{manifest['answered_by']}`",
        f"- budget: `{manifest['budget_tokens']}` tokens",
    ]
    if real:
        g = manifest["budget_guard"]
        lines += [
            f"- cost guard: spent-upper-est `{g['spent_yuan_upper_est']}¥` / cap `{g['max_cost_yuan']}¥`"
            f" (prices: `{g['price_table']}`)",
            "- token columns: answer/judge are PROVIDER-reported; context is a local estimate.",
        ]
    lines += ["", "| condition | accuracy | judged/total | unjudged |", "|---|---|---|---|"]
    if real:
        for row in rows:
            lines.append(
                f"| {row['condition']} | {row['accuracy']} | {row['judged']}/{row['total']} "
                f"| {row['unjudged']} | ctx~{row['tokens_context_est']} | ans {row['tokens_answer']} "
                f"| judge {row['tokens_judge']} | {row['cost_yuan_upper_est']}¥ upper-est |"
            )
    else:
        for row in rows:
            acc = row["accuracy"] if row["accuracy"] is not None else "n/a"
            lines.append(
                f"| {row['condition']} | {acc} | {row['judged']}/{row['total']} "
                f"| {row['unjudged']} | ctx~{row['tokens_context_est']} |"
            )
    lines += [
        "",
        "> accuracy 语义: "
        + (
            "qa-accuracy（真实 provider 回答 + 真实 LLM judge；须伴 30 题人工复核后才能进简历）。"
            if real
            else "context-evidence recall（离线代理指标），绝不作为 QA 准确率引用。"
        ),
        "> `cost_yuan`（控制台实价）/ `ttft_ms` / `cache_hit_tokens` 保持 null（见各 result 文件"
        " uncomputed 段），绝不虚构.",
        "",
        f"*generated {datetime.now(UTC).isoformat()} — derived from the raw",
        "write-once files in this directory; raw files are never edited.*",
    ]
    return "\n".join(lines) + "\n"


def _close_quiet(adapter) -> None:
    """Sync-scope shutdown for a sync-built adapter; never masks run outcome."""
    try:
        asyncio.run(adapter.aclose())
    except Exception:  # noqa: BLE001 — shutdown best-effort by design
        pass


def main() -> None:
    ap = argparse.ArgumentParser(description="RecallWeave eval runner (write-once)")
    ap.add_argument("--dataset", required=True, type=Path)
    ap.add_argument("--conditions", default="C0,C3")
    ap.add_argument("--judge", default="mock", choices=["mock", "llm"])
    ap.add_argument(
        "--judge-model",
        default="",
        help="judge model override (real provider mode; default = the answer model)",
    )
    ap.add_argument(
        "--answer-mode",
        default="echo",
        choices=["echo", "provider"],
        help="echo = offline proxy (answer = emitted context); provider = real answering model",
    )
    ap.add_argument(
        "--max-cost-yuan",
        type=float,
        default=200.0,
        help="cost guard cap on the cumulative UPPER-BOUND estimate (fail-closed abort)",
    )
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

    real = args.answer_mode == "provider"
    protocol = PROTOCOL_REAL if real else PROTOCOL_ECHO
    settings = from_env()

    # Fail-closed protocol refusals — BEFORE any network I/O or file write.
    if real and settings.llm_provider != "openai_compat":
        raise SystemExit(
            "REFUSAL: --answer-mode provider needs a real provider "
            "(RECALLWEAVE_LLM_PROVIDER=openai_compat + key in .env); "
            f"got llm_provider={settings.llm_provider!r}. The offline echo protocol "
            "must stay in echo mode — never fake online behavior."
        )
    if real and args.judge == "mock":
        raise SystemExit(
            "REFUSAL: real-provider answers would be graded by the mock keyword judge — "
            "that pairing could masquerade as measured quality. Use --judge llm."
        )
    if not real and args.judge == "llm":
        raise SystemExit(
            "REFUSAL: the LLM judge grades REAL answers; in echo mode the answer is the "
            "context itself. Use --answer-mode provider."
        )

    date = datetime.now(UTC).strftime("%Y-%m-%d")
    run_id = uuid.uuid4().hex[:12]
    from recallweave.core.logging import configure_logging

    configure_logging("test")  # keep run output readable; files carry the details

    answer_adapter = None
    judge_adapter = None
    if real:
        from recallweave.adapters.llm import OpenAICompatAdapter, make_llm_adapter

        answer_adapter = make_llm_adapter(settings)
        judge_adapter = (
            answer_adapter
            if args.judge_model in ("", settings.llm_model)
            else OpenAICompatAdapter(
                base_url=settings.llm_base_url,
                api_key=settings.llm_api_key,
                model=args.judge_model,
                temperature=settings.llm_temperature,
                timeout_s=settings.llm_timeout_s,
                max_retries=settings.llm_max_retries,
            )
        )
    judge = make_judge(args.judge, llm=judge_adapter, schema=JudgeVerdict)
    guard = CostGuard(load_price_table(), args.max_cost_yuan) if real else None
    answered_by = answer_adapter.provider_label if answer_adapter is not None else "echo:context-text"

    async def run() -> list[tuple[str, Path]]:
        with tempfile.TemporaryDirectory(prefix="rw-eval-") as td:
            harness_runs = await conditions.run_harness(
                args.dataset,
                conditions_list,
                judge,
                budget=args.budget,
                tmp_root=Path(td),
                answer_adapter=answer_adapter,
                answer_system=ANSWER_SYSTEM,
                cost_guard=guard,
            )
            pairs: list[tuple[str, Path]] = []
            for cond, run_obj in zip(conditions_list, harness_runs, strict=True):
                out_path = _result_filename(args.results_dir, date, cond)
                payload = {
                    "schema": "rw-eval-result/2",
                    "run_id": run_id,
                    "date": date,
                    "condition": cond,
                    "dataset": str(args.dataset),
                    "protocol": protocol,
                    "budget_tokens": args.budget,
                    "provider": {
                        "llm": settings.llm_provider,
                        "embedding": settings.embedding_provider,
                        "judge": judge.judge_label,
                        "answered_by": answered_by,
                        "note": PROVIDER_NOTE_REAL if real else PROVIDER_NOTE_MOCK,
                    },
                    "items": [r.__dict__ for r in run_obj.records],
                    "metrics": run_obj.metrics(),
                    "budget_guard": None
                    if guard is None
                    else {
                        "max_cost_yuan": args.max_cost_yuan,
                        "spent_yuan_upper_est": round(guard.spent_yuan, 6),
                        "tokens_prompt": guard.tokens_prompt,
                        "tokens_completion": guard.tokens_completion,
                        "price_table": "configs/prices.toml (UPPER-BOUND placeholders)",
                    },
                    "uncomputed": {
                        "cost_yuan": None,
                        "ttft_ms": None,
                        "cache_hit_tokens": None,
                        "note": (
                            "null by protocol: console-priced cost needs the §7.4 console "
                            "check; TTFT/cache-hit live in bench/latency.py — never fabricated."
                        )
                        if real
                        else (
                            "provider-usage metrics need a REAL provider; always null in echo "
                            "mode — never fabricated."
                        ),
                    },
                }
                _write_once(out_path, payload)
                pairs.append((cond, out_path))
        return pairs

    try:
        pairs = asyncio.run(run())
    finally:
        if answer_adapter is not None:
            _close_quiet(answer_adapter)
        if judge_adapter is not None and judge_adapter is not answer_adapter:
            _close_quiet(judge_adapter)

    # derived summary (also write-once): consumed by humans, raw files stay raw
    rows = [
        json.loads(path.read_text(encoding="utf-8"))["metrics"] | {"condition": cond} for cond, path in pairs
    ]
    manifest = {
        "run_id": run_id,
        "date": date,
        "dataset": str(args.dataset),
        "protocol": protocol,
        "judge_label": judge.judge_label,
        "answered_by": answered_by,
        "budget_tokens": args.budget,
        "budget_guard": None
        if guard is None
        else {
            "max_cost_yuan": args.max_cost_yuan,
            "spent_yuan_upper_est": round(guard.spent_yuan, 6),
            "price_table": "configs/prices.toml (UPPER-BOUND placeholders)",
        },
    }
    summary_path = args.results_dir / f"summary-{date}-{run_id}.md"
    _write_once_text(summary_path, _summary_markdown(manifest, rows))
    print(summary_path.read_text(encoding="utf-8"))
    for _cond, path in pairs:
        print(f"result file: {path}")
    if guard is not None:
        print(
            f"cost guard: spent upper-est {guard.spent_yuan:.4f}¥ / cap {args.max_cost_yuan}¥ "
            f"(provider tokens: {guard.tokens_prompt} in / {guard.tokens_completion} out)"
        )


if __name__ == "__main__":
    main()
