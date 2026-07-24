"""The compaction-summary metadata contract — framework-free.

A compaction summary is a `messages` row whose `metadata` carries a fixed shape.
Compaction itself is an agents-context concern (R9.10), but the row belongs to
the conversation context, and three other places now have to *read* that shape:
the model-facing history loader, the retention purge, and the repair command.
Leaving each to spell out `"compact_summary"` and `"compacted_ids"` for itself
means a reader that drifts from the writer fails silently — the purge simply
stops matching, and nobody notices until content outlives its horizon.

So the contract lives here, with the row, and everyone reads it from one place.

The values are **persisted**: they exist in `messages.metadata` in every
deployment, so they cannot be renamed without a data migration. Treat them as a
wire format, not as internal names.

One deliberate exception. `contexts.agents.application.context` repeats the
`"compact_summary"` literal in `choose_range_to_compact`. That module's whole
design property is that it imports nothing concrete — it is pure orchestration
over two Protocols, which is what keeps the circular-import risk at zero — so it
carries a comment pointing here instead of an import.
"""

from __future__ import annotations

from typing import Any

# The `metadata["type"]` discriminator of a live compaction summary.
COMPACT_SUMMARY_TYPE = "compact_summary"

# A summary that has been retired by the repair command: it no longer elides its
# range and is no longer injected into any prompt. A distinct value rather than a
# deleted key so the row stays greppable and the void stays reversible.
VOIDED_SUMMARY_TYPE = "compact_summary_voided"

COMPACTED_IDS_KEY = "compacted_ids"
PRODUCER_AGENT_ID_KEY = "producer_agent_id"
# Where `compacted_ids` is moved on a void, so the void can be undone.
ORIGINAL_COMPACTED_IDS_KEY = "original_compacted_ids"

__all__ = [
    "COMPACTED_IDS_KEY",
    "COMPACT_SUMMARY_TYPE",
    "ORIGINAL_COMPACTED_IDS_KEY",
    "PRODUCER_AGENT_ID_KEY",
    "VOIDED_SUMMARY_TYPE",
    "compacted_ids",
    "is_compact_summary",
    "summary_metadata",
    "summary_producer",
]


def is_compact_summary(metadata: Any) -> bool:
    """True for a live summary row. Anything that is not a dict is not one."""
    return isinstance(metadata, dict) and metadata.get("type") == COMPACT_SUMMARY_TYPE


def summary_producer(metadata: Any) -> str | None:
    """The agent a summary belongs to, or None for a pre-scoping row.

    A missing key and an explicit null are the same answer — belongs to no one —
    so neither can match a reader (R9.09, dossier Q-7).
    """
    if not isinstance(metadata, dict):
        return None
    producer = metadata.get(PRODUCER_AGENT_ID_KEY)
    return str(producer) if producer else None


def compacted_ids(metadata: Any) -> list[str]:
    """The message ids a summary folded, as strings. Empty for a non-summary."""
    if not isinstance(metadata, dict):
        return []
    return [str(c) for c in (metadata.get(COMPACTED_IDS_KEY) or [])]


def summary_metadata(*, message_ids: list[Any], producer_agent_id: Any) -> dict[str, Any]:
    """The metadata payload for a new summary row, minus the service-stamped `type`.

    The single writer. `type` is omitted because `insert_system_message` stamps
    it server-side, which is what keeps it unforgeable by a client.
    """
    return {
        COMPACTED_IDS_KEY: [str(m) for m in message_ids],
        PRODUCER_AGENT_ID_KEY: str(producer_agent_id),
    }
