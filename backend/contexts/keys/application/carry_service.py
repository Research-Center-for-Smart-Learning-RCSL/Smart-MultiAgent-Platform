"""Key carrying use-cases (D.5 / §22.4).

Two call paths converge here:

1. **User-initiated** — `POST /api/projects/{pid}/keys` / `DELETE` hits
   `carry` / `withdraw` directly.
2. **Membership-removal fanout** — when tenancy removes a user from a
   project, it calls `revoke_for_leaving_user` which withdraws every carry
   whose underlying key is owned by the departing user (R7.04). In-flight
   calls finish naturally because router-side cache invalidation fires via
   `key.carry_revoked` pub/sub; no mid-flight request gets aborted.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from contexts.keys.domain.errors import KeyNotFound, KeyNotOwnedByCaller
from contexts.keys.domain.models import ApiKey
from contexts.keys.infrastructure import tables as t
from contexts.keys.infrastructure.carry_repository import KeyProjectRepository
from contexts.keys.infrastructure.key_revocation_events import publish_carry_revoked
from contexts.keys.infrastructure.repositories import ApiKeyRepository
from shared_kernel import audit
from shared_kernel.infra import redis_buckets

_WINDOWS: dict[str, timedelta | None] = {
    "1h": None,  # served from Redis (sliding bucket, accurate to the minute).
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
}


@dataclass(frozen=True, slots=True)
class UsageSummary:
    window: str
    input_tokens: int
    output_tokens: int
    requests: int
    errors: int  # NULL http_status or non-2xx


class CarryService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._keys = ApiKeyRepository(db)
        self._carries = KeyProjectRepository(db)

    async def list_in_project(self, project_id: uuid.UUID) -> list[ApiKey]:
        return await self._carries.list_active_in_project(project_id)

    async def key_owner(self, key_id: uuid.UUID) -> uuid.UUID | None:
        """Return the owner of `key_id`, or None if no such key exists.

        Used by the withdraw AuthZ gate to resolve the `OWN_ONLY` decision:
        the key's owner withdraws via `key.delete_own`, a Project/Org Owner
        withdraws anyone's carry via `key.delete_other` (SEC-5).
        """
        return await self._keys.owner_of(key_id)

    async def carry(
        self,
        *,
        key_id: uuid.UUID,
        project_id: uuid.UUID,
        caller_user_id: uuid.UUID,
        actor_ip: str | None = None,
        request_id: uuid.UUID | None = None,
    ) -> None:
        """Attach `key_id` (owned by caller) into `project_id`.

        Non-owners cannot carry someone else's key — each Individual owns
        their own secrets (R7.03). AuthZ at the HTTP layer already enforces
        project-membership; ownership is enforced here.
        """
        key = await self._keys.get_active(key_id)
        if key is None:
            raise KeyNotFound(str(key_id))
        if key.owner_user_id != caller_user_id:
            raise KeyNotOwnedByCaller(str(key_id))

        await self._carries.upsert_carry(
            key_id=key_id,
            project_id=project_id,
            added_by_user_id=caller_user_id,
        )
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="key.carried_into_project",
                actor_user_id=caller_user_id,
                actor_ip=actor_ip,
                resource_type="api_key",
                resource_id=key_id,
                metadata={"project_id": str(project_id)},
                request_id=request_id,
            ),
        )

    async def withdraw(
        self,
        *,
        key_id: uuid.UUID,
        project_id: uuid.UUID,
        caller_user_id: uuid.UUID,
        actor_ip: str | None = None,
        request_id: uuid.UUID | None = None,
    ) -> None:
        """Withdraw `key_id` from `project_id`.

        The key owner may always withdraw their own. Project Owners may
        withdraw anyone's via the `key.delete_other` capability — that check
        is done at the HTTP layer; here we only validate the row exists.
        """
        if not await self._carries.withdraw(key_id=key_id, project_id=project_id, at=datetime.now(tz=UTC)):
            # Row missing or already withdrawn — surface as not-found so the
            # caller gets a stable 404 path instead of a silent 204.
            raise KeyNotFound(f"carry {key_id}→{project_id}")

        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="key.withdrawn_from_project",
                actor_user_id=caller_user_id,
                actor_ip=actor_ip,
                resource_type="api_key",
                resource_id=key_id,
                metadata={"project_id": str(project_id)},
                request_id=request_id,
            ),
        )
        await publish_carry_revoked(key_id=key_id, project_id=project_id)

    async def usage(
        self,
        *,
        key_id: uuid.UUID,
        project_id: uuid.UUID,
        window: str,
    ) -> UsageSummary:
        """§22.4 `GET /api/projects/{pid}/keys/{key_id}/usage?window=…`.

        1h is served from the Redis sliding-window buckets (D.8); wider
        windows (24h/7d/30d) aggregate from `key_usage_events`. The key must
        be *currently* carried into `project_id` — a withdrawn carry returns
        404 rather than leaking a usage number into a project that no longer
        has the key (R7.05).
        """
        if window not in _WINDOWS:
            raise ValueError(f"unknown window: {window}")
        carry = await self._db.execute(
            sa.select(sa.literal(1)).where(
                sa.and_(
                    t.key_projects.c.key_id == key_id,
                    t.key_projects.c.project_id == project_id,
                    t.key_projects.c.carried.is_(True),
                )
            )
        )
        if carry.first() is None:
            raise KeyNotFound(f"carry {key_id}→{project_id}")

        delta = _WINDOWS[window]
        if delta is None:
            used = await redis_buckets.usage(key_id)
            # Token/request counts come from the Redis sliding window (accurate
            # to the minute). Redis does not track errors, so fall back to a
            # targeted DB query for the error count only.
            since_1h = datetime.now(tz=UTC) - timedelta(hours=1)
            err_count = (
                await self._db.execute(
                    sa.select(
                        sa.func.count(t.key_usage_events.c.id).filter(
                            sa.or_(
                                t.key_usage_events.c.http_status.is_(None),
                                t.key_usage_events.c.http_status < 200,
                                t.key_usage_events.c.http_status >= 300,
                            )
                        )
                    ).where(
                        sa.and_(
                            t.key_usage_events.c.key_id == key_id,
                            t.key_usage_events.c.at >= since_1h,
                        )
                    )
                )
            ).scalar_one()
            return UsageSummary(
                window=window,
                input_tokens=used.input_tokens,
                output_tokens=used.output_tokens,
                requests=used.requests,
                errors=int(err_count),
            )

        since = datetime.now(tz=UTC) - delta
        stmt = sa.select(
            sa.func.coalesce(sa.func.sum(t.key_usage_events.c.input_tokens), 0),
            sa.func.coalesce(sa.func.sum(t.key_usage_events.c.output_tokens), 0),
            sa.func.count(t.key_usage_events.c.id),
            sa.func.count(t.key_usage_events.c.id).filter(
                sa.or_(
                    t.key_usage_events.c.http_status.is_(None),
                    t.key_usage_events.c.http_status < 200,
                    t.key_usage_events.c.http_status >= 300,
                )
            ),
        ).where(
            sa.and_(
                t.key_usage_events.c.key_id == key_id,
                t.key_usage_events.c.at >= since,
            )
        )
        row = (await self._db.execute(stmt)).one()
        return UsageSummary(
            window=window,
            input_tokens=int(row[0]),
            output_tokens=int(row[1]),
            requests=int(row[2]),
            errors=int(row[3]),
        )

    async def revoke_for_leaving_user(
        self,
        *,
        user_id: uuid.UUID,
        project_id: uuid.UUID,
        actor_user_id: uuid.UUID | None = None,
        request_id: uuid.UUID | None = None,
    ) -> list[uuid.UUID]:
        """Withdraw every carry of `user_id` in `project_id` (R7.04).

        Called by tenancy when a user leaves / is removed from the project.
        Runs inside the caller's transaction so a rollback of the membership
        change also rolls back the cascade. Returns the list of `key_id`s
        revoked so the caller can log a summary.
        """
        now = datetime.now(tz=UTC)
        revoked: list[uuid.UUID] = []
        key_ids = await self._carries.list_active_carries_for_user(user_id=user_id, project_id=project_id)
        for key_id in key_ids:
            if await self._carries.withdraw(key_id=key_id, project_id=project_id, at=now):
                revoked.append(key_id)
                await audit.emit(
                    self._db,
                    audit.AuditEvent(
                        action="key.withdrawn_from_project",
                        actor_user_id=actor_user_id,
                        resource_type="api_key",
                        resource_id=key_id,
                        metadata={
                            "project_id": str(project_id),
                            "reason": "user_left_project",
                            "leaving_user_id": str(user_id),
                        },
                        request_id=request_id,
                    ),
                )
                await publish_carry_revoked(key_id=key_id, project_id=project_id)
        return revoked


__all__ = ["CarryService", "UsageSummary"]
