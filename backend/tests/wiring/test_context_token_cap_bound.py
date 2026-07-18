"""`agents_context_cap_bounded` DB CHECK (task dossier:
docs/tasks/2026-07-16-context-token-cap-upper-bound/, AC-3).

The API's `le=MAX_CONTEXT_TOKEN_CAP` (AC-1/AC-2, unit-tested in
`test_agents_api_models.py`) is the intended gate; this proves the DB-level backstop
mirroring `agents_skill_index_cap_bounded` also holds for a write that bypasses the API
entirely — the same shape as 0056's sibling constraint.
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from contexts.agents.domain.models import MAX_CONTEXT_TOKEN_CAP
from shared_kernel.db.session import async_session
from tests.wiring.seed import seed_agent

pytestmark = pytest.mark.wiring


async def test_direct_update_above_the_bound_raises_integrity_error() -> None:
    async with async_session() as db:
        agent_id = await seed_agent(db, email_prefix="ctc")

        stmt = sa.text("UPDATE agents SET context_token_cap = :cap WHERE id = :id")
        params = {"cap": MAX_CONTEXT_TOKEN_CAP + 1, "id": agent_id}
        with pytest.raises(IntegrityError, match="agents_context_cap_bounded"):
            await db.execute(stmt, params)


async def test_direct_update_at_the_bound_succeeds() -> None:
    async with async_session() as db:
        agent_id = await seed_agent(db, email_prefix="ctc")

        await db.execute(
            sa.text("UPDATE agents SET context_token_cap = :cap WHERE id = :id"),
            {"cap": MAX_CONTEXT_TOKEN_CAP, "id": agent_id},
        )
        await db.commit()

        row = await db.execute(
            sa.text("SELECT context_token_cap FROM agents WHERE id = :id"), {"id": agent_id}
        )
        assert row.scalar_one() == MAX_CONTEXT_TOKEN_CAP
