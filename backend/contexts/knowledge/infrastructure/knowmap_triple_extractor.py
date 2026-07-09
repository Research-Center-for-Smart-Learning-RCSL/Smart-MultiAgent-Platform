"""Document-oriented triple extractor for the Knowledge Map (Phase 3, WS2).

Forks :class:`LlmTripleExtractor` for a document corpus rather than a chat feed
(R11.15 — share the engine, fork the product + domain). It plugs into the shared
``TripleExtractor`` Protocol and the shared 2PC builder unchanged: only the prompt
and the source-unit renderer differ. Evidence tokens are opaque
``"{knowmap_document_id}#{chunk_idx}"`` strings (no UUID cast) — the renderer shows
them per chunk and the model echoes them into ``evidence_msg_ids``, which the reused
:func:`_parse_triples` coerces to ``Triple.evidence_refs`` via ``normalize_evidence_refs``.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Protocol, cast

from contexts.keys.application.provider_router import ProviderRequest, ProviderRouter
from contexts.keys.domain.errors import KeyGroupExhausted
from contexts.keys.domain.providers import ProviderCapability
from contexts.knowledge.application.graphrag_ports import DeltaMessage
from contexts.knowledge.domain.graphrag import Triple
from contexts.knowledge.infrastructure.triple_extractor import (
    _DEFAULT_EXTRACTION_MODELS,
    _parse_triples,
)

_log = logging.getLogger(__name__)


class _DocUnit(Protocol):
    """A single knowmap chunk as the extractor sees it — a ``DeltaMessage`` plus
    the document-provenance fields the renderer needs to build evidence tokens."""

    id: uuid.UUID
    role: str
    content: str
    source_member_id: uuid.UUID | None
    created_at: datetime
    document_id: uuid.UUID
    chunk_idx: int


_EXTRACTION_PROMPT = (
    "You are an information extraction engine. From the following document "
    "excerpts, extract factual relations as a JSON array of objects with fields: "
    "subject (string), relation (string), object (string), subject_type (one of: "
    "person, organization, location, concept, event, product, other), object_type "
    "(same set), confidence (float 0..1), evidence_msg_ids (array of the bracketed "
    'excerpt id strings, e.g. "<uuid>#3", that support the relation). Each excerpt '
    "is prefixed with its id in square brackets. Respond with ONLY the JSON array, "
    "no prose.\n\nEXCERPTS:\n{excerpts}"
)


def _render_units(units: Sequence[_DocUnit]) -> str:
    return "\n".join(f"[{u.document_id}#{u.chunk_idx}] {u.content}" for u in units)


class DocTripleExtractor:
    """Concrete :class:`TripleExtractor` over a document corpus."""

    def __init__(self, *, router: ProviderRouter, models: dict[str, str] | None = None) -> None:
        self._router = router
        self._models = models or _DEFAULT_EXTRACTION_MODELS

    async def extract(
        self,
        *,
        config_id: uuid.UUID,
        builder_key_group_id: uuid.UUID,
        messages: list[DeltaMessage],
    ) -> list[Triple]:
        if not messages:
            return []
        # The DocDeltaLoader yields DocSourceUnit instances (structural DeltaMessage
        # + document_id/chunk_idx); the shared builder types the window as
        # list[DeltaMessage], so recover the document-provenance view here.
        units = cast("Sequence[_DocUnit]", messages)
        request = ProviderRequest(
            capability=ProviderCapability.LLM_CHAT,
            payload={
                "models": self._models,
                "max_tokens": 4096,
                "messages": [
                    {"role": "user", "content": _EXTRACTION_PROMPT.format(excerpts=_render_units(units))}
                ],
            },
        )
        try:
            result = await self._router.call(group_id=builder_key_group_id, request=request)
        except KeyGroupExhausted as exc:
            _log.warning("knowmap extractor: key group %s unusable (%s)", builder_key_group_id, exc.reason)
            return []
        if result.http_status != 200:
            _log.warning(
                "knowmap extractor: provider returned %s for group %s",
                result.http_status,
                builder_key_group_id,
            )
            return []
        return _parse_triples(str(result.body.get("text", "")))


__all__ = ["DocTripleExtractor"]
