"""AC-13 — `_SystemBlocks` owns system-block order and per-block role.

`_fixed_system_text` and the inline `system_parts` list were nested closures in
`_run_locked`, unreachable from a unit test — which is why they could drift apart
unnoticed (the F-16/F-17 bug class). They are replaced by one ordered list whose
blocks each declare a role.

The characterization here is **differential**: `_legacy_fixed_system_text` and
`_legacy_render` below are verbatim transcriptions of the two closures as they
stood before the refactor (turn_engine.py:998-1013 and :1071-1133 at 44c66e4).
Every test asserts the new implementation is byte-identical to them across the
matrix of block combinations. Pinning the old behaviour this way is what the
closures themselves made impossible.

Two departures from "verbatim", both post-dating the refactor. §31's `skills_note`
is carried by both baselines in the position §31 specifies (measured with the
fixed context, rendered after knowledge and before activity). And the participant
note now names the room's owner, which costs a DB read to resolve on a pass that
runs before the turn knows whether the note renders at all — so measure counts a
worst-case note and render supplies the real one, and `_legacy_fixed_system_text`
follows. The baseline is still doing its job in both cases: it is the independent
statement of order and role that the implementation must match, and each block's
own semantics are pinned separately (`test_skill_index_budget.py`,
`test_turn_participant_note.py`).
"""

from __future__ import annotations

import itertools

import pytest

from contexts.agents.application import context as ctxmod
from contexts.agents.application.runtime.turn_engine import (
    _OBSERVER_SYSTEM_NOTE,
    _PARTICIPANT_LABEL_NOTE,
    _PARTICIPANT_NOTE_BLOCK,
    _PARTICIPANT_NOTE_MEASURE,
    _BlockRole,
    _BlockSlot,
    _participant_note,
    _SystemBlock,
    _SystemBlocks,
)
from contexts.skills.interfaces.facade import SkillsFacade
from shared_kernel.tokens import estimate_tokens
from tests.unit.skill_fakes import make_skill

# --------------------------------------------------------------------------- #
# Verbatim transcriptions of the pre-refactor closures (the baseline)
# --------------------------------------------------------------------------- #


def _legacy_fixed_system_text(
    summaries: list[str],
    *,
    base_system: str,
    is_observer: bool,
    memory_block: str | None,
    skills_note: str | None,
    activity_block: str | None,
    staged_note: str | None,
    notify_block: str | None,
) -> str:
    parts = [base_system] if base_system else []
    if is_observer:
        parts.append(_OBSERVER_SYSTEM_NOTE)
        if memory_block:
            parts.append(memory_block)
    parts.extend(summaries)
    if skills_note:
        parts.append(skills_note)
    if activity_block:
        parts.append(activity_block)
    if staged_note:
        parts.append(staged_note)
    if notify_block:
        parts.append(notify_block)
    # Departure #2 from "verbatim" (see the module docstring): the note now names
    # the room's owner, and resolving that costs a DB read on a pass that runs
    # before the turn knows whether the note will render at all. So measure counts
    # a worst-case note and render supplies the real one. The baseline follows,
    # because the claim it pins is order-and-role, not this string's contents —
    # and an under-counted fixed context is precisely the F-16 direction.
    parts.append(_PARTICIPANT_NOTE_MEASURE)
    return "\n\n".join(p for p in parts if p)


def _legacy_render(
    summaries: list[str],
    knowledge_blocks: list[str],
    *,
    base_system: str,
    is_observer: bool,
    memory_block: str | None,
    skills_note: str | None,
    activity_block: str | None,
    staged_note: str | None,
    notify_block: str | None,
    include_participant_note: bool,
) -> str:
    system_parts = [base_system] if base_system else []
    if is_observer:
        system_parts.append(_OBSERVER_SYSTEM_NOTE)
        if memory_block:
            system_parts.append(memory_block)
    system_parts.extend(summaries)
    system_parts.extend(knowledge_blocks)
    if skills_note:
        system_parts.append(skills_note)
    if activity_block:
        system_parts.append(activity_block)
    if staged_note:
        system_parts.append(staged_note)
    if notify_block:
        system_parts.append(notify_block)
    if include_participant_note:
        system_parts.append(_PARTICIPANT_LABEL_NOTE)
    return "\n\n".join(p for p in system_parts if p)


# Each optional block is exercised present and absent; base_system is exercised
# empty too (a falsy prompt is dropped, not rendered as a blank part).
_CASES = [
    {
        "base_system": base_system,
        "is_observer": is_observer,
        "memory_block": memory_block,
        "skills_note": skills_note,
        "activity_block": activity_block,
        "staged_note": staged_note,
        "notify_block": notify_block,
    }
    for (
        base_system,
        is_observer,
        memory_block,
        skills_note,
        activity_block,
        staged_note,
        notify_block,
    ) in (
        itertools.product(
            ["You are a helpful agent.", ""],
            [True, False],
            ["[Your recent observations]\n- a", None],
            ["<<<SMAP_SKILLS_UNTRUSTED>>>\n- pdf-fill: Fills PDFs.\n<<<END_SMAP_SKILLS_UNTRUSTED>>>", None],
            ["[Recent room activity]\n- x", None],
            ["[Files available in the code_exec workspace: /session/inputs/a.csv]", None],
            ["[Pending notifications]\n- n", None],
        )
    )
]

_SUMMARY_SETS = [[], ["[Earlier conversation summary]\nfoo"], ["[s1]", "[s2]"]]
_KNOWLEDGE_SETS = [[], ["[RAG]\nchunk"], ["[RAG]", "[CONCEPT]", "[KNOWMAP]"]]


def _build(case: dict) -> _SystemBlocks:
    return _SystemBlocks.build(**case)


# --------------------------------------------------------------------------- #
# Differential characterization: new == old, byte for byte
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("case", _CASES)
@pytest.mark.parametrize("summaries", _SUMMARY_SETS)
def test_measure_matches_legacy_fixed_system_text(case: dict, summaries: list[str]) -> None:
    assert _build(case).measure(summaries) == _legacy_fixed_system_text(summaries, **case)


@pytest.mark.parametrize("case", _CASES)
@pytest.mark.parametrize("summaries", _SUMMARY_SETS)
@pytest.mark.parametrize("knowledge", _KNOWLEDGE_SETS)
@pytest.mark.parametrize("include_note", [True, False])
def test_render_matches_legacy_system_parts(
    case: dict, summaries: list[str], knowledge: list[str], include_note: bool
) -> None:
    conditional = [_PARTICIPANT_NOTE_BLOCK] if include_note else []
    assert _build(case).render(summaries, knowledge, include_conditional=conditional) == _legacy_render(
        summaries, knowledge, include_participant_note=include_note, **case
    )


# --------------------------------------------------------------------------- #
# The honest invariant: every block declares exactly one role, and the two
# passes honour it. "Every rendered block is measured" is NOT the invariant —
# it fails on correct code in both directions.
# --------------------------------------------------------------------------- #


def test_every_block_has_exactly_one_role_and_roles_partition_the_list() -> None:
    blocks = _build(_CASES[0]).blocks

    by_role: dict[_BlockRole, list[str]] = {role: [] for role in _BlockRole}
    for block in blocks:
        by_role[block.role].append(block.name)

    # Partition: every block counted once, nothing lost, nothing double-counted.
    assert sum(len(names) for names in by_role.values()) == len(blocks)
    assert {b.name for b in blocks} == {n for names in by_role.values() for n in names}

    # The two asymmetric roles are the whole reason this class exists; pin the
    # membership so a new block cannot quietly join the wrong side.
    assert by_role[_BlockRole.MEASURED_ONLY] == [_PARTICIPANT_NOTE_BLOCK]
    assert by_role[_BlockRole.RENDERED_ONLY] == ["knowledge"]


def test_measured_only_block_is_counted_but_omitted_when_not_included() -> None:
    # The participant note is measured every turn (conservative — its inclusion
    # depends on history that is not known yet) but rendered only on demand.
    blocks = _build(_CASES[0])

    assert _PARTICIPANT_LABEL_NOTE in blocks.measure([])
    assert _PARTICIPANT_LABEL_NOTE not in blocks.render([], [], include_conditional=[])
    assert _PARTICIPANT_LABEL_NOTE in blocks.render([], [], include_conditional=[_PARTICIPANT_NOTE_BLOCK])


class TestTheParticipantNoteSlot:
    """The note is a slot, not fixed text, so its owner sentence can be paid for
    only on the turns that show it.

    The measure pass runs before history exists, so it cannot know whether the
    note will render — and the owner label costs two DB reads. Counting a
    worst-case note there and supplying the real one at render is what lets the
    caller skip the lookup entirely on a silent wakeup, without ever handing the
    knowledge budget an under-count."""

    def test_measure_counts_the_owner_sentence_even_with_no_owner_resolved(self) -> None:
        measured = _build(_CASES[0]).measure([])

        assert "created this room and owns its settings" in measured
        # And by at least as much as any real label could cost: the placeholder is
        # the longest label the label layer can produce.
        real = _build(_CASES[0]).render(
            [],
            [],
            include_conditional=[_PARTICIPANT_NOTE_BLOCK],
            participant_note=_participant_note("A very long teacher name indeed"),
        )
        assert len(measured) >= len(real)

    def test_render_uses_the_note_the_caller_supplies(self) -> None:
        rendered = _build(_CASES[0]).render(
            [],
            [],
            include_conditional=[_PARTICIPANT_NOTE_BLOCK],
            participant_note=_participant_note("Alice Chen"),
        )

        assert '"Alice Chen"' in rendered
        assert _PARTICIPANT_NOTE_MEASURE not in rendered

    def test_an_unnamed_turn_renders_the_ownerless_note_by_default(self) -> None:
        """The default matters: a caller that opts the block in without resolving
        an owner must get the plain note, not an empty slot or a placeholder."""
        rendered = _build(_CASES[0]).render([], [], include_conditional=[_PARTICIPANT_NOTE_BLOCK])

        assert rendered.endswith(_PARTICIPANT_LABEL_NOTE)
        assert "created this room" not in rendered


def test_each_conditional_block_is_gated_by_its_own_name() -> None:
    # A single shared flag would gate every MEASURED_ONLY block on the first
    # one's reason: naming a second block must not drag the participant note in,
    # and an unnamed block stays out (measured, not rendered — its role's meaning).
    extra = _SystemBlock("some_other_note", _BlockRole.MEASURED_ONLY, text="[OTHER]")
    blocks = _SystemBlocks(blocks=(*_build(_CASES[0]).blocks, extra))

    only_other = blocks.render([], [], include_conditional=["some_other_note"])
    assert "[OTHER]" in only_other
    assert _PARTICIPANT_LABEL_NOTE not in only_other

    only_note = blocks.render([], [], include_conditional=[_PARTICIPANT_NOTE_BLOCK])
    assert "[OTHER]" not in only_note
    assert _PARTICIPANT_LABEL_NOTE in only_note

    # Both are still measured either way — that is what MEASURED_ONLY means.
    measured = blocks.measure([])
    assert "[OTHER]" in measured
    assert _PARTICIPANT_LABEL_NOTE in measured


# --------------------------------------------------------------------------- #
# §31 AC-4 — the skills index is fixed context, so the knowledge budget pays for
# it. Numerically: "it appears in the prompt somewhere" is not the claim.
# --------------------------------------------------------------------------- #

_BUDGET_CASE = {
    "base_system": "You are a helpful agent.",
    "is_observer": False,
    "memory_block": None,
    "activity_block": None,
    "staged_note": None,
    "notify_block": None,
}


def _budget(blocks: _SystemBlocks) -> int:
    return ctxmod.knowledge_budget(
        ceiling=100_000,
        response_reserve=4_096,
        fixed_context_tokens=estimate_tokens(blocks.measure([])),
        safety_margin_frac=0.1,
    )


def test_the_rendered_skills_index_is_charged_against_the_knowledge_budget() -> None:
    index = SkillsFacade.render_index(
        [make_skill(name="pdf-fill", description="Fills PDF forms from a data mapping.")]
    )
    without = _SystemBlocks.build(skills_note=None, **_BUDGET_CASE)
    with_skills = _SystemBlocks.build(skills_note=index, **_BUDGET_CASE)

    fixed_without = estimate_tokens(without.measure([]))
    fixed_with = estimate_tokens(with_skills.measure([]))

    # The charge is exactly what appending the rendered block costs — not
    # estimate_tokens(index) on its own, which is a different number: the heuristic
    # is max(1, cjk + latin // 4) and so non-additive over a concatenation.
    expected = estimate_tokens(without.measure([]) + "\n\n" + index) - fixed_without
    assert expected > 0
    assert fixed_with - fixed_without == expected

    # And it lands on the knowledge budget, shrunk by the safety margin the budget
    # applies to everything else.
    assert _budget(without) - _budget(with_skills) == pytest.approx(expected * 0.9, abs=1)


def test_an_agent_with_nothing_bound_pays_no_index_tokens() -> None:
    # render_index returns "" for an empty snapshot, and a falsy block is dropped
    # rather than rendered as a blank part — so the budget is byte-identical.
    empty = _SystemBlocks.build(skills_note=SkillsFacade.render_index([]), **_BUDGET_CASE)
    absent = _SystemBlocks.build(skills_note=None, **_BUDGET_CASE)

    assert empty.measure([]) == absent.measure([])
    assert _budget(empty) == _budget(absent)


def test_rendered_only_block_is_never_measured() -> None:
    # Knowledge blocks are what the budget buys, so measuring them against it
    # would be circular.
    blocks = _build(_CASES[0])
    knowledge = ["[RAG]\nchunk text"]

    assert "[RAG]" not in blocks.measure([])
    assert "[RAG]" in blocks.render([], knowledge, include_conditional=[])


def test_measure_raises_when_a_block_needs_a_slot_it_does_not_supply() -> None:
    # measure supplies no knowledge text because the role guard skips that block.
    # If the role ever changed, contributing nothing to the estimate would be a
    # silent under-count (F-16's failure mode) — so it must be loud instead.
    blocks = _SystemBlocks(
        blocks=(_SystemBlock("knowledge", _BlockRole.MEASURED_AND_RENDERED, slot=_BlockSlot.KNOWLEDGE),)
    )

    with pytest.raises(RuntimeError, match="knowledge"):
        blocks.measure([])


def test_summaries_are_both_measured_and_rendered_in_place() -> None:
    blocks = _build(_CASES[0])
    summaries = ["[Earlier conversation summary]\nfoo"]

    assert summaries[0] in blocks.measure(summaries)
    rendered = blocks.render(summaries, ["[RAG]"], include_conditional=[])
    # Order is declared once: summaries precede knowledge in both passes.
    assert rendered.index(summaries[0]) < rendered.index("[RAG]")
