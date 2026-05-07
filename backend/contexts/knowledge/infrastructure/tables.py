"""SQLAlchemy Core tables for the knowledge (RAG) context.

DDL is owned by `alembic/versions/0012_rag.py`. This module exists so the
shared_kernel db registry can import the table bindings.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from shared_kernel.db import metadata

rag_configs = sa.Table(
    "rag_configs",
    metadata,
    sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
    sa.Column(
        "project_id", pg.UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    ),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column(
        "chunk_strategy",
        pg.ENUM("fixed", "semantic", name="rag_chunk_strategy", create_type=False),
        nullable=False,
    ),
    sa.Column("chunk_params", pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    sa.Column(
        "embed_key_id",
        pg.UUID(as_uuid=True),
        sa.ForeignKey("api_keys.id", ondelete="RESTRICT"),
        nullable=True,
    ),
    sa.Column("embed_provider", sa.Text, nullable=False),
    sa.Column("embed_model", sa.Text, nullable=False),
    sa.Column("rerank_enabled", sa.Boolean, nullable=False, server_default=sa.text("false")),
    sa.Column(
        "rerank_key_id",
        pg.UUID(as_uuid=True),
        sa.ForeignKey("api_keys.id", ondelete="RESTRICT"),
        nullable=True,
    ),
    sa.Column("rerank_provider", sa.Text, nullable=True),
    sa.Column("rerank_model", sa.Text, nullable=True),
    sa.Column("top_k", sa.Integer, nullable=False, server_default=sa.text("8")),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    sa.Column("deleted_at", sa.TIMESTAMP(timezone=True), nullable=True),
)


rag_documents = sa.Table(
    "rag_documents",
    metadata,
    sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
    sa.Column(
        "rag_config_id",
        pg.UUID(as_uuid=True),
        sa.ForeignKey("rag_configs.id", ondelete="CASCADE"),
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
)


rag_chunks = sa.Table(
    "rag_chunks",
    metadata,
    sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
    sa.Column(
        "document_id",
        pg.UUID(as_uuid=True),
        sa.ForeignKey("rag_documents.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column("chunk_idx", sa.Integer, nullable=False),
    sa.Column("text", sa.Text, nullable=False),
    sa.Column("qdrant_point_id", pg.UUID(as_uuid=True), nullable=False),
)


__all__ = ["rag_chunks", "rag_configs", "rag_documents"]
