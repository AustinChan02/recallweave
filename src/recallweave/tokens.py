"""Token budgeting.

tiktoken (cl100k_base) is close enough for budgeting modern chat models (DeepSeek/Qwen
count differently but within ~±20%; budgets are soft caps, documented in ADR-003).
Fallback heuristic: CJK ≈ 1 token/char (upper bound), latin ≈ word/1.3 — never crashes.
"""

from __future__ import annotations

import re

_TOKEN_RE = re.compile(r"[a-zA-Z]+|\d+")
_CJK_RE = re.compile(r"[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]")


class TokenCounter:
    def __init__(self) -> None:
        self._enc = None
        try:
            import tiktoken  # type: ignore

            self._enc = tiktoken.get_encoding("cl100k_base")
        except Exception:  # pragma: no cover - degrade path
            self._enc = None

    def count(self, text: str) -> int:
        if not text:
            return 0
        if self._enc is not None:
            try:
                return len(self._enc.encode(text))
            except Exception:  # pragma: no cover
                pass
        latin = len(_TOKEN_RE.findall(text))
        cjk = len(_CJK_RE.findall(text))
        return cjk + max(1, int(latin / 1.3))

    def count_messages(self, messages: list[dict]) -> int:
        return sum(self.count(m.get("content", "")) + 4 for m in messages)


_counter = TokenCounter()


def counter() -> TokenCounter:
    return _counter
