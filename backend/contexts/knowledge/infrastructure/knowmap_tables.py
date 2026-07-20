"""SQLAlchemy Core tables for the Knowledge Map (knowmap) subsystem.

DDL is owned by ``alembic/versions/0048_knowmap.py``, amended by ``0058`` (which
moved ``last_build_state`` off the shared PG ENUM onto Text + CHECK). This module
exists so the shared_kernel db registry can import the table bindings. Column types
mirror the migration exactly (ORM enum-match rule): the remaining shared PG ENUMs
are referenced with ``create_type=False`` since they are minted by the rag migrations.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from contexts.knowledge.domain.graphrag import build_state_check_sql
from shared_kernel.db import metadata

knowmap_configs = sa.Table(
    "knowmap_configs",
    metadata,
    sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
    sa.Column(
        "project_id", pg.UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    ),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column(
        "builder_key_group_id",
        pg.UUID(as_uuid=True),
        sa.ForeignKey("key_groups.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    sa.Column(
        "chunk_strategy",
        pg.ENUM("fixed", "semantic", name="rag_chunk_strategy", create_type=False),
        nullable=False,
    ),
    sa.Column("chunk_params", pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    # Embedding pin (Phase 2a D2) for the knowmap_{project_id} collection. Plain
    # Text/Integer — no PG ENUM (ORM enum-match rule). Nullable to share the
    # graphrag pin helpers; the config service requires a resolvable key at create.
    sa.Column("embed_provider", sa.Text, nullable=True),
    sa.Column("embed_model", sa.Text, nullable=True),
    sa.Column("embed_dim", sa.Integer, nullable=True),
    sa.Column("last_build_at", sa.TIMESTAMP(timezone=True), nullable=True),
    # F-4 (migration 0059): started-at watermark for the *current* build. Distinct
    # from last_build_at, which is only written on a terminal outcome and so still
    # holds the previous build's time while this one runs. Observability only.
    sa.Column("build_started_at", sa.TIMESTAMP(timezone=True), nullable=True),
    # Text + CHECK since 0058 — see graphrag_tables.py for why the ENUM was retired.
    sa.Column(
        "last_build_state",
        sa.Text,
        nullable=False,
        server_default=sa.text("'idle'"),
    ),
    sa.Column("last_build_error", sa.Text, nullable=True),
    # F-12: monotonic corpus revision bumped per committed document mutation; the
    # build dedup job id keys on it. ``built_corpus_revision`` is the revision the
    # last successful build processed (for the completion re-check).
    sa.Column("corpus_revision", sa.Integer, nullable=False, server_default=sa.text("0")),
    sa.Column("built_corpus_revision", sa.Integer, nullable=True),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    sa.Column("deleted_at", sa.TIMESTAMP(timezone=True), nullable=True),
    sa.CheckConstraint(build_state_check_sql(), name="build_state_valid"),
)


knowmap_documents = sa.Table(
    "knowmap_documents",
    metadata,
    sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
    sa.Column(
        "knowmap_config_id",
        pg.UUID(as_uuid=True),
        sa.ForeignKey("knowmap_configs.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column("filename", sa.Text, nullable=False),
    sa.Column("mime", sa.Text, nullable=False),
    sa.Column("size_bytes", sa.BigInteger, nullable=False),
    sa.Column("sha256", sa.Text, nullable=False),
    sa.Column("minio_path", sa.Text, nullable=False),
    sa.Column(
        "status",
        pg.ENUM("ingesting", "ready", "failed", "quarantined", name="rag_document_status", create_type=False),
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
        "uploaded_by", pg.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    ),
    sa.Column("uploaded_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    # Strict per-agent allowlist (R11.12/R10.11). Empty = no agent may see any
    # relation sourced from this document; the retrieval edge filter gates a
    # relation on *all* of its source documents (Q-2).
    sa.Column(
        "agent_ids",
        pg.ARRAY(pg.UUID(as_uuid=True)),
        nullable=False,
        server_default=sa.text("'{}'::uuid[]"),
    ),
    # Per-document ingest-attempt counter (F-23), parallel to rag_documents:
    # bumped on each genuine tus re-upload of a terminal-non-READY document and
    # folded into the ingest/scan Arq job ids. See migration 0055.
    sa.Column("ingest_attempt", sa.Integer, nullable=False, server_default=sa.text("0")),
)


knowmap_chunks = sa.Table(
    "knowmap_chunks",
    metadata,
    sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
    sa.Column(
        "document_id",
        pg.UUID(as_uuid=True),
        sa.ForeignKey("knowmap_documents.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column("chunk_idx", sa.Integer, nullable=False),
    sa.Column("text", sa.Text, nullable=False),
    # Mirrors the migration — without it `compare_type=True` autogenerate would
    # emit a spurious drop_constraint for this constraint.
    sa.UniqueConstraint("document_id", "chunk_idx", name="uq_knowmap_chunk_doc_idx"),
)


__all__ = ["knowmap_chunks", "knowmap_configs", "knowmap_documents"]
