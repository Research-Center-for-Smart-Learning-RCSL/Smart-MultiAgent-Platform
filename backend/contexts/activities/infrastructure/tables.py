"""SQLAlchemy Core tables for the activities context (Chapter §30).

DDL is owned by ``alembic/versions/0049_activities.py``. This module exists so
the shared_kernel db registry can import the bindings; every column type
(especially the PG ENUMs, referenced with ``create_type=False``) matches that
migration verbatim (the ORM enum/type-match rule).
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from shared_kernel.db import metadata

_validator_kind = pg.ENUM("in_process", "mcp", "webhook", name="validator_kind", create_type=False)
_validation_status = pg.ENUM("pending", "validated", "error", name="validation_status", create_type=False)
_session_status = pg.ENUM("open", "closed", name="session_status", create_type=False)
_activation_status = pg.ENUM("active", "ended", name="activation_status", create_type=False)

activity_types = sa.Table(
    "activity_types",
    metadata,
    sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
    # NULL exactly when scope='platform' — 0076 pairs the two with a CHECK, so a
    # half-converted row cannot exist.
    sa.Column(
        "project_id",
        pg.UUID(as_uuid=True),
        sa.ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=True,
    ),
    # 'project' | 'platform' (0076). Text + CHECK rather than a PG ENUM, matching
    # activity_policies.scope: adding a scope stays a code change, not a migration
    # against a type other tables reference.
    sa.Column("scope", sa.Text, nullable=False, server_default=sa.text("'project'")),
    sa.Column("key", sa.Text, nullable=False),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column("payload_schema", pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    sa.Column("validator_kind", _validator_kind, nullable=False),
    sa.Column("validator_config", pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    sa.Column("retention_days", sa.Integer, nullable=True),
    sa.Column("version", sa.Integer, nullable=False, server_default=sa.text("1")),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    # Presentation gates on ActivitySubmission.agent_digest — 0065. Not fully
    # independent: echo_includes_content only takes effect when
    # expose_payload_to_agent is also true (submission_service.py::submit).
    sa.Column("expose_payload_to_agent", sa.Boolean, nullable=False, server_default=sa.text("true")),
    sa.Column("echo_includes_content", sa.Boolean, nullable=False, server_default=sa.text("false")),
    sa.Column("deleted_at", sa.TIMESTAMP(timezone=True), nullable=True),
    # NULL means individual-only, which is every type that predates 0081 ([R30.40]).
    # A behavioural definition field like payload_schema: an edit bumps `version`
    # and is refused while an activation of the type is live, so a consent
    # threshold never changes under a vote in progress.
    sa.Column("group_config", pg.JSONB, nullable=True),
)

activity_sessions = sa.Table(
    "activity_sessions",
    metadata,
    sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
    sa.Column(
        "activity_type_id",
        pg.UUID(as_uuid=True),
        sa.ForeignKey("activity_types.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column(
        "chatroom_id",
        pg.UUID(as_uuid=True),
        sa.ForeignKey("chatrooms.id", ondelete="CASCADE"),
        nullable=False,
    ),
    # Exactly one of the two subject columns is set -- 0081's
    # ck_activity_sessions_one_subject, which replaced this column's NOT NULL
    # rather than merely removing it ([R30.39]).
    sa.Column(
        "subject_user_id",
        pg.UUID(as_uuid=True),
        sa.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True,
    ),
    # Deliberately NO ForeignKey to `member_groups`: that table belongs to the
    # tenancy context and [R30.09] forbids the cross-context join a constraint
    # would invite (the shape `activity_activations.started_by_agent_id` uses).
    sa.Column("subject_member_group_id", pg.UUID(as_uuid=True), nullable=True),
    # The round this session was answered under -- 0077. NULL only for
    # pre-0077 rows; every writer sets it, which is a writer invariant rather
    # than a schema one (a NOT NULL would need activations invented for history).
    sa.Column(
        "activation_id",
        pg.UUID(as_uuid=True),
        sa.ForeignKey("activity_activations.id", ondelete="CASCADE"),
        nullable=True,
    ),
    sa.Column("status", _session_status, nullable=False, server_default=sa.text("'open'::session_status")),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    sa.Column("closed_at", sa.TIMESTAMP(timezone=True), nullable=True),
    # The subject's own "I am finished" declaration -- 0077. Deliberately not
    # `status`, which answers whether the session can still take submissions and
    # is driven by the facilitator ending the round.
    sa.Column("completed_at", sa.TIMESTAMP(timezone=True), nullable=True),
)

activity_activations = sa.Table(
    "activity_activations",
    metadata,
    sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
    sa.Column(
        "chatroom_id",
        pg.UUID(as_uuid=True),
        sa.ForeignKey("chatrooms.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column(
        "activity_type_id",
        pg.UUID(as_uuid=True),
        sa.ForeignKey("activity_types.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column(
        "started_by_user_id",
        pg.UUID(as_uuid=True),
        sa.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column(
        "status",
        _activation_status,
        nullable=False,
        server_default=sa.text("'active'::activation_status"),
    ),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    sa.Column("ended_at", sa.TIMESTAMP(timezone=True), nullable=True),
    # Delegated activity control ([R30.37], migration 0078). Deliberately NO
    # ForeignKey to `agents`: this context must not couple to the agents context,
    # in imports ([R30.05], pinned by tests/unit/test_activities_no_agents_import.py)
    # or in the schema. A deleted agent leaves an id that resolves to nothing,
    # which is the correct reading of "the agent that started this is gone".
    sa.Column("started_by_agent_id", pg.UUID(as_uuid=True), nullable=True),
)

activity_submissions = sa.Table(
    "activity_submissions",
    metadata,
    sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
    sa.Column(
        "session_id",
        pg.UUID(as_uuid=True),
        sa.ForeignKey("activity_sessions.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column(
        "activity_type_id",
        pg.UUID(as_uuid=True),
        sa.ForeignKey("activity_types.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column(
        "chatroom_id",
        pg.UUID(as_uuid=True),
        sa.ForeignKey("chatrooms.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column(
        "producer_user_id",
        pg.UUID(as_uuid=True),
        sa.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column("payload", pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    sa.Column("attempt_no", sa.Integer, nullable=False),
    sa.Column(
        "validation_status",
        _validation_status,
        nullable=False,
        server_default=sa.text("'pending'::validation_status"),
    ),
    sa.Column("is_valid", sa.Boolean, nullable=True),
    sa.Column("error_class", sa.Text, nullable=True),
    sa.Column("sub_scores", pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    sa.Column("latency_ms", sa.Integer, nullable=True),
    sa.Column("retain_until", sa.TIMESTAMP(timezone=True), nullable=True),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    # Agent-visible digest of ``payload`` — 0065; NULL only for pre-migration rows.
    sa.Column("agent_digest", sa.Text, nullable=True),
    sa.Column("validated_at", sa.TIMESTAMP(timezone=True), nullable=True),
    sa.Column("deleted_at", sa.TIMESTAMP(timezone=True), nullable=True),
)


activity_policies = sa.Table(
    "activity_policies",
    metadata,
    sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
    # Only 'platform' is legal in v1; the column exists so a per-org row is an
    # insert rather than a migration (0075).
    sa.Column("scope", sa.Text, nullable=False, server_default=sa.text("'platform'")),
    sa.Column("expose_payload_to_agent_default", sa.Boolean, nullable=False, server_default=sa.text("true")),
    sa.Column("expose_payload_to_agent_locked", sa.Boolean, nullable=False, server_default=sa.text("false")),
    sa.Column("echo_includes_content_default", sa.Boolean, nullable=False, server_default=sa.text("false")),
    sa.Column("echo_includes_content_locked", sa.Boolean, nullable=False, server_default=sa.text("false")),
    sa.Column("retention_days_default", sa.Integer, nullable=True),
    sa.Column("retention_days_max", sa.Integer, nullable=True),
    sa.Column("version", sa.Integer, nullable=False, server_default=sa.text("1")),
    sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    sa.Column(
        "updated_by_user_id",
        pg.UUID(as_uuid=True),
        sa.ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    ),
)


project_activity_type_optins = sa.Table(
    "project_activity_type_optins",
    metadata,
    sa.Column(
        "project_id",
        pg.UUID(as_uuid=True),
        sa.ForeignKey("projects.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    sa.Column(
        "activity_type_id",
        pg.UUID(as_uuid=True),
        sa.ForeignKey("activity_types.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    # SET NULL, not CASCADE: deleting the admin who enabled a type must not
    # silently revoke a project's access to it mid-course.
    sa.Column(
        "enabled_by_user_id",
        pg.UUID(as_uuid=True),
        sa.ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    ),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
)


activity_group_proposals = sa.Table(
    "activity_group_proposals",
    metadata,
    sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
    sa.Column(
        "chatroom_id",
        pg.UUID(as_uuid=True),
        sa.ForeignKey("chatrooms.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column(
        "activation_id",
        pg.UUID(as_uuid=True),
        sa.ForeignKey("activity_activations.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column(
        "activity_type_id",
        pg.UUID(as_uuid=True),
        sa.ForeignKey("activity_types.id", ondelete="CASCADE"),
        nullable=False,
    ),
    # No FK, for the reason activity_sessions.subject_member_group_id carries none.
    sa.Column("member_group_id", pg.UUID(as_uuid=True), nullable=False),
    sa.Column(
        "proposer_user_id",
        pg.UUID(as_uuid=True),
        sa.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column("payload", pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    # The voter set pinned at creation, and the bar computed from it ([R30.41]).
    # Both stored, so resolution needs no membership re-read and a mid-vote
    # membership change moves neither.
    sa.Column("voter_user_ids", pg.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
    sa.Column("required_approvals", sa.Integer, nullable=False),
    # Text + CHECK rather than a PG ENUM, matching activity_types.scope: adding a
    # terminal status stays a code change, not a migration against a shared type.
    sa.Column("status", sa.Text, nullable=False, server_default=sa.text("'open'")),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("resolved_at", sa.TIMESTAMP(timezone=True), nullable=True),
    sa.Column(
        "submission_id",
        pg.UUID(as_uuid=True),
        sa.ForeignKey("activity_submissions.id", ondelete="SET NULL"),
        nullable=True,
    ),
)

activity_group_proposal_votes = sa.Table(
    "activity_group_proposal_votes",
    metadata,
    sa.Column(
        "proposal_id",
        pg.UUID(as_uuid=True),
        sa.ForeignKey("activity_group_proposals.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    sa.Column(
        "user_id",
        pg.UUID(as_uuid=True),
        sa.ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    sa.Column("choice", sa.Text, nullable=False),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
)


__all__ = [
    "activity_activations",
    "activity_group_proposal_votes",
    "activity_group_proposals",
    "activity_policies",
    "activity_sessions",
    "activity_submissions",
    "activity_types",
    "project_activity_type_optins",
]
