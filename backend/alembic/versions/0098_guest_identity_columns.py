"""Let anonymous guests write activity rows and attachments.

An anonymous guest's principal id is a ``guest_sessions.id``, never a
``users.id`` (R5.04, R13.06). Three columns that guests write still referenced
``users``, so every guest activity submission, completion toggle and attachment
upload failed with a foreign-key violation (audit
``docs/audits/2026-10-05-guest-anonymous-session`` F-1, F-2; dossier
``docs/tasks/2026-10-05-guest-identity-foreign-keys``).

The foreign keys are dropped, as 0096 did for ``audit_logs.actor_user_id``. The
two activity columns gain a stored discriminator, the shape
``messages.sender_type`` + ``sender_id`` already uses for polymorphic human
identity, so a guest subject stays distinguishable from a user after its guest
session is purged. No discriminator for attachments: NULL keeps meaning "agent
artifact", and a bound attachment's uploader kind is its message's
``sender_type``.

User erasure no longer rides the dropped CASCADEs; ``hard_delete_user`` purges a
user's activity rows explicitly (``ActivitiesFacade.purge_user_activity_rows``).

Re-runnable: every statement is guarded, so a retried upgrade after a partial
failure does not trip on its own earlier work.

DOWNGRADE recreates the three foreign keys, which fails once any guest row
exists. Delete guest activity rows (``subject_kind = 'guest'`` sessions and
``producer_kind = 'guest'`` submissions) and guest uploads first.

Revision ID: 0098_guest_identity_columns
Revises: 0097_bump_context_cap_bound
"""

from __future__ import annotations

from alembic import op

revision = "0098_guest_identity_columns"
down_revision = "0097_bump_context_cap_bound"

# Module-level so the db-tier test can name exactly what this migration installs.
SUBJECT_KIND_CHECK_SQL = "subject_kind IN ('user', 'guest', 'member_group')"
SUBJECT_KIND_GROUP_CHECK_SQL = "(subject_kind = 'member_group') = (subject_member_group_id IS NOT NULL)"
PRODUCER_KIND_CHECK_SQL = "producer_kind IN ('user', 'guest')"

_USER_FKS = (
    ("activity_sessions", "fk_activity_sessions_subject_user_id_users", "subject_user_id", "CASCADE"),
    ("activity_submissions", "fk_activity_submissions_producer_user_id_users", "producer_user_id", "CASCADE"),
    (
        "message_attachments",
        "fk_message_attachments_uploaded_by_user_id_users",
        "uploaded_by_user_id",
        "SET NULL",
    ),
)


def upgrade() -> None:
    op.execute(
        "ALTER TABLE activity_sessions ADD COLUMN IF NOT EXISTS subject_kind text NOT NULL DEFAULT 'user'"
    )
    # A group session's kind must say so before the pairing CHECK exists.
    op.execute(
        "UPDATE activity_sessions SET subject_kind = 'member_group' "
        "WHERE subject_member_group_id IS NOT NULL AND subject_kind <> 'member_group'"
    )
    _replace_check("activity_sessions", "ck_activity_sessions_subject_kind", SUBJECT_KIND_CHECK_SQL)
    _replace_check(
        "activity_sessions", "ck_activity_sessions_subject_kind_group", SUBJECT_KIND_GROUP_CHECK_SQL
    )

    op.execute(
        "ALTER TABLE activity_submissions ADD COLUMN IF NOT EXISTS producer_kind text NOT NULL DEFAULT 'user'"
    )
    _replace_check("activity_submissions", "ck_activity_submissions_producer_kind", PRODUCER_KIND_CHECK_SQL)

    for table, name, _column, _ondelete in _USER_FKS:
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {name}")


def downgrade() -> None:
    for table, name, column, ondelete in _USER_FKS:
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {name}")
        op.execute(
            f"ALTER TABLE {table} ADD CONSTRAINT {name} "
            f"FOREIGN KEY ({column}) REFERENCES users (id) ON DELETE {ondelete}"
        )
    op.execute("ALTER TABLE activity_submissions DROP CONSTRAINT IF EXISTS ck_activity_submissions_producer_kind")
    op.execute("ALTER TABLE activity_submissions DROP COLUMN IF EXISTS producer_kind")
    op.execute("ALTER TABLE activity_sessions DROP CONSTRAINT IF EXISTS ck_activity_sessions_subject_kind_group")
    op.execute("ALTER TABLE activity_sessions DROP CONSTRAINT IF EXISTS ck_activity_sessions_subject_kind")
    op.execute("ALTER TABLE activity_sessions DROP COLUMN IF EXISTS subject_kind")


def _replace_check(table: str, name: str, predicate: str) -> None:
    op.execute(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {name}")
    op.execute(f"ALTER TABLE {table} ADD CONSTRAINT {name} CHECK ({predicate})")
