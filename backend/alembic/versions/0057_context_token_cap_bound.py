"""agents.context_token_cap upper bound (task dossier:
docs/tasks/2026-07-16-context-token-cap-upper-bound/).

`context_token_cap` was unbounded above at every layer (API, DB, domain), unlike its
neighbour `skill_index_token_cap` (0056_skills.py, `agents_skill_index_cap_bounded`).
In compact mode the cap becomes the compaction ceiling verbatim
(`turn_engine.py`); a value above every provider's context window silently disables
compaction and guarantees the request is rejected at the provider. See R9.10a.

Replaces the 0011 `agents_context_cap_positive` CHECK with `agents_context_cap_bounded`,
mirroring `agents_skill_index_cap_bounded`'s naming and shape. The upper bound
(1 000 000) is `MAX_CONTEXT_TOKEN_CAP` in `contexts/agents/domain/models.py`, derived
from `CONTEXT_LIMITS` there — this migration's literal is a snapshot of that constant at
this revision, same as 0056's `_INDEX_CAP_CHECK` is a snapshot of
`MAX_SKILL_INDEX_TOKEN_CAP`.

Pre-existing rows above the bound are clamped to it, not nulled or left to fail on next
write: nulling would silently change the agent's behaviour to "provider default", a
different turn shape than the operator configured; clamping preserves intent as closely
as the new rule allows and matches the value the agent would already have effectively
got (every ceiling above the provider window already behaves as the window). The clamp
is a data write and is not reversible — the downgrade drops the CHECK but does not
restore clamped values.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0057_context_token_cap_bound"
down_revision: str | Sequence[str] | None = "0056_skills"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_log = logging.getLogger("alembic.runtime.migration")

# Snapshot of MAX_CONTEXT_TOKEN_CAP (contexts/agents/domain/models.py) at this
# revision — max(CONTEXT_LIMITS.values()), currently Gemini's 1_000_000 window.
_MAX_CONTEXT_TOKEN_CAP = 1_000_000

_CAP_CHECK = (
    f"context_token_cap IS NULL OR "
    f"(context_token_cap > 0 AND context_token_cap <= {_MAX_CONTEXT_TOKEN_CAP})"
)


def upgrade() -> None:
    bind = op.get_bind()
    result = bind.execute(
        sa.text("UPDATE agents SET context_token_cap = :cap WHERE context_token_cap > :cap"),
        {"cap": _MAX_CONTEXT_TOKEN_CAP},
    )
    if result.rowcount:
        _log.warning(
            "0057: clamped context_token_cap to %d on %d agent row(s) above the bound",
            _MAX_CONTEXT_TOKEN_CAP,
            result.rowcount,
        )

    op.drop_constraint("agents_context_cap_positive", "agents", type_="check")
    op.create_check_constraint("agents_context_cap_bounded", "agents", _CAP_CHECK)


def downgrade() -> None:
    op.drop_constraint("agents_context_cap_bounded", "agents", type_="check")
    op.create_check_constraint(
        "agents_context_cap_positive", "agents", "context_token_cap IS NULL OR context_token_cap > 0"
    )
