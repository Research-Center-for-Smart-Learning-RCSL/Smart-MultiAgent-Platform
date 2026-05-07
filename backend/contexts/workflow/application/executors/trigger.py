"""Trigger node executor — entry point of every workflow run.

W5: validates cron_expression syntax for cron-type triggers so an invalid
expression fails fast at run-start rather than silently being ignored by the
cron scheduler.
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


@register(NodeType.TRIGGER)
async def execute(ctx: RunContext, node: NodeSpec, db: AsyncSession) -> StepOutcome:
    config = node.config
    trigger_type = config.get("trigger_type", "manual")

    if trigger_type == "cron":
        cron_expr = config.get("cron_expression", "")
        if not cron_expr:
            return StepOutcome(
                state=StepState.FAILED,
                error="cron trigger requires a non-empty cron_expression",
            )
        try:
            from croniter import croniter  # type: ignore[import-untyped]

            if not croniter.is_valid(cron_expr):
                return StepOutcome(
                    state=StepState.FAILED,
                    error=f"invalid cron_expression: {cron_expr!r}",
                )
        except ImportError:
            logger.warning("croniter not installed; skipping cron expression validation")

        tz_str = config.get("timezone", "UTC")
        logger.info(
            "run %s: cron trigger fired (expr=%r tz=%s)",
            ctx.run_id,
            cron_expr,
            tz_str,
        )

    return StepOutcome(
        state=StepState.SUCCEEDED,
        output={"trigger_type": trigger_type},
        port="default",
    )
