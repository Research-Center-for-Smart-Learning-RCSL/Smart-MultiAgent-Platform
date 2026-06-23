"""Chunkers (R10.04).

Two strategies only — `fixed` and `semantic`.

`fixed` uses a simple whitespace-tokenised sliding window. We deliberately
avoid a hard dependency on tiktoken for token counts — counting whitespace
tokens is close enough for chunk sizing (true embedding token counts only
matter at the embedder call, which is provider-specific) and keeps the
chunker dependency-free.

`semantic` delegates to the `semantic-text-splitter` wheel, which is a hard
requirement of the strategy. When the wheel is missing :func:`chunk_semantic`
raises :class:`ChunkParamsInvalid` rather than falling back, so chunk
boundaries — and the embeddings derived from them — never diverge silently
between an environment that has the wheel and one that does not.

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
    """Capacity-bounded semantic chunking via ``semantic-text-splitter``.

    ``similarity_threshold`` is part of the persisted ``chunk_params``
    contract (REQUIREMENTS.md R10.04) and is validated here, but the current
    ``semantic-text-splitter`` backend splits purely by token *capacity* at
    sentence/paragraph boundaries — it has no embedding-similarity cutoff, so
    the value is not consumed. It is kept reserved for a future
    similarity-aware backend; validating it now keeps stored configs honest.

    The wheel is mandatory for this strategy: a missing import raises
    :class:`ChunkParamsInvalid` instead of falling back, so chunk boundaries
    (and the embeddings derived from them) are identical across environments.
    """
    if max_tokens_per_chunk <= 0:
        raise ChunkParamsInvalid("max_tokens_per_chunk must be positive")
    if not 0.0 < similarity_threshold <= 1.0:
        raise ChunkParamsInvalid("similarity_threshold must be in (0, 1]")
    try:
        # Naming: the package installs as `semantic-text-splitter` and
        # imports as `semantic_text_splitter`. Lazy-import so a missing
        # wheel breaks only use of this strategy, not module load.
        from semantic_text_splitter import TextSplitter  # type: ignore[import-not-found]
    except ImportError as exc:
        raise ChunkParamsInvalid(
            "the 'semantic' chunk strategy requires the optional "
            "'semantic-text-splitter' wheel, which is not installed; "
            "install it or switch the RAG config to the 'fixed' strategy",
        ) from exc

    splitter = TextSplitter(capacity=max_tokens_per_chunk)
    return [c for c in splitter.chunks(text) if c.strip()]


def chunk_text(
    text: str,
    *,
    strategy: ChunkStrategy,
    params: dict[str, Any],
) -> list[str]:
    """Dispatch with sane defaults for missing params."""
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
        return chunk_semantic(text, max_tokens_per_chunk=max_tok, similarity_threshold=sim_thresh)
    raise ChunkParamsInvalid(f"unknown chunk strategy {strategy!r}")
