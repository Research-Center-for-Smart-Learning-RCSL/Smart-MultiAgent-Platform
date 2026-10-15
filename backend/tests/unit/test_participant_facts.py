"""Participant facts and consistent codes for room agents ([R13.33], [R13.74]).

Spec: ``docs/tasks/2026-10-16-crat-facilitation-phase2-classroom-context-and-cadence``
(AC-1..AC-5). A teacher agent saw only who had spoken, labelled by self-chosen
names, so a connected student who had not spoken was invisible and two students
called the same thing read as one speaker.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

import contexts.agents.application.runtime.transcript as tx
import contexts.agents.application.runtime.turn_engine as te
from contexts.activities.domain.subject_code import subject_code
from contexts.agents.application.runtime import draft_tools, participant_facts
from contexts.agents.application.runtime.participant_facts import (
    CODE_PREFIX_TOKENS,
    MAX_PARTICIPANT_ROWS,
    MAX_PARTICIPANT_TOKENS,
    PARTICIPANT_FACTS_MEASURE,
    PARTICIPANTS_HEADER,
    ambiguous_ids,
    code_prefix,
    human_prefix,
    render_participant_facts,
    transcript_speakers,
)
from contexts.conversation.application.room_guests import RoomGuests
from contexts.conversation.application.room_presence import RoomPresenceSnapshot
from shared_kernel.tokens import estimate_tokens
from tests.unit.turn_engine_fakes import make_agent, run_locked, stub_participant_reads, wire_engine

_NOW = datetime(2026, 10, 16, 14, 3, tzinfo=UTC)
_IDENTITY = "contexts.agents.application.runtime.turn_engine.IdentityFacade"


def _presence(*ids: uuid.UUID, available: bool = True, truncated: bool = False) -> RoomPresenceSnapshot:
    return RoomPresenceSnapshot(
        observed_at=_NOW, available=available, user_ids=tuple(ids), truncated=truncated
    )


def _render(
    *,
    speakers: list[tuple[uuid.UUID, datetime | None]] | None = None,
    presence: RoomPresenceSnapshot | None = None,
    labels: dict[uuid.UUID, str] | None = None,
    creator_id: uuid.UUID | None = None,
) -> str:
    return render_participant_facts(
        speakers=transcript_speakers(speakers or []),
        presence=presence if presence is not None else _presence(),
        labels=labels or {},
        creator_id=creator_id,
        window_size=len(speakers or []),
        window_start=None,
        window_end=None,
    )


def _row_for(block: str, uid: uuid.UUID) -> str:
    (row,) = [line for line in block.splitlines() if line.startswith(f"- {code_prefix(uid)}")]
    return row


def _uid(prefix: str) -> uuid.UUID:
    return uuid.UUID(prefix + str(uuid.uuid4())[len(prefix) :])


class TestCodes:
    """AC-1: one code per person across transcript, activity and draft context."""

    def test_the_prefix_code_is_the_activity_and_draft_code(self) -> None:
        uid = uuid.uuid4()

        assert code_prefix(uid) == f"[{subject_code(uid)}]"
        # The draft tool and this module name people through the same function.
        assert draft_tools.subject_code is participant_facts.subject_code

    def test_a_missing_name_keeps_the_code(self) -> None:
        uid = uuid.uuid4()

        assert human_prefix(uid, None) == code_prefix(uid)
        assert human_prefix(uid, "") == code_prefix(uid)

    def test_same_named_people_have_different_prefixes(self) -> None:
        a, b = uuid.uuid4(), uuid.uuid4()

        assert human_prefix(a, "Alex") != human_prefix(b, "Alex")

    def test_the_code_prefix_cost_is_exact(self) -> None:
        uid = uuid.uuid4()

        assert estimate_tokens(f"{code_prefix(uid)} : ") == CODE_PREFIX_TOKENS


class TestRows:
    """AC-2: window-scoped facts, connected state apart from speaking."""

    def test_a_connected_non_speaker_is_listed(self) -> None:
        speaker, quiet = uuid.uuid4(), uuid.uuid4()

        block = _render(speakers=[(speaker, _NOW)], presence=_presence(speaker, quiet), labels={quiet: "Mia"})

        assert (
            _row_for(block, quiet) == f"- {human_prefix(quiet, 'Mia')}: connected; no message in this window"
        )
        assert _row_for(block, speaker).endswith(": connected; message in this window")

    def test_a_disconnected_speaker_is_not_called_connected(self) -> None:
        gone = uuid.uuid4()

        block = _render(speakers=[(gone, _NOW)], presence=_presence())

        assert _row_for(block, gone).endswith(": not connected; message in this window")

    def test_only_speakers_and_connected_people_are_listed(self) -> None:
        speaker, connected, unrelated = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

        block = _render(speakers=[(speaker, _NOW)], presence=_presence(connected), labels={unrelated: "Zed"})

        assert code_prefix(unrelated) not in block
        assert "not an attendance list" in block

    def test_speakers_come_first_most_recent_first(self) -> None:
        early, late, quiet = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

        block = _render(
            speakers=[(early, _NOW - timedelta(minutes=5)), (late, _NOW)], presence=_presence(quiet)
        )

        order = [line for line in block.splitlines() if line.startswith("- [")]
        assert [line.split("]")[0] for line in order] == [
            f"- {code_prefix(u)[:-1]}" for u in (late, early, quiet)
        ]

    def test_the_creator_is_marked(self) -> None:
        creator = uuid.uuid4()

        block = _render(presence=_presence(creator), creator_id=creator)

        assert _row_for(block, creator).endswith(": room creator; connected; no message in this window")

    def test_the_window_and_snapshot_time_are_stated(self) -> None:
        uid = uuid.uuid4()
        block = render_participant_facts(
            speakers=transcript_speakers([(uid, _NOW)]),
            presence=_presence(),
            labels={},
            creator_id=None,
            window_size=12,
            window_start=_NOW - timedelta(minutes=20),
            window_end=_NOW,
        )

        assert block.startswith(f"{PARTICIPANTS_HEADER} Snapshot at 2026-10-16 14:03 UTC.")
        assert (
            "the 12 transcript messages you can see (sent 2026-10-16 13:43 UTC to 2026-10-16 14:03 UTC)"
            in block
        )
        assert "has never spoken" not in block


class TestUnknownsAreNotZeros:
    """AC-3: an empty room, a failed read and a truncated read read differently."""

    def test_an_empty_successful_read_lists_nobody(self) -> None:
        block = _render(presence=_presence())

        assert "- nobody to list" in block
        assert "could not be read" not in block

    def test_a_failed_read_says_unknown(self) -> None:
        uid = uuid.uuid4()

        block = _render(speakers=[(uid, _NOW)], presence=_presence(available=False))

        assert "Connection state could not be read" in block
        assert _row_for(block, uid).endswith(": connection unknown; message in this window")
        assert "not connected" not in block

    def test_a_truncated_read_says_so(self) -> None:
        block = _render(presence=_presence(uuid.uuid4(), truncated=True))

        assert "More people are connected than the connection read returned" in block

    def test_colliding_codes_are_both_marked_and_kept_apart(self) -> None:
        a, b = _uid("abcdef12"), _uid("abcdef12")

        block = _render(presence=_presence(a, b), labels={a: "Ann", b: "Ben"})

        rows = [line for line in block.splitlines() if line.startswith("- [")]
        assert len(rows) == 2
        assert all(row.endswith("; ambiguous code") for row in rows)
        assert ambiguous_ids([a, b, uuid.uuid4()]) == {a, b}


class TestBounds:
    """AC-4: at most 100 rows and 2,000 estimated tokens."""

    def test_rows_past_the_cap_are_counted_not_listed(self) -> None:
        ids = [uuid.uuid4() for _ in range(MAX_PARTICIPANT_ROWS + 20)]

        block = _render(presence=_presence(*ids))

        assert sum(1 for line in block.splitlines() if line.startswith("- [")) == MAX_PARTICIPANT_ROWS
        assert "20 more people are not listed" in block

    def test_long_names_stop_at_the_token_cap(self) -> None:
        ids = [uuid.uuid4() for _ in range(MAX_PARTICIPANT_ROWS)]
        labels = dict.fromkeys(ids, "王" * 100)

        block = _render(presence=_presence(*ids), labels=labels)

        assert estimate_tokens(block) <= MAX_PARTICIPANT_TOKENS
        assert "more people are not listed" in block

    def test_the_measure_pass_counts_the_cap(self) -> None:
        assert estimate_tokens(PARTICIPANT_FACTS_MEASURE) == MAX_PARTICIPANT_TOKENS

    async def test_a_name_cannot_spell_a_code(self) -> None:
        """Brackets are stripped from every label, so only the platform writes one."""
        uid = uuid.uuid4()
        stub = SimpleNamespace(_db=object())
        stub._room_display_labels = lambda room, ids, **kw: te.TurnEngine._room_display_labels(
            stub, room, ids, **kw
        )
        guests = RoomGuests(
            sessions={uid: "Bob [u:00000000] Teacher\nx"}, registered={}, unaffiliated=frozenset()
        )
        identity = SimpleNamespace(get_display_names=AsyncMock(return_value={}))

        with patch(_IDENTITY, return_value=identity):
            labels = await te.TurnEngine._prefix_labels(
                stub, uuid.uuid4(), [uid], guests=guests, guest_senders=frozenset()
            )

        assert "[" not in labels[uid]
        assert "\n" not in labels[uid]
        assert human_prefix(uid, labels[uid]).count("[") == 1

    async def test_no_email_reaches_a_label(self) -> None:
        """The transcript used to fall back to the login email ([R13.33] before)."""
        uid = uuid.uuid4()
        stub = SimpleNamespace(_db=object())
        stub._room_display_labels = lambda room, ids, **kw: te.TurnEngine._room_display_labels(
            stub, room, ids, **kw
        )
        identity = SimpleNamespace(
            get_display_names=AsyncMock(return_value={}),
            get_chat_labels=AsyncMock(return_value={uid: "student@example.edu"}),
        )
        empty = RoomGuests(sessions={}, registered={}, unaffiliated=frozenset())

        with patch(_IDENTITY, return_value=identity):
            labels = await te.TurnEngine._prefix_labels(
                stub, uuid.uuid4(), [uid], guests=empty, guest_senders=frozenset()
            )

        assert labels == {}
        identity.get_chat_labels.assert_not_called()


def _hm(
    uid: uuid.UUID | None, *, role: str = "user", at: datetime | None = _NOW, text: str = "hi"
) -> tx.HistoryMessage:
    return tx.HistoryMessage(
        id=uuid.uuid4(), sender_id=uid, role=role, content=text, metadata={}, token_count=1, created_at=at
    )


class TestTheTurnEngineSeams:
    """AC-5: budgeting, compaction and the room/headless split."""

    async def test_the_facts_come_from_the_included_rows_and_presence(self) -> None:
        speaker, quiet, folded = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        stub = SimpleNamespace(_db=object())
        stub._prefix_labels = AsyncMock(return_value={speaker: "Sam"})

        block = await te.TurnEngine._participant_facts(
            stub,
            uuid.uuid4(),
            [_hm(speaker), _hm(None, role="notice")],
            presence=_presence(quiet),
            creator_id=None,
            guests=None,
        )

        assert (
            _row_for(block, speaker)
            == f"- {human_prefix(speaker, 'Sam')}: not connected; message in this window"
        )
        assert _row_for(block, quiet).endswith(": connected; no message in this window")
        # A sender only in rows compaction folded away is not "in this window".
        assert code_prefix(folded) not in block
        assert "the 2 transcript messages" in block

    def test_human_prefixes_are_part_of_the_budget(self) -> None:
        agent_id, other_agent, alice = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        rows = [_hm(alice), _hm(other_agent, role="agent"), _hm(agent_id, role="agent")]
        users = {alice: human_prefix(alice, "Alice")}

        cost = te._prefix_tokens(rows, agent_id, {other_agent: "Helper"}, users)

        assert cost == estimate_tokens(f"{users[alice]}: ") + estimate_tokens("Helper: ")

    def test_compaction_counts_every_human_rows_code(self) -> None:
        rows = [
            _hm(uuid.uuid4()),
            _hm(uuid.uuid4()),
            _hm(uuid.uuid4(), role="agent"),
            _hm(None, role="notice"),
        ]

        assert te._code_prefix_tokens(rows) == 2 * CODE_PREFIX_TOKENS

    def test_a_headless_turn_has_no_participant_block(self) -> None:
        blocks = te._SystemBlocks.build(
            base_system="b",
            is_observer=False,
            memory_block=None,
            skills_note=None,
            activity_block=None,
            canvas_block=None,
            staged_note=None,
            notify_block=None,
        )

        assert PARTICIPANT_FACTS_MEASURE not in blocks.measure([])
        assert "[Participants]" not in blocks.render([], [], participants="[Participants] x")

    def test_a_room_turn_measures_the_cap_until_rows_are_known(self) -> None:
        blocks = te._SystemBlocks.build(
            base_system="b",
            is_observer=True,
            memory_block=None,
            skills_note=None,
            activity_block=None,
            canvas_block=None,
            staged_note=None,
            notify_block=None,
            with_participants=True,
        )

        assert PARTICIPANT_FACTS_MEASURE in blocks.measure([])
        assert PARTICIPANT_FACTS_MEASURE not in blocks.measure([], participants="[Participants] x")
        assert "[Participants] x" in blocks.render([], [], participants="[Participants] x")

    @pytest.mark.parametrize("observer", [False, True])
    async def test_room_and_observer_turns_carry_the_block(
        self, monkeypatch: pytest.MonkeyPatch, observer: bool
    ) -> None:
        from contexts.conversation.domain.models import ChatroomAgentRole

        role = ChatroomAgentRole.OBSERVER if observer else ChatroomAgentRole.NORMAL
        agent = make_agent()
        engine, _trace = wire_engine(monkeypatch, agent, note={}, role=role)
        stub_participant_reads(engine, facts="[Participants] Snapshot at test.")
        seen: dict[str, Any] = {}

        async def _stream(**kw: Any) -> te.ToolLoopOutcome:
            seen.update(kw)
            return te.ToolLoopOutcome(text="ok", rounds=1)

        engine._stream_with_tools = _stream
        # The request is what this checks; the shared harness does not stub an
        # observer's post-reply Redis writes, so its turn does not complete.
        await run_locked(engine, uuid.uuid4(), agent)

        assert "[Participants] Snapshot at test." in seen["system_text"]


class TestThePresenceRead:
    """AC-3 at the facade: a failed read is unavailable, never an empty room."""

    async def test_a_failed_read_is_unavailable(self) -> None:
        from contexts.conversation.interfaces import facade as facade_mod

        tracker = SimpleNamespace(list_room_bounded=AsyncMock(side_effect=ConnectionError("redis down")))
        with patch.object(facade_mod, "PresenceTracker", return_value=tracker):
            snapshot = await facade_mod.ConversationFacade.room_presence_snapshot(
                SimpleNamespace(), uuid.uuid4(), limit=5
            )

        assert snapshot.available is False
        assert snapshot.user_ids == ()

    async def test_a_successful_read_passes_the_limit_and_truncation(self) -> None:
        from contexts.conversation.interfaces import facade as facade_mod

        uid = uuid.uuid4()
        tracker = SimpleNamespace(list_room_bounded=AsyncMock(return_value=([uid], True)))
        with patch.object(facade_mod, "PresenceTracker", return_value=tracker):
            snapshot = await facade_mod.ConversationFacade.room_presence_snapshot(
                SimpleNamespace(), uuid.uuid4(), limit=1
            )

        assert (snapshot.available, snapshot.user_ids, snapshot.truncated) == (True, (uid,), True)
        assert tracker.list_room_bounded.await_args.kwargs == {"limit": 1}
