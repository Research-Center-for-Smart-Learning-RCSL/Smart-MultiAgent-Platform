"""Repair agents stranded by a File RAG / Knowledge Map config soft-delete (F-18).

Deleting a RAG or Knowledge Map config soft-deletes the config row (``UPDATE
deleted_at``) but never fires the ``ON DELETE SET NULL`` FK on
``agents.rag_config_id`` / ``agents.knowmap_config_id`` — that FK only triggers
on a physical row DELETE. Every agent attached to an already-deleted config is
left pointing at a tombstone: retrieval silently returns no context, and the
agent's next full-form PATCH re-submits the dangling id and fails validation, so
the agent becomes uneditable through the UI.

The forward fix (nulling the binding inside the config soft-delete) is in the
delete services; this one-time data repair nulls the bindings that were already
stranded in the field. It also disables the dependent ``hosted_file_search``
tool for every agent whose ``rag_config_id`` it nulls, preserving the invariant
*file_search enabled => rag_config_id present* (an enabled File Search tool with
no backing config is a worse state than the dangling FK).

Data-only ``UPDATE``s; the file-search disable runs *before* the null so it can
still join through ``rag_config_id``. ``downgrade`` is a no-op: nulled bindings
cannot be reconstructed and the pre-fix state was invalid. The ``UPDATE agents``
fires ``trg_agents_bump_version`` (migration 0029), bumping ``version`` for each
repaired agent — expected and harmless (an old client refetches on the next
``If-Match`` mismatch).
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0054_config_delete_agent_unbind"
down_revision: str | Sequence[str] | None = "0053_knowmap_corpus_revision"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Disable the File Search singleton for agents bound to a soft-deleted RAG
    #    config. Must run before step 2 while the join through rag_config_id holds.
    op.execute(
        """
        UPDATE agent_tools tl
        SET enabled = false
        WHERE tl.tool_type = 'hosted_file_search'
          AND tl.enabled = true
          AND EXISTS (
              SELECT 1
              FROM agents a
              JOIN rag_configs c ON c.id = a.rag_config_id
              WHERE a.id = tl.agent_id
                AND c.deleted_at IS NOT NULL
          )
        """
    )
    # 2. Null the RAG binding where the referenced config is soft-deleted.
    op.execute(
        """
        UPDATE agents a
        SET rag_config_id = NULL
        WHERE a.rag_config_id IS NOT NULL
          AND EXISTS (
              SELECT 1 FROM rag_configs c
              WHERE c.id = a.rag_config_id AND c.deleted_at IS NOT NULL
          )
        """
    )
    # 3. Null the Knowledge Map binding where the referenced config is soft-deleted.
    op.execute(
        """
        UPDATE agents a
        SET knowmap_config_id = NULL
        WHERE a.knowmap_config_id IS NOT NULL
          AND EXISTS (
              SELECT 1 FROM knowmap_configs c
              WHERE c.id = a.knowmap_config_id AND c.deleted_at IS NOT NULL
          )
        """
    )


def downgrade() -> None:
    # No-op: the nulled bindings are unrecoverable and the pre-fix state (a
    # dangling reference to a tombstoned config) was invalid.
    pass
