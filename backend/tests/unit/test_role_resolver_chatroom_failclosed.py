"""The chatroom-participant resolver must not fail open (SEC-L3).

It previously returned `True` for everyone, which would grant any ROOM_ACL
capability the moment a route fed a `chatroom_id` scope into the matrix. It is
unreachable today, but a silent allow-all is a landmine — so it must refuse
loudly rather than approve.
"""

from __future__ import annotations

import uuid

import pytest

from contexts.tenancy.interfaces.role_resolver import TenancyRoleResolver


@pytest.mark.asyncio()
async def test_is_chatroom_participant_does_not_fail_open() -> None:
    resolver = TenancyRoleResolver(db=None)  # type: ignore[arg-type]
    with pytest.raises(NotImplementedError):
        await resolver.is_chatroom_participant(
            user_id=uuid.uuid4(),
            chatroom_id=uuid.uuid4(),
        )
