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
tighter topical cohesion and yields smaller, more focused chunks.

Sentence splitting recognises both ASCII (``.!?``) and CJK/full-width
(``。！？；``) terminators and blank lines, and an oversized unit (a delimiter-
less blob — CJK without spaces, CSV, minified text) is hard-split to the
capacity so it can never become one giant chunk the embedder would reject.
Token length is estimated with one unit per CJK character so the capacity cap
fires for space-less scripts too.

Cost note: the semantic strategy embeds sentences to find boundaries AND the
ingest pipeline embeds the resulting chunks for storage — two passes over
similar token volume on the caller's BYO key. Reusing the sentence vectors
would change the stored chunk vectors to mean-pooled embeddings (a different,
lower-fidelity representation than embedding the chunk text), so the second
pass is intentional; ``fixed`` avoids it entirely.

SoC: ``fixed`` is a pure function over text. ``semantic`` performs embedding
I/O via the injected embedder only — no other side effects.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable
from itertools import islice
from typing import Any, Protocol

from contexts.knowledge.domain.errors import ChunkParamsInvalid
from contexts.knowledge.domain.models import (
    DEFAULT_FIXED_CHUNK_PARAMS,
    DEFAULT_SEMANTIC_CHUNK_PARAMS,
    MAX_CHUNK_OUTPUT_BYTES,
    ChunkStrategy,
    validate_chunk_params,
)
from shared_kernel.text_extraction.parsers import ResourceBudgetError

__all__ = ["chunk_document", "chunk_fixed", "chunk_semantic"]


class _Embedder(Protocol):
    """Minimal embedder surface the semantic chunker needs (see ports.Embedder)."""

    async def embed_batch(self, texts: list[str]) -> list[list[float]]: ...


# Embed sentences in bounded batches so a large document can't 413 the provider
# or hold every sentence vector at once (mirrors IngestService._EMBED_BATCH).
_SENTENCE_EMBED_BATCH = 128

# Sentence/paragraph boundaries:
#   - after an ASCII terminator (. ! ? ;) ONLY when whitespace follows, so
#     "3.14", "v1.2", "U.S.A." do not fragment on the embedded dots;
#   - after a CJK/full-width terminator, which has no following space (zero-width);
#   - on a blank line.
_SENTENCE_RE = re.compile(r"(?<=[.!?;])\s+|(?<=[。！？；])\s*|\n{2,}")


def _is_cjk(ch: str) -> bool:
    o = ord(ch)
    return (
        0x4E00 <= o <= 0x9FFF  # CJK Unified Ideographs
        or 0x3400 <= o <= 0x4DBF  # Ext A
        or 0x3040 <= o <= 0x30FF  # Hiragana + Katakana
        or 0xAC00 <= o <= 0xD7A3  # Hangul syllables
    )


def _token_len(text: str) -> int:
    """Rough token estimate that also bounds space-less scripts.

    Whitespace tokenisation alone counts a space-less CJK passage as ~1 token,
    so a token-expressed capacity cap would never fire. Adding one unit per CJK
    character over-estimates vs BPE, which is the safe direction — it yields
    smaller, never larger, chunks.
    """
    word_count = sum(1 for _ in re.finditer(r"\S+", text))
    cjk_count = sum(1 for c in text if _is_cjk(c))
    if word_count <= 1:
        return max(len(text), word_count + cjk_count)
    return word_count + cjk_count


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
    max_chunks: int = 20_000,
    max_output_bytes: int = MAX_CHUNK_OUTPUT_BYTES,
) -> list[str]:
    validate_chunk_params(
        ChunkStrategy.FIXED,
        {
            "chunk_size_tokens": chunk_size_tokens,
            "chunk_overlap_tokens": chunk_overlap_tokens,
        },
    )

    step = chunk_size_tokens - chunk_overlap_tokens
    out: list[str] = []
    window: list[str] = []
    fresh_since_emit = 0
    output_bytes = 0
    for match in re.finditer(r"\S+", text):
        window.append(match.group())
        fresh_since_emit += 1
        if len(window) < chunk_size_tokens:
            continue
        output_bytes = _append_tokens_bounded(
            out,
            window,
            max_chunks=max_chunks,
            output_bytes=output_bytes,
            max_output_bytes=max_output_bytes,
        )
        fresh_since_emit = 0
        window = window[step:]
    if window and (not out or fresh_since_emit):
        _append_tokens_bounded(
            out,
            window,
            max_chunks=max_chunks,
            output_bytes=output_bytes,
            max_output_bytes=max_output_bytes,
        )
    return out


def _append_tokens_bounded(
    out: list[str],
    tokens: list[str],
    *,
    max_chunks: int,
    output_bytes: int,
    max_output_bytes: int,
) -> int:
    if len(out) >= max_chunks:
        raise ResourceBudgetError("chunks")
    value_bytes = sum(len(token.encode("utf-8")) for token in tokens) + max(0, len(tokens) - 1)
    if output_bytes + value_bytes > max_output_bytes:
        raise ResourceBudgetError("chunk_output_bytes")
    out.append(_detokenise(tokens))
    return output_bytes + value_bytes


def _append_bounded(out: list[str], value: str, max_chunks: int) -> None:
    if len(out) >= max_chunks:
        raise ResourceBudgetError("chunks")
    out.append(value)


def _segment_unspaced(text: str, max_tokens: int) -> Iterable[str]:
    for start in range(0, len(text), max_tokens):
        yield text[start : start + max_tokens]


def _segment(text: str, max_tokens: int) -> Iterable[str]:
    """Hard-split a single over-capacity unit into <= max_tokens pieces.

    Guards against a delimiter-less blob (CJK without spaces, CSV, minified
    text) becoming one oversized chunk. Splits on whitespace when present, else
    on characters.
    """
    if _token_len(text) <= max_tokens:
        yield text
        return
    if re.search(r"\s", text) is None:
        yield from _segment_unspaced(text, max_tokens)
        return

    cur: list[str] = []
    cur_len = 0
    for match in re.finditer(r"\S+", text):
        unit = match.group()
        unit_len = 1 + sum(1 for char in unit if _is_cjk(char))
        if len(unit) > max_tokens or unit_len > max_tokens:
            if cur:
                yield " ".join(cur)
                cur, cur_len = [], 0
            yield from _segment_unspaced(unit, max_tokens)
            continue
        if cur and cur_len + unit_len > max_tokens:
            yield " ".join(cur)
            cur, cur_len = [], 0
        cur.append(unit)
        cur_len += unit_len
    if cur:
        yield " ".join(cur)


def _iter_sentences(text: str, max_tokens: int) -> Iterable[str]:
    start = 0
    for match in _SENTENCE_RE.finditer(text):
        raw = text[start : match.start()]
        start = match.end()
        stripped = raw.strip() if raw else ""
        if stripped:
            yield from _segment(stripped, max_tokens)
    tail = text[start:]
    stripped = tail.strip()
    if stripped:
        yield from _segment(stripped, max_tokens)


def _split_sentences(text: str, max_tokens: int) -> list[str]:
    return list(_iter_sentences(text, max_tokens))


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
    max_chunks: int = 20_000,
) -> list[str]:
    """Similarity-aware semantic chunking.

    Grows a chunk sentence-by-sentence, closing it when the next sentence is
    less similar to the chunk's running centroid than ``similarity_threshold``
    (a topic shift) or when the chunk would exceed ``max_tokens_per_chunk``.
    Oversized individual sentences are hard-split to the capacity beforehand.
    """
    if max_tokens_per_chunk <= 0:
        raise ChunkParamsInvalid("max_tokens_per_chunk must be positive")
    if not 0.0 < similarity_threshold <= 1.0:
        raise ChunkParamsInvalid("similarity_threshold must be in (0, 1]")

    chunks: list[str] = []
    current: list[str] = []
    current_tokens = 0
    centroid: list[float] | None = None
    count = 0
    sentences = iter(_iter_sentences(text, max_tokens_per_chunk))

    while batch := list(islice(sentences, _SENTENCE_EMBED_BATCH)):
        vectors = await embedder.embed_batch(batch)
        if len(vectors) != len(batch):
            raise ChunkParamsInvalid(f"embedder returned {len(vectors)} vectors for {len(batch)} sentences")
        for sentence, vec in zip(batch, vectors, strict=True):
            sentence_tokens = _token_len(sentence)
            over_capacity = current_tokens + sentence_tokens > max_tokens_per_chunk
            topic_shift = centroid is not None and _cosine(centroid, vec) < similarity_threshold
            if current and (over_capacity or topic_shift):
                _append_bounded(chunks, " ".join(current), max_chunks)
                current, current_tokens, centroid, count = [], 0, None, 0

            current.append(sentence)
            current_tokens += sentence_tokens
            count += 1
            if centroid is None:
                centroid = list(vec)
            else:
                centroid = [c + (v - c) / count for c, v in zip(centroid, vec, strict=True)]

    if current:
        _append_bounded(chunks, " ".join(current), max_chunks)
    return chunks


async def chunk_document(
    text: str,
    *,
    strategy: ChunkStrategy,
    params: dict[str, Any],
    embedder: _Embedder,
    max_chunks: int = 20_000,
) -> list[str]:
    """Dispatch to the configured strategy with sane defaults for missing params.

    ``embedder`` is required by the ``semantic`` strategy and ignored by
    ``fixed`` (kept in the signature so the ingest path can call uniformly).
    """
    if strategy is ChunkStrategy.FIXED:
        merged = {**DEFAULT_FIXED_CHUNK_PARAMS, **(params or {})}
        validate_chunk_params(strategy, merged)
        try:
            size = int(merged["chunk_size_tokens"])
            overlap = int(merged["chunk_overlap_tokens"])
        except (ValueError, TypeError) as exc:
            raise ChunkParamsInvalid(f"invalid fixed chunk params: {exc}") from exc
        return chunk_fixed(
            text,
            chunk_size_tokens=size,
            chunk_overlap_tokens=overlap,
            max_chunks=max_chunks,
        )
    if strategy is ChunkStrategy.SEMANTIC:
        merged = {**DEFAULT_SEMANTIC_CHUNK_PARAMS, **(params or {})}
        validate_chunk_params(strategy, merged)
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
            max_chunks=max_chunks,
        )
    raise ChunkParamsInvalid(f"unknown chunk strategy {strategy!r}")
