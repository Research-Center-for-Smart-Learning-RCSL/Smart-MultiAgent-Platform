"""SQLAlchemy Core table for the durable project embedding pin (F-11).

DDL is owned by ``alembic/versions/0052_project_embedding_pins.py``. One row per
``(project_id, kind)`` records the ``(provider, model, dim)`` a project's
fixed-size Qdrant collection is pinned to, independent of the config lifecycle,
so the pin survives config deletion and cannot be changed by a concurrent
first-create race.

``kind`` is plain ``Text`` + a CHECK constraint rather than a PG ENUM (the ORM
enum-match rule: a bespoke ENUM would have to be minted and kept in lock-step
with the migration; the three-value CHECK carries the same guarantee without the
asyncpg type-binding hazard).
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from shared_kernel.db import metadata

project_embedding_pins = sa.Table(
    "project_embedding_pins",
    metadata,
    sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
    sa.Column(
        "project_id",
        pg.UUID(as_uuid=True),
        sa.ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column("kind", sa.Text, nullable=False),
    sa.Column("provider", sa.Text, nullable=False),
    sa.Column("model", sa.Text, nullable=False),
    sa.Column("dim", sa.Integer, nullable=False),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    sa.CheckConstraint(
        "kind IN ('file_rag', 'knowmap', 'graphrag')",
        name="ck_project_embedding_pins_kind",
    ),
    sa.UniqueConstraint("project_id", "kind", name="uq_project_embedding_pins_project_kind"),
)


__all__ = ["project_embedding_pins"]
