"""project_embedding_pins — durable per-(project, kind) embedding pin (F-11).

Each project's Qdrant collection (``rag_{project_id}`` / ``knowmap_{project_id}``
/ ``graphrag_{project_id}``) is fixed-size, sized by whichever config indexes
first. Before this, the "pin" that keeps every sibling config at that one
dimension was derived from *live configs only* — so deleting the pinning config
made the pin vanish while the sized collection survived, and a later config at a
different dimension was accepted into an unusable state. File RAG had no
``embed_dim`` column at all, so it had no durable pin to derive from.

This table records one authoritative ``(provider, model, dim)`` per
``(project_id, kind)``, independent of the config lifecycle. The
``EmbeddingPinRepository`` reads/creates it under a transaction-scoped advisory
lock so concurrent first-creates cannot race, and the delete path drops the
empty collection and clears the pin when the last config for a
``(project, kind)`` is removed.

Expand-only: the new table plus a data backfill of one pin per existing
``(project, kind)`` with a live config. Old code ignores the table, so this is
forward-compatible; the downgrade drops it and the system reverts to
derive-from-live-configs behaviour. No collections are resized or dropped by this
migration.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision: str = "0052_project_embedding_pins"
down_revision: str | Sequence[str] | None = "0051_agent_sampling"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_log = logging.getLogger("alembic.runtime.migration")

# Whitelisted embedding-model dimensions (mirror of
# contexts.knowledge.domain.models.EMBED_MODEL_DIMENSIONS). Inlined so the
# migration does not import application code, which may have moved by the time an
# old migration replays.
_DIMS: dict[tuple[str, str], int] = {
    ("openai", "text-embedding-3-small"): 1536,
    ("openai", "text-embedding-3-large"): 3072,
    ("gemini", "text-embedding-004"): 768,
    ("voyage", "voyage-3"): 1024,
}


def upgrade() -> None:
    op.create_table(
        "project_embedding_pins",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "project_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("dim", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint(
            "kind IN ('file_rag', 'knowmap', 'graphrag')",
            name="ck_project_embedding_pins_kind",
        ),
        sa.UniqueConstraint("project_id", "kind", name="uq_project_embedding_pins_project_kind"),
    )

    _backfill()


def _backfill() -> None:
    """Seed one pin per existing ``(project, kind)`` with a live config.

    ``graphrag``/``knowmap`` carry a stored ``embed_dim``; file RAG has none, so
    its dimension is computed from the whitelist. A project whose live configs
    disagree on dimension (only reachable via the pre-fix race) is logged for
    manual review and pinned to the first (oldest) config's dimension. Idempotent
    via ``ON CONFLICT (project_id, kind) DO NOTHING``.
    """
    bind = op.get_bind()

    # (kind, SQL) — ordered by created_at so the oldest live config wins a tie.
    sources = [
        (
            "file_rag",
            "SELECT project_id, embed_provider, embed_model, NULL::int AS embed_dim "
            "FROM rag_configs WHERE deleted_at IS NULL ORDER BY created_at",
        ),
        (
            "knowmap",
            "SELECT project_id, embed_provider, embed_model, embed_dim "
            "FROM knowmap_configs WHERE deleted_at IS NULL AND embed_dim IS NOT NULL "
            "ORDER BY created_at",
        ),
        (
            "graphrag",
            "SELECT project_id, embed_provider, embed_model, embed_dim "
            "FROM graphrag_configs WHERE deleted_at IS NULL AND embed_dim IS NOT NULL "
            "ORDER BY created_at",
        ),
    ]

    insert = sa.text(
        "INSERT INTO project_embedding_pins (project_id, kind, provider, model, dim) "
        "VALUES (:project_id, :kind, :provider, :model, :dim) "
        "ON CONFLICT (project_id, kind) DO NOTHING"
    )

    for kind, query in sources:
        # project_id -> (provider, model, dim) chosen for the pin (first seen).
        chosen: dict[str, tuple[str, str, int]] = {}
        dims_seen: dict[str, set[int]] = defaultdict(set)
        for row in bind.execute(sa.text(query)):
            provider, model = row.embed_provider, row.embed_model
            if provider is None or model is None:
                continue
            dim = row.embed_dim
            if dim is None:
                dim = _DIMS.get((provider, model))
            if dim is None:
                # A legacy model no longer in the whitelist — cannot size a pin.
                continue
            pid = str(row.project_id)
            dims_seen[pid].add(int(dim))
            chosen.setdefault(pid, (provider, model, int(dim)))

        for pid, (provider, model, dim) in chosen.items():
            if len(dims_seen[pid]) > 1:
                _log.warning(
                    "0052 backfill: project %s has %s configs at conflicting dimensions %s; "
                    "pinning to %d — review manually",
                    pid,
                    kind,
                    sorted(dims_seen[pid]),
                    dim,
                )
            bind.execute(
                insert,
                {"project_id": pid, "kind": kind, "provider": provider, "model": model, "dim": dim},
            )


def downgrade() -> None:
    op.drop_table("project_embedding_pins")
