"""Classroom design run worker ([R34.08]).

A composition root like the judge worker: it reads and writes the run through
``AgentsFacade`` and calls the design agent through the provider router on the
agent's own key group, with no tools. Nothing the model writes leaves this
module except through ``parse_design_reply``, and the result is a proposal the
owner must apply; no agent is written here.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from loguru import logger

from contexts.agents.domain.classroom_design import (
    DESIGN_MISCONFIGURED,
    DESIGN_TIMEOUT,
    DESIGN_UNAVAILABLE,
    MAX_OUTPUT_TOKENS,
    ClassroomDesignRun,
    DesignOutputInvalid,
    DesignProposal,
    build_design_prompt,
    parse_design_reply,
)

# Covers the router's quota wait plus the default retry policy, as the judge
# job does, and stays well under the run's stale threshold.
DESIGN_JOB_TIMEOUT_S = 660


class _DesignFailed(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


async def _call_design_agent(db: Any, run: ClassroomDesignRun) -> DesignProposal:
    from contexts.agents.interfaces.facade import AgentsFacade
    from contexts.keys.application.provider_router import ProviderRequest
    from contexts.keys.domain.errors import KeyGroupExhausted
    from contexts.keys.domain.providers import ApiKeyProvider, ProviderCapability
    from contexts.keys.infrastructure.adapters import build_router

    agents = AgentsFacade(db)
    agent = await agents.get_agent(run.design_agent_id)
    # Checked at submit; this holds if the agent was deleted or moved since.
    if agent is None or agent.project_id != run.project_id:
        raise _DesignFailed(DESIGN_MISCONFIGURED)
    provider = ApiKeyProvider(agent.model_hint.value)
    model = agent.model_id
    if not model:
        entry = next((c for c in agents.chat_model_catalog() if c.provider == provider.value), None)
        model = entry.default if entry is not None else None
    if not model:
        raise _DesignFailed(DESIGN_MISCONFIGURED)

    system, user = build_design_prompt(
        design_system_prompt=agent.system_prompt,
        params=run.params,
        teacher_prompt=run.teacher_prompt_before,
        peer_prompt=run.peer_prompt_before,
    )
    payload: dict[str, Any] = {
        "model": model,
        "system": system,
        "messages": [{"role": "user", "content": user}],
        "max_tokens": MAX_OUTPUT_TOKENS,
    }
    payload.update(agents.chat_model_capabilities(provider.value, model))
    request = ProviderRequest(
        capability=ProviderCapability.LLM_CHAT,
        payload=payload,
        agent_id=agent.id,
        provider=provider,
        usage_context="classroom_design",
    )
    try:
        result = await build_router(db).call(group_id=agent.key_group_id, request=request)
    except KeyGroupExhausted as exc:
        raise _DesignFailed(DESIGN_UNAVAILABLE) from exc
    except Exception as exc:
        # Only the class: a provider message can echo the prompts.
        logger.bind(run_id=str(run.id)).warning(f"design call failed: {type(exc).__name__}")
        raise _DesignFailed(DESIGN_UNAVAILABLE) from exc
    text = result.body.get("text") if isinstance(result.body, dict) else None
    if result.http_status != 200 or not isinstance(text, str):
        raise _DesignFailed(DESIGN_UNAVAILABLE)
    try:
        return parse_design_reply(text)
    except DesignOutputInvalid as exc:
        raise _DesignFailed(exc.reason) from exc


async def _record_timeout(db: Any, run_id: uuid.UUID) -> None:
    from contexts.agents.interfaces.facade import AgentsFacade

    try:
        await AgentsFacade(db).record_design_failure(run_id, DESIGN_TIMEOUT)
        await db.commit()
    except Exception as exc:
        # The stale threshold still frees the project; only the usage rows are lost.
        logger.bind(run_id=str(run_id)).warning(f"design timeout write-back failed: {type(exc).__name__}")


async def run_classroom_design(ctx: dict[str, Any], run_id: str) -> str:
    """Draft both prompts for one run and store the proposal or the failure.

    Single attempt (``max_tries=1``): a retry would bill the design agent's key
    again. A job that never runs is freed by the stale threshold at the next
    submit.
    """
    _ = ctx
    from contexts.agents.interfaces.facade import AgentsFacade
    from shared_kernel.audit import flush_tail_events
    from shared_kernel.db.session import async_session

    rid = uuid.UUID(str(run_id))
    async with async_session() as db:
        facade = AgentsFacade(db)
        run = await facade.begin_design_run(rid)
        if run is None:
            return "not-pending"
        # Committed before the call, so the panel shows `running` while it waits.
        await db.commit()
        # Only the call sits in the try: a failed write below must not be taken
        # for a design failure and retried on the same aborted transaction.
        proposal: DesignProposal | None = None
        reason: str | None = None
        try:
            proposal = await _call_design_agent(db, run)
        except _DesignFailed as exc:
            reason = exc.reason
        except asyncio.CancelledError:
            # arq enforces the timeout by cancelling; shielded so the failure and
            # the usage rows of a call already billed still land.
            logger.bind(run_id=str(rid)).warning("classroom design timed out")
            await asyncio.shield(_record_timeout(db, rid))
            raise
        except Exception as exc:
            logger.bind(run_id=str(rid)).warning(f"design setup failed: {type(exc).__name__}")
            reason = DESIGN_UNAVAILABLE
        if proposal is not None:
            changed = await facade.record_design_success(rid, proposal)
        else:
            changed = await facade.record_design_failure(rid, reason or DESIGN_UNAVAILABLE)
        # Commit even when nothing changed: the router has recorded the usage row.
        await db.commit()
        await flush_tail_events(db)

    if not changed:
        return "not-running"
    status = "failed" if reason is not None else "succeeded"
    logger.bind(run_id=str(rid), status=status, reason=reason).info("classroom design finished")
    return status


__all__ = ["DESIGN_JOB_TIMEOUT_S", "run_classroom_design"]
