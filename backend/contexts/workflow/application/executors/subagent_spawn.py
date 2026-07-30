"""subagent_spawn executor — fails fast; sub-agent execution is not implemented.

Only the bookkeeping half of G.8 ([R15.18]-[R15.23]) was ever built: the node created
an ``agent_instances`` row and armed a Redis callback nothing ever read, so every run
parked until the idle watchdog killed it. Failing immediately on the ``failure`` port
is the truthful outcome. The capability is deferred, NOT cancelled — [R15.18]-[R15.23]
remain live requirements.

Rationale, deferral and inherited follow-ups:
``docs/tasks/2026-07-22-subagent-spawn-fail-fast/spec.md``
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.workflow.application.executors.registry import register
from contexts.workflow.domain.models import (
    NodeSpec,
    NodeType,
    RunContext,
    StepOutcome,
    StepState,
)

logger = logging.getLogger(__name__)

# Mirrors #/$defs/subagent_spawn_config/properties/timeout_seconds in
# docs/workflow.schema.json. Unread while the node fails fast, but kept in one named
# place because the two silently diverged before: the executor defaulted to 3600
# against a schema maximum of 600, so an omitting config got 20x the declared budget
# while the editor displayed 180. The feature dossier reads this instead of
# re-deriving it.
DEFAULT_TIMEOUT_SECONDS = 180

_UNIMPLEMENTED_ERROR = (
    "subagent_spawn is not implemented. Sub-agent execution ([R15.18]-[R15.23]) has no "
    "runtime: spawning would create an agent instance whose task never runs, then park "
    "this run until the idle watchdog killed it. The node fails immediately on its "
    "'failure' port instead. With on_error.strategy='continue' the run proceeds past "
    "this node on its 'success' port and output_variable is left unset. This capability "
    "is deferred to a feature dossier, not cancelled — see "
    "docs/tasks/2026-07-22-subagent-spawn-fail-fast/spec.md."
)


@register(NodeType.SUBAGENT_SPAWN)
async def execute(ctx: RunContext, node: NodeSpec, db: AsyncSession) -> StepOutcome:
    # Returns before reaching the orchestration facade, so no agent_instances row is
    # created and no Redis callback key is written — the orphan-row pressure on
    # retention's _sweep_orphaned_subagent_roots stops at source.
    logger.warning(
        "run %s: subagent_spawn node %s failed fast — capability not implemented",
        ctx.run_id,
        node.id,
    )
    return StepOutcome(
        state=StepState.FAILED,
        output={},
        port="failure",
        error=_UNIMPLEMENTED_ERROR,
    )
