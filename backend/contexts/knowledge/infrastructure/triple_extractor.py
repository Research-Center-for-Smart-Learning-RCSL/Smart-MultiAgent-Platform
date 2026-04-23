"""LLM-backed (subject, relation, object) triple extractor (R11.03).

The adapter resolves the builder Key Group to a concrete provider key via
:class:`contexts.keys.interfaces.facade.KeysFacade`, calls the selected
LLM with a fixed extraction prompt, and parses a JSON array of triples
from the response. Failure modes (non-JSON output, missing fields) are
surfaced as empty result sets so the builder can still mark phase-1
complete instead of compensating on a soft parse error.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.keys.interfaces.facade import KeysFacade
from contexts.knowledge.application.graphrag_ports import DeltaMessage
from contexts.knowledge.domain.graphrag import Triple

_log = logging.getLogger(__name__)

_EXTRACTION_PROMPT = (
    "You are an information extraction engine. From the following chat "
    "messages, extract factual relations as a JSON array of objects with "
    "fields: subject (string), relation (string), object (string), "
    "confidence (float 0..1), evidence_msg_ids (array of message id "
    "strings from the provided messages). Respond with ONLY the JSON "
    "array, no prose.\n\nMESSAGES:\n{messages}"
)


class ChatCompleter(Protocol):
    """Minimal LLM surface used by the extractor (provider-agnostic)."""

    async def complete(self, *, prompt: str, api_key: str) -> str: ...


class LlmTripleExtractor:
    """Concrete :class:`TripleExtractor` — resolves key, prompts, parses."""

    def __init__(
        self,
        db: AsyncSession,
        *,
        completer: ChatCompleter,
    ) -> None:
        self._db = db
        self._completer = completer

    async def extract(
        self,
        *,
        config_id: uuid.UUID,
        builder_key_group_id: uuid.UUID,
        messages: list[DeltaMessage],
    ) -> list[Triple]:
        if not messages:
            return []
        key_id = await self._resolve_key_id(builder_key_group_id)
        if key_id is None:
            _log.warning(
                "graphrag extractor: key group %s has no active keys",
                builder_key_group_id,
            )
            return []

        facade = KeysFacade(self._db)
        plaintext = await facade.unwrap_api_key_plaintext(key_id)
        try:
            prompt = _EXTRACTION_PROMPT.format(
                messages=_render_messages(messages),
            )
            raw = await self._completer.complete(
                prompt=prompt, api_key=plaintext.decode("utf-8"),
            )
        finally:
            plaintext = b"\x00" * len(plaintext)  # noqa: F841
        return _parse_triples(raw)

    async def _resolve_key_id(
        self, group_id: uuid.UUID,
    ) -> uuid.UUID | None:
        """Pick one active key from the builder Key Group."""
        from contexts.keys.infrastructure.group_repository import (  # noqa: PLC0415
            KeyGroupMemberRepository,
        )
        facade = KeysFacade(self._db)
        group = await facade.get_key_group(group_id)
        if group is None:
            return None
        members = await KeyGroupMemberRepository(self._db).list_ordered(group_id)
        if not members:
            return None
        return members[0].key_id


def _render_messages(messages: list[DeltaMessage]) -> str:
    lines: list[str] = []
    for m in messages:
        lines.append(f"[{m.id} role={m.role}] {m.content}")
    return "\n".join(lines)


def _parse_triples(raw: str) -> list[Triple]:
    """Best-effort JSON parse — tolerates wrapping prose."""
    text = raw.strip()
    # Strip a ```json fence if the model added one.
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.DOTALL)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # Fallback — find the first array-like span.
        match = re.search(r"\[[\s\S]*\]", text)
        if not match:
            return []
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            return []
    if not isinstance(data, list):
        return []

    out: list[Triple] = []
    for row in data:
        if not isinstance(row, dict):
            continue
        subj = row.get("subject")
        rel = row.get("relation")
        obj = row.get("object")
        if not (isinstance(subj, str) and isinstance(rel, str) and isinstance(obj, str)):
            continue
        conf_raw = row.get("confidence", 0.5)
        try:
            conf = float(conf_raw)
        except (TypeError, ValueError):
            conf = 0.5
        ev_raw = row.get("evidence_msg_ids") or []
        ev: list[uuid.UUID] = []
        if isinstance(ev_raw, list):
            for v in ev_raw:
                try:
                    ev.append(uuid.UUID(str(v)))
                except ValueError:
                    continue
        out.append(
            Triple(
                subject=subj.strip(),
                relation=rel.strip(),
                object=obj.strip(),
                confidence=max(0.0, min(1.0, conf)),
                evidence_msg_ids=tuple(ev),
            )
        )
    return out


__all__ = ["ChatCompleter", "LlmTripleExtractor"]
