"""Dataset download/convert bridge (proposal §7.1) — operational, fail-closed.

Sources are pinned in configs/datasets.toml (url + sha256 + license). Actions:

  uv run python scripts/download_data.py list
  uv run python scripts/download_data.py download --dataset both
  uv run python scripts/download_data.py convert --dataset longmemeval_s --total 30
  uv run python scripts/download_data.py convert --dataset locomo10 --total 30

Invariants:
  * download verifies sha256 BEFORE anything else; mismatch → SystemExit.
  * converted subsets are Deterministic: stratified per question-type quota,
    random.Random(seed) selection → same pinned source + same params produce a
    BYTE-IDENTICAL jsonl (rerun to verify; sha256 recorded in the manifest).
  * raw sources and converted jsonl live in data/ (gitignored); only the small
    converted manifest is committed (evals/fixtures/converted-manifest.json).
  * preprocessing (session-date injection, speaker role mapping) is documented
    in docs/eval-datasets.md and implemented HERE as code.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import sys
import tomllib
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_DATASETS_TOML = _ROOT / "configs" / "datasets.toml"
_MANIFEST_PATH = _ROOT / "evals" / "fixtures" / "converted-manifest.json"

# Stratified per-type quotas for a 30-question subset (coverage-driven, NOT the
# natural distribution — rare types are intentionally boosted; documented).
_LME_QUOTA = {
    "multi-session": 6,
    "temporal-reasoning": 6,
    "knowledge-update": 5,
    "single-session-user": 5,
    "single-session-assistant": 4,
    "single-session-preference": 4,
}
_LOCOMO_CATEGORY = {"1": "single-hop", "2": "multi-hop", "3": "temporal", "4": "open-domain", "5": "adversarial"}
_LOCOMO_QUOTA = {"single-hop": 8, "multi-hop": 8, "temporal": 6, "open-domain": 4, "adversarial": 4}


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _load_pins() -> dict[str, dict]:
    rows = tomllib.loads(_DATASETS_TOML.read_text(encoding="utf-8"))["datasets"]
    return {r["id"]: r for r in rows}


def _fail(msg: str) -> None:
    print(f"download_data.py: REFUSAL — {msg}", file=sys.stderr)
    raise SystemExit(1)


def do_download(pins: dict[str, dict], which: str) -> None:
    for ds_id, pin in pins.items():
        if which != "both" and which != ds_id:
            continue
        target = _ROOT / pin["file"]
        if target.exists() and _sha256(target) == pin["sha256"]:
            print(f"[{ds_id}] already present and checksum-verified: {target}")
            continue
        if target.exists():
            _fail(
                f"{target} exists but sha256 ≠ pinned value. Delete it manually after "
                "checking no local edits are valuable, then rerun."
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        print(f"[{ds_id}] downloading {pin['url']}")
        urllib.request.urlretrieve(pin["url"], target)
        got = _sha256(target)
        if got != pin["sha256"]:
            target.unlink(missing_ok=True)
            _fail(f"sha256 mismatch after download: got {got}, pinned {pin['sha256']}")
        print(f"[{ds_id}] verified sha256 (source pin matches): {target} ({pin['size_bytes']} bytes)")


def _lme_subset(raw: list[dict], total: int, seed: int) -> list[dict]:
    by_type: dict[str, list[dict]] = {}
    for it in raw:
        by_type.setdefault(it["question_type"], []).append(it)
    rng = random.Random(seed)
    for items in by_type.values():
        rng.shuffle(items)
    picked: list[dict] = []
    pools = list(by_type.items())
    for t, q in _LME_QUOTA.items():
        picked.extend(by_type.get(t, [])[:q])
    if len(picked) > total:  # trim evenly for total overrides
        picked = picked[:total]
    assert len(picked) == sum(_LME_QUOTA.values()), "type quota does not sum to expected total"
    return picked


def _convert_lme_item(it: dict) -> dict:
    """Turns: sessions flattened in order; each session's first message is
    prefixed with its dated header (haystack_dates[i]) so temporal QA has the
    date IN TEXT (same information the benchmark stores in haystack_dates)."""
    turns: list[dict] = []
    sessions = it.get("haystack_sessions") or []
    dates = it.get("haystack_dates") or [""] * len(sessions)
    for i, session in enumerate(sessions):
        date = dates[i] if i < len(dates) else ""
        for j, msg in enumerate(session):
            role = msg.get("role", "user")
            content = msg.get("content") or ""
            if not isinstance(content, str):  # multimodal payloads not supported
                continue
            if j == 0 and date:
                content = f"[Session date: {date}] {content}"
            turns.append({"role": role, "content": content})
    assert turns, f"empty haystack for {it['question_id']}"
    return {
        "qid": f"lme-s:{it['question_id']}",
        "type": it["question_type"],
        "turns": turns,
        "question": it["question"],
        "reference": str(it["answer"]),
    }


def _convert_locomo_item(conv: dict, qa: dict) -> dict:
    """All turns are role=user with the SPEAKER NAME prefixed into the content:
    the memory pipeline extracts on user-role turns, and limiting memory to one
    speaker would silently bias the benchmark (QA concerns both speakers).
    Session dates become a header line so category-3 (temporal) QA sees dates."""
    conversation = conv["conversation"]
    speakers = {conversation.get("speaker_a", "A"), conversation.get("speaker_b", "B")}
    turns: list[dict] = []
    sessions = sorted(
        ((int(k.rsplit("_", 1)[1]), k) for k in conversation if k.startswith("session_") and not k.endswith(("date_time",))),
    )
    for n, key in sessions:
        date_time = conversation.get(f"{key}_date_time")
        msgs = conversation[key]
        if date_time:
            turns.append({"role": "user", "content": f"[Session {n} date_time: {date_time}]"})
        for msg in msgs:
            if msg.get("speaker") in speakers:
                turns.append({"role": "user", "content": f"{msg['speaker']}: {msg['text']}"})
    category = _LOCOMO_CATEGORY.get(str(qa.get("category")), f"category-{qa.get('category')}")
    return {
        "qid": f"locomo:{conv.get('sample_id', conv.get('conversation_id'))}:{qa.get('evidence')}",
        "type": category,
        "turns": turns,
        "question": qa["question"],
        "reference": str(qa.get("answer", "")),
    }


def do_convert(pins: dict[str, dict], ds_id: str, total: int, seed: int) -> None:
    pin = pins.get(ds_id)
    if not pin:
        _fail(f"unknown dataset {ds_id!r}; pinned ids: {sorted(pins)}")
    src = _ROOT / pin["file"]
    if not src.exists() or _sha256(src) != pin["sha256"]:
        _fail(f"source missing or checksum mismatch ({src}); run `download` first.")
    raw = json.loads(src.read_text(encoding="utf-8"))
    out_dir = _ROOT / "data" / "datasets" / "converted"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{pin['id']}-t{total}-seed{seed}.jsonl"

    if ds_id == "longmemeval_s":
        items: list[dict] = []
        for orig in _lme_subset(raw, total, seed):
            items.append(_convert_lme_item(orig))
    elif ds_id == "locomo10":
        rng = random.Random(seed)
        buckets: dict[str, list[dict]] = {v: [] for v in _LOCOMO_QUOTA}
        for conv in raw:
            for qa in conv.get("qa", []):
                cat = _LOCOMO_CATEGORY.get(str(qa.get("category")))
                if cat in buckets:
                    buckets[cat].append((conv, qa))
        for lst in buckets.values():
            rng.shuffle(lst)
        items = []
        for cat, quota in _LOCOMO_QUOTA.items():
            # round-robin conversations so one chat does not dominate the sample
            chosen = buckets[cat][:quota]
            for conv, qa in chosen:
                items.append(_convert_locomo_item(conv, qa))
    else:
        _fail(f"no converter for {ds_id}")

    text = "".join(json.dumps(o, ensure_ascii=False) + "\n" for o in items)
    out_path.write_text(text, encoding="utf-8")
    counts = {}
    for o in items:
        counts[o["type"]] = counts.get(o["type"], 0) + 1
    manifest_rows = json.loads(_MANIFEST_PATH.read_text(encoding="utf-8")) if _MANIFEST_PATH.exists() else []
    manifest_rows[:] = [r for r in manifest_rows if r["output"] != str(out_path.relative_to(_ROOT))]
    manifest_rows.append(
        {
            "dataset": ds_id,
            "output": str(out_path.relative_to(_ROOT)),
            "output_sha256": _sha256(out_path),
            "items": len(items),
            "counts_by_type": counts,
            "source_sha256": pin["sha256"],
            "source_url": pin["url"],
            "license": pin["license"],
            "total": total,
            "seed": seed,
            "generated_at": datetime.now(UTC).isoformat(),
            "note": "byte-identical rerun expected: same source pin + same seed + same quotas",
        }
    )
    _MANIFEST_PATH.write_text(json.dumps(manifest_rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[{ds_id}] converted → {out_path} ({len(items)} items, types={counts})")
    print(f"         output sha256: {_sha256(out_path)} (recorded in {rel_path_of(_MANIFEST_PATH)})")


def rel_path_of(p: Path) -> str:
    return str(p.relative_to(_ROOT))


def do_list(pins: dict[str, dict]) -> None:
    for ds_id, pin in pins.items():
        present = (_ROOT / pin["file"]).exists()
        state = "present" if present and _sha256(_ROOT / pin["file"]) == pin["sha256"] else ("present, checksum differs" if present else "not downloaded")
        print(f"- {ds_id}: {pin['source']}")
        print(f"    license={pin['license']} | pinned={pin['pinned']} | sha256={pin['sha256'][:12]}…")
        print(f"    local: {pin['file']} [{state}]")


def main() -> int:
    ap = argparse.ArgumentParser(description="Pinned eval datasets: download / verify / convert")
    ap.add_argument("action", choices=["list", "download", "convert"])
    ap.add_argument("--dataset", default="both", help="both | longmemeval_s | locomo10 (convert: single)")
    ap.add_argument("--total", type=int, default=30, help="converted subset size")
    ap.add_argument("--seed", type=int, default=2026)
    args = ap.parse_args()
    pins = _load_pins()
    if args.action == "list":
        do_list(pins)
    elif args.action == "download":
        do_download(pins, args.dataset)
    else:
        if args.dataset == "both":
            for ds in sorted(pins):
                do_convert(pins, ds, args.total, args.seed)
        else:
            do_convert(pins, args.dataset, args.total, args.seed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
