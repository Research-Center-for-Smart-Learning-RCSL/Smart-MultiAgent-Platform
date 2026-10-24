"""Room wake-up settings for one agent binding ([R15.25], matrix row 29).

- ``GET   /api/chatrooms/{room_id}/agents/{agent_id}/wakeup``       effective values and sources
- ``PATCH /api/chatrooms/{room_id}/agents/{agent_id}/wakeup``       change room overrides
- ``POST  /api/chatrooms/{room_id}/agents/{agent_id}/wakeup/reset`` drop runtime or all room layers

Only wake-up fields are exposed: never the agent's prompt, tools, skills or keys.
An observer binding the caller may not manage answers exactly like an unbound
agent (404), so the endpoint cannot reveal a hidden observer ([R28.10]).
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from contexts.conversation.application.access import (
    can_manage_room_wakeup,
    ensure_can_read,
    resolve_room_access,
)
from contexts.conversation.interfaces.facade import BindingWakeupState, ChatroomAgentRole, ConversationFacade
from contexts.orchestration.interfaces.facade import (
    AUTOSTOP_HARD_CAP,
    N_MAX,
    T_MINUTES_MAX,
    OrchestrationFacade,
    RoomWakeup,
    RoomWakeupChange,
    WakeupOverrides,
)
from shared_kernel.auth.dependencies import current_principal
from shared_kernel.auth.permissions import Principal
from shared_kernel.db.session import db_session

chatroom_router = APIRouter(prefix="/api/chatrooms", tags=["chatrooms"])
_log = logging.getLogger(__name__)

Source = Literal["designer", "room", "runtime"]


class WakeupIntFieldOut(BaseModel):
    value: int
    designer_value: int
    source: Source


class WakeupBoolFieldOut(BaseModel):
    value: bool
    designer_value: bool
    source: Source


class WakeupCountFieldOut(BaseModel):
    value: Literal["all", "human"]
    designer_value: Literal["all", "human"]
    source: Source


class RoomWakeupOut(BaseModel):
    agent_id: uuid.UUID
    binding_id: uuid.UUID
    version: int
    role: Literal["normal", "observer"]
    every_n_messages_enabled: WakeupBoolFieldOut
    every_n_messages: WakeupIntFieldOut
    count: WakeupCountFieldOut
    silence_minutes_enabled: WakeupBoolFieldOut
    silence_minutes: WakeupIntFieldOut
    autostop_rounds: WakeupIntFieldOut
    # [R15.26]: absent for an observer binding, which cannot wake on arrivals.
    on_join_enabled: WakeupBoolFieldOut | None = None
    # Designer policy the room cannot change, shown so the editor can say why.
    call_only: bool
    agent_may_toggle: list[Literal["every_n_messages", "silence_minutes"]]
    # Agent adjustments stored but not applied under the designer's current policy.
    ignored_agent_fields: list[str]
    runtime_refresh_at: datetime | None


class RoomWakeupPatchIn(BaseModel):
    binding_id: uuid.UUID
    expected_version: int = Field(ge=1)
    every_n_messages_enabled: bool | None = None
    every_n_messages: int | None = Field(default=None, ge=1, le=N_MAX)
    count: Literal["all", "human"] | None = None
    silence_minutes_enabled: bool | None = None
    silence_minutes: int | None = Field(default=None, ge=1, le=T_MINUTES_MAX)
    autostop_rounds: int | None = Field(default=None, ge=1, le=AUTOSTOP_HARD_CAP)
    on_join_enabled: bool | None = None

    @model_validator(mode="after")
    def _at_least_one_field(self) -> RoomWakeupPatchIn:
        if self.overrides().is_empty():
            raise ValueError("supply at least one wake-up field to change")
        return self

    def overrides(self) -> WakeupOverrides:
        return WakeupOverrides(
            every_n_messages_enabled=self.every_n_messages_enabled,
            every_n_messages=self.every_n_messages,
            count=self.count,
            silence_minutes_enabled=self.silence_minutes_enabled,
            silence_minutes=self.silence_minutes,
            autostop_rounds=self.autostop_rounds,
            on_join_enabled=self.on_join_enabled,
        )


class RoomWakeupResetIn(BaseModel):
    binding_id: uuid.UUID
    expected_version: int = Field(ge=1)
    scope: Literal["runtime", "room"]


def _out(current: RoomWakeup) -> RoomWakeupOut:
    eff, base = current.effective.config.triggers, current.defaults.triggers
    src = current.effective.sources
    observer = current.is_observer
    return RoomWakeupOut(
        agent_id=current.state.agent_id,
        binding_id=current.state.binding_id,
        version=current.state.version,
        role="observer" if observer else "normal",
        every_n_messages_enabled=WakeupBoolFieldOut(
            value=eff.every_n_messages.enabled,
            designer_value=base.every_n_messages.enabled,
            source=src["every_n_messages_enabled"],
        ),
        every_n_messages=WakeupIntFieldOut(
            value=eff.every_n_messages.n,
            designer_value=base.every_n_messages.n,
            source=src["every_n_messages"],
        ),
        count=WakeupCountFieldOut(
            value=eff.every_n_messages.count, designer_value=base.every_n_messages.count, source=src["count"]
        ),
        silence_minutes_enabled=WakeupBoolFieldOut(
            value=eff.silence_minutes.enabled,
            designer_value=base.silence_minutes.enabled,
            source=src["silence_minutes_enabled"],
        ),
        silence_minutes=WakeupIntFieldOut(
            value=eff.silence_minutes.t_minutes,
            designer_value=base.silence_minutes.t_minutes,
            source=src["silence_minutes"],
        ),
        autostop_rounds=WakeupIntFieldOut(
            value=eff.silence_minutes.autostop_limit_for(is_observer=observer),
            designer_value=base.silence_minutes.autostop_limit_for(is_observer=observer),
            source=src["autostop_rounds"],
        ),
        on_join_enabled=None
        if observer
        else WakeupBoolFieldOut(
            value=eff.on_join.enabled,
            designer_value=base.on_join.enabled,
            source=src["on_join_enabled"],
        ),
        call_only=base.call_only.enabled,
        agent_may_toggle=sorted(current.defaults.self_toggle),
        ignored_agent_fields=sorted(current.effective.ignored_runtime),
        runtime_refresh_at=current.refresh_due_at,
    )


async def _authorized_state(
    db: AsyncSession, *, principal: Principal, chatroom_id: uuid.UUID, agent_id: uuid.UUID
) -> BindingWakeupState:
    """The binding the caller may manage, from its row alone; raises 404/403 otherwise."""
    access = await resolve_room_access(db, principal=principal, chatroom_id=chatroom_id)
    ensure_can_read(access, is_admin=principal.is_admin)
    # Decided from the binding row alone, before the agent is loaded: an observer
    # the caller may not manage must cost the same work as an unbound agent, or
    # the response time would say which it is ([R28.10]).
    state = await ConversationFacade(db).binding_wakeup_state(chatroom_id=chatroom_id, agent_id=agent_id)
    observer = state is not None and state.role is ChatroomAgentRole.OBSERVER
    if state is None or (
        observer and not can_manage_room_wakeup(access, principal=principal, observer_binding=True)
    ):
        raise HTTPException(status_code=404, detail="agent is not bound to this chatroom")
    if not can_manage_room_wakeup(access, principal=principal, observer_binding=observer):
        raise HTTPException(status_code=403, detail="caller cannot manage this room's agent wake-up settings")
    return state


async def _managed(
    db: AsyncSession, *, principal: Principal, chatroom_id: uuid.UUID, agent_id: uuid.UUID
) -> RoomWakeup:
    state = await _authorized_state(db, principal=principal, chatroom_id=chatroom_id, agent_id=agent_id)
    current = await OrchestrationFacade(db).room_wakeup_for_state(state)
    if current is None:
        raise HTTPException(status_code=404, detail="agent is not bound to this chatroom")
    return current


@chatroom_router.get("/{chatroom_id}/agents/{agent_id}/wakeup")
async def get_room_wakeup(
    chatroom_id: uuid.UUID = Path(...),
    agent_id: uuid.UUID = Path(...),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> RoomWakeupOut:
    return _out(await _managed(db, principal=principal, chatroom_id=chatroom_id, agent_id=agent_id))


@chatroom_router.patch("/{chatroom_id}/agents/{agent_id}/wakeup")
async def patch_room_wakeup(
    body: RoomWakeupPatchIn,
    chatroom_id: uuid.UUID = Path(...),
    agent_id: uuid.UUID = Path(...),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> RoomWakeupOut:
    """Supplied fields replace the room's value and drop the agent's adjustment of
    the same field; nothing is written to the shared agent."""
    # The write re-reads the binding and agent itself; loading them here too was
    # a third read of each.
    state = await _authorized_state(db, principal=principal, chatroom_id=chatroom_id, agent_id=agent_id)
    if body.on_join_enabled is not None and state.role is ChatroomAgentRole.OBSERVER:
        raise HTTPException(status_code=422, detail="an observer binding cannot wake on arrivals")
    change = await OrchestrationFacade(db).patch_room_wakeup(
        agent_id=agent_id,
        room_id=chatroom_id,
        binding_id=body.binding_id,
        expected_version=body.expected_version,
        patch=body.overrides(),
        actor_user_id=principal.user_id,
    )
    await db.commit()
    await _apply_timers(change)
    return _out(change.wakeup)


@chatroom_router.post("/{chatroom_id}/agents/{agent_id}/wakeup/reset")
async def reset_room_wakeup(
    body: RoomWakeupResetIn,
    chatroom_id: uuid.UUID = Path(...),
    agent_id: uuid.UUID = Path(...),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> RoomWakeupOut:
    """``runtime`` drops the agent's own adjustments; ``room`` also returns every
    room value to the designer's current defaults. Neither touches other rooms."""
    await _authorized_state(db, principal=principal, chatroom_id=chatroom_id, agent_id=agent_id)
    change = await OrchestrationFacade(db).reset_room_wakeup(
        agent_id=agent_id,
        room_id=chatroom_id,
        binding_id=body.binding_id,
        expected_version=body.expected_version,
        scope=body.scope,
        actor_user_id=principal.user_id,
    )
    await db.commit()
    await _apply_timers(change)
    return _out(change.wakeup)


async def _apply_timers(change: RoomWakeupChange) -> None:
    """After the commit: the settings are stored, so a Redis failure costs at most
    an early reply and must not turn a saved change into an error."""
    try:
        await OrchestrationFacade.apply_room_wakeup_effects([change.effects])
    except Exception:
        _log.warning("room wake-up timer effects failed after commit", exc_info=True)


__all__ = ["chatroom_router"]
