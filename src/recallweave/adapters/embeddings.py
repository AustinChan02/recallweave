"""Embedding adapters.

HashEmbedding: deterministic bag-of-token hashed pseudo-embeddings. Zero deps,
offline, stable across runs — **pipeline/stage fitting only**, similarity has no
semantic meaning (documented in ADR-001 + README honesty note).

BGEEmbedding: local BAAI/bge-small-zh-v1.5 (512-dim, L2-normalized) via
sentence-transformers — lazy import so the default offline dev path does not
require the model download.
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import Protocol, runtime_checkable

_TOKEN_RE = re.compile(r"[a-zA-Z]+|\d+|[\u4e00-\u9fff]")


def tokenize_for_lexical(text: str) -> list[str]:
    """Shared by HashEmbedding + the lexical retrieval lane (ADR-002: one tokenizer)."""
    latin = re.findall(r"[a-zA-Z]+|\d+", text)
    cjk = re.findall(r"[\u4e00-\u9fff]+", text)
    bigrams: list[str] = []
    for run in cjk:
        if len(run) == 1:
            bigrams.append(run)
        else:
            bigrams.extend(run[i : i + 2] for i in range(len(run) - 1))
    return [t.lower() for t in latin] + bigrams


def _hash_floats(seed: bytes, dim: int) -> list[float]:
    out: list[float] = []
    counter = 0
    while len(out) < dim:
        digest = hashlib.sha1(seed + counter.to_bytes(4, "big")).digest()
        for i in range(0, len(digest), 4):
            if len(out) >= dim:
                break
            b = digest[i : i + 4]
            out.append((int.from_bytes(b, "big") / 0xFFFFFFFF) * 2.0 - 1.0)
        counter += 1
    return out


@runtime_checkable
class EmbeddingAdapter(Protocol):
    dim: int
    provider: str

    async def embed_many(self, texts: list[str]) -> list[list[float]]: ...


class HashEmbedding:
    def __init__(self, dim: int = 512, cache_size: int = 8192) -> None:
        self.dim = dim
        self.provider = "hash"
        self._cache: dict[str, list[float]] = {}
        self._cache_size = cache_size

    async def embed_many(self, texts: list[str]) -> list[list[float]]:
        return [self._one(t) for t in texts]

    def _one(self, text: str) -> list[float]:
        hit = self._cache.get(text)
        if hit is not None:
            return hit
        acc = [0.0] * self.dim
        toks = tokenize_for_lexical(text.lower())
        for tok in toks[:64]:  # cap: pathological long contents must not explode latency
            vec = _hash_floats(hashlib.sha1(("rwv1:" + tok).encode()).digest(), self.dim)
            for i in range(self.dim):
                acc[i] += vec[i]
        if not toks:
            acc = _hash_floats(hashlib.sha1(("rwv1-empty:" + text).encode()).digest(), self.dim)
        vec = _l2_normalize(acc)
        if len(self._cache) >= self._cache_size:  # tiny FIFO keeps memory bounded
            self._cache.pop(next(iter(self._cache)))
        self._cache[text] = vec
        return vec


class BGEEmbedding:
    def __init__(self, model_name: str = "BAAI/bge-small-zh-v1.5") -> None:
        from sentence_transformers import SentenceTransformer  # lazy: heavy import

        self._model = SentenceTransformer(model_name)
        self.dim = int(self._model.get_sentence_embedding_dimension())
        self.provider = f"bge:{model_name}"

    async def embed_many(self, texts: list[str]) -> list[list[float]]:
        vecs = self._model.encode(texts, normalize_embeddings=True, show_progress_bar=False).tolist()
        return [list(map(float, v)) for v in vecs]


def make_embedder(settings) -> EmbeddingAdapter:
    if settings.embedding_provider == "bge":
        return BGEEmbedding()
    return HashEmbedding(dim=settings.embedding_dim)


def _l2_normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vec))
    if norm == 0.0:
        return vec
    return [x / norm for x in vec]
