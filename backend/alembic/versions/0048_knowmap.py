"""knowmap_configs + knowmap_documents + knowmap_chunks + agents.knowmap_config_id.

GraphRAG Phase 3 — the Knowledge Map (Axis-1 GraphRAG over uploaded documents,
R11.12/R11.13/R11.14/R11.15/R11.20). A Knowledge Map owns its own document corpus
(parallel to the file-RAG tables, reusing the shared parser/chunker/MinIO code,
not its rows) and builds a triple graph over that corpus through the shared 2PC
engine scoped to the ``knowmap_{project_id}`` Qdrant collection.

Reuses the ENUM types installed by earlier migrations rather than minting parallel
ones (the ORM enum-match rule requires the Table binding and the PG type to agree):
``rag_chunk_strategy`` / ``rag_document_status`` / ``rag_scan_status`` (0012_rag) and
``graphrag_build_state`` (0013_graphrag). The downgrade drops only what this migration
created, never those shared types.

Expand-only, non-destructive: new tables + one nullable ``agents.knowmap_config_id``
column (mirroring the ``agents.rag_config_id`` deferred-FK pattern). Rollback drops
them and the Knowledge Map subsystem disappears with no effect on file-RAG or the
Concept Map.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision: str = "0048_knowmap"
down_revision: str | Sequence[str] | None = "0047_graphrag_recency_half_life"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "knowmap_configs",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "project_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        # R11.11-analogue — a builder Key Group resolves the embedding + extraction
        # keys via the shared carried-key path (SEC-H3). RESTRICT: a group with a
        # live Knowledge Map cannot be deleted out from under it.
        sa.Column(
            "builder_key_group_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("key_groups.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        # Corpus chunking config (shared with file-RAG's strategy enum + params).
        sa.Column(
            "chunk_strategy",
            pg.ENUM("fixed", "semantic", name="rag_chunk_strategy", create_type=False),
            nullable=False,
        ),
        sa.Column("chunk_params", pg.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        # Embedding pin (Phase 2a D2) applied to the knowmap_{project_id} collection.
        # Nullable to mirror graphrag_configs so the shared pin helpers apply
        # verbatim; the config service requires a resolvable key at create, so in
        # practice these are always populated for a Knowledge Map.
        sa.Column("embed_provider", sa.Text(), nullable=True),
        sa.Column("embed_model", sa.Text(), nullable=True),
        sa.Column("embed_dim", sa.Integer(), nullable=True),
        # 2PC build state (shared enum with graphrag_configs).
        sa.Column("last_build_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "last_build_state",
            pg.ENUM(
                "idle",
                "running",
                "neo4j_committed",
                "qdrant_committed",
                "failed_compensating",
                "failed",
                name="graphrag_build_state",
                create_type=False,
            ),
            nullable=False,
            server_default=sa.text("'idle'::graphrag_build_state"),
        ),
        sa.Column("last_build_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("deleted_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.create_index("ix_knowmap_configs_project", "knowmap_configs", ["project_id"])
    op.execute(
        "CREATE UNIQUE INDEX uq_knowmap_configs_project_name_active "
        "ON knowmap_configs (project_id, name) WHERE deleted_at IS NULL"
    )

    op.create_table(
        "knowmap_documents",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "knowmap_config_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("knowmap_configs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("mime", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.Text(), nullable=False),
        sa.Column("minio_path", sa.Text(), nullable=False),
        sa.Column(
            "status",
            pg.ENUM(
                "ingesting", "ready", "failed", "quarantined", name="rag_document_status", create_type=False
            ),
            nullable=False,
            server_default=sa.text("'ingesting'::rag_document_status"),
        ),
        sa.Column(
            "scan_status",
            pg.ENUM("pending", "clean", "quarantined", "skipped", name="rag_scan_status", create_type=False),
            nullable=False,
            server_default=sa.text("'pending'::rag_scan_status"),
        ),
        sa.Column("scan_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "uploaded_by",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "uploaded_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")
        ),
        # Strict per-agent allowlist (R11.12/R10.11 mirror of rag_documents.agent_ids).
        # Empty = no agent may see any relation sourced from this document; the
        # retrieval edge filter gates each relation on all its source docs.
        sa.Column(
            "agent_ids",
            pg.ARRAY(pg.UUID(as_uuid=True)),
            nullable=False,
            server_default=sa.text("'{}'::uuid[]"),
        ),
    )
    op.create_index("ix_knowmap_documents_config", "knowmap_documents", ["knowmap_config_id"])
    op.create_index("ix_knowmap_documents_config_sha", "knowmap_documents", ["knowmap_config_id", "sha256"])
    # GIN index backing the per-turn allowed-doc resolution (agent_ids @> [agent_id]).
    op.create_index(
        "ix_knowmap_documents_agent_ids", "knowmap_documents", ["agent_ids"], postgresql_using="gin"
    )

    op.create_table(
        "knowmap_chunks",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "document_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("knowmap_documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("chunk_idx", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        # No qdrant_point_id: knowmap chunks are the build corpus (read by
        # DocDeltaLoader) and the evidence-excerpt source keyed by (document_id,
        # chunk_idx). Only graph *entities* are embedded into Qdrant, at build time.
        sa.UniqueConstraint("document_id", "chunk_idx", name="uq_knowmap_chunk_doc_idx"),
    )
    op.create_index("ix_knowmap_chunks_document", "knowmap_chunks", ["document_id"])

    # Per-agent Knowledge Map binding (parallel to agents.rag_config_id). Bare
    # nullable UUID + late-bound FK, SET NULL so deleting a config unbinds agents.
    op.add_column("agents", sa.Column("knowmap_config_id", pg.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        "fk_agents_knowmap_config",
        source_table="agents",
        referent_table="knowmap_configs",
        local_cols=["knowmap_config_id"],
        remote_cols=["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_agents_knowmap_config", "agents", type_="foreignkey")
    op.drop_column("agents", "knowmap_config_id")
    op.drop_index("ix_knowmap_chunks_document", table_name="knowmap_chunks")
    op.drop_table("knowmap_chunks")
    op.drop_index("ix_knowmap_documents_agent_ids", table_name="knowmap_documents")
    op.drop_index("ix_knowmap_documents_config_sha", table_name="knowmap_documents")
    op.drop_index("ix_knowmap_documents_config", table_name="knowmap_documents")
    op.drop_table("knowmap_documents")
    op.execute("DROP INDEX IF EXISTS uq_knowmap_configs_project_name_active")
    op.drop_index("ix_knowmap_configs_project", table_name="knowmap_configs")
    op.drop_table("knowmap_configs")
