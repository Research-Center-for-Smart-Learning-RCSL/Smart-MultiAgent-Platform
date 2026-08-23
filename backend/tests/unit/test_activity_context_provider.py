"""ActivityContextProvider — block formatting, coverage gate, best-effort (AC-1,2,4).

The provider is where the activity block is built; these cover the deterministic
formatting, the per-row agent-digest gate (``expose_payload_to_agent``), the
None-on-empty coverage gate, and the None-on-exception degradation. Engine-level
wiring (every agent's turn, not just observer) is covered in
``test_turn_engine_observer_activity.py``.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import replace
from unittest.mock import AsyncMock, MagicMock, patch

from contexts.activities.application.activity_context_provider import ActivityContextProvider
from contexts.activities.domain.models import (
    PERMISSIVE_POLICY,
    ActivityPolicy,
    RecentActivityRow,
    ValidationStatus,
)

_FACADE = "contexts.activities.interfaces.facade.ActivitiesFacade"
_NOW = dt.datetime(2026, 7, 13, 10, 30, tzinfo=dt.UTC)


def _row(**over: object) -> RecentActivityRow:
    base = {
        "created_at": _NOW,
        "subject_user_id": uuid.UUID("11111111-1111-1111-1111-111111111111"),
        "attempt_no": 3,
        "type_key": "creativity_probe",
        "validation_status": ValidationStatus.VALIDATED,
        "is_valid": True,
        "error_class": None,
    }
    base.update(over)
    return RecentActivityRow(**base)  # type: ignore[arg-type]


def _facade_returning(rows: list[RecentActivityRow], policy: ActivityPolicy = PERMISSIVE_POLICY) -> MagicMock:
    facade = MagicMock()
    facade.list_recent_activity = AsyncMock(return_value=rows)
    facade.get_activity_policy = AsyncMock(return_value=policy)
    return facade


def _locked_off_policy() -> ActivityPolicy:
    """A platform policy that forbids submission content reaching an agent."""
    return replace(
        PERMISSIVE_POLICY,
        expose_payload_to_agent_default=False,
        expose_payload_to_agent_locked=True,
    )


class TestFormatting:
    async def test_block_lists_deterministic_facts(self) -> None:
        rows = [
            _row(attempt_no=1, validation_status=ValidationStatus.VALIDATED, is_valid=True),
            _row(
                attempt_no=2,
                validation_status=ValidationStatus.VALIDATED,
                is_valid=False,
                error_class="off_topic",
            ),
            _row(attempt_no=3, validation_status=ValidationStatus.PENDING, is_valid=None),
            _row(attempt_no=4, validation_status=ValidationStatus.ERROR, is_valid=None),
        ]
        with patch(_FACADE, return_value=_facade_returning(rows)):
            block = await ActivityContextProvider(MagicMock()).query(chatroom_id=uuid.uuid4())

        assert block is not None
        assert block.startswith("[Recent room activity]\n")
        assert "#1 creativity_probe: valid" in block
        assert "#2 creativity_probe: invalid [off_topic]" in block
        assert "#3 creativity_probe: pending" in block
        assert "#4 creativity_probe: error" in block
        # deterministic subject label + iso timestamp, no LLM inference
        assert "u:11111111" in block
        assert "2026-07-13T10:30" in block

    async def test_passes_limit_through(self) -> None:
        facade = _facade_returning([_row()])
        with patch(_FACADE, return_value=facade):
            await ActivityContextProvider(MagicMock()).query(chatroom_id=uuid.uuid4(), limit=5)
        assert facade.list_recent_activity.await_args.args[1] == 5

    async def test_digest_included_when_type_exposes_it(self) -> None:
        """Agent-visibility follow-up: a row whose type opts in surfaces its
        digest inline."""
        rows = [_row(agent_digest="drew a red circle", expose_payload_to_agent=True)]
        with patch(_FACADE, return_value=_facade_returning(rows)):
            block = await ActivityContextProvider(MagicMock()).query(chatroom_id=uuid.uuid4())

        assert block is not None
        assert "drew a red circle" in block

    async def test_digest_omitted_when_type_opts_out(self) -> None:
        rows = [_row(agent_digest="should not leak", expose_payload_to_agent=False)]
        with patch(_FACADE, return_value=_facade_returning(rows)):
            block = await ActivityContextProvider(MagicMock()).query(chatroom_id=uuid.uuid4())

        assert block is not None
        assert "should not leak" not in block

    async def test_mixed_type_room_gates_per_row(self) -> None:
        """A room with two activity types gates independently per submission's
        own type, not room-wide."""
        rows = [
            _row(attempt_no=1, agent_digest="visible content", expose_payload_to_agent=True),
            _row(attempt_no=2, agent_digest="hidden content", expose_payload_to_agent=False),
        ]
        with patch(_FACADE, return_value=_facade_returning(rows)):
            block = await ActivityContextProvider(MagicMock()).query(chatroom_id=uuid.uuid4())

        assert block is not None
        assert "visible content" in block
        assert "hidden content" not in block


class TestSubjectLegend:
    """The block's codes and the transcript's "Name:" prefixes must be connectable.

    Without the legend an agent holding a participant's own answer in its context
    still cannot tell that the row belongs to the person asking about it: the feed
    says ``u:11111111`` and the transcript says ``Alice``, and nothing joins them.
    The codes stay on the rows on purpose — an observer agent is meant to report by
    code, not by name — so the legend, not a rewrite, is the bridge.
    """

    _ALICE = uuid.UUID("11111111-1111-1111-1111-111111111111")
    _BOB = uuid.UUID("22222222-2222-2222-2222-222222222222")

    async def test_legend_maps_every_subject_in_the_window(self) -> None:
        rows = [_row(subject_user_id=self._ALICE), _row(subject_user_id=self._BOB)]

        async def resolve(ids: object) -> dict[uuid.UUID, str]:
            return {self._ALICE: "Alice Chen", self._BOB: "Bob Lin"}

        with patch(_FACADE, return_value=_facade_returning(rows)):
            block = await ActivityContextProvider(MagicMock()).query(
                chatroom_id=uuid.uuid4(), resolve_labels=resolve
            )

        assert block is not None
        assert "Codes: u:11111111 = Alice Chen; u:22222222 = Bob Lin" in block
        # The rows keep the code, not the name.
        assert "u:11111111 #3 creativity_probe" in block
        assert "Alice Chen #3" not in block

    async def test_no_resolver_keeps_the_bare_codes(self) -> None:
        with patch(_FACADE, return_value=_facade_returning([_row()])):
            block = await ActivityContextProvider(MagicMock()).query(chatroom_id=uuid.uuid4())

        assert block is not None
        assert "Codes:" not in block
        assert "u:11111111" in block

    async def test_a_failing_resolver_costs_the_legend_not_the_block(self) -> None:
        """Best-effort, like every other read on this path: an identity lookup that
        falls over must not take the deterministic outcome facts down with it."""

        async def resolve(ids: object) -> dict[uuid.UUID, str]:
            raise RuntimeError("identity down")

        with patch(_FACADE, return_value=_facade_returning([_row()])):
            block = await ActivityContextProvider(MagicMock()).query(
                chatroom_id=uuid.uuid4(), resolve_labels=resolve
            )

        assert block is not None
        assert "Codes:" not in block
        assert "creativity_probe: valid" in block

    async def test_the_block_says_what_it_is(self) -> None:
        """The framing belongs to the block, not to each agent's prompt: a pack that
        forgets to restate it leaves the model guessing whether an opaque row of
        JSON is something it may discuss at all."""
        with patch(_FACADE, return_value=_facade_returning([_row()])):
            block = await ActivityContextProvider(MagicMock()).query(chatroom_id=uuid.uuid4())

        assert block is not None
        assert "server-computed facts" in block
        assert "not a roster" in block


class TestPlatformPolicyGate:
    """A tightened policy has to reach an activity that is already running.

    Both enforcement gates ([R30.30]) run before a room goes live — authoring and
    activation start — so without a check here an admin who locks
    `expose_payload_to_agent=false` mid-class keeps feeding that room's answers
    to every agent until someone ends the activity. The switch exists for
    consent, and consent withdrawn has to take effect now.
    """

    async def test_a_locked_off_policy_suppresses_a_running_activitys_digests(self) -> None:
        rows = [_row(agent_digest="a student's answer", expose_payload_to_agent=True)]
        with patch(_FACADE, return_value=_facade_returning(rows, _locked_off_policy())):
            block = await ActivityContextProvider(MagicMock()).query(chatroom_id=uuid.uuid4())

        assert block is not None
        assert "a student's answer" not in block
        # The outcome facts stay: they are server-computed and carry no answer text.
        assert "creativity_probe: valid" in block

    async def test_a_locked_on_policy_leaves_the_types_own_choice_alone(self) -> None:
        """Locking the switch *on* does not force content out of a type that
        opted out — the platform sets a ceiling here, not a floor."""
        policy = replace(
            PERMISSIVE_POLICY,
            expose_payload_to_agent_default=True,
            expose_payload_to_agent_locked=True,
        )
        rows = [
            _row(attempt_no=1, agent_digest="opted in", expose_payload_to_agent=True),
            _row(attempt_no=2, agent_digest="opted out", expose_payload_to_agent=False),
        ]
        with patch(_FACADE, return_value=_facade_returning(rows, policy)):
            block = await ActivityContextProvider(MagicMock()).query(chatroom_id=uuid.uuid4())

        assert block is not None
        assert "opted in" in block
        assert "opted out" not in block

    async def test_an_unreadable_policy_withholds_content(self) -> None:
        """Fails closed. A consent control that defaults to permitting on error
        is not a consent control."""
        facade = _facade_returning([_row(agent_digest="answer text", expose_payload_to_agent=True)])
        facade.get_activity_policy = AsyncMock(side_effect=RuntimeError("db down"))
        with patch(_FACADE, return_value=facade):
            block = await ActivityContextProvider(MagicMock()).query(chatroom_id=uuid.uuid4())

        assert block is not None
        assert "answer text" not in block
        assert "creativity_probe" in block


class TestCoverageGateAndFailure:
    async def test_none_when_no_activity(self) -> None:
        with patch(_FACADE, return_value=_facade_returning([])):
            block = await ActivityContextProvider(MagicMock()).query(chatroom_id=uuid.uuid4())
        assert block is None

    async def test_none_on_exception(self) -> None:
        facade = MagicMock()
        facade.list_recent_activity = AsyncMock(side_effect=RuntimeError("db down"))
        with patch(_FACADE, return_value=facade):
            block = await ActivityContextProvider(MagicMock()).query(chatroom_id=uuid.uuid4())
        assert block is None
