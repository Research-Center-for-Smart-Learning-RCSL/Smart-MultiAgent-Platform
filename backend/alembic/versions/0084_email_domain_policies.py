"""Durable email-domain policy singleton (R19a.13).

Revision ID: 0084_email_domain_policies
Revises: 0083_widen_agent_effort

The policy this table replaces lived in three unversioned Redis keys with no
writer, no TTL and no version, under `--maxmemory-policy allkeys-lru`: an evicted
or restored Redis read as `mode=off`, which admits every domain, so the control
failed open exactly when it mattered.

The table is created **empty**. A row appears only when the ordered startup
initializer imports one atomic snapshot of the legacy triple, in
`rollout_state='compatibility'` — importing does not switch authority, because
replicas that know only the three Redis keys may still be serving. An explicit
maintenance command activates once the operator has drained them.

**Singleton is a schema constraint, not a convention.** `id` is a smallint pinned
to 1 by a CHECK, so a second row is impossible regardless of what races on the
insert; the bootstrap advisory lock then only has to pick a winner among
concurrent first starts rather than be the sole guard.

Downgrade touches PostgreSQL only, and deliberately: this migration never reads
or writes Redis, so an `alembic downgrade` cannot restore the legacy triple. The
`prepare-email-domain-policy-rollback` command does that, and its verified marker
(`legacy_mirrored_version = version`) is the documented precondition for running
this downgrade or starting an old image. See `docs/operations.md` §7a.6.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision: str = "0084_email_domain_policies"
down_revision: str | Sequence[str] | None = "0083_widen_agent_effort"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "email_domain_policies",
        sa.Column("id", sa.SmallInteger, primary_key=True, autoincrement=False),
        sa.Column("mode", sa.Text, nullable=False),
        sa.Column("rollout_state", sa.Text, nullable=False),
        # `text[]` rather than a child table: the lists are replaced wholesale by
        # one guarded UPDATE, never queried by element, and a child table would
        # make the version guard span two statements.
        sa.Column(
            "allow_domains",
            pg.ARRAY(sa.Text),
            nullable=False,
            server_default=sa.text("'{}'::text[]"),
        ),
        sa.Column(
            "deny_domains",
            pg.ARRAY(sa.Text),
            nullable=False,
            server_default=sa.text("'{}'::text[]"),
        ),
        sa.Column("version", sa.Integer, nullable=False, server_default=sa.text("1")),
        # Set only by a rollback preparation that wrote and read back the legacy
        # triple. Equality with `version` is the marker an operator must see
        # before starting an old image.
        sa.Column("legacy_mirrored_version", sa.Integer, nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        # NULL for the row the bootstrap import writes: no Admin authored it.
        sa.Column(
            "updated_by_user_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.CheckConstraint("id = 1", name="ck_email_domain_policies_singleton"),
        # The API is not the only writer — an operator can UPDATE — so the legal
        # value sets are enforced here rather than only in Pydantic. A row whose
        # mode or state the application cannot parse would otherwise be
        # indistinguishable from a corrupt cache and fail every request closed.
        sa.CheckConstraint("mode IN ('allow', 'deny', 'off')", name="ck_email_domain_policies_mode"),
        sa.CheckConstraint(
            "rollout_state IN ('compatibility', 'active', 'rollback_frozen')",
            name="ck_email_domain_policies_rollout_state",
        ),
        sa.CheckConstraint("version >= 1", name="ck_email_domain_policies_version_positive"),
        sa.CheckConstraint(
            "legacy_mirrored_version IS NULL OR legacy_mirrored_version >= 1",
            name="ck_email_domain_policies_mirrored_version_positive",
        ),
    )


def downgrade() -> None:
    op.drop_table("email_domain_policies")
