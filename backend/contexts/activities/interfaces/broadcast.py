"""Realtime dispatches for the activation lifecycle.

Moved here from ``app/api/v1/activities.py`` when delegated activity control
([R30.37]) gave the agent runtime a second way to start and end a round: the two
events have to be published identically whichever path produced them, and the turn
engine cannot import a route module. Both need only ``Publisher`` plus the two
channel helpers, and ``room_channel``/``user_channel`` are already exported from
their contexts' ``interfaces`` packages, so the move adds no cross-context edge.

Every function here is **post-commit and best-effort**: the write is already durable
by the time one is called, so neither a stale read nor a Redis hiccup may surface as
a failed request or a failed turn. Each reports its own failure and returns.
"""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING, Any

from contexts.activities.domain.models import ActivityActivation, ActivityType
from contexts.conversation.interfaces import room_channel
from contexts.identity.interfaces import user_channel
from shared_kernel.realtime.pubsub import Publisher

if TYPE_CHECKING:
    from contexts.activities.interfaces.facade import ActivitiesFacade

_log = logging.getLogger(__name__)


def activity_type_public_payload(activity_type: ActivityType) -> dict[str, Any]:
    """The participant rendering contract as a JSON-ready dict ([R30.26]).

    Identity, key, display name and payload schema — never ``validator_config``,
    which is confidential to Project Owners ([R30.25]).

    The single authority for this projection. ``ActivityTypePublicOut`` in the route
    layer is constructed **from** this dict rather than beside it, so the HTTP
    response and the realtime payload cannot drift into carrying different fields;
    adding a field here without adding it there is a Pydantic error at the route.
    """
    return {
        "id": str(activity_type.id),
        "key": activity_type.key,
        "name": activity_type.name,
        "payload_schema": activity_type.payload_schema,
    }


async def dispatch_activation_started(
    activation: ActivityActivation,
    activity_type: ActivityType | None,
    *,
    started_by_agent_name: str | None = None,
) -> None:
    """Tell one room a round began.

    ``started_by_agent_name`` is resolved by the caller (the agents context is not
    importable from here, [R30.05]) and is absent for a facilitator-started round.
    Naming the agent to the whole room is not a disclosure: an agent bound to a room
    is already named on every message it sends.
    """
    try:
        payload: dict[str, Any] = {
            "activation_id": str(activation.id),
            "activity_type_id": str(activation.activity_type_id),
            "started_by": str(activation.started_by_user_id),
        }
        if activation.started_by_agent_id is not None:
            payload["started_by_agent_id"] = str(activation.started_by_agent_id)
        if started_by_agent_name is not None:
            payload["started_by_agent_name"] = started_by_agent_name
        if activity_type is not None:
            # Same participant projection as the HTTP reads (R30.26) — no
            # validator_config on any realtime payload.
            payload["activity_type"] = activity_type_public_payload(activity_type)
        await Publisher(room_channel(activation.chatroom_id)).emit("activity.activation.started", payload)
    except Exception:
        _log.error("realtime publish failed for activity activation %s", activation.id, exc_info=True)


async def dispatch_activation_ended(chatroom_id: uuid.UUID, activation_id: uuid.UUID) -> None:
    """Tell one room its activation ended. Public because several routes across two
    modules, plus the agent runtime's ``end_activity`` tool, end activations — and
    each must broadcast the same event after its own commit."""
    try:
        await Publisher(room_channel(chatroom_id)).emit(
            "activity.activation.ended", {"activation_id": str(activation_id)}
        )
    except Exception:
        _log.error("realtime publish failed for ended activity activation %s", activation_id, exc_info=True)


async def dispatch_activation_progress(facade: ActivitiesFacade, activation: ActivityActivation) -> None:
    """Tell the facilitator who started this round that its progress moved.

    Addressed to that one user's channel, never the room's ([R28.13] does the
    same for observations): the counts are the facilitator's view of the class,
    and in a two-person group "1 finished" identifies the other participant.

    A delegated round reaches the same recipient, because ``started_by_user_id``
    stays the granting teacher whoever pressed start ([R30.37]) — that property is
    load-bearing for this function and must not be traded away.

    Post-commit and best-effort in both halves -- the count read as much as the
    publish. The write is already durable, so neither a stale count nor a Redis
    hiccup may surface as a failed request.

    A dropped event does NOT self-heal for the starter: they are the only viewer
    this targets and they have no poll (``useActivationProgress`` polls only the
    viewers who cannot receive it), so they hold a stale count until the next
    event or a remount. That is the accepted cost of keeping per-round counts off
    the room channel -- and the reason every writer that moves the counts has to
    call this, the submit path included.
    """
    try:
        completed, in_progress = await facade.count_activation_sessions(
            chatroom_id=activation.chatroom_id, activation_id=activation.id
        )
        await Publisher(user_channel(activation.started_by_user_id)).emit(
            "activity.session.completion",
            {
                "chatroom_id": str(activation.chatroom_id),
                "activation_id": str(activation.id),
                "completed": completed,
                "in_progress": in_progress,
            },
        )
    except Exception:
        _log.warning("activity progress publish failed for activation %s", activation.id, exc_info=True)


async def dispatch_room_activation_progress(facade: ActivitiesFacade, chatroom_id: uuid.UUID) -> None:
    """As :func:`dispatch_activation_progress`, for a caller holding the room
    rather than the round.

    The submit path is the case: it has committed by the time it reaches here, so
    re-reading the active activation is both cheaper and less fragile than
    threading it out through ``submit``'s return. ``None`` means the facilitator
    ended the round in between, which is exactly when there is nothing to report.
    """
    try:
        activation = await facade.get_active_activation(chatroom_id)
    except Exception:
        _log.warning("activity progress lookup failed for room %s", chatroom_id, exc_info=True)
        return
    if activation is None:
        return
    await dispatch_activation_progress(facade, activation)


__all__ = [
    "activity_type_public_payload",
    "dispatch_activation_ended",
    "dispatch_activation_progress",
    "dispatch_activation_started",
    "dispatch_room_activation_progress",
]
