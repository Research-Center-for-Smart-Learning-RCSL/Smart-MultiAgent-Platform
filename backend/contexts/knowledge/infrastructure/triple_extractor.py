"""LLM-backed (subject, relation, object) triple extractor (R11.03).

The extractor hands the builder Key Group to the :class:`ProviderRouter`,
which selects + rotates a concrete provider key, signs the call through the
matching adapter (K.1), records usage, and returns normalised text. The
extractor parses a JSON array of triples from that text. Failure modes
(no keys, exhaustion, non-JSON output, missing fields) are surfaced as empty
result sets so the builder can still mark phase-1 complete instead of
compensating on a soft parse error.

No key material is handled here anymore — the router owns unwrap + scrub
(K.∞ gate: no `unwrap_api_key_plaintext` LLM bypass survives K).
"""

from __future__ import annotations

import json
import logging
import re
import uuid

from contexts.keys.application.provider_router import ProviderRequest, ProviderRouter
from contexts.keys.domain.errors import KeyGroupExhausted
from contexts.keys.domain.providers import ProviderCapability
from contexts.knowledge.application.graphrag_ports import DeltaMessage
from contexts.knowledge.domain.graphrag import Triple

_log = logging.getLogger(__name__)

# Builder-level extraction models, supplied to the adapter per provider so the
# router can pick whichever key it rotates to. These are the *builder's* model
# choice (cheap/fast tier), NOT an adapter hardcode — an agent turn supplies its
# own ``model`` instead (K.1 contract).
_DEFAULT_EXTRACTION_MODELS: dict[str, str] = {
    "claude": "claude-3-5-haiku-latest",
    "openai": "gpt-4o-mini",
    "gemini": "gemini-2.0-flash",
}

_EXTRACTION_PROMPT = (
    "You are an information extraction engine. From the following chat "
    "messages, extract factual relations as a JSON array of objects with "
    "fields: subject (string), relation (string), object (string), "
    "confidence (float 0..1), evidence_msg_ids (array of message id "
    "strings from the provided messages). Respond with ONLY the JSON "
    "array, no prose.\n\nMESSAGES:\n{messages}"
)


class LlmTripleExtractor:
    """Concrete :class:`TripleExtractor` — routes through the key group, parses."""

    def __init__(
        self,
        *,
        router: ProviderRouter,
        models: dict[str, str] | None = None,
    ) -> None:
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
        request = ProviderRequest(
            capability=ProviderCapability.LLM_CHAT,
            payload={
                "models": self._models,
                "max_tokens": 4096,
                "messages": [
                    {
                        "role": "user",
                        "content": _EXTRACTION_PROMPT.format(messages=_render_messages(messages)),
                    }
                ],
            },
        )
        try:
            result = await self._router.call(group_id=builder_key_group_id, request=request)
        except KeyGroupExhausted as exc:
            _log.warning(
                "graphrag extractor: key group %s unusable (%s)",
                builder_key_group_id,
                exc.reason,
            )
            return []
        if result.http_status != 200:
            _log.warning(
                "graphrag extractor: provider returned %s for group %s",
                result.http_status,
                builder_key_group_id,
            )
            return []
        return _parse_triples(str(result.body.get("text", "")))


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


__all__ = ["LlmTripleExtractor"]
