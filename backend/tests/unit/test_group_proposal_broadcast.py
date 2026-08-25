"""What a room learns about a group's vote — AC-11.

The room channel is a blind relay to every participant and to every agent reading
that room, so anything on it is effectively public to the class. That makes the
NEGATIVE assertions here the point of the file: the proposed answer and every
per-person vote must be absent from the payload, not merely absent from the shape
a client happens to render.

The room echo's half of AC-11 lives beside the submit path
(``TestTheEchoNamesTheGroup``), because it is produced there.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

import pytest

from contexts.activities.domain.models import (
    GroupProposal,
    GroupProposalTally,
    ProposalStatus,
    ProposalVote,
    VoteChoice,
)
from contexts.activities.interfaces import broadcast

_NOW = dt.datetime(2026, 8, 25, tzinfo=dt.UTC)
_ROOM = uuid.uuid4()
_GROUP = uuid.uuid4()
_ALICE, _BOB, _CARA = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
_SECRET = "our shared case is the school trip that went wrong"


def _tally() -> GroupProposalTally:
    """A tally holding BOTH of the things the room may not see: the payload and
    a recorded dissent."""
    proposal = GroupProposal(
        id=uuid.uuid4(),
        chatroom_id=_ROOM,
        activation_id=uuid.uuid4(),
        activity_type_id=uuid.uuid4(),
        member_group_id=_GROUP,
        proposer_user_id=_ALICE,
        payload={"case": _SECRET},
        voter_user_ids=(_ALICE, _BOB, _CARA),
        required_approvals=2,
        status=ProposalStatus.OPEN,
        created_at=_NOW,
        expires_at=_NOW,
    )
    return GroupProposalTally(
        proposal=proposal,
        approvals=1,
        rejections=1,
        undecided=1,
        votes=(
            ProposalVote(proposal_id=proposal.id, user_id=_ALICE, choice=VoteChoice.APPROVE, created_at=_NOW),
            ProposalVote(proposal_id=proposal.id, user_id=_BOB, choice=VoteChoice.REJECT, created_at=_NOW),
        ),
    )


@pytest.fixture
def emitted() -> list[tuple[str, dict[str, Any]]]:
    return []


@pytest.fixture
def publisher(monkeypatch: pytest.MonkeyPatch, emitted: list[tuple[str, dict[str, Any]]]) -> None:
    class _Publisher:
        def __init__(self, _channel: object) -> None:
            pass

        async def emit(self, event: str, payload: dict[str, Any]) -> None:
            emitted.append((event, payload))

    monkeypatch.setattr(broadcast, "Publisher", _Publisher)
    monkeypatch.setattr(broadcast, "room_channel", lambda _r: "room")


class TestTheRoomLearnsThatAGroupIsDeciding:
    async def test_the_payload_carries_ids_and_counts(
        self, publisher: None, emitted: list[tuple[str, dict[str, Any]]]
    ) -> None:
        tally = _tally()

        await broadcast.dispatch_group_proposal("activity.proposal.voted", tally)

        event, payload = emitted[0]
        assert event == "activity.proposal.voted"
        assert payload["member_group_id"] == str(_GROUP)
        assert payload["required_approvals"] == 2
        assert payload["approvals"] == 1
        assert payload["rejections"] == 1
        assert payload["undecided"] == 1
        assert payload["voter_count"] == 3

    async def test_the_payload_never_carries_the_proposed_answer(
        self, publisher: None, emitted: list[tuple[str, dict[str, Any]]]
    ) -> None:
        await broadcast.dispatch_group_proposal("activity.proposal.opened", _tally())

        assert _SECRET not in repr(emitted[0][1])
        assert "payload" not in emitted[0][1]

    async def test_the_payload_never_carries_a_per_person_vote(
        self, publisher: None, emitted: list[tuple[str, dict[str, Any]]]
    ) -> None:
        """A dissent reaching the room channel would reach every agent reading
        that room, which is how a disagreement becomes class material."""
        await broadcast.dispatch_group_proposal("activity.proposal.resolved", _tally())

        rendered = repr(emitted[0][1])
        for user_id in (_ALICE, _BOB, _CARA):
            assert str(user_id) not in rendered
        assert "votes" not in emitted[0][1]

    async def test_a_publish_failure_is_swallowed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Post-commit and best-effort like every other dispatch here: the vote is
        already durable, so a Redis hiccup must not surface as a failed request."""

        class _Broken:
            def __init__(self, _channel: object) -> None:
                pass

            async def emit(self, *_a: object, **_k: object) -> None:
                raise RuntimeError("redis down")

        monkeypatch.setattr(broadcast, "Publisher", _Broken)
        monkeypatch.setattr(broadcast, "room_channel", lambda _r: "room")

        await broadcast.dispatch_group_proposal("activity.proposal.voted", _tally())

    async def test_the_expiry_dispatch_says_only_that_it_expired(
        self, publisher: None, emitted: list[tuple[str, dict[str, Any]]]
    ) -> None:
        proposal_id = uuid.uuid4()

        await broadcast.dispatch_group_proposal_expired(_ROOM, proposal_id, _GROUP)

        event, payload = emitted[0]
        assert event == "activity.proposal.resolved"
        assert payload == {
            "proposal_id": str(proposal_id),
            "member_group_id": str(_GROUP),
            "status": "expired",
        }
