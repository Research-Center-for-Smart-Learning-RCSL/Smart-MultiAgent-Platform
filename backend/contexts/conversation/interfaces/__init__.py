from contexts.conversation.domain.compaction import (
    COMPACT_SUMMARY_TYPE,
    COMPACTED_IDS_KEY,
    ORIGINAL_COMPACTED_IDS_KEY,
    PRODUCER_AGENT_ID_KEY,
    VOIDED_SUMMARY_TYPE,
    compacted_ids,
    is_compact_summary,
    summary_metadata,
    summary_producer,
)
from contexts.conversation.infrastructure.channels import emit_agent_finished_error, room_channel

# §32's live-draft surface, re-exported on exactly the terms `PresenceTracker` is:
# both are room-scoped Redis state that a *caller outside this context* legitimately
# needs, and neither belongs to the facade (there is no session, and no domain
# operation to wrap). Exporting them here is what keeps the WS route and the agents
# runtime off `contexts.conversation.infrastructure` — an import `lint-imports` does
# not catch, because its contracts enforce domain purity rather than the
# application/infrastructure direction.
#
# Aliased on the way out: `ACTIVITY` / `SURFACES` / `normalise_key` read fine inside
# a module that is entirely about drafts, and read as nothing at all beside
# `PresenceTracker` and `room_channel`.
from contexts.conversation.infrastructure.drafts import (
    ACTIVITY as ACTIVITY_SURFACE,
)
from contexts.conversation.infrastructure.drafts import (
    COMPOSER as COMPOSER_SURFACE,
)
from contexts.conversation.infrastructure.drafts import (
    SURFACES as DRAFT_SURFACES,
)
from contexts.conversation.infrastructure.drafts import (
    DraftEntry,
    DraftStore,
)
from contexts.conversation.infrastructure.drafts import (
    normalise_key as normalise_draft_key,
)
from contexts.conversation.infrastructure.presence import PresenceTracker

__all__ = [
    "ACTIVITY_SURFACE",
    "COMPACTED_IDS_KEY",
    "COMPACT_SUMMARY_TYPE",
    "COMPOSER_SURFACE",
    "DRAFT_SURFACES",
    "ORIGINAL_COMPACTED_IDS_KEY",
    "PRODUCER_AGENT_ID_KEY",
    "VOIDED_SUMMARY_TYPE",
    "DraftEntry",
    "DraftStore",
    "PresenceTracker",
    "compacted_ids",
    "emit_agent_finished_error",
    "is_compact_summary",
    "normalise_draft_key",
    "room_channel",
    "summary_metadata",
    "summary_producer",
]
