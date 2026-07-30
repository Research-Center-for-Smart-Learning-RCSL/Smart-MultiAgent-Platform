"""subagent_spawn executor — fails fast; sub-agent execution is not implemented.

The node's contract is "spawn a sub-agent and run its task". Only the bookkeeping
half of G.8 ([R15.18]-[R15.23]) was ever built: ``SubagentService.spawn`` inserts an
``agent_instances`` row, but nothing hydrates a runtime, runs the task, or tears the
instance down. The completion protocol had a writer and no reader — the node armed
``wf:subagent_callback:{instance_id}`` and parked, and the only reader sits behind
``SubagentService.destroy``, which has no production caller. Every run therefore
parked until the watchdog force-failed it on ``idle_max_seconds``, reporting a cause
unrelated to the actual defect, and leaking one synthetic root plus one child
instance per spawn.

Failing immediately on the ``failure`` port is the truthful outcome: the platform
cannot honour the node's contract, and the workflow's own declared failure path is
the designed channel for that. Linter rule 13 (``application/linter.py``) already
makes an unconnected ``failure`` port a blocking save error unless
``on_error.strategy`` is ``continue``, so every saved workflow containing this node
already has that path wired — fail-fast lands where the author designed.

Deferred, NOT cancelled: [R15.18]-[R15.23] remain live requirements. The runtime
hydration, turn execution and teardown this node needs are a feature, tracked with
their inherited follow-ups in
``docs/tasks/2026-07-22-subagent-spawn-fail-fast/spec.md``.
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
    "this node and output_variable is left unset. This capability is deferred to a "
    "feature dossier, not cancelled — see "
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
