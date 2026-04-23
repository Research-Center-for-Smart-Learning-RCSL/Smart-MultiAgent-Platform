"""SQLAlchemy Core tables for the identity context.

These Table objects are bound to the shared `metadata` registry so
Alembic sees them on autogenerate. Hand-written revisions own the DDL
(see `alembic/versions/0001_identity.py`); this module exists so that
application-layer queries can target typed columns.

SoC: the `domain/` layer does not import this file (otherwise it'd pull in
SQLAlchemy). All ORM access is routed through `repositories.py`.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from shared_kernel.db import metadata

users = sa.Table(
    "users",
    metadata,
    sa.Column("id", pg.UUID(as_uuid=True), primary_key=True,
              server_default=sa.text("gen_random_uuid()")),
    sa.Column("email", sa.Text, nullable=False),
    sa.Column("password_hash", sa.Text, nullable=False),
    sa.Column("email_verified", sa.Boolean, nullable=False, server_default=sa.text("false")),
    sa.Column("status", sa.Text, nullable=False, server_default=sa.text("'pending'")),
    sa.Column("banned_reason", sa.Text, nullable=True),
    sa.Column("banned_at", sa.TIMESTAMP(timezone=True), nullable=True),
    sa.Column("deleted_at", sa.TIMESTAMP(timezone=True), nullable=True),
    sa.Column("last_login_at", sa.TIMESTAMP(timezone=True), nullable=True),
    sa.Column("version", sa.Integer, nullable=False, server_default=sa.text("1")),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False,
              server_default=sa.text("now()")),
)

sessions = sa.Table(
    "sessions",
    metadata,
    sa.Column("id", pg.UUID(as_uuid=True), primary_key=True,
              server_default=sa.text("gen_random_uuid()")),
    sa.Column("user_id", pg.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"),
              nullable=False),
    sa.Column("family_id", pg.UUID(as_uuid=True), nullable=False),
    sa.Column("refresh_token_hash", sa.Text, nullable=False, unique=True),
    sa.Column("user_agent", sa.Text, nullable=True),
    sa.Column("ip_inet", pg.INET(), nullable=True),
    sa.Column("last_jti", pg.UUID(as_uuid=True), nullable=True),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False,
              server_default=sa.text("now()")),
    sa.Column("last_used_at", sa.TIMESTAMP(timezone=True), nullable=False,
              server_default=sa.text("now()")),
    sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("revoked_at", sa.TIMESTAMP(timezone=True), nullable=True),
)

password_reset_tokens = sa.Table(
    "password_reset_tokens",
    metadata,
    sa.Column("id", pg.UUID(as_uuid=True), primary_key=True,
              server_default=sa.text("gen_random_uuid()")),
    sa.Column("user_id", pg.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"),
              nullable=False),
    sa.Column("token_hash", sa.Text, nullable=False, unique=True),
    sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("used_at", sa.TIMESTAMP(timezone=True), nullable=True),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False,
              server_default=sa.text("now()")),
)

email_verify_tokens = sa.Table(
    "email_verify_tokens",
    metadata,
    sa.Column("id", pg.UUID(as_uuid=True), primary_key=True,
              server_default=sa.text("gen_random_uuid()")),
    sa.Column("user_id", pg.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"),
              nullable=False),
    sa.Column("token_hash", sa.Text, nullable=False, unique=True),
    sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("used_at", sa.TIMESTAMP(timezone=True), nullable=True),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False,
              server_default=sa.text("now()")),
)

admins = sa.Table(
    "admins",
    metadata,
    sa.Column("user_id", pg.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"),
              primary_key=True),
    sa.Column("promoted_by_user_id", pg.UUID(as_uuid=True),
              sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
    sa.Column("promoted_at", sa.TIMESTAMP(timezone=True), nullable=False,
              server_default=sa.text("now()")),
    sa.Column("revoked_at", sa.TIMESTAMP(timezone=True), nullable=True),
)

admin_impersonation_sessions = sa.Table(
    "admin_impersonation_sessions",
    metadata,
    sa.Column("id", pg.UUID(as_uuid=True), primary_key=True,
              server_default=sa.text("gen_random_uuid()")),
    sa.Column("admin_user_id", pg.UUID(as_uuid=True),
              sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    sa.Column("target_user_id", pg.UUID(as_uuid=True),
              sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=False,
              server_default=sa.text("now()")),
    sa.Column("ended_at", sa.TIMESTAMP(timezone=True), nullable=True),
    sa.Column("started_request_id", pg.UUID(as_uuid=True), nullable=True),
)

ip_bans = sa.Table(
    "ip_bans",
    metadata,
    sa.Column("id", pg.UUID(as_uuid=True), primary_key=True,
              server_default=sa.text("gen_random_uuid()")),
    sa.Column("cidr", pg.CIDR(), nullable=False, unique=True),
    sa.Column("reason", sa.Text, nullable=False),
    sa.Column("banned_at", sa.TIMESTAMP(timezone=True), nullable=False,
              server_default=sa.text("now()")),
    sa.Column("created_by_user_id", pg.UUID(as_uuid=True),
              sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
)


__all__ = [
    "admin_impersonation_sessions",
    "admins",
    "email_verify_tokens",
    "ip_bans",
    "password_reset_tokens",
    "sessions",
    "users",
]
