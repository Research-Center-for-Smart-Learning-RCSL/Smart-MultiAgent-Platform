"""Node executors — one per node type (§2).

All follow the signature:
    async def execute(ctx: RunContext, node: NodeSpec, db: AsyncSession) -> StepOutcome
"""

from contexts.workflow.application.executors.registry import get_executor

__all__ = ["get_executor"]
