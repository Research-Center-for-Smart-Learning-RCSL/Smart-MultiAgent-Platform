"""K.3 Pass 2 — A2A turn dispatch + approval participation.

Covers the headless turn path, the A2A handler's call/instruct/notify branches,
the cast_approval_vote tool, the pending-notification context drain, and the
pending_notify Redis store. The synchronous round trip over real streams is the
compose-backed K.7 wiring tier; here we pin the branch logic with fakes.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import contexts.agents.application.runtime.tool_registry as tr
import contexts.agents.application.runtime.turn_engine as te
import contexts.orchestration.application.a2a_handler as h
import contexts.orchestration.infrastructure.pending_notify as pn
from contexts.agents.application.context import KnowledgeBudget
from contexts.orchestration.domain.models import A2AEnvelope, A2AMessageType
from contexts.skills.application.binding_service import BoundSet
from tests.unit.skill_fakes import make_skill


def _async_return(value):
    async def _f(*_a, **_k):
        return value

    return _f


class _FakeDB:
    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None

    async def flush(self) -> None:
        return None


def _agent():
    return SimpleNamespace(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        key_group_id=uuid.uuid4(),
        system_prompt="prompt",
        model_hint=SimpleNamespace(value="claude"),
        model_id=None,
        rag_config_id=None,
        knowmap_config_id=None,
        context_mode=SimpleNamespace(value="general"),
        context_token_cap=None,
    )


class _RagCtx:
    def __init__(self, block: str) -> None:
        self.block = block


def _wire_knowledge(engine, *, rag=None, graphrag=None, knowmap=None, graphrag_calls=None, budgets=None):
    """Stub the three per-provider context methods on a bare engine instance.

    ``rag`` is the File RAG block body (wrapped in a RagContext-like object);
    ``graphrag`` / ``knowmap`` are the Concept Map / Knowledge Map block strings.
    ``graphrag_calls`` records the ``chatroom_id`` each Concept Map query runs
    against so tests can assert room-scoping. ``budgets`` records the
    ``token_budget`` each source was granted, so a test can assert the grant is
    finite rather than merely that a block arrived.
    """

    async def _rag(agent, queries, *, token_budget=None):
        if budgets is not None:
            budgets["rag"] = token_budget
        return _RagCtx(rag) if rag is not None else None

    async def _graph(agent, chatroom_id, queries, *, token_budget=None):
        if graphrag_calls is not None:
            graphrag_calls.append(chatroom_id)
        if budgets is not None:
            budgets["concept"] = token_budget
        return graphrag

    async def _km(agent, queries, *, token_budget=None):
        if budgets is not None:
            budgets["knowmap"] = token_budget
        return knowmap

    engine._rag_context = _rag  # type: ignore[attr-defined]
    engine._graphrag_context = _graph  # type: ignore[attr-defined]
    engine._knowmap_context = _km  # type: ignore[attr-defined]


def _headless_engine(monkeypatch, agent, *, member=True, drain=None):
    """Bare engine wired for a headless ``run_input_turn`` that captures the
    kwargs handed to ``_stream_with_tools`` (notably ``system_text``)."""
    _wire_engine(monkeypatch, agent, member=member, drain=drain)
    engine = te.TurnEngine.__new__(te.TurnEngine)
    engine._db = _FakeDB()  # type: ignore[attr-defined]
    engine._router = object()  # type: ignore[attr-defined]
    captured: dict = {}

    async def _fake_stream(**kw):
        captured.update(kw)
        return ("reply", 0)

    async def _noop_audit(*a, **k):
        return None

    engine._stream_with_tools = _fake_stream  # type: ignore[attr-defined]
    engine._audit = _noop_audit  # type: ignore[attr-defined]
    return engine, captured


# --------------------------------------------------------------------------- #
# run_input_turn (headless)
# --------------------------------------------------------------------------- #


def _wire_engine(monkeypatch, agent, *, drain=None, member=True, group="match", hint_serviceable=True):
    """``group`` mirrors ``test_no_response_notices.py``'s ``_wire_locked``: 'match'
    (key group OK — what every pre-existing test here means), 'mismatch' (wrong
    project), 'none' (deleted)."""

    class _Facade:
        def __init__(self, db) -> None:
            pass

        async def get_agent(self, aid):
            return agent

        async def list_agent_tools(self, aid):
            # The headless path resolves the agent's tools once and feeds them to the
            # skills tap, so the double has to answer this too. No tools is what every
            # test here means.
            return []

    monkeypatch.setattr(te, "AgentsFacade", _Facade)

    grp = None
    if group == "match" and agent is not None:
        grp = SimpleNamespace(project_id=agent.project_id)
    elif group == "mismatch":
        grp = SimpleNamespace(project_id=uuid.uuid4())

    class _KeysFacade:
        def __init__(self, db) -> None:
            pass

        async def get_key_group(self, kgid):
            return grp

        async def has_carried_provider_in_group(self, kgid, provider):
            return hint_serviceable

    monkeypatch.setattr(te, "KeysFacade", _KeysFacade)

    class _ConvFacade:
        def __init__(self, db) -> None:
            pass

        async def is_agent_in_chatroom(self, *, chatroom_id, agent_id):
            return member

    monkeypatch.setattr(te, "ConversationFacade", _ConvFacade)
    monkeypatch.setattr(
        "contexts.orchestration.infrastructure.pending_notify.drain",
        _async_return(drain if drain is not None else []),
    )
    _stub_skills(monkeypatch)
    monkeypatch.setattr(te, "build_registry", lambda *a, **k: _fake_registry())


def _fake_registry(specs=None):
    """The turn measures the serialized tool schemas against the context budget,
    so the registry double has to answer ``specs()``."""
    return SimpleNamespace(specs=lambda: list(specs or []))


def _stub_skills(monkeypatch, *, bound=None):
    """The headless path runs the §31 turn-time tap like the room path does.

    Nothing is bound by default, which is what every test here means: `render_index`
    is a staticmethod on the real facade, so the double has to carry one too.
    """
    resolved = bound if bound is not None else BoundSet(skills=())

    class _SkillsFacade:
        def __init__(self, db) -> None:
            pass

        async def resolve_bound_set(self, *, agent_id, agent_project_id, enabled_tools):
            return resolved

        @staticmethod
        def render_index(skills):
            return "\n".join(f"- {s.name}: {s.description}" for s in skills)

    monkeypatch.setattr(te, "SkillsFacade", _SkillsFacade)


@pytest.mark.asyncio
async def test_run_input_turn_headless_completed(monkeypatch) -> None:
    agent = _agent()
    _wire_engine(monkeypatch, agent)

    engine = te.TurnEngine.__new__(te.TurnEngine)
    engine._db = _FakeDB()  # type: ignore[attr-defined]
    engine._router = object()  # type: ignore[attr-defined]

    captured: dict = {}

    async def _fake_stream(**kw):
        captured.update(kw)
        return ("hello from agent", 0)

    async def _noop_audit(*a, **k):
        return None

    engine._stream_with_tools = _fake_stream  # type: ignore[attr-defined]
    engine._audit = _noop_audit  # type: ignore[attr-defined]
    _wire_knowledge(engine)  # no bound sources → no knowledge blocks

    result = await engine.run_input_turn(agent_id=agent.id, input_text="hi")

    assert result.status == "completed"
    assert result.text == "hello from agent"
    # Headless: no room, no chatroom — no WS stream, no persistence.
    assert captured["room"] is None
    assert captured["chatroom_id"] is None
    assert captured["messages"] == [{"role": "user", "content": "hi"}]


@pytest.mark.asyncio
async def test_run_input_turn_indexes_the_agents_bound_skills(monkeypatch) -> None:
    # §31: the headless path is the cross-agent one — an A2A turn is triggered by
    # another agent — so it gets the same tap and the same index the room path does.
    agent = _agent()
    skill = make_skill(name="pdf-fill", description="Fills PDF forms.")
    engine, captured = _headless_engine(monkeypatch, agent)
    _stub_skills(monkeypatch, bound=BoundSet(skills=(skill,)))
    _wire_knowledge(engine)

    result = await engine.run_input_turn(agent_id=agent.id, input_text="hi")

    assert result.status == "completed"
    assert "- pdf-fill: Fills PDF forms." in captured["system_text"]


@pytest.mark.asyncio
async def test_run_input_turn_passes_the_snapshot_to_the_registry(monkeypatch) -> None:
    # The tool must be built from the snapshot the tap just validated. Re-querying by
    # name at call time would make the tap decorative.
    agent = _agent()
    skill = make_skill(name="pdf-fill")
    engine, _captured = _headless_engine(monkeypatch, agent)
    _stub_skills(monkeypatch, bound=BoundSet(skills=(skill,)))
    _wire_knowledge(engine)
    built: dict = {}
    monkeypatch.setattr(te, "build_registry", lambda *a, **k: built.update(k) or _fake_registry())

    await engine.run_input_turn(agent_id=agent.id, input_text="hi")

    # The whole snapshot, not just its skills — see the sibling assertion in
    # test_observer_agents.py for why the three views must travel together.
    assert built["skills"].skills == (skill,)


# --------------------------------------------------------------------------- #
# run_input_turn knowledge assembly (F-15)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_run_input_turn_assembles_knowledge_map(monkeypatch) -> None:
    # [R11.14] a headless invocation queries the attached Knowledge Map.
    agent = _agent()
    agent.knowmap_config_id = uuid.uuid4()
    engine, captured = _headless_engine(monkeypatch, agent)
    graphrag_calls: list = []
    _wire_knowledge(engine, knowmap="KNOWMAP_BLOCK", graphrag_calls=graphrag_calls)

    result = await engine.run_input_turn(agent_id=agent.id, input_text="hi")

    assert result.status == "completed"
    assert "KNOWMAP_BLOCK" in captured["system_text"]
    # No room supplied → Concept Maps are never resolved.
    assert graphrag_calls == []


@pytest.mark.asyncio
async def test_run_input_turn_assembles_file_rag(monkeypatch) -> None:
    # [R10.09] a headless invocation queries the agent's File RAG corpus.
    agent = _agent()
    agent.rag_config_id = uuid.uuid4()
    engine, captured = _headless_engine(monkeypatch, agent)
    _wire_knowledge(engine, rag="RAG_BLOCK")

    await engine.run_input_turn(agent_id=agent.id, input_text="hi")

    assert "RAG_BLOCK" in captured["system_text"]


@pytest.mark.asyncio
async def test_run_input_turn_without_room_skips_concept_maps(monkeypatch) -> None:
    agent = _agent()
    engine, captured = _headless_engine(monkeypatch, agent)
    graphrag_calls: list = []
    _wire_knowledge(engine, graphrag="CONCEPT_BLOCK", graphrag_calls=graphrag_calls)

    await engine.run_input_turn(agent_id=agent.id, input_text="hi")

    # Room-scoped Concept Maps must not resolve without a chatroom.
    assert graphrag_calls == []
    assert "CONCEPT_BLOCK" not in captured["system_text"]


@pytest.mark.asyncio
async def test_run_input_turn_with_room_includes_concept_maps(monkeypatch) -> None:
    agent = _agent()
    room = uuid.uuid4()
    engine, captured = _headless_engine(monkeypatch, agent)
    graphrag_calls: list = []
    _wire_knowledge(engine, graphrag="CONCEPT_BLOCK", graphrag_calls=graphrag_calls)

    await engine.run_input_turn(agent_id=agent.id, input_text="hi", chatroom_id=room)

    # Concept Maps resolve against exactly the supplied room.
    assert graphrag_calls == [room]
    assert "CONCEPT_BLOCK" in captured["system_text"]


@pytest.mark.asyncio
async def test_run_input_turn_non_member_room_skips_concept_maps(monkeypatch) -> None:
    # F-15 AC-7 trust boundary: a headless turn threaded into a room the agent is
    # NOT a member of must not resolve that room's Concept Map — the gate's
    # chatroom_id can be an arbitrary in-project room chosen by the workflow
    # author, so room membership is verified server-side, not trusted.
    agent = _agent()
    room = uuid.uuid4()
    engine, captured = _headless_engine(monkeypatch, agent, member=False)
    graphrag_calls: list = []
    _wire_knowledge(
        engine,
        rag="RAG_BLOCK",
        graphrag="CONCEPT_BLOCK",
        knowmap="KNOWMAP_BLOCK",
        graphrag_calls=graphrag_calls,
    )
    agent.rag_config_id = uuid.uuid4()
    agent.knowmap_config_id = uuid.uuid4()

    await engine.run_input_turn(agent_id=agent.id, input_text="hi", chatroom_id=room)

    # Room-scoped Concept Map is withheld from the non-member approver...
    assert graphrag_calls == []
    assert "CONCEPT_BLOCK" not in captured["system_text"]
    # ...but the agent's own File RAG and Knowledge Map bindings still assemble.
    assert "RAG_BLOCK" in captured["system_text"]
    assert "KNOWMAP_BLOCK" in captured["system_text"]


@pytest.mark.asyncio
async def test_assemble_agent_knowledge_order_and_empty_handling(monkeypatch) -> None:
    # Characterization of the room-path block sequence now living in the shared
    # helper: File RAG, then Concept Map (room only), then Knowledge Map; empty
    # blocks dropped. The budget is generous here so this pins *ordering* alone —
    # allocation arithmetic is pinned in test_turn_context_budget.py.
    engine = te.TurnEngine.__new__(te.TurnEngine)
    graphrag_calls: list = []
    _wire_knowledge(engine, rag="RAG", graphrag="CONCEPT", knowmap="KNOWMAP", graphrag_calls=graphrag_calls)
    room = uuid.uuid4()
    budget = KnowledgeBudget(total=100_000, graph_source_cap=700)

    blocks, rag_ctx = await engine._assemble_agent_knowledge(_agent(), ["q"], chatroom_id=room, budget=budget)

    assert blocks == ["RAG", "CONCEPT", "KNOWMAP"]
    assert graphrag_calls == [room]
    # The RagContext is surfaced so the room path can persist RAG citations.
    assert rag_ctx is not None
    assert rag_ctx.block == "RAG"

    # No room drops the Concept Map; a None File RAG block is omitted, not empty.
    engine2 = te.TurnEngine.__new__(te.TurnEngine)
    _wire_knowledge(engine2, rag=None, graphrag="CONCEPT", knowmap="KNOWMAP")
    blocks2, rag_ctx2 = await engine2._assemble_agent_knowledge(
        _agent(), ["q"], chatroom_id=None, budget=budget
    )
    assert blocks2 == ["KNOWMAP"]
    assert rag_ctx2 is None


# --------------------------------------------------------------------------- #
# Headless knowledge token budget (R9.10 / R11.19)
# --------------------------------------------------------------------------- #


def _spy_requeue(engine) -> list:
    """Capture what the turn hands back to the notification queue."""
    requeued: list = []

    async def _requeue(agent, notes):
        requeued.append(notes)

    engine._requeue_notifications = _requeue  # type: ignore[attr-defined]
    return requeued


@pytest.mark.asyncio
async def test_run_input_turn_grants_every_source_a_finite_budget(monkeypatch) -> None:
    # The defect: headless assembly ran uncapped, so a large payload reached the
    # provider regardless of the context limit. Every source must now be granted
    # a finite budget, in Concept Map > Knowledge Map > File RAG precedence.
    agent = _agent()
    agent.rag_config_id = uuid.uuid4()
    agent.knowmap_config_id = uuid.uuid4()
    room = uuid.uuid4()
    engine, captured = _headless_engine(monkeypatch, agent)
    budgets: dict = {}
    _wire_knowledge(engine, rag="RAG", graphrag="CONCEPT", knowmap="KNOWMAP", budgets=budgets)

    result = await engine.run_input_turn(agent_id=agent.id, input_text="hi", chatroom_id=room)

    assert result.status == "completed"
    # The graph sources draw first, each capped; File RAG takes the measured
    # remainder. None of the three may be handed an unbounded grant.
    assert budgets["concept"] == te._GRAPH_BLOCK_TOKEN_BUDGET
    assert budgets["knowmap"] == te._GRAPH_BLOCK_TOKEN_BUDGET
    assert budgets["rag"] is not None
    assert 0 < budgets["rag"] < te._CONTEXT_LIMITS["claude"]
    assert "CONCEPT" in captured["system_text"]


@pytest.mark.asyncio
async def test_run_input_turn_compact_mode_budgets_against_the_cap(monkeypatch) -> None:
    # Compact mode bounds the request at the agent's own cap, not the provider
    # limit — the same ceiling rule the room path applies.
    agent = _agent()
    agent.rag_config_id = uuid.uuid4()
    agent.context_mode = SimpleNamespace(value="compact")
    agent.context_token_cap = 20_000
    engine, _captured = _headless_engine(monkeypatch, agent)
    budgets: dict = {}
    _wire_knowledge(engine, rag="RAG", budgets=budgets)

    result = await engine.run_input_turn(agent_id=agent.id, input_text="hi")

    assert result.status == "completed"
    # Bounded by the cap minus the response reserve and fixed context, then the
    # safety margin — well under the provider's 128k limit.
    assert 0 < budgets["rag"] <= 20_000


@pytest.mark.asyncio
async def test_run_input_turn_knowledge_starved_skips_before_the_provider(monkeypatch) -> None:
    # A cap too small to leave any knowledge room drops every configured source.
    # Answering from nothing while the config says otherwise reads as
    # confabulation, so the turn fails loudly instead — and the notifications it
    # drained but never acted on go back on the queue.
    agent = _agent()
    agent.rag_config_id = uuid.uuid4()
    agent.context_mode = SimpleNamespace(value="compact")
    agent.context_token_cap = 100
    engine, captured = _headless_engine(monkeypatch, agent, drain=[{"id": "n1"}])
    requeued = _spy_requeue(engine)
    audits: list = []

    async def _audit(agent_, room_, action, extra):
        audits.append((action, extra))

    engine._audit = _audit  # type: ignore[attr-defined]
    _wire_knowledge(engine, rag="RAG")

    result = await engine.run_input_turn(agent_id=agent.id, input_text="hi")

    assert result.status == "skipped"
    assert result.reason == "knowledge_starved"
    assert captured == {}  # the provider was never called
    assert requeued == [[{"id": "n1"}]]
    skip = next(extra for action, extra in audits if extra.get("reason") == "knowledge_starved")
    assert skip["context_token_cap"] == 100
    assert skip["fixed_context_tokens"] > 0
    assert skip["ceiling_tokens"] == 100


@pytest.mark.asyncio
async def test_run_input_turn_without_a_source_runs_under_any_cap(monkeypatch) -> None:
    # Nothing to drop means nothing to complain about: an agent with no bound
    # knowledge source must keep working however tight the cap.
    agent = _agent()
    agent.context_mode = SimpleNamespace(value="compact")
    agent.context_token_cap = 100
    engine, captured = _headless_engine(monkeypatch, agent)
    _wire_knowledge(engine)

    result = await engine.run_input_turn(agent_id=agent.id, input_text="hi")

    assert result.status == "completed"
    assert captured["system_text"] == "prompt"


@pytest.mark.asyncio
async def test_run_input_turn_oversized_payload_never_reaches_the_provider(monkeypatch) -> None:
    # Fixed context alone can exceed the provider limit — an unconstrained A2A
    # input is the obvious vector. Headless has no history to shed and no room UI
    # to surface a provider context error, so it skips deterministically rather
    # than issuing a request that is guaranteed to be rejected.
    monkeypatch.setitem(te._CONTEXT_LIMITS, "claude", 5_000)
    agent = _agent()
    engine, captured = _headless_engine(monkeypatch, agent, drain=[{"id": "n1"}])
    requeued = _spy_requeue(engine)
    audits: list = []

    async def _audit(agent_, room_, action, extra):
        audits.append((action, extra))

    engine._audit = _audit  # type: ignore[attr-defined]
    _wire_knowledge(engine)

    # ~2500 tokens of input on top of the 4096-token response reserve.
    result = await engine.run_input_turn(agent_id=agent.id, input_text="x" * 10_000)

    assert result.status == "skipped"
    assert result.reason == "context_overflow"
    assert captured == {}
    assert requeued == [[{"id": "n1"}]]
    overflow = next(extra for action, extra in audits if extra.get("reason") == "context_overflow")
    assert overflow["bound"] == "provider"
    assert overflow["payload_tokens"] > overflow["context_limit_tokens"] == 5_000
    # The audit must carry the arithmetic, never the prompt that produced it.
    assert not any("x" * 100 in str(v) for v in overflow.values())


@pytest.mark.asyncio
async def test_run_input_turn_oversized_input_is_overflow_not_starvation(monkeypatch) -> None:
    # A fixed context too large for the provider floors the knowledge budget just
    # as a tight cap does — but no cap or knowledge change can rescue it. Reporting
    # it as knowledge_starved would send the operator after the wrong lever, so the
    # provider bound is judged first, before retrieval that would be discarded.
    monkeypatch.setitem(te._CONTEXT_LIMITS, "claude", 5_000)
    agent = _agent()
    agent.rag_config_id = uuid.uuid4()  # a bound source: would otherwise starve
    engine, captured = _headless_engine(monkeypatch, agent)
    audits: list = []

    async def _audit(agent_, room_, action, extra):
        audits.append((action, extra))

    engine._audit = _audit  # type: ignore[attr-defined]
    budgets: dict = {}
    _wire_knowledge(engine, rag="RAG", budgets=budgets)

    result = await engine.run_input_turn(agent_id=agent.id, input_text="x" * 10_000)

    assert result.reason == "context_overflow"
    assert captured == {}
    # Retrieval is never paid for a request that cannot be sent.
    assert budgets == {}
    assert next(e for _a, e in audits if e.get("reason") == "context_overflow")["bound"] == "provider"


@pytest.mark.asyncio
async def test_run_input_turn_dispatches_above_a_cap_the_prompt_alone_overruns(monkeypatch) -> None:
    # [R9.10] makes context_token_cap the point at which compaction runs, not a
    # bound on the request. This path has no history to compact, so a prompt larger
    # than the cap must still dispatch while it fits the provider — bounding the
    # payload by the cap here would skip every turn for such an agent.
    agent = _agent()
    agent.context_mode = SimpleNamespace(value="compact")
    agent.context_token_cap = 6_000
    engine, captured = _headless_engine(monkeypatch, agent)
    _wire_knowledge(engine)  # no bound source, so nothing to starve

    # ~3000 tokens of input plus the 4096 reserve overruns the 6k cap while sitting
    # far inside claude's 200k window.
    result = await engine.run_input_turn(agent_id=agent.id, input_text="x" * 12_000)

    assert result.status == "completed"
    assert captured["system_text"] == "prompt"


@pytest.mark.asyncio
async def test_run_input_turn_skip_audits_carry_the_gates_room(monkeypatch) -> None:
    # The approval worker threads the gate's room in. A skip that drops it leaves an
    # operator unable to correlate a timed-out gate with the turn that refused to run.
    agent = _agent()
    agent.rag_config_id = uuid.uuid4()
    agent.context_mode = SimpleNamespace(value="compact")
    agent.context_token_cap = 100
    room = uuid.uuid4()
    engine, _captured = _headless_engine(monkeypatch, agent)
    rooms: list = []

    async def _audit(agent_, room_, action, extra):
        rooms.append((extra.get("reason"), room_))

    engine._audit = _audit  # type: ignore[attr-defined]
    _wire_knowledge(engine, rag="RAG")

    await engine.run_input_turn(agent_id=agent.id, input_text="hi", chatroom_id=room)

    assert (("knowledge_starved", room)) in rooms


@pytest.mark.asyncio
async def test_run_input_turn_agent_gone(monkeypatch) -> None:
    _wire_engine(monkeypatch, None)
    engine = te.TurnEngine.__new__(te.TurnEngine)
    engine._db = _FakeDB()  # type: ignore[attr-defined]
    engine._router = object()  # type: ignore[attr-defined]
    result = await engine.run_input_turn(agent_id=uuid.uuid4(), input_text="hi")
    assert result.status == "skipped"
    assert result.reason == "agent_gone"


def _wire_key_group_scope_skip(monkeypatch, agent, *, group):
    """A bare engine wired so the key-group-scope guard is the only thing that can
    fire: reaching the provider or emitting a room event is a defect, not a path
    this test tolerates."""
    _wire_engine(monkeypatch, agent, group=group)

    async def _unreached_stream(**_kw):
        raise AssertionError("must not reach the provider when the key group is out of scope")

    async def _unreached_emit(*_a, **_k):
        raise AssertionError("must not emit — there is no room on the headless path")

    monkeypatch.setattr(te, "emit_agent_finished_error", _unreached_emit)

    engine = te.TurnEngine.__new__(te.TurnEngine)
    engine._db = _FakeDB()  # type: ignore[attr-defined]
    engine._router = object()  # type: ignore[attr-defined]
    engine._stream_with_tools = _unreached_stream  # type: ignore[attr-defined]
    audit_mock = AsyncMock()
    engine._audit = audit_mock  # type: ignore[attr-defined]
    return engine, audit_mock


@pytest.mark.asyncio
async def test_run_input_turn_key_group_deleted_skips(monkeypatch) -> None:
    # R7.09a / AC-2: `_run_locked` has this gate for the room path; the headless
    # path lacked it entirely and would run the turn (and bill the key) for an
    # agent whose Key Group was soft-deleted.
    agent = _agent()
    engine, audit_mock = _wire_key_group_scope_skip(monkeypatch, agent, group="none")

    result = await engine.run_input_turn(agent_id=agent.id, input_text="hi")

    assert result.status == "skipped"
    assert result.reason == "key_group_scope"
    audit_mock.assert_awaited_once_with(
        agent,
        None,
        "agent.turn_skipped",
        {"reason": "key_group_scope", "key_group_id": str(agent.key_group_id)},
    )


@pytest.mark.asyncio
async def test_run_input_turn_key_group_cross_project_skips(monkeypatch) -> None:
    # Q-4 defence-in-depth arm: unreachable via any API today, but `_run_locked`
    # keeps the same check, so the shared predicate must too.
    agent = _agent()
    engine, audit_mock = _wire_key_group_scope_skip(monkeypatch, agent, group="mismatch")

    result = await engine.run_input_turn(agent_id=agent.id, input_text="hi")

    assert result.status == "skipped"
    assert result.reason == "key_group_scope"
    audit_mock.assert_awaited_once_with(
        agent,
        None,
        "agent.turn_skipped",
        {"reason": "key_group_scope", "key_group_id": str(agent.key_group_id)},
    )


# --------------------------------------------------------------------------- #
# model-hint routing
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_run_input_turn_skips_when_model_hint_is_unserviceable(monkeypatch) -> None:
    agent = _agent()
    engine, captured = _headless_engine(monkeypatch, agent)
    _wire_engine(monkeypatch, agent, hint_serviceable=False)

    result = await engine.run_input_turn(agent_id=agent.id, input_text="hi")

    assert result.status == "skipped"
    assert result.reason == "model_hint_unserviceable"
    assert captured == {}


@pytest.mark.asyncio
async def test_run_input_turn_unserviceable_hint_audits_the_gate_room(monkeypatch) -> None:
    agent = _agent()
    room = uuid.uuid4()
    engine, captured = _headless_engine(monkeypatch, agent)
    _wire_engine(monkeypatch, agent, hint_serviceable=False)
    audit_mock = AsyncMock()
    engine._audit = audit_mock  # type: ignore[attr-defined]

    result = await engine.run_input_turn(agent_id=agent.id, input_text="hi", chatroom_id=room)

    assert result.reason == "model_hint_unserviceable"
    assert captured == {}
    audit_mock.assert_awaited_once_with(
        agent,
        room,
        "agent.turn_skipped",
        {
            "reason": "model_hint_unserviceable",
            "model_hint": agent.model_hint.value,
            "key_group_id": str(agent.key_group_id),
        },
    )


# --------------------------------------------------------------------------- #
# _pending_context_and_tools
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_pending_context_adds_approval_tool(monkeypatch) -> None:
    approval_id = uuid.uuid4()
    room_id = uuid.uuid4()
    notes = [
        {
            "kind": "approval_request",
            "approval_id": str(approval_id),
            "mode": "majority",
            "chatroom_id": str(room_id),
        },
        {"kind": "notify", "from_agent": "x", "payload": {"a": 1}},
    ]
    monkeypatch.setattr("contexts.orchestration.infrastructure.pending_notify.drain", _async_return(notes))
    sentinel = SimpleNamespace(name="cast_approval_vote")
    seen: dict = {}

    def _build(db, *, agent_id, allowed_approvals):
        seen["allowed"] = dict(allowed_approvals)
        return sentinel

    monkeypatch.setattr(te, "build_cast_approval_vote_tool", _build)

    engine = te.TurnEngine.__new__(te.TurnEngine)
    engine._db = object()  # type: ignore[attr-defined]
    engine._requeue_notifications = _async_return(None)  # type: ignore[attr-defined]
    block, tools, _notes = await engine._pending_context_and_tools(_agent(), room_id)

    assert block is not None
    assert str(approval_id) in block
    assert tools == [sentinel]
    # Tool scoped to exactly the pending gate, carrying its originating room.
    assert seen["allowed"] == {approval_id: room_id}


@pytest.mark.asyncio
async def test_pending_context_empty(monkeypatch) -> None:
    monkeypatch.setattr("contexts.orchestration.infrastructure.pending_notify.drain", _async_return([]))
    engine = te.TurnEngine.__new__(te.TurnEngine)
    engine._db = object()  # type: ignore[attr-defined]
    block, tools, _notes = await engine._pending_context_and_tools(_agent(), uuid.uuid4())
    assert block is None
    assert tools == []


# --------------------------------------------------------------------------- #
# cast_approval_vote tool
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_cast_approval_vote_records(monkeypatch) -> None:
    approval_id, agent_id = uuid.uuid4(), uuid.uuid4()
    captured: dict = {}

    room_id = uuid.uuid4()

    class _Facade:
        def __init__(self, db) -> None:
            pass

        async def cast_approval_vote(self, *, approval_id, voter_agent_id, vote, rationale, chatroom_id):
            captured.update(
                approval_id=approval_id,
                voter=voter_agent_id,
                vote=vote,
                rationale=rationale,
                chatroom_id=chatroom_id,
            )
            return SimpleNamespace(vote=vote)

    monkeypatch.setattr("contexts.orchestration.interfaces.facade.OrchestrationFacade", _Facade)
    tool = tr.build_cast_approval_vote_tool(
        object(), agent_id=agent_id, allowed_approvals={approval_id: room_id}
    )

    ok = await tool.invoke({"approval_id": str(approval_id), "vote": True, "rationale": "lgtm"})
    assert not ok.is_error
    assert captured["vote"] is True
    assert captured["voter"] == agent_id
    assert captured["rationale"] == "lgtm"
    # The gate's originating chatroom is threaded through to resolution.
    assert captured["chatroom_id"] == room_id


@pytest.mark.asyncio
async def test_cast_approval_vote_rejects_unscoped_and_bad_id(monkeypatch) -> None:
    class _Facade:
        def __init__(self, db) -> None:
            raise AssertionError("must not reach the service for an invalid gate")

    monkeypatch.setattr("contexts.orchestration.interfaces.facade.OrchestrationFacade", _Facade)
    tool = tr.build_cast_approval_vote_tool(
        object(), agent_id=uuid.uuid4(), allowed_approvals={uuid.uuid4(): None}
    )
    not_allowed = await tool.invoke({"approval_id": str(uuid.uuid4()), "vote": True})
    assert not_allowed.is_error
    bad = await tool.invoke({"approval_id": "not-a-uuid", "vote": True})
    assert bad.is_error


# --------------------------------------------------------------------------- #
# a2a_handler dispatch
# --------------------------------------------------------------------------- #


def _env(type_, payload, to_agent=None):
    return A2AEnvelope(
        id=uuid.uuid4(),
        from_agent=uuid.uuid4(),
        to_agent=to_agent or str(uuid.uuid4()),
        workflow_run_id=None,
        type=type_,
        payload=payload,
        correlation_id=uuid.uuid4(),
        created_at=datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_handle_call_delivers_reply(monkeypatch) -> None:
    delivered: dict = {}

    async def _deliver(cid, env):
        delivered["cid"], delivered["env"] = cid, env

    monkeypatch.setattr(h.a2a_rendezvous, "deliver_reply", _deliver)
    monkeypatch.setattr(
        h, "_run_turn", _async_return(SimpleNamespace(status="completed", text="ANSWER", reason=None))
    )

    env = _env(A2AMessageType.CALL, {"input": "do x"})
    await h.handle_envelope(env)

    assert delivered["cid"] == env.correlation_id
    assert delivered["env"]["reply"] == "ANSWER"  # top-level for agent_invocation
    assert delivered["env"]["payload"]["output"] == "ANSWER"
    assert delivered["env"]["to_agent"] == str(env.from_agent)


@pytest.mark.asyncio
async def test_handle_call_failed_delivers_error(monkeypatch) -> None:
    delivered: dict = {}

    async def _deliver(cid, env):
        delivered["env"] = env

    monkeypatch.setattr(h.a2a_rendezvous, "deliver_reply", _deliver)
    monkeypatch.setattr(
        h, "_run_turn", _async_return(SimpleNamespace(status="failed", text="", reason="boom"))
    )

    await h.handle_envelope(_env(A2AMessageType.CALL, {"input": "x"}))
    assert h.a2a_rendezvous.A2A_ERROR_KEY in delivered["env"]["payload"]


@pytest.mark.asyncio
async def test_handle_instruct_marks_states(monkeypatch) -> None:
    calls: list = []

    class _Facade:
        def __init__(self, db) -> None:
            pass

        async def mark_instruct_delivered(self, iid):
            calls.append(("delivered", iid))

        async def mark_instruct_completed(self, iid):
            calls.append(("completed", iid))

        async def mark_instruct_timeout(self, iid):
            calls.append(("timeout", iid))

    monkeypatch.setattr("contexts.orchestration.interfaces.facade.OrchestrationFacade", _Facade)

    @asynccontextmanager
    async def _sess():
        yield _FakeDB()

    monkeypatch.setattr(h, "async_session", _sess)
    monkeypatch.setattr(
        h, "_run_turn_with_db", _async_return(SimpleNamespace(status="completed", text="x", reason=None))
    )

    iid = uuid.uuid4()
    await h.handle_envelope(_env(A2AMessageType.INSTRUCT, {"instruction_id": str(iid), "input": "go"}))

    assert ("delivered", iid) in calls
    assert ("completed", iid) in calls
    assert ("timeout", iid) not in calls


@pytest.mark.asyncio
async def test_handle_instruct_failed_turn_marks_failed(monkeypatch) -> None:
    calls: list = []

    class _Facade:
        def __init__(self, db) -> None:
            pass

        async def mark_instruct_delivered(self, iid):
            calls.append(("delivered", iid))

        async def mark_instruct_completed(self, iid):
            calls.append(("completed", iid))

        async def mark_instruct_timeout(self, iid):
            calls.append(("timeout", iid))

    class _Instruct:
        def __init__(self, db) -> None:
            pass

        async def mark_failed(self, iid):
            calls.append(("failed", iid))

    monkeypatch.setattr("contexts.orchestration.interfaces.facade.OrchestrationFacade", _Facade)
    monkeypatch.setattr("contexts.orchestration.application.instruct_service.InstructService", _Instruct)

    @asynccontextmanager
    async def _sess():
        yield _FakeDB()

    monkeypatch.setattr(h, "async_session", _sess)
    monkeypatch.setattr(
        h, "_run_turn_with_db", _async_return(SimpleNamespace(status="failed", text="", reason="x"))
    )

    iid = uuid.uuid4()
    await h.handle_envelope(_env(A2AMessageType.INSTRUCT, {"instruction_id": str(iid), "input": "go"}))
    # A provider/turn failure is recorded as a failure, not misfiled as a
    # deadline timeout.
    assert ("failed", iid) in calls
    assert ("completed", iid) not in calls
    assert ("timeout", iid) not in calls


@pytest.mark.asyncio
async def test_handle_instruct_tolerates_rejected_completion(monkeypatch) -> None:
    """T-11: Q-2 behaviour — a rejected completion (the deadline already committed
    TIMEOUT) must not raise, and the post-commit resume enqueue must still run so
    the resume task reads whatever state actually won."""
    calls: list = []

    class _Facade:
        def __init__(self, db) -> None:
            pass

        async def mark_instruct_delivered(self, iid):
            calls.append(("delivered", iid))
            return True

        async def mark_instruct_completed(self, iid):
            calls.append(("completed", iid))
            return False  # rejected — the deadline already won (Q-2)

        async def mark_instruct_timeout(self, iid):
            calls.append(("timeout", iid))
            return True

    monkeypatch.setattr("contexts.orchestration.interfaces.facade.OrchestrationFacade", _Facade)

    @asynccontextmanager
    async def _sess():
        yield _FakeDB()

    monkeypatch.setattr(h, "async_session", _sess)
    monkeypatch.setattr(
        h, "_run_turn_with_db", _async_return(SimpleNamespace(status="completed", text="x", reason=None))
    )
    enqueue_mock = AsyncMock()
    monkeypatch.setattr("shared_kernel.queue.enqueue", enqueue_mock)

    iid = uuid.uuid4()
    await h.handle_envelope(_env(A2AMessageType.INSTRUCT, {"instruction_id": str(iid), "input": "go"}))

    assert ("completed", iid) in calls
    # _dispatch_a2a_workflow_signal (handle_envelope's first step) also calls
    # enqueue for unrelated a2a triggers, so assert the instruct resume call
    # specifically rather than the mock's total call count.
    enqueue_mock.assert_any_await("workflow_resume_instruct", str(iid))


@pytest.mark.asyncio
async def test_run_turn_with_db_passes_parent_agent_id(monkeypatch) -> None:
    captured: dict = {}

    class _Engine:
        def __init__(self, db, *, qdrant_url, qdrant_api_key, bge_reranker_url=None) -> None:
            pass

        async def run_input_turn(self, **kw):
            captured.update(kw)
            return SimpleNamespace(status="completed", text="ok", reason=None)

    monkeypatch.setattr("contexts.agents.application.runtime.turn_engine.TurnEngine", _Engine)
    monkeypatch.setattr(
        "app.config.settings.get_settings",
        lambda: SimpleNamespace(
            qdrant=SimpleNamespace(url="http://q", api_key=None),
            knowledge=SimpleNamespace(bge_reranker_url="http://bge:80"),
        ),
    )

    env = _env(A2AMessageType.CALL, {"input": "x"})
    await h._run_turn_with_db(_FakeDB(), uuid.UUID(env.to_agent), env)

    # Usage attribution: the calling agent rides through as parent_agent_id.
    assert captured["parent_agent_id"] == uuid.UUID(str(env.from_agent))
    # A2A envelopes carry no room — Concept Maps never apply, so no room is passed.
    assert "chatroom_id" not in captured


@pytest.mark.asyncio
async def test_run_turn_with_db_tolerates_non_uuid_sender(monkeypatch) -> None:
    captured: dict = {}

    class _Engine:
        def __init__(self, db, *, qdrant_url, qdrant_api_key, bge_reranker_url=None) -> None:
            pass

        async def run_input_turn(self, **kw):
            captured.update(kw)
            return SimpleNamespace(status="completed", text="ok", reason=None)

    monkeypatch.setattr("contexts.agents.application.runtime.turn_engine.TurnEngine", _Engine)
    monkeypatch.setattr(
        "app.config.settings.get_settings",
        lambda: SimpleNamespace(
            qdrant=SimpleNamespace(url="http://q", api_key=None),
            knowledge=SimpleNamespace(bge_reranker_url="http://bge:80"),
        ),
    )

    env = A2AEnvelope(
        id=uuid.uuid4(),
        from_agent="system",  # not a UUID — must not break the turn
        to_agent=str(uuid.uuid4()),
        workflow_run_id=None,
        type=A2AMessageType.CALL,
        payload={"input": "x"},
        correlation_id=uuid.uuid4(),
        created_at=datetime.now(UTC),
    )
    await h._run_turn_with_db(_FakeDB(), uuid.UUID(env.to_agent), env)
    assert captured["parent_agent_id"] is None


@pytest.mark.asyncio
async def test_handle_notify_parks_notification(monkeypatch) -> None:
    pushed: list = []

    async def _push(agent_id, payload):
        pushed.append((agent_id, payload))

    monkeypatch.setattr(h.pending_notify, "push", _push)
    to = uuid.uuid4()
    await h.handle_envelope(_env(A2AMessageType.NOTIFY, {"hello": "world"}, to_agent=str(to)))

    assert pushed[0][0] == to
    assert pushed[0][1]["kind"] == "notify"
    assert pushed[0][1]["payload"] == {"hello": "world"}


# --------------------------------------------------------------------------- #
# pending_notify store
# --------------------------------------------------------------------------- #


class _FakePipe:
    def __init__(self, store) -> None:
        self._store = store
        self._ops: list = []

    def rpush(self, k, v):
        self._ops.append(("rpush", k, v))

    def lpush(self, k, v):
        self._ops.append(("lpush", k, v))

    def ltrim(self, k, a, b):
        self._ops.append(("ltrim", k, a, b))

    def expire(self, k, t):
        self._ops.append(("expire", k, t))

    def lrange(self, k, a, b):
        self._ops.append(("lrange", k, a, b))

    def delete(self, k):
        self._ops.append(("delete", k))

    async def execute(self):
        results: list = []
        for op in self._ops:
            kind = op[0]
            if kind == "rpush":
                self._store.setdefault(op[1], []).append(op[2])
                results.append(len(self._store[op[1]]))
            elif kind == "lpush":
                self._store.setdefault(op[1], []).insert(0, op[2])
                results.append(len(self._store[op[1]]))
            elif kind == "ltrim":
                lst = self._store.get(op[1], [])
                n = len(lst)
                start = op[2] if op[2] >= 0 else max(0, n + op[2])
                stop = op[3] if op[3] >= 0 else n + op[3]
                self._store[op[1]] = lst[start : stop + 1]
                results.append("OK")
            elif kind == "lrange":
                results.append(list(self._store.get(op[1], [])))
            elif kind == "delete":
                self._store.pop(op[1], None)
                results.append(1)
            else:  # expire
                results.append(1)
        return results


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict = {}

    def pipeline(self, transaction=False):
        return _FakePipe(self.store)


@pytest.mark.asyncio
async def test_pending_notify_roundtrip(monkeypatch) -> None:
    fake = _FakeRedis()
    monkeypatch.setattr(pn, "get_redis", lambda: fake)
    aid = uuid.uuid4()

    await pn.push(aid, {"kind": "notify", "x": 1})
    await pn.push(aid, {"kind": "approval_request", "approval_id": "a"})

    out = await pn.drain(aid)
    assert [n["kind"] for n in out] == ["notify", "approval_request"]
    # Drained queue is empty on the next read.
    assert await pn.drain(aid) == []


@pytest.mark.asyncio
async def test_requeue_over_cap_drops_oldest_not_newest(monkeypatch) -> None:
    # F-19: when a failed turn requeues a drained batch on top of notifications
    # that arrived while it ran, the cap must drop the OLDEST, keeping every
    # freshly-arrived note (a new approval ballot sits among the newest).
    fake = _FakeRedis()
    monkeypatch.setattr(pn, "get_redis", lambda: fake)
    aid = uuid.uuid4()

    for i in range(1, 46):  # 45 restored notes, well under the cap on their own
        await pn.push(aid, {"n": f"old{i}"})
    restored = await pn.drain(aid)
    assert len(restored) == 45

    for i in range(1, 11):  # 10 concurrent arrivals while the turn ran
        await pn.push(aid, {"n": f"n{i}"})

    await pn.requeue(aid, restored)

    survivors = await pn.drain(aid)
    assert len(survivors) == pn._MAX_PENDING  # 55 pushed, capped at 50
    names = [note["n"] for note in survivors]
    # The 10 newest all survive and sit at the tail, in arrival order.
    assert names[-10:] == [f"n{i}" for i in range(1, 11)]
    # The 5 dropped are the oldest restored notes, not the newest arrivals.
    assert "old1" not in names
    assert "old5" not in names
    assert names[0] == "old6"


@pytest.mark.asyncio
async def test_requeue_under_cap_is_lossless(monkeypatch) -> None:
    # Below the cap the trim is a no-op: every note survives, restored batch
    # first (oldest) then concurrent arrivals (newest).
    fake = _FakeRedis()
    monkeypatch.setattr(pn, "get_redis", lambda: fake)
    aid = uuid.uuid4()

    for i in range(1, 6):  # 5 concurrent arrivals already parked
        await pn.push(aid, {"n": f"c{i}"})
    restored = [{"n": f"r{i}"} for i in range(1, 11)]  # 10 to restore

    await pn.requeue(aid, restored)

    survivors = await pn.drain(aid)
    names = [note["n"] for note in survivors]
    assert names == [f"r{i}" for i in range(1, 11)] + [f"c{i}" for i in range(1, 6)]


# --------------------------------------------------------------------------- #
# ApprovalService gate-open hook (notify approvers + arm timeout)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_notify_and_arm_notifies_and_schedules(monkeypatch) -> None:
    from datetime import UTC, datetime

    import contexts.orchestration.application.approval_service as appr
    from contexts.orchestration.domain.models import Approval, ApprovalMode, ApprovalState

    leader, other = uuid.uuid4(), uuid.uuid4()
    approval_id, run_id, room_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    # _notify_and_arm now reads the persisted row (the announce job re-read it),
    # not the transient config, and takes the question separately.
    approval = Approval(
        id=approval_id,
        workflow_run_id=run_id,
        mode=ApprovalMode.MAJORITY,
        leader_agent_id=leader,
        approver_agent_ids=(leader, other),
        timeout_seconds=120,
        state=ApprovalState.PENDING,
        started_at=datetime.now(UTC),
        ended_at=None,
    )

    pushed: list = []

    async def _push(agent_id, payload):
        pushed.append((agent_id, payload))

    enq: list = []

    async def _enqueue(job, *args, **kwargs):
        enq.append((job, args, kwargs))

    # Patch where _notify_and_arm imports them (function-local imports resolve
    # against the source modules).
    monkeypatch.setattr("contexts.orchestration.infrastructure.pending_notify.push", _push)
    monkeypatch.setattr("shared_kernel.queue.enqueue", _enqueue)

    svc = appr.ApprovalService.__new__(appr.ApprovalService)
    await svc._notify_and_arm(
        approval=approval,
        chatroom_id=room_id,
        question="Deploy v2 to production?",
    )

    # Every approver got an approval_request carrying the gate id + room +
    # the question being decided.
    assert {p[0] for p in pushed} == {leader, other}
    for _aid, note in pushed:
        assert note["kind"] == "approval_request"
        assert note["approval_id"] == str(approval_id)
        assert note["chatroom_id"] == str(room_id)
        assert note["question"] == "Deploy v2 to production?"
    # One turn-driving job per approver — without it the parked notification
    # is never drained and every gate falls to the timeout port.
    drives = [(j, a, k) for j, a, k in enq if j == "drive_approver_turn"]
    assert {a[0] for _j, a, _k in drives} == {str(leader), str(other)}
    for _j, args, kwargs in drives:
        assert args[1] == str(approval_id)
        assert args[2] == str(room_id)
        # AC-8: no dispatch delay — the announce job is the commit barrier now.
        assert "_defer_by" not in kwargs
    # The timeout was armed as a deferred job for this gate.
    timeouts = [(j, a, k) for j, a, k in enq if j == "approval_timeout"]
    assert len(timeouts) == 1
    _job, args, kwargs = timeouts[0]
    assert args[0] == str(approval_id)
    assert args[1] == str(room_id)
    assert "_defer_by" in kwargs
