"""Dataset download bridge (proposal §7.1) — intentionally not operational yet.

LongMemEval/LoCoMo must be pinned to a release + license before reproducible
checksums can be published (§7.1: 数据集不入仓). Running this prints exactly
what remains to be decided instead of half-downloading into the repo.
"""

from __future__ import annotations

import sys

DATASETS = {
    "longmemeval_s": {
        "source": "LongMemEval-S (pending: pin release + license check)",
        "items": "~120-150 questions",
        "license": "确认后写入 docs/eval-datasets.md",
    },
    "locomo": {
        "source": "LoCoMo subset (pending: pin release + license check)",
        "items": "~100 questions (multi-hop/temporal/adversarial)",
        "license": "确认后写入 docs/eval-datasets.md",
    },
}


def main() -> int:
    print("download_data.py: NOT YET OPERATIONAL (intentional, honesty-first).")
    print("Before it can run, the following must be pinned in docs/eval-datasets.md:")
    for name, meta in DATASETS.items():
        print(f"- {name}: {meta['source']}; {meta['items']}; {meta['license']}")
    print("Mini dataset for offline smoke: evals/fixtures/mini_eval.jsonl (in-repo, synthetic).")
    return 1


if __name__ == "__main__":
    sys.exit(main())
