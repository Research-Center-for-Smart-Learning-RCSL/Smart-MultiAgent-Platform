"""Unified agent_tools table — replaces the agent_mcp_servers + builtin gate.

Every agent tool (built-in singletons, MCP servers, functions) becomes an
explicit row in ``agent_tools`` with a ``tool_type`` discriminator and an
``enabled`` flag.  This eliminates the ``source='builtin'`` overload and the
legacy "no builtin binding => all three on" back-compat rule.

The old ``agent_mcp_servers`` table is kept until migration 0037 (after code
cutover + Phase B E2E green) — this migration only ADDS the new table and
backfills it.  N-1 safe: old code keeps reading ``agent_mcp_servers``.

Backfill logic replays ``_enabled_builtins`` per agent:
  - No builtin-source rows => all 3 built-ins enabled (legacy all-on).
  - At least one builtin-source row => union of reference + allowed_tools
    entries matching {web_search, code_exec, file}.
  - The ``__none__`` sentinel => all 3 disabled.
  - url/package rows become ``hosted_mcp`` with their original id preserved
    (so envelope-encrypted auth AAD bound to the id keeps decrypting).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision: str = "0036_agent_tools"
down_revision: str | Sequence[str] | None = "0035_rag_document_agent_scope"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_TOOL_TYPES: tuple[str, ...] = (
    "hosted_mcp",
    "hosted_web_search",
    "hosted_code_interpreter",
    "hosted_file_workspace",
    "hosted_file_search",
    "local_function",
    "local_shell",
)

_BUILTIN_TOOL_NAMES = frozenset({"web_search", "code_exec", "file"})
_BUILTIN_NONE_SENTINEL = "__none__"

_BUILTIN_TO_TOOL_TYPE = {
    "web_search": "hosted_web_search",
    "code_exec": "hosted_code_interpreter",
    "file": "hosted_file_workspace",
}


def upgrade() -> None:
    op.execute(
        "CREATE TYPE agent_tool_type AS ENUM ("
        + ", ".join(f"'{v}'" for v in _TOOL_TYPES)
        + ")"
    )

    op.create_table(
        "agent_tools",
        sa.Column(
            "id",
            pg.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "agent_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("agents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "tool_type",
            pg.ENUM(*_TOOL_TYPES, name="agent_tool_type", create_type=False),
            nullable=False,
        ),
        sa.Column(
            "enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column("display_name", sa.Text(), nullable=True),
        sa.Column(
            "config",
            pg.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    op.create_index("ix_agent_tools_agent", "agent_tools", ["agent_id"])
    op.execute(
        "CREATE UNIQUE INDEX uq_agent_tools_singleton "
        "ON agent_tools (agent_id, tool_type) "
        "WHERE tool_type IN ("
        "'hosted_web_search','hosted_code_interpreter',"
        "'hosted_file_workspace','hosted_file_search'"
        ")"
    )

    _backfill()


def _backfill() -> None:
    """Replay the legacy _enabled_builtins gate per agent, then copy MCP rows."""
    conn = op.get_bind()

    agents_t = sa.table("agents", sa.column("id", pg.UUID(as_uuid=True)))
    mcp_t = sa.table(
        "agent_mcp_servers",
        sa.column("id", pg.UUID(as_uuid=True)),
        sa.column("agent_id", pg.UUID(as_uuid=True)),
        sa.column("source", sa.Text),
        sa.column("reference", sa.Text),
        sa.column("allowed_tools", pg.ARRAY(sa.Text)),
        sa.column("config", pg.JSONB),
        sa.column("created_at", sa.TIMESTAMP(timezone=True)),
    )
    tools_t = sa.table(
        "agent_tools",
        sa.column("id", pg.UUID(as_uuid=True)),
        sa.column("agent_id", pg.UUID(as_uuid=True)),
        sa.column("tool_type", sa.Text),
        sa.column("enabled", sa.Boolean),
        sa.column("display_name", sa.Text),
        sa.column("config", pg.JSONB),
        sa.column("created_at", sa.TIMESTAMP(timezone=True)),
    )

    agent_ids = [
        row[0] for row in conn.execute(sa.select(agents_t.c.id)).fetchall()
    ]

    for agent_id in agent_ids:
        old_rows = conn.execute(
            sa.select(
                mcp_t.c.id,
                mcp_t.c.source,
                mcp_t.c.reference,
                mcp_t.c.allowed_tools,
                mcp_t.c.config,
                mcp_t.c.created_at,
            ).where(mcp_t.c.agent_id == agent_id)
        ).fetchall()

        # --- Compute enabled builtins (replay _enabled_builtins) ---
        builtin_rows = [r for r in old_rows if r.source == "builtin"]
        if not builtin_rows:
            enabled_set = set(_BUILTIN_TOOL_NAMES)
        else:
            enabled_set: set[str] = set()
            for b in builtin_rows:
                if b.reference in _BUILTIN_TOOL_NAMES:
                    enabled_set.add(b.reference)
                if b.allowed_tools:
                    enabled_set.update(
                        t for t in b.allowed_tools if t in _BUILTIN_TOOL_NAMES
                    )

        # --- Insert singleton rows ---
        for builtin_name, tool_type in _BUILTIN_TO_TOOL_TYPE.items():
            conn.execute(
                tools_t.insert().values(
                    agent_id=agent_id,
                    tool_type=tool_type,
                    enabled=builtin_name in enabled_set,
                    config={},
                )
            )
        # file_search is new — always starts disabled
        conn.execute(
            tools_t.insert().values(
                agent_id=agent_id,
                tool_type="hosted_file_search",
                enabled=False,
                config={},
            )
        )

        # --- Copy MCP (url/package) rows with original id ---
        mcp_rows = [r for r in old_rows if r.source in ("url", "package")]
        for mr in mcp_rows:
            mcp_config = dict(mr.config or {})
            sealed = mcp_config.get("auth")
            if sealed and isinstance(sealed, dict) and not sealed.get("__sealed__"):
                raise RuntimeError(
                    f"agent_mcp_servers row {mr.id} has config.auth that is not "
                    f"sealed (__sealed__ marker absent) — cannot migrate safely"
                )
            mcp_config["source"] = mr.source
            mcp_config["reference"] = mr.reference
            mcp_config["allowed_tools"] = list(mr.allowed_tools or [])
            conn.execute(
                tools_t.insert().values(
                    id=mr.id,
                    agent_id=agent_id,
                    tool_type="hosted_mcp",
                    enabled=True,
                    display_name=(mr.reference or "")[:200] or None,
                    config=mcp_config,
                    created_at=mr.created_at,
                )
            )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_agent_tools_singleton")
    op.drop_index("ix_agent_tools_agent", table_name="agent_tools")
    op.drop_table("agent_tools")
    op.execute("DROP TYPE agent_tool_type")
