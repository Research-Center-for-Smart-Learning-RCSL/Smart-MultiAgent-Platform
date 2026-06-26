"""Unit tests for `contexts.knowledge.infrastructure.chunkers` (E.5)."""

from __future__ import annotations

import pytest

from contexts.knowledge.domain.errors import ChunkParamsInvalid
from contexts.knowledge.domain.models import ChunkStrategy
from contexts.knowledge.infrastructure.chunkers import (
    chunk_document,
    chunk_fixed,
    chunk_semantic,
)


def test_fixed_simple_windowing() -> None:
    out = chunk_fixed(
        "a b c d e f g h",
        chunk_size_tokens=4,
        chunk_overlap_tokens=1,
    )
    assert out == ["a b c d", "d e f g", "g h"]


def test_fixed_rejects_bad_params() -> None:
    with pytest.raises(ChunkParamsInvalid):
        chunk_fixed("a b c", chunk_size_tokens=0, chunk_overlap_tokens=0)
    with pytest.raises(ChunkParamsInvalid):
        chunk_fixed("a b c", chunk_size_tokens=3, chunk_overlap_tokens=3)


async def test_chunk_document_defaults_fixed() -> None:
    txt = " ".join(str(i) for i in range(1500))
    # embedder is required by the signature but ignored for the fixed strategy.
    out = await chunk_document(txt, strategy=ChunkStrategy.FIXED, params={}, embedder=_StubEmbedder({}))
    # Default 512/64 → ~3 chunks.
    assert len(out) >= 3


# ---------------------------------------------------------------------------
# Similarity-aware semantic chunking
# ---------------------------------------------------------------------------


class _StubEmbedder:
    """Maps each sentence to a fixed vector, so similarity is deterministic."""

    def __init__(self, vectors: dict[str, list[float]]) -> None:
        self._vectors = vectors

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [self._vectors[t] for t in texts]


async def test_semantic_splits_on_topic_shift() -> None:
    # Two sentences point one way, the third is orthogonal → boundary before it.
    text = "Cats purr. Cats nap. Servers crash."
    vectors = {
        "Cats purr.": [1.0, 0.0],
        "Cats nap.": [1.0, 0.0],
        "Servers crash.": [0.0, 1.0],
    }
    out = await chunk_semantic(
        text,
        embedder=_StubEmbedder(vectors),
        max_tokens_per_chunk=100,
        similarity_threshold=0.5,
    )
    assert out == ["Cats purr. Cats nap.", "Servers crash."]


async def test_semantic_keeps_cohesive_text_together() -> None:
    text = "Cats purr. Cats nap. Cats hunt."
    same = [1.0, 0.0]
    out = await chunk_semantic(
        text,
        embedder=_StubEmbedder({s.strip(): same for s in ["Cats purr.", "Cats nap.", "Cats hunt."]}),
        max_tokens_per_chunk=100,
        similarity_threshold=0.5,
    )
    assert out == ["Cats purr. Cats nap. Cats hunt."]


async def test_semantic_respects_capacity() -> None:
    # All identical direction (never a topic-shift boundary), but capacity forces a split.
    text = "aa bb. cc dd. ee ff."
    same = [1.0, 0.0]
    out = await chunk_semantic(
        text,
        embedder=_StubEmbedder({s: same for s in ["aa bb.", "cc dd.", "ee ff."]}),
        max_tokens_per_chunk=2,  # each sentence is 2 tokens → one sentence per chunk
        similarity_threshold=0.1,
    )
    assert out == ["aa bb.", "cc dd.", "ee ff."]


async def test_semantic_rejects_bad_threshold() -> None:
    with pytest.raises(ChunkParamsInvalid):
        await chunk_semantic(
            "x.", embedder=_StubEmbedder({"x.": [1.0]}), max_tokens_per_chunk=10, similarity_threshold=0.0
        )


async def test_semantic_empty_text() -> None:
    out = await chunk_semantic(
        "   ", embedder=_StubEmbedder({}), max_tokens_per_chunk=10, similarity_threshold=0.5
    )
    assert out == []


class _ConstEmbedder:
    """Maps any sentence to the same vector (similarity never triggers)."""

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0] for _ in texts]


async def test_semantic_splits_cjk_on_fullwidth_terminators() -> None:
    # zh-TW: 。terminators, no inter-character spaces. Must still split into
    # sentences and detect the topic shift (cats -> servers).
    text = "貓咪會喵喵叫。貓咪愛睡覺。伺服器當機了。"
    vectors = {
        "貓咪會喵喵叫。": [1.0, 0.0],
        "貓咪愛睡覺。": [1.0, 0.0],
        "伺服器當機了。": [0.0, 1.0],
    }
    out = await chunk_semantic(
        text, embedder=_StubEmbedder(vectors), max_tokens_per_chunk=100, similarity_threshold=0.5
    )
    assert out == ["貓咪會喵喵叫。 貓咪愛睡覺。", "伺服器當機了。"]


async def test_semantic_hard_splits_oversized_delimiterless_text() -> None:
    # A long space-less CJK blob with no terminators must be capacity-split, not
    # returned as one giant chunk that the embedder would reject.
    text = "字" * 250
    out = await chunk_semantic(
        text, embedder=_ConstEmbedder(), max_tokens_per_chunk=100, similarity_threshold=0.5
    )
    assert len(out) >= 3
    assert all(c != text for c in out)  # the blob was split, not passed through whole
    assert "".join(out) == text  # and nothing was dropped
