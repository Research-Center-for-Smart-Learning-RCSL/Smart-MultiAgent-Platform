"""SQLAlchemy Core tables for the tenancy context."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from shared_kernel.db import metadata

orgs = sa.Table(
    "orgs",
    metadata,
    sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column(
        "creator_user_id",
        pg.UUID(as_uuid=True),
        sa.ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    sa.Column("version", sa.Integer, nullable=False, server_default=sa.text("1")),
    sa.Column("deleted_at", sa.TIMESTAMP(timezone=True), nullable=True),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
)

org_members = sa.Table(
    "org_members",
    metadata,
    sa.Column(
        "org_id", pg.UUID(as_uuid=True), sa.ForeignKey("orgs.id", ondelete="CASCADE"), primary_key=True
    ),
    sa.Column(
        "user_id", pg.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    ),
    sa.Column("role", sa.Text, nullable=False),
    sa.Column("is_original_creator", sa.Boolean, nullable=False, server_default=sa.text("false")),
    sa.Column("joined_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
)

projects = sa.Table(
    "projects",
    metadata,
    sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
    sa.Column(
        "owner_user_id", pg.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    ),
    sa.Column(
        "owner_org_id", pg.UUID(as_uuid=True), sa.ForeignKey("orgs.id", ondelete="CASCADE"), nullable=True
    ),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column(
        "created_by_user_id",
        pg.UUID(as_uuid=True),
        sa.ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    sa.Column("version", sa.Integer, nullable=False, server_default=sa.text("1")),
    sa.Column("deleted_at", sa.TIMESTAMP(timezone=True), nullable=True),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
)

project_members = sa.Table(
    "project_members",
    metadata,
    sa.Column(
        "project_id",
        pg.UUID(as_uuid=True),
        sa.ForeignKey("projects.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    sa.Column(
        "user_id", pg.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    ),
    sa.Column("role", sa.Text, nullable=False),
    sa.Column("joined_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
)

invites = sa.Table(
    "invites",
    metadata,
    sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
    sa.Column("scope_type", sa.Text, nullable=False),
    sa.Column("scope_id", pg.UUID(as_uuid=True), nullable=False),
    sa.Column("role", sa.Text, nullable=False),
    sa.Column(
        "inviter_user_id",
        pg.UUID(as_uuid=True),
        sa.ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    ),
    sa.Column("invitee_email", sa.Text, nullable=False),
    sa.Column(
        "invitee_user_id",
        pg.UUID(as_uuid=True),
        sa.ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    ),
    sa.Column("state", sa.Text, nullable=False, server_default=sa.text("'pending'")),
    sa.Column("token_hash", sa.Text, nullable=False, unique=True),
    sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    sa.Column("resolved_at", sa.TIMESTAMP(timezone=True), nullable=True),
)

original_creator_transfers = sa.Table(
    "original_creator_transfers",
    metadata,
    sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
    sa.Column("org_id", pg.UUID(as_uuid=True), sa.ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False),
    sa.Column(
        "initiator_user_id",
        pg.UUID(as_uuid=True),
        sa.ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    sa.Column(
        "target_user_id",
        pg.UUID(as_uuid=True),
        sa.ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    sa.Column("state", sa.Text, nullable=False, server_default=sa.text("'pending'")),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("resolved_at", sa.TIMESTAMP(timezone=True), nullable=True),
)


__all__ = [
    "invites",
    "org_members",
    "orgs",
    "original_creator_transfers",
    "project_members",
    "projects",
]
