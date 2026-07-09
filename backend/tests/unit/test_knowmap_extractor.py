"""Unit tests for the DocTripleExtractor (Phase 3, WS2).

The document extractor forks the chat extractor: it renders knowmap chunks as
``[{document_id}#{chunk_idx}] {content}`` excerpts and re-uses ``_parse_triples`` so
the model's echoed bracket ids land in ``Triple.evidence_refs`` as opaque
``"{uuid}#{idx}"`` strings — never UUID-cast."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from contexts.knowledge.infrastructure.knowmap_delta_loader import DocSourceUnit
from contexts.knowledge.infrastructure.knowmap_triple_extractor import (
    DocTripleExtractor,
    _render_units,
)

_NOW = datetime(2026, 7, 7, 12, 0, 0)


def _unit(doc_id: uuid.UUID, chunk_idx: int, content: str) -> DocSourceUnit:
    return DocSourceUnit(
        id=uuid.uuid4(),
        role="document",
        content=content,
        document_id=doc_id,
        chunk_idx=chunk_idx,
        created_at=_NOW,
    )


class _Result:
    def __init__(self, http_status: int, text: str) -> None:
        self.http_status = http_status
        self.body: dict[str, Any] = {"text": text}


class _FakeRouter:
    """Captures the single ProviderRequest and returns a canned extraction body."""

    def __init__(self, *, text: str = "[]", http_status: int = 200) -> None:
        self._text = text
        self._http_status = http_status
        self.last_group_id: uuid.UUID | None = None
        self.last_request: Any = None

    async def call(self, *, group_id: uuid.UUID, request: Any) -> _Result:
        self.last_group_id = group_id
        self.last_request = request
        return _Result(self._http_status, self._text)


def test_render_units_prefixes_opaque_evidence_tokens() -> None:
    d = uuid.uuid4()
    rendered = _render_units([_unit(d, 0, "first"), _unit(d, 3, "second")])
    assert rendered == f"[{d}#0] first\n[{d}#3] second"


async def test_extract_echoes_bracket_ids_into_evidence_refs() -> None:
    d = uuid.uuid4()
    ref = f"{d}#2"
    body = (
        '[{"subject": "Acme", "relation": "acquired", "object": "Beta", '
        '"subject_type": "organization", "object_type": "organization", '
        f'"confidence": 0.9, "evidence_msg_ids": ["{ref}"]}}]'
    )
    router = _FakeRouter(text=body)
    extractor = DocTripleExtractor(router=router)  # type: ignore[arg-type]
    group = uuid.uuid4()

    triples = await extractor.extract(
        config_id=uuid.uuid4(),
        builder_key_group_id=group,
        messages=[_unit(d, 2, "Acme acquired Beta in 2020.")],  # type: ignore[list-item]
    )

    assert len(triples) == 1
    tr = triples[0]
    assert (tr.subject, tr.relation, tr.object) == ("Acme", "acquired", "Beta")
    # Opaque ref survives intact — no UUID cast, chunk index preserved.
    assert tr.evidence_refs == (ref,)
    assert router.last_group_id == group
    # The rendered excerpt (with its bracket id) reached the provider payload.
    sent = router.last_request.payload["messages"][0]["content"]
    assert f"[{ref}]" in sent


async def test_extract_empty_messages_short_circuits() -> None:
    router = _FakeRouter(text="[]")
    extractor = DocTripleExtractor(router=router)  # type: ignore[arg-type]
    triples = await extractor.extract(config_id=uuid.uuid4(), builder_key_group_id=uuid.uuid4(), messages=[])
    assert triples == []
    assert router.last_request is None  # no provider call for an empty window


async def test_extract_non_200_returns_no_triples() -> None:
    router = _FakeRouter(text="ignored", http_status=503)
    extractor = DocTripleExtractor(router=router)  # type: ignore[arg-type]
    triples = await extractor.extract(
        config_id=uuid.uuid4(),
        builder_key_group_id=uuid.uuid4(),
        messages=[_unit(uuid.uuid4(), 0, "text")],  # type: ignore[list-item]
    )
    assert triples == []
