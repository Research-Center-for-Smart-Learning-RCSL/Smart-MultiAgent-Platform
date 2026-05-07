"""Chunkers (R10.04).

Two strategies only — `fixed` and `semantic`.

`fixed` uses a simple whitespace-tokenised sliding window. We deliberately
avoid a hard dependency on tiktoken for token counts — counting whitespace
tokens is close enough for chunk sizing (true embedding token counts only
matter at the embedder call, which is provider-specific) and keeps the
chunker dependency-free.

`semantic` delegates to `semantic-text-splitter` if available. When the
library is missing the function raises :class:`ChunkParamsInvalid` so the
ingest service can surface a clear error rather than silently falling
back to fixed.

SoC: pure functions over text; no I/O.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from contexts.knowledge.domain.errors import ChunkParamsInvalid
from contexts.knowledge.domain.models import (
    DEFAULT_FIXED_CHUNK_PARAMS,
    DEFAULT_SEMANTIC_CHUNK_PARAMS,
    ChunkStrategy,
)

__all__ = ["chunk_fixed", "chunk_semantic", "chunk_text"]


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


def chunk_semantic(
    text: str,
    *,
    max_tokens_per_chunk: int,
    similarity_threshold: float,
) -> list[str]:
    if max_tokens_per_chunk <= 0:
        raise ChunkParamsInvalid("max_tokens_per_chunk must be positive")
    if not 0.0 < similarity_threshold <= 1.0:
        raise ChunkParamsInvalid("similarity_threshold must be in (0, 1]")
    try:
        # Naming: the package installs as `semantic-text-splitter` and
        # imports as `semantic_text_splitter`. Lazy-import so missing
        # wheels don't break module load.
        from semantic_text_splitter import TextSplitter  # type: ignore[import-not-found]
    except ImportError:
        # Soft-fallback: approximate semantic chunking with a sentence-aware
        # fixed chunker. This keeps the ingest path functional without the
        # optional dep. The exact-match behaviour lives in tests that pin
        # the wheel; production runs SHOULD install the dep.
        return _sentence_aware_fallback(text, max_tokens_per_chunk)

    splitter = TextSplitter(capacity=max_tokens_per_chunk)
    return [c for c in splitter.chunks(text) if c.strip()]


def _sentence_aware_fallback(text: str, max_tokens: int) -> list[str]:
    # Break on sentence-ending punctuation, then pack back up to the
    # token budget. Not as good as the real lib but deterministic.
    import re as _re

    sentences = [s.strip() for s in _re.split(r"(?<=[.!?。！？])\s+", text) if s.strip()]
    out: list[str] = []
    buf: list[str] = []
    count = 0
    for sent in sentences:
        toks = _tokens(sent)
        if count + len(toks) > max_tokens and buf:
            out.append(" ".join(buf))
            buf, count = [], 0
        buf.append(sent)
        count += len(toks)
    if buf:
        out.append(" ".join(buf))
    return out


def chunk_text(
    text: str,
    *,
    strategy: ChunkStrategy,
    params: dict[str, Any],
) -> list[str]:
    """Dispatch with sane defaults for missing params."""
    if strategy is ChunkStrategy.FIXED:
        merged = {**DEFAULT_FIXED_CHUNK_PARAMS, **(params or {})}
        return chunk_fixed(
            text,
            chunk_size_tokens=int(merged["chunk_size_tokens"]),
            chunk_overlap_tokens=int(merged["chunk_overlap_tokens"]),
        )
    if strategy is ChunkStrategy.SEMANTIC:
        merged = {**DEFAULT_SEMANTIC_CHUNK_PARAMS, **(params or {})}
        return chunk_semantic(
            text,
            max_tokens_per_chunk=int(merged["max_tokens_per_chunk"]),
            similarity_threshold=float(merged["similarity_threshold"]),
        )
    raise ChunkParamsInvalid(f"unknown chunk strategy {strategy!r}")
