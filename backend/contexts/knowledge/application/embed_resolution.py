"""Shared embed-key resolution for the GraphRAG builder + retrieval (SEC-H3).

One source of truth for "which embedding key and model does a builder key group
resolve to". It lists members with :meth:`KeyGroupMemberRepository.list_ordered_carried`,
so a key whose project *carry* was revoked is never selected — the same active-carry
invariant the provider router enforces (SEC-H3). Kept free of any ``app.*`` import so
both the worker build path and the retrieval provider consume the identical logic;
before consolidation the worker path used the unguarded ``list_ordered`` and could embed
with a revoked-carry key.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

# Default embedding model per provider is owned by the domain catalog, which keeps
# it in lockstep with the dimension whitelist via a runtime assert. Every GraphRAG
# config in a project shares one Qdrant collection, so the model — hence the vector
# dimension — must be stable across the group's keys; reuse the single source of
# truth rather than a second copy that could drift.
from contexts.knowledge.domain.models import DEFAULT_EMBED_MODELS


async def resolve_embed_key(
    db: AsyncSession,
    builder_key_group_id: uuid.UUID,
) -> tuple[str, str, uuid.UUID] | None:
    """Return ``(provider, model, key_id)`` for the first actively-carried,
    embedding-capable key in the builder group, or ``None`` if there is none.

    SEC-H3: ``list_ordered_carried`` filters to keys with an active ``key_projects``
    carry into the group's project, so a revoked-carry key is never selected or
    decrypted by the build / retrieval embedder. No plaintext leaves this function.
    """
    from contexts.keys.infrastructure.group_repository import KeyGroupMemberRepository
    from contexts.keys.infrastructure.repositories import ApiKeyRepository

    members = await KeyGroupMemberRepository(db).list_ordered_carried(builder_key_group_id)
    for m in members:
        key = await ApiKeyRepository(db).get_active(m.key_id)
        if key is None:
            continue
        provider = key.provider.value
        model = DEFAULT_EMBED_MODELS.get(provider)
        if model is None:
            continue
        return provider, model, key.id
    return None
