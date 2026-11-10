"""Generate a blind-scoring draft for the 30-question human review (P0 阶段 3).

Protocol anchor: `evals/human-review-template.md` — the human scores WITHOUT
seeing the judge's numbers first (anchoring is the failure mode this guards).
This script makes that mechanical:

  * reads a REAL-provider result file (rw-eval-result/2) + the converted
    dataset jsonl (question/reference live in the dataset, not the result);
  * emits a write-once draft table with ONE row per judged item
    (qid / item_type / question / reference / model answer) — the judge
    scores are deliberately EXCLUDED from the scoring table;
  * judge scores + rationales land in a clearly separated bottom section,
    to be opened only AFTER all rows are scored;
  * the draft contains ALL judged items — up to 30 per result file here; if
    a future run has more, the template's stratified-sampling rule applies
    and the header says so.

The completed review (with 人工分 filled) is the user's deliverable, saved as
`human-review-<run_id>.md` per the template — one file per run, write-once.

Usage (offline, no API access):
  uv run python scripts/make_human_review.py \
      --result evals/results/eval-2026-11-10-C3-run1.json \
      --dataset data/datasets/converted/longmemeval_s-t30-seed2026.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_TEMPLATE = "evals/human-review-template.md"
_ANCHORS = (
    "1.0 完整且与参考一致 ｜ 0.5 部分正确 ｜ 0.0 错误/矛盾/编造/拒答但参考有答案 ｜ 弃判=参考可疑（出分母）"
)


def _cell(text: str) -> str:
    """Table-safe, near-lossless: pipes escaped, newlines shown as ⏎
    (verbatim source stays authoritative in the result file)."""
    return (text or "").replace("|", "\\|").replace("\n", " ⏎ ").strip()


def _load_dataset_rows(dataset: Path) -> dict[str, dict]:
    rows: dict[str, dict] = {}
    with dataset.open(encoding="utf-8") as fh:
        for n, line in enumerate(fh, 1):
            row = json.loads(line)
            qid = row.get("qid")
            if not qid:
                raise SystemExit(f"dataset row {n} has no qid — refusing to guess")
            rows[qid] = row
    return rows


def _build(result_path: Path, dataset_path: Path, out_path: Path) -> str:
    run = json.loads(result_path.read_text(encoding="utf-8"))
    rows = _load_dataset_rows(dataset_path)

    prov = run["provider"]
    items = [i for i in run["items"] if i.get("judged")]
    missing = [i["qid"] for i in items if i["qid"] not in rows]
    if missing:
        raise SystemExit(f"judged qids absent from dataset file: {missing[:5]}")

    lines = [
        f"# Human review draft — run `{run['run_id']}` / condition `{run['condition']}`",
        f"- protocol: `{run['protocol']}` ｜ judge: `{prov['judge']}`"
        f" ｜ answered_by: `{prov['answered_by']}`",
        f"- judged items: {len(items)} ｜ 抽样规则见 `{_TEMPLATE}`（≤30 全判；>30 分层抽样 30）",
        f"- 评分锚点：{_ANCHORS}",
        "- 规则：先全部打完再看文末 judge 对照（防锚定）；换行以 ⏎ 标示，`|` 转义为 `\\|`——",
        "  原文以 result 文件为准。正式送审文件名应为 `human-review-<run_id>.md`（write-once）。",
        "",
        "| # | qid | type | question | reference | answer（model 原文） | 人工分 | 备注 |",
        "|---|-----|------|----------|-----------|----------------------|--------|------|",
    ]
    for n, it in enumerate(items, 1):
        drow = rows[it["qid"]]
        lines.append(
            f"| {n} | {it['qid']} | {drow.get('type', '')} "
            f"| {_cell(drow.get('question', ''))} | {_cell(drow.get('reference', ''))} "
            f"| {_cell(it.get('answer', ''))} |  |  |"
        )

    lines += [
        "",
        "## ↓↓ judge 对照（评分期间勿看；打完后再核对并算一致率）↓↓",
        "",
        "| qid | judge 分 | judge rationale |",
        "|-----|----------|-----------------|",
    ]
    for it in items:
        lines.append(f"| {it['qid']} | {it.get('score', '')} | {_cell(it.get('rationale', ''))[:120]} |")
    content = "\n".join(lines) + "\n"

    if out_path.exists():
        raise SystemExit(f"WRITE-ONCE VIOLATION: draft exists: {out_path}")
    out_path.write_text(content, encoding="utf-8")
    return content


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate one blind human-review draft per result file")
    ap.add_argument("--result", type=Path, required=True, help="a rw-eval-result/2 result file")
    ap.add_argument("--dataset", type=Path, required=True, help="the matching converted dataset jsonl")
    ap.add_argument(
        "--out",
        type=Path,
        default=None,
        help="default: evals/results/human-review-<run_id>-<condition>-draft.md",
    )
    args = ap.parse_args()
    run = json.loads(args.result.read_text(encoding="utf-8"))
    out = args.out or args.result.parent / (f"human-review-{run['run_id']}-{run['condition']}-draft.md")
    _build(args.result, args.dataset, out)
    print(f"draft written: {out}  (offline; judge scores inside are masked to the bottom)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
