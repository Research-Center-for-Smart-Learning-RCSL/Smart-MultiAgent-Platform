"""Repair max_alive_subagents and backfill workflow_capabilities (Q-8/Q-9).

workflow-capability-enforcement spec §7.5-7.6/7.8. Two independent, unrelated
data repairs landing in one migration because both touch the same JSONB
column and both must ship before/with the runtime enforcement (A/B) and API
validator (F) this spec adds, or they retroactively break existing agents:

1. **Repair.** Any `max_alive_subagents` outside 1..20 (including wrong-typed
   values) is set to 3 (R15.20 / SUBAGENT_MAX_CONCURRENT_DEFAULT), regardless
   of `can_create_subagent`. Unconditional and unambiguous: the value is out
   of spec on every source that defines it, and leaving it would 422 the next
   unrelated PATCH of that agent once the API validator ships (Q-9).

2. **Backfill (Q-8, option (i) — derived, narrow).** Every existing agent
   carries `workflow_capabilities: {}` (`seed.py`'s default), so enforcing
   `can_instruct`/`can_approve` for real would deny every agent currently
   named in that role by a saved workflow -- breaking every working approval
   gate and instruct node at deploy. This grants `can_instruct` / `can_approve`
   only to agents actually referenced in that role (`issuer_agent_id`;
   `leader_agent_id` + `approvers`, mirroring the executor's own fold of the
   leader into `approvers`) by a live (non-deleted) workflow definition -- the
   same role-specific extraction as
   `contexts.workflow.application.linter._collect_agent_ids`, inlined rather
   than imported so this migration keeps replaying correctly if that module
   moves. Insert-only: an agent whose stored value is already an explicit
   `False` is left untouched (an operator's decision is not overridden), and
   `can_create_subagent` is never granted -- it has no runtime meaning
   (`2026-07-22-subagent-spawn-fail-fast` removed the only caller `spawn` had).

Neither repair is reversible in the meaningful sense: the max_alive_subagents
clamp cannot restore the original out-of-range value (nor should it -- it was
never valid), and post-deploy operator edits to workflow_capabilities are
indistinguishable from this migration's own grants once made. `downgrade()` is
therefore a documented no-op, same posture as 0057's context_token_cap clamp.
"""

from __future__ import annotations

import logging
import uuid
from collections import defaultdict
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision: str = "0073_workflow_capability_backfill"
down_revision: str | Sequence[str] | None = "0072_message_turn_job_idempotency"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_log = logging.getLogger("alembic.runtime.migration")

# Snapshot of SUBAGENT_MAX_CONCURRENT_DEFAULT / _HARD (orchestration/domain/models.py)
# at this revision, inlined for the same reason 0064/0057 inline their snapshots.
_MAX_ALIVE_SUBAGENTS_DEFAULT = 3
_MAX_ALIVE_SUBAGENTS_MAX = 20


def _needs_max_alive_repair(caps: dict[str, Any]) -> bool:
    if caps.get("max_alive_subagents") is None:
        return False
    value = caps["max_alive_subagents"]
    if isinstance(value, bool) or not isinstance(value, int):
        return True
    return not (1 <= value <= _MAX_ALIVE_SUBAGENTS_MAX)


def _add_grant(grants: dict[uuid.UUID, set[str]], raw_id: Any, capability: str) -> None:
    if not raw_id:
        return
    try:
        agent_id = uuid.UUID(str(raw_id))
    except (ValueError, TypeError, AttributeError):
        return
    grants[agent_id].add(capability)


def upgrade() -> None:
    conn = op.get_bind()

    agents_t = sa.table(
        "agents",
        sa.column("id", pg.UUID(as_uuid=True)),
        sa.column("workflow_capabilities", pg.JSONB),
    )
    workflows_t = sa.table(
        "workflows",
        sa.column("definition", pg.JSONB),
        sa.column("deleted_at", sa.TIMESTAMP(timezone=True)),
    )

    grants: dict[uuid.UUID, set[str]] = defaultdict(set)
    wf_query = sa.select(workflows_t.c.definition).where(workflows_t.c.deleted_at.is_(None))
    wf_rows = conn.execute(wf_query).fetchall()
    for (definition,) in wf_rows:
        for node in (definition or {}).get("nodes", []) or []:
            config = node.get("config") or {}
            ntype = node.get("type")
            if ntype == "instruct":
                _add_grant(grants, config.get("issuer_agent_id"), "can_instruct")
            elif ntype == "approval_gate":
                _add_grant(grants, config.get("leader_agent_id"), "can_approve")
                for approver in config.get("approvers") or []:
                    _add_grant(grants, approver, "can_approve")

    repaired = 0
    granted: dict[str, int] = {"can_instruct": 0, "can_approve": 0}
    agent_rows = conn.execute(sa.select(agents_t.c.id, agents_t.c.workflow_capabilities)).fetchall()
    for agent_id, raw_caps in agent_rows:
        caps = dict(raw_caps or {})
        changed = False

        if _needs_max_alive_repair(caps):
            caps["max_alive_subagents"] = _MAX_ALIVE_SUBAGENTS_DEFAULT
            changed = True
            repaired += 1

        for capability in grants.get(agent_id, ()):
            if caps.get(capability) is False:
                continue  # never overwrite an explicit operator denial
            if caps.get(capability) is not True:
                caps[capability] = True
                changed = True
                granted[capability] += 1

        if changed:
            update = agents_t.update().where(agents_t.c.id == agent_id).values(workflow_capabilities=caps)
            conn.execute(update)

    if repaired:
        _log.warning(
            "0073: repaired max_alive_subagents to %d on %d agent row(s)",
            _MAX_ALIVE_SUBAGENTS_DEFAULT,
            repaired,
        )
    if granted["can_instruct"] or granted["can_approve"]:
        _log.warning(
            "0073: backfilled can_instruct on %d agent row(s), can_approve on %d agent row(s) "
            "(derived from live workflow definitions, Q-8 option (i))",
            granted["can_instruct"],
            granted["can_approve"],
        )


def downgrade() -> None:
    # Not reversible in any meaningful sense (see module docstring): the
    # max_alive_subagents clamp cannot recover the original out-of-range
    # value, and a post-deploy operator grant/revoke is indistinguishable
    # from this migration's own write once made. Same posture as 0057.
    pass
