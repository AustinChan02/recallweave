#!/usr/bin/env bash
# RecallWeave live demo (mirrors the README quickstart): walks the REST API
# end to end against a RUNNING local server — every line printed below comes
# off the wire; nothing is faked.
#
# OFFLINE HONESTY: this demo REQUIRES the server in mock mode
# (LLM_PROVIDER=mock + EMBEDDING_PROVIDER=hash). Every response carries its
# `provider` label — mock content is mechanics-only and is NOT a measured
# result (see README §诚实性).
#
# Start it like this (the script health-checks and tells you how):
#   uv sync && RECALLWEAVE_LLM_PROVIDER=mock make run    # http://127.0.0.1:8100
#
# Usage: ./scripts/demo.sh
set -euo pipefail

BASE=http://127.0.0.1:8100
AUTH="Authorization: Bearer dev-key-1"
SESSION=$(python3 -c 'import uuid;print(uuid.uuid4())')

say()  { printf '\n\033[1;34m== %s\033[0m\n' "$*"; }
show() { printf '%s\n' "$*"; }
jqpy() { python3 -c "import json,sys; d=json.load(sys.stdin); $1"; }

say "0) Health check (offline demo requires mock providers)"
curl -fsS "$BASE/v1/health" >/dev/null 2>&1 || {
  echo "server NOT up. start:  RECALLWEAVE_LLM_PROVIDER=mock make run"; exit 1; }
PROV=$(curl -fsS "$BASE/v1/health" | jqpy 'print(d["llm_provider"])')
if [[ "$PROV" != "mock" ]]; then
  echo "server is running with llm_provider=$PROV (real API key from .env)."
  echo "for the OFFLINE demo restart it in mock mode:"
  echo "  RECALLWEAVE_LLM_PROVIDER=mock make run"
  exit 1
fi
show "GET /v1/health:"
curl -fsS "$BASE/v1/health" | jqpy 'print(json.dumps(d, ensure_ascii=False)[:260])'

say "1) Write pipeline under mock mode: extract REFUSES to invent memories"
show "POST /v1/memories:extract  (session=$SESSION)"
curl -fsS -X POST "$BASE/v1/memories:extract" -H "$AUTH" -H 'Content-Type: application/json' \
  -d "{\"session_id\":\"$SESSION\",\"messages\":[
        {\"role\":\"user\",\"content\":\"我对花生过敏，千万不要给我推荐含花生的菜。\"},
        {\"role\":\"assistant\",\"content\":\"好的，已记录：花生过敏。\"},
        {\"role\":\"user\",\"content\":\"我平时喜欢吃粤菜，尤其是清蒸鱼。\"},
        {\"role\":\"user\",\"content\":\"对了，我周四晚上一般要加班，别约那个时间。\"}]}" \
  | jqpy '
print("provider:", d.get("provider"), "| extracted:", d.get("extracted"))
if d.get("extraction_error"):
    print("  extraction_error:", d["extraction_error"])
    print("  -> honesty design: the live mock has an EMPTY scripted queue and")
    print("     will never silently answer for a real provider (fail-closed).")'

say "1b) Seed demo memories: scripts/seed_demo.py (DECLARED scripted mock, labeled)"
uv run python scripts/seed_demo.py

say "2) Hybrid semantic search (vector + lexical + recency lanes, RRF-fused)"
show "POST /v1/memories:search  query=用户能吃什么 k=3"
curl -fsS -X POST "$BASE/v1/memories:search" -H "$AUTH" -H 'Content-Type: application/json' \
  -d '{"query":"用户能吃什么","k":3}' \
  | jqpy '
print("provider:", d.get("provider"))
for it in d.get("items", []):
    m = it.get("memory", {})
    score = it.get("score")
    lanes = it.get("lanes")
    print("  [%s] lanes=%s %s" % (score, lanes, (m.get("content") or "")[:64]))'

say "3) Context compiler (the headline module): layered context under a token budget"
show "POST /v1/context:compile  budget_tokens=600"
curl -fsS -X POST "$BASE/v1/context:compile" -H "$AUTH" -H 'Content-Type: application/json' \
  -d "{\"session_id\":\"$SESSION\",\"messages\":[
        {\"role\":\"user\",\"content\":\"帮我推荐一个周四能吃的工作餐，注意我的忌口。\"}],
       \"budget_tokens\":600}" \
  | jqpy '
msgs=d.get("context_messages") or []
u=d.get("usage") or {}
print("provider:", d.get("provider"), "| messages:", len(msgs),
      "| layers:", json.dumps(u.get("layers"), ensure_ascii=False),
      "| total_tokens:", u.get("total_tokens"), "/", u.get("budget"))
if msgs: print("system head:", (msgs[0].get("content") or "")[:96])'

say "4) SDK view of the same flow (recallweave.sdk, mock mode, offline)"
uv run sdk_demo.py

say "5) Stats + OpenAPI docs"
show "GET /v1/stats:"
curl -fsS "$BASE/v1/stats" -H "$AUTH" | jqpy 'print(json.dumps(d, ensure_ascii=False)[:360])'
show "OpenAPI docs: $BASE/docs"
show "SDK demo: sdk_demo.py  |  this demo: scripts/demo.sh"

say "Demo complete — every response carried its honest provider label (mock = mechanics only)"
