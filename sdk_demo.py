"""SDK demo against a RUNNING server (README quickstart step 3).

Prereq: `make run` in another shell (http://127.0.0.1:8100), or set
RECALLWEAVE_BASE_URL. Fully offline (server defaults to mock LLM + hash
embeddings): output carries honest provider labels — mock content is
mechanics-only and must not be quoted as measured results.
"""

from __future__ import annotations

import os

from recallweave.sdk import RecallWeaveAPIError, RecallWeaveClient

URL = os.environ.get("RECALLWEAVE_BASE_URL", "http://127.0.0.1:8100")
KEY = os.environ.get("RECALLWEAVE_DEV_KEY", "dev-key-1")


def main() -> int:
    with RecallWeaveClient(URL, api_key=KEY) as c:
        print("health:", c.health())

        turns = [
            {"role": "user", "content": "我对花生过敏，吃了花生会起疹子。"},
            {"role": "assistant", "content": "记下了。"},
        ]
        report = c.ingest(turns)
        if report["extraction_error"]:
            print(
                "ingest: fail-closed (server mock-LLM queue is empty → extraction_error, "
                "zero writes). This is the honesty design: offline no LLM run is faked."
            )
        else:
            print("ingest:", report)
        print("search:", c.search("用户能吃什么", k=3))

        out = c.compile([{"role": "user", "content": "我吃什么要小心？"}], budget_tokens=600)
        print("compile.system_head:", out["context_messages"][0]["content"][:120])
        print("compile.usage:", out["usage"])
        print("provider labels:", out["provider"], "/ search provider is local-hybrid")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RecallWeaveAPIError as exc:
        print(f"API error: [{exc.status}] {exc.code}: {exc.message}")
        print("(did you start the server first? `make run`)")
        raise SystemExit(1) from exc
    except ConnectionError as exc:
        print(f"server unreachable at {URL}: {exc}")
        print("(start it first: `make run`)")
        raise SystemExit(1) from exc
