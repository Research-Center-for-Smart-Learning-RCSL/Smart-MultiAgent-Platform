"""Chunkers (R10.04).

Two strategies — ``fixed`` and ``semantic``.

``fixed`` uses a whitespace-tokenised sliding window. We deliberately avoid a
hard dependency on tiktoken for token counts — counting whitespace tokens is
close enough for chunk sizing (true embedding token counts only matter at the
embedder call, which is provider-specific) and keeps the fixed path dependency-
free and pure.

``semantic`` is *similarity-aware*: it splits the text into sentences, embeds
them, and grows a chunk sentence-by-sentence until either the running chunk
diverges from the next sentence (cosine similarity to the chunk centroid drops
below ``similarity_threshold``) or the chunk reaches ``max_tokens_per_chunk``.
This consumes ``similarity_threshold`` (R10.04) — a higher threshold demands
tighter topical cohesion and yields smaller, more focused chunks. Because it
needs to embed, the semantic path is async and takes an embedder; the fixed
path stays pure.

SoC: ``fixed`` is a pure function over text. ``semantic`` performs embedding
I/O via the injected embedder only — no other side effects.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable
from typing import Any, Protocol

from contexts.knowledge.domain.errors import ChunkParamsInvalid
from contexts.knowledge.domain.models import (
    DEFAULT_FIXED_CHUNK_PARAMS,
    DEFAULT_SEMANTIC_CHUNK_PARAMS,
    ChunkStrategy,
)

__all__ = ["chunk_document", "chunk_fixed", "chunk_semantic"]


class _Embedder(Protocol):
    """Minimal embedder surface the semantic chunker needs (see ports.Embedder)."""

    async def embed_batch(self, texts: list[str]) -> list[list[float]]: ...


# Embed sentences in bounded batches so a large document can't 413 the provider
# or hold every sentence vector at once (mirrors IngestService._EMBED_BATCH).
_SENTENCE_EMBED_BATCH = 128

# Sentence/paragraph boundaries: after .!? + whitespace, or a blank line. Cheap
# and dependency-free — exact sentence segmentation is unnecessary because the
# similarity pass, not the split, decides the real chunk boundaries.
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+|\n{2,}")


def _tokens(text: str) -> list[str]:
    """Whitespace tokenisation. Cheap, deterministic, dependency-free."""
    return text.split()


def _detokenise(tokens: Iterable[str]) -> str:
    return " ".join(tokens)


def chunk_fixed(
    text: str,
    *,
    chunk_size_tokens: int,
    chunk_overlap_tokens: int,
) -> list[str]:
    if chunk_size_tokens <= 0:
        raise ChunkParamsInvalid("chunk_size_tokens must be positive")
    if chunk_overlap_tokens < 0 or chunk_overlap_tokens >= chunk_size_tokens:
        raise ChunkParamsInvalid("chunk_overlap_tokens must be in [0, chunk_size)")

    tokens = _tokens(text)
    if not tokens:
        return []
    step = chunk_size_tokens - chunk_overlap_tokens
    out: list[str] = []
    i = 0
    while i < len(tokens):
        window = tokens[i : i + chunk_size_tokens]
        out.append(_detokenise(window))
        if i + chunk_size_tokens >= len(tokens):
            break
        i += step
    return out


def _split_sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_RE.split(text) if s.strip()]


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


async def _embed_sentences(embedder: _Embedder, sentences: list[str]) -> list[list[float]]:
    out: list[list[float]] = []
    for start in range(0, len(sentences), _SENTENCE_EMBED_BATCH):
        out.extend(await embedder.embed_batch(sentences[start : start + _SENTENCE_EMBED_BATCH]))
    return out


async def chunk_semantic(
    text: str,
    *,
    embedder: _Embedder,
    max_tokens_per_chunk: int,
    similarity_threshold: float,
) -> list[str]:
    """Similarity-aware semantic chunking.

    Grows a chunk sentence-by-sentence, closing it when the next sentence is
    less similar to the chunk's running centroid than ``similarity_threshold``
    (a topic shift) or when the chunk would exceed ``max_tokens_per_chunk``. A
    single sentence longer than the capacity becomes its own chunk — sentences
    are never split mid-way.
    """
    if max_tokens_per_chunk <= 0:
        raise ChunkParamsInvalid("max_tokens_per_chunk must be positive")
    if not 0.0 < similarity_threshold <= 1.0:
        raise ChunkParamsInvalid("similarity_threshold must be in (0, 1]")

    sentences = _split_sentences(text)
    if not sentences:
        return []
    vectors = await _embed_sentences(embedder, sentences)
    if len(vectors) != len(sentences):
        # A short vector list would silently misalign sentences with embeddings
        # and corrupt every downstream boundary decision — fail instead.
        raise ChunkParamsInvalid(
            f"embedder returned {len(vectors)} vectors for {len(sentences)} sentences"
        )

    chunks: list[str] = []
    current: list[str] = []
    current_tokens = 0
    centroid: list[float] | None = None
    count = 0

    for sentence, vec in zip(sentences, vectors, strict=True):
        sentence_tokens = len(_tokens(sentence))
        over_capacity = current_tokens + sentence_tokens > max_tokens_per_chunk
        topic_shift = centroid is not None and _cosine(centroid, vec) < similarity_threshold
        if current and (over_capacity or topic_shift):
            chunks.append(" ".join(current))
            current, current_tokens, centroid, count = [], 0, None, 0

        current.append(sentence)
        current_tokens += sentence_tokens
        count += 1
        # Incremental running-mean centroid of the chunk's sentence vectors.
        if centroid is None:
            centroid = list(vec)
        else:
            centroid = [c + (v - c) / count for c, v in zip(centroid, vec, strict=True)]

    if current:
        chunks.append(" ".join(current))
    return chunks


async def chunk_document(
    text: str,
    *,
    strategy: ChunkStrategy,
    params: dict[str, Any],
    embedder: _Embedder,
) -> list[str]:
    """Dispatch to the configured strategy with sane defaults for missing params.

    ``embedder`` is required by the ``semantic`` strategy and ignored by
    ``fixed`` (kept in the signature so the ingest path can call uniformly).
    """
    if strategy is ChunkStrategy.FIXED:
        merged = {**DEFAULT_FIXED_CHUNK_PARAMS, **(params or {})}
        try:
            size = int(merged["chunk_size_tokens"])
            overlap = int(merged["chunk_overlap_tokens"])
        except (ValueError, TypeError) as exc:
            raise ChunkParamsInvalid(f"invalid fixed chunk params: {exc}") from exc
        return chunk_fixed(text, chunk_size_tokens=size, chunk_overlap_tokens=overlap)
    if strategy is ChunkStrategy.SEMANTIC:
        merged = {**DEFAULT_SEMANTIC_CHUNK_PARAMS, **(params or {})}
        try:
            max_tok = int(merged["max_tokens_per_chunk"])
            sim_thresh = float(merged["similarity_threshold"])
        except (ValueError, TypeError) as exc:
            raise ChunkParamsInvalid(f"invalid semantic chunk params: {exc}") from exc
        return await chunk_semantic(
            text,
            embedder=embedder,
            max_tokens_per_chunk=max_tok,
            similarity_threshold=sim_thresh,
        )
    raise ChunkParamsInvalid(f"unknown chunk strategy {strategy!r}")
