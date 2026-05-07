"""Unit tests for `contexts.knowledge.infrastructure.chunkers` (E.5)."""

from __future__ import annotations

import pytest

from contexts.knowledge.domain.errors import ChunkParamsInvalid
from contexts.knowledge.domain.models import ChunkStrategy
from contexts.knowledge.infrastructure.chunkers import chunk_fixed, chunk_text


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


def test_chunk_text_defaults_fixed() -> None:
    txt = " ".join(str(i) for i in range(1500))
    out = chunk_text(txt, strategy=ChunkStrategy.FIXED, params={})
    # Default 512/64 → ~3 chunks.
    assert len(out) >= 3
