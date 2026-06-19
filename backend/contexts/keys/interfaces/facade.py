"""Keys facade — cross-context read/revoke surface.

Other contexts (tenancy's member-removal flow; E's agent router) use this
facade instead of reaching into `contexts.keys.infrastructure` directly.
Keeps the import graph acyclic and makes the contract between contexts
explicit.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from contexts.keys.application.carry_service import CarryService
from contexts.keys.domain.errors import CapabilityMismatch as KeysCapabilityMismatch
from contexts.keys.domain.groups import KeyGroup
from contexts.keys.domain.models import ApiKey
from contexts.keys.domain.providers import (
    ApiKeyProvider,
    CapabilityRequirement,
    ProviderCapability,
    assert_capability,
    supports,
)
from contexts.keys.infrastructure import tables as _t
from contexts.keys.infrastructure.group_repository import KeyGroupRepository
from contexts.keys.infrastructure.repositories import ApiKeyRepository
from shared_kernel.auth.clients import now


class KeysFacade:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._keys = ApiKeyRepository(db)

    async def list_keys_carried_in_project(self, project_id: uuid.UUID) -> list[ApiKey]:
        return await CarryService(self._db).list_in_project(project_id)

    async def get_key_group(self, group_id: uuid.UUID) -> KeyGroup | None:
        """Return the active Key Group (or None if missing / soft-deleted).

        Used by the agents context (E.1) to validate that an attached
        `key_group_id` belongs to the agent's project and to the
        expected capability class.
        """
        return await KeyGroupRepository(self._db).get_active(group_id)

    async def unwrap_api_key_plaintext(self, key_id: uuid.UUID) -> bytes:
        """Decrypt an `api_keys` row's envelope and return the plaintext.

        Used by the knowledge context (E.5 / E.6) so embedder + reranker
        adapters can sign outbound provider calls without re-implementing
        the envelope unwrap. Caller is responsible for *zeroising* the
        returned bytes after use.
        """
        from contexts.keys.domain.errors import KeyNotFound
        from contexts.keys.infrastructure.repositories import (
            ApiKeyRepository,
        )
        from shared_kernel.security import envelope as env

        repo = ApiKeyRepository(self._db)
        loaded = await repo.get_active_with_envelope(key_id)
        if loaded is None:
            raise KeyNotFound(str(key_id))
        _key, record = loaded
        return env.decrypt_envelope(record, env.api_key_aad(key_id))

    async def revoke_carries_for_user_leaving_project(
        self,
        *,
        user_id: uuid.UUID,
        project_id: uuid.UUID,
        actor_user_id: uuid.UUID | None = None,
        request_id: uuid.UUID | None = None,
    ) -> list[uuid.UUID]:
        """Fanout entry point for R7.04.

        Tenancy's project-member-removal code calls this with the caller's
        open `AsyncSession`, so the cascade shares the membership change's
        unit of work.
        """
        return await CarryService(self._db).revoke_for_leaving_user(
            user_id=user_id,
            project_id=project_id,
            actor_user_id=actor_user_id,
            request_id=request_id,
        )

    async def get_key(self, key_id: uuid.UUID) -> ApiKey | None:
        """Return the active (non-deleted) API key, or ``None``."""
        return await self._keys.get_active(key_id)

    async def validate_key_capability(
        self,
        key_id: uuid.UUID,
        capability: ProviderCapability,
    ) -> bool:
        """Check whether the key's provider supports *capability*.

        Returns ``False`` if the key is missing/deleted or if the provider
        does not support the given capability.
        """
        key = await self._keys.get_active(key_id)
        if key is None:
            return False
        return supports(ApiKeyProvider(key.provider.value), capability)

    async def is_key_in_project_scope(
        self,
        key_id: uuid.UUID,
        project_id: uuid.UUID,
    ) -> bool:
        """True if *key_id* is a member of any active key-group in *project_id*.

        Encapsulates the ``key_group_members JOIN key_groups`` scope check that
        the knowledge context uses when validating embed/rerank keys.
        """
        row = (
            await self._db.execute(
                sa.select(sa.literal(1))
                .select_from(
                    _t.key_group_members.join(
                        _t.key_groups,
                        _t.key_groups.c.id == _t.key_group_members.c.group_id,
                    )
                )
                .where(
                    sa.and_(
                        _t.key_group_members.c.key_id == key_id,
                        _t.key_groups.c.project_id == project_id,
                        _t.key_groups.c.deleted_at.is_(None),
                    )
                )
                .limit(1)
            )
        ).first()
        return row is not None

    async def list_keys_for_group(self, group_id: uuid.UUID) -> list[ApiKey]:
        """Return every active API key that is a member of *group_id*."""
        # Fetch member key_ids, then batch-resolve via the repository so the
        # row-to-model mapping stays encapsulated inside ApiKeyRepository.
        id_rows = (
            await self._db.execute(
                sa.select(_t.key_group_members.c.key_id).where(
                    _t.key_group_members.c.group_id == group_id,
                )
            )
        ).all()
        keys: list[ApiKey] = []
        for row in id_rows:
            key = await self._keys.get_active(row.key_id)
            if key is not None:
                keys.append(key)
        return keys

    # -- Retention helpers (H4) ------------------------------------------------

    async def rollup_usage_events(self, *, retention_months: int = 13) -> int:
        """Roll up old key_usage_events into key_usage_daily and delete originals.

        Aggregates events older than *retention_months* months into daily
        summaries (upsert) and removes the source rows.
        """
        cutoff = now() - timedelta(days=retention_months * 30)
        result = await self._db.execute(
            sa.text(
                "WITH old AS ("
                "  SELECT id, key_id, date_trunc('day', at) AS day, "
                "         input_tokens, output_tokens "
                "  FROM key_usage_events WHERE at < :cutoff LIMIT 1000"
                "), agg AS ("
                "  SELECT key_id, day, count(*) AS req_count, "
                "         sum(input_tokens) AS sum_input, "
                "         sum(output_tokens) AS sum_output "
                "  FROM old GROUP BY key_id, day"
                "), upserted AS ("
                "  INSERT INTO key_usage_daily "
                "  (key_id, day, requests, input_tokens, output_tokens) "
                "  SELECT key_id, day, req_count, sum_input, sum_output "
                "  FROM agg "
                "  ON CONFLICT (key_id, day) DO UPDATE SET "
                "    requests = key_usage_daily.requests + EXCLUDED.requests, "
                "    input_tokens = key_usage_daily.input_tokens + EXCLUDED.input_tokens, "
                "    output_tokens = key_usage_daily.output_tokens + EXCLUDED.output_tokens "
                "  RETURNING 1"
                ") "
                "DELETE FROM key_usage_events WHERE id IN (SELECT id FROM old)"
            ).bindparams(cutoff=cutoff)
        )
        return result.rowcount or 0  # type: ignore[attr-defined]

    async def manage_partitions(self) -> int:
        """Monthly-range partition manager for ``key_usage_events`` (B3).

        Creates the partition for the *next* calendar month if absent, and
        detaches + drops partitions whose upper bound is older than 13 months.

        Returns the number of partitions created + dropped.
        """
        today = now().date()
        year = today.year
        month = today.month
        nm_year, nm_month = (year + 1, 1) if month == 12 else (year, month + 1)
        nm_after_year, nm_after_month = (nm_year + 1, 1) if nm_month == 12 else (nm_year, nm_month + 1)
        next_part_name = f"key_usage_events_{nm_year:04d}{nm_month:02d}"
        next_lower = f"{nm_year:04d}-{nm_month:02d}-01"
        next_upper = f"{nm_after_year:04d}-{nm_after_month:02d}-01"

        created = 0
        dropped = 0

        # 1. Create next month's partition if missing.
        create_sql = (
            f'CREATE TABLE IF NOT EXISTS "{next_part_name}" '
            f"PARTITION OF key_usage_events "
            f"FOR VALUES FROM ('{next_lower}') TO ('{next_upper}')"
        )
        before = await self._db.execute(
            sa.text("SELECT 1 FROM pg_class WHERE relname = :rn").bindparams(rn=next_part_name)
        )
        existed = before.scalar() is not None
        await self._db.execute(sa.text(create_sql))
        if not existed:
            created += 1

        # 2. Detach + drop partitions older than 13 months.
        cutoff = today - timedelta(days=13 * 30)
        cutoff_str = cutoff.strftime("%Y-%m-%d")
        rows = await self._db.execute(
            sa.text(
                "SELECT c.relname, pg_get_expr(c.relpartbound, c.oid) AS bound "
                "FROM pg_inherits i "
                "JOIN pg_class p ON p.oid = i.inhparent "
                "JOIN pg_class c ON c.oid = i.inhrelid "
                "WHERE p.relname = 'key_usage_events' "
                "  AND c.relname ~ '^key_usage_events_[0-9]{6}$'"
            )
        )
        for relname, bound in rows.fetchall():
            if bound is None:
                continue
            try:
                after_to = bound.split(" TO ", 1)[1]
                upper = after_to.split("'", 2)[1]
            except (IndexError, ValueError):
                continue
            if upper <= cutoff_str:
                await self._db.execute(sa.text(f'ALTER TABLE key_usage_events DETACH PARTITION "{relname}"'))
                await self._db.execute(sa.text(f'DROP TABLE "{relname}"'))
                dropped += 1

        return created + dropped


# Re-export domain types so cross-context consumers need only one import path.
__all__ = [
    "ApiKeyProvider",
    "CapabilityRequirement",
    "KeysCapabilityMismatch",
    "KeysFacade",
    "ProviderCapability",
    "assert_capability",
]
