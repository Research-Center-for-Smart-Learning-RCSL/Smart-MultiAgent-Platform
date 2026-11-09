"""Prompt revisions and classroom design runs (AC-17 to AC-20).

Spec: docs/tasks/2026-11-09-classroom-agent-collaboration/spec.md, milestone 4.

Repositories, facades and the provider router are faked; the database half
(compare-and-set, the one-in-flight index, the apply transaction) is in
tests/integration/test_classroom_design_db.py.
"""

from __future__ import annotations

import json
import uuid
from contextlib import ExitStack
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.workers.tasks import classroom_design as worker
from contexts.agents.application.agent_service import AgentService
from contexts.agents.application.classroom_design_service import ClassroomDesignService
from contexts.agents.domain.classroom_design import (
    DESIGN_BAD_OUTPUT,
    DESIGN_MISCONFIGURED,
    DESIGN_TOO_LARGE,
    DESIGN_UNAVAILABLE,
    MAX_DESIGN_NOTES_CHARS,
    MAX_PROMPT_CHARS,
    MAX_REPLY_CHARS,
    MAX_SCHEMA_CHARS,
    ClassroomDesignRun,
    DesignActivity,
    DesignForm,
    DesignOutputInvalid,
    DesignRunStatus,
    Difficulty,
    PeerErrorPattern,
    PromptRevision,
    PromptRevisionSource,
    Tone,
    build_design_prompt,
    parse_design_reply,
)
from contexts.agents.domain.errors import (
    AgentVersionMismatch,
    DesignAgentOutOfProject,
    DesignApplyConflict,
    DesignRunNotApplicable,
    DesignTargetsInvalid,
    PromptRevisionNotFound,
)
from contexts.agents.domain.models import Agent, AgentDraft, AgentModelHint, ContextMode
from contexts.keys.application.provider_router import ProviderCallResult
from contexts.keys.domain.errors import KeyGroupExhausted
from shared_kernel.auth.permissions import Capability

_PROJECT = uuid.uuid4()
_OTHER_PROJECT = uuid.uuid4()
_USER = uuid.uuid4()
_NOW = datetime(2026, 11, 9, tzinfo=UTC)


def _agent(
    *, prompt: str = "old prompt", version: int = 3, project_id: uuid.UUID = _PROJECT, **over: Any
) -> Agent:
    base = Agent(
        id=uuid.uuid4(),
        project_id=project_id,
        name="a",
        model_hint=AgentModelHint.CLAUDE,
        model_id="m",
        effort=None,
        key_group_id=uuid.uuid4(),
        system_prompt=prompt,
        rag_config_id=None,
        knowmap_config_id=None,
        context_mode=ContextMode.GENERAL,
        context_token_cap=None,
        skill_index_token_cap=None,
        temperature=None,
        top_p=None,
        seed=None,
        custom_capabilities=None,
        a2a_enabled=False,
        wakeup_config={},
        wakeup_authored_snapshot=None,
        workflow_capabilities={},
        version=version,
        deleted_at=None,
        created_at=_NOW,
    )
    return replace(base, **over)


def _reply(**over: Any) -> str:
    body: dict[str, Any] = {"teacher_system_prompt": "T new", "peer_system_prompt": "P new", "notes": "why"}
    body.update(over)
    return json.dumps(body, ensure_ascii=False)


# -- Output contract (AC-18) --------------------------------------------------


class TestParseDesignReply:
    def test_a_valid_reply_is_parsed_and_stripped(self) -> None:
        p = parse_design_reply(_reply(teacher_system_prompt="  T  ", notes=" n "))
        assert (p.teacher_system_prompt, p.peer_system_prompt, p.notes) == ("T", "P new", "n")

    def test_a_fenced_reply_is_accepted(self) -> None:
        assert parse_design_reply(f"```json\n{_reply()}\n```").peer_system_prompt == "P new"

    @pytest.mark.parametrize(
        "text",
        [
            "not json at all",
            "[1, 2]",
            json.dumps({"teacher_system_prompt": "T", "peer_system_prompt": "P"}),
            _reply(extra="x"),
            _reply(notes=3),
            _reply(peer_system_prompt="   "),
        ],
    )
    def test_a_reply_off_the_contract_fails_as_bad_output(self, text: str) -> None:
        with pytest.raises(DesignOutputInvalid) as exc:
            parse_design_reply(text)
        assert exc.value.reason == DESIGN_BAD_OUTPUT

    def test_an_oversized_prompt_is_refused_not_cut(self) -> None:
        with pytest.raises(DesignOutputInvalid) as exc:
            parse_design_reply(_reply(teacher_system_prompt="x" * (MAX_PROMPT_CHARS + 1)))
        assert exc.value.reason == DESIGN_TOO_LARGE

    def test_a_prompt_at_the_cap_is_accepted(self) -> None:
        assert len(
            parse_design_reply(_reply(peer_system_prompt="y" * MAX_PROMPT_CHARS)).peer_system_prompt
        ) == (MAX_PROMPT_CHARS)

    def test_an_oversized_reply_is_refused_before_parsing(self) -> None:
        with pytest.raises(DesignOutputInvalid) as exc:
            parse_design_reply(" " * (MAX_REPLY_CHARS + 1))
        assert exc.value.reason == DESIGN_TOO_LARGE

    def test_long_notes_are_trimmed(self) -> None:
        assert len(parse_design_reply(_reply(notes="n" * (MAX_DESIGN_NOTES_CHARS + 50))).notes) == (
            MAX_DESIGN_NOTES_CHARS
        )


class TestBuildDesignPrompt:
    _PARAMS: dict[str, Any] = {  # noqa: RUF012
        "activity": {
            "id": "x",
            "key": "crat",
            "name": "Word puzzle",
            "payload_schema": {"properties": {"a": {}}},
        },
        "difficulty": "advanced",
        "rounds": 3,
        "peer_error_pattern": "one_in_three",
        "tone": "warm",
        "notes": "grade 5",
    }

    def test_the_agent_prompt_keeps_its_text_and_gains_the_contract(self) -> None:
        system, user = build_design_prompt(
            design_system_prompt="I design.", params=self._PARAMS, teacher_prompt="T0", peer_prompt="P0"
        )
        assert system.startswith("I design.")
        assert '"teacher_system_prompt"' in system
        data = json.loads(user)
        assert data["form"] == {
            "difficulty": "advanced",
            "rounds": 3,
            "peer_error_pattern": "one_in_three",
            "tone": "warm",
            "notes": "grade 5",
        }
        assert data["activity"]["name"] == "Word puzzle"
        assert (data["current_teacher_system_prompt"], data["current_peer_system_prompt"]) == ("T0", "P0")
        assert "T0" not in system

    def test_a_large_schema_is_reduced_to_field_names(self) -> None:
        props = {f"f{i}": {"description": "d" * 100} for i in range(MAX_SCHEMA_CHARS // 50)}
        params = {
            **self._PARAMS,
            "activity": {**self._PARAMS["activity"], "payload_schema": {"properties": props}},
        }
        _, user = build_design_prompt(
            design_system_prompt="", params=params, teacher_prompt="", peer_prompt=""
        )
        assert json.loads(user)["activity"]["payload_schema"] == {"field_names": sorted(props)[:200]}


# -- Revisions (AC-17) ----------------------------------------------------------


def _revision(**over: Any) -> PromptRevision:
    base = PromptRevision(
        id=uuid.uuid4(),
        agent_id=uuid.uuid4(),
        agent_version=2,
        system_prompt="restored text",
        source=PromptRevisionSource.MANUAL,
        actor_user_id=_USER,
        design_run_id=None,
        restored_from_id=None,
        created_at=_NOW,
    )
    return replace(base, **over)


def _service(current: Agent, updated: Agent, *, has_history: bool) -> AgentService:
    svc = AgentService(AsyncMock())
    svc._agents = MagicMock()
    svc._agents.get = AsyncMock(return_value=current)
    svc._agents.patch = AsyncMock(return_value=updated)
    svc._revisions = MagicMock()
    svc._revisions.has_any = AsyncMock(return_value=has_history)
    svc._revisions.add = AsyncMock()
    return svc


@pytest.fixture
def _audit():
    with patch("contexts.agents.application.agent_service.audit.emit", new=AsyncMock()) as emit:
        yield emit


class TestRevisionHook:
    async def test_the_first_change_records_the_replaced_prompt_then_the_new_one(self, _audit) -> None:
        current = _agent(prompt="before", version=3)
        updated = replace(current, system_prompt="after", version=4)
        svc = _service(current, updated, has_history=False)

        await svc.patch(
            agent_id=current.id,
            draft=AgentDraft(system_prompt="after"),
            expected_version=3,
            actor_user_id=_USER,
            actor_ip=None,
        )

        calls = [c.kwargs for c in svc._revisions.add.await_args_list]
        assert [(c["source"], c["agent_version"], c["system_prompt"]) for c in calls] == [
            (PromptRevisionSource.INITIAL, 3, "before"),
            (PromptRevisionSource.MANUAL, 4, "after"),
        ]
        assert calls[0]["actor_user_id"] is None
        assert calls[1]["actor_user_id"] == _USER

    async def test_a_later_change_records_only_itself_with_its_source(self, _audit) -> None:
        current = _agent(prompt="before", version=7)
        updated = replace(current, system_prompt="after", version=8)
        svc = _service(current, updated, has_history=True)
        run_id = uuid.uuid4()

        await svc.patch(
            agent_id=current.id,
            draft=AgentDraft(system_prompt="after"),
            expected_version=7,
            actor_user_id=_USER,
            actor_ip=None,
            prompt_source=PromptRevisionSource.DESIGN,
            design_run_id=run_id,
        )

        svc._revisions.add.assert_awaited_once()
        kwargs = svc._revisions.add.await_args.kwargs
        assert (kwargs["source"], kwargs["design_run_id"], kwargs["agent_version"]) == (
            PromptRevisionSource.DESIGN,
            run_id,
            8,
        )

    async def test_an_unchanged_prompt_records_nothing(self, _audit) -> None:
        current = _agent(prompt="same", version=3)
        svc = _service(current, replace(current, version=4), has_history=False)

        await svc.patch(
            agent_id=current.id,
            draft=AgentDraft(system_prompt="same"),
            expected_version=3,
            actor_user_id=_USER,
            actor_ip=None,
        )

        svc._revisions.add.assert_not_awaited()


class TestRestore:
    async def test_an_unknown_revision_is_not_found(self, _audit) -> None:
        current = _agent()
        svc = _service(current, current, has_history=True)
        svc._revisions.get = AsyncMock(return_value=None)
        with pytest.raises(PromptRevisionNotFound):
            await svc.restore_prompt_revision(
                agent_id=current.id,
                revision_id=uuid.uuid4(),
                expected_version=current.version,
                actor_user_id=_USER,
                actor_ip=None,
            )

    async def test_a_stale_version_is_refused(self, _audit) -> None:
        current = _agent(version=5)
        svc = _service(current, current, has_history=True)
        svc._revisions.get = AsyncMock(return_value=_revision(agent_id=current.id))
        with pytest.raises(AgentVersionMismatch):
            await svc.restore_prompt_revision(
                agent_id=current.id,
                revision_id=uuid.uuid4(),
                expected_version=4,
                actor_user_id=_USER,
                actor_ip=None,
            )
        svc._agents.patch.assert_not_awaited()

    async def test_restoring_the_current_prompt_writes_nothing(self, _audit) -> None:
        current = _agent(prompt="restored text")
        svc = _service(current, current, has_history=True)
        svc._revisions.get = AsyncMock(return_value=_revision(agent_id=current.id))

        result = await svc.restore_prompt_revision(
            agent_id=current.id,
            revision_id=uuid.uuid4(),
            expected_version=current.version,
            actor_user_id=_USER,
            actor_ip=None,
        )

        assert result is current
        svc._agents.patch.assert_not_awaited()
        _audit.assert_not_awaited()

    async def test_a_restore_sets_the_prompt_and_records_a_restore_revision(self, _audit) -> None:
        current = _agent(prompt="now", version=5)
        updated = replace(current, system_prompt="restored text", version=6)
        svc = _service(current, updated, has_history=True)
        revision = _revision(agent_id=current.id)
        svc._revisions.get = AsyncMock(return_value=revision)

        await svc.restore_prompt_revision(
            agent_id=current.id,
            revision_id=revision.id,
            expected_version=5,
            actor_user_id=_USER,
            actor_ip=None,
        )

        assert svc._agents.patch.await_args.kwargs["values"] == {"system_prompt": "restored text"}
        kwargs = svc._revisions.add.await_args.kwargs
        assert (kwargs["source"], kwargs["restored_from_id"]) == (PromptRevisionSource.RESTORE, revision.id)
        actions = [c.args[1].action for c in _audit.await_args_list]
        assert actions == ["agent.edited", "agent.prompt_restored"]
        restored = _audit.await_args_list[-1].args[1]
        assert restored.metadata == {"revision_id": str(revision.id)}


# -- Design runs (AC-18 to AC-20) ----------------------------------------------


def _form(design: Agent, teacher: Agent, peer: Agent) -> DesignForm:
    return DesignForm(
        design_agent_id=design.id,
        teacher_agent_id=teacher.id,
        peer_agent_id=peer.id,
        activity=DesignActivity(id=uuid.uuid4(), key="k", name="n", payload_schema={}),
        difficulty=Difficulty.INTRODUCTORY,
        rounds=2,
        peer_error_pattern=PeerErrorPattern.NONE,
        tone=Tone.NEUTRAL,
        notes="",
    )


def _run(teacher: Agent, peer: Agent, **over: Any) -> ClassroomDesignRun:
    base = ClassroomDesignRun(
        id=uuid.uuid4(),
        project_id=_PROJECT,
        requested_by_user_id=_USER,
        design_agent_id=uuid.uuid4(),
        teacher_agent_id=teacher.id,
        peer_agent_id=peer.id,
        params={},
        teacher_prompt_before=teacher.system_prompt,
        teacher_version_before=teacher.version,
        peer_prompt_before=peer.system_prompt,
        peer_version_before=peer.version,
        status=DesignRunStatus.SUCCEEDED,
        proposed_teacher_prompt="T new",
        proposed_peer_prompt="P new",
        notes="why",
        failure_reason=None,
        created_at=_NOW,
        finished_at=_NOW,
        applied_at=None,
        applied_by_user_id=None,
    )
    return replace(base, **over)


def _design_service(*agents: Agent, run: ClassroomDesignRun | None = None) -> ClassroomDesignService:
    svc = ClassroomDesignService(AsyncMock())
    by_id = {a.id: a for a in agents}
    svc._agents = MagicMock()
    svc._agents.get = AsyncMock(side_effect=by_id.get)
    svc._runs = MagicMock()
    svc._runs.expire_stale = AsyncMock(return_value=0)
    svc._runs.create = AsyncMock(side_effect=SimpleNamespace)
    svc._runs.get = AsyncMock(return_value=run)
    svc._runs.mark_applied = AsyncMock(return_value=True)
    return svc


class TestCreateRun:
    async def test_the_run_snapshots_both_targets(self) -> None:
        design, teacher, peer = _agent(), _agent(prompt="T0", version=4), _agent(prompt="P0", version=9)
        svc = _design_service(design, teacher, peer)

        await svc.create_run(project_id=_PROJECT, form=_form(design, teacher, peer), actor_user_id=_USER)

        kwargs = svc._runs.create.await_args.kwargs
        assert (kwargs["teacher_prompt_before"], kwargs["teacher_version_before"]) == ("T0", 4)
        assert (kwargs["peer_prompt_before"], kwargs["peer_version_before"]) == ("P0", 9)
        svc._runs.expire_stale.assert_awaited_once()

    async def test_the_same_agent_cannot_be_teacher_and_peer(self) -> None:
        design, teacher = _agent(), _agent()
        svc = _design_service(design, teacher)
        with pytest.raises(DesignTargetsInvalid):
            await svc.create_run(
                project_id=_PROJECT, form=_form(design, teacher, teacher), actor_user_id=_USER
            )

    @pytest.mark.parametrize("which", ["design", "teacher", "peer"])
    async def test_an_agent_of_another_project_is_refused(self, which: str) -> None:
        agents = {
            w: _agent(project_id=_OTHER_PROJECT if w == which else _PROJECT)
            for w in ("design", "teacher", "peer")
        }
        svc = _design_service(*agents.values())
        with pytest.raises(DesignAgentOutOfProject):
            await svc.create_run(
                project_id=_PROJECT,
                form=_form(agents["design"], agents["teacher"], agents["peer"]),
                actor_user_id=_USER,
            )
        svc._runs.create.assert_not_awaited()

    async def test_a_missing_agent_is_refused_like_a_foreign_one(self) -> None:
        design, teacher, peer = _agent(), _agent(), _agent()
        svc = _design_service(design, teacher)
        with pytest.raises(DesignAgentOutOfProject):
            await svc.create_run(project_id=_PROJECT, form=_form(design, teacher, peer), actor_user_id=_USER)


class TestApply:
    async def test_both_agents_are_patched_at_the_snapshotted_versions(self) -> None:
        teacher, peer = _agent(version=4), _agent(version=9)
        run = _run(teacher, peer)
        svc = _design_service(teacher, peer, run=run)
        patch_mock = AsyncMock(side_effect=lambda **kw: _agent(version=kw["expected_version"] + 1))
        with (
            patch.object(AgentService, "patch", new=patch_mock),
            patch("contexts.agents.application.classroom_design_service.audit.emit", new=AsyncMock()) as emit,
        ):
            t, p = await svc.apply(project_id=_PROJECT, run_id=run.id, actor_user_id=_USER, actor_ip=None)

        calls = [c.kwargs for c in patch_mock.await_args_list]
        assert [(c["agent_id"], c["expected_version"], c["draft"].system_prompt) for c in calls] == [
            (teacher.id, 4, "T new"),
            (peer.id, 9, "P new"),
        ]
        assert all(
            c["prompt_source"] is PromptRevisionSource.DESIGN and c["design_run_id"] == run.id for c in calls
        )
        assert (t.version, p.version) == (5, 10)
        event = emit.await_args.args[1]
        assert event.action == "classroom_design.applied"
        assert "T new" not in json.dumps(event.metadata)

    async def test_a_changed_agent_is_a_conflict(self) -> None:
        teacher, peer = _agent(version=4), _agent(version=9)
        run = _run(teacher, peer)
        svc = _design_service(teacher, peer, run=run)
        patch_mock = AsyncMock(side_effect=[_agent(), AgentVersionMismatch(str(peer.id))])
        with patch.object(AgentService, "patch", new=patch_mock), pytest.raises(DesignApplyConflict):
            await svc.apply(project_id=_PROJECT, run_id=run.id, actor_user_id=_USER, actor_ip=None)

    @pytest.mark.parametrize(
        "status", [DesignRunStatus.PENDING, DesignRunStatus.FAILED, DesignRunStatus.APPLIED]
    )
    async def test_a_run_without_a_live_proposal_is_not_applicable(self, status: DesignRunStatus) -> None:
        teacher, peer = _agent(), _agent()
        svc = _design_service(teacher, peer, run=_run(teacher, peer, status=status))
        with pytest.raises(DesignRunNotApplicable):
            await svc.apply(project_id=_PROJECT, run_id=uuid.uuid4(), actor_user_id=_USER, actor_ip=None)
        svc._runs.mark_applied.assert_not_awaited()

    async def test_the_loser_of_two_applies_writes_nothing(self) -> None:
        teacher, peer = _agent(), _agent()
        svc = _design_service(teacher, peer, run=_run(teacher, peer))
        svc._runs.mark_applied = AsyncMock(return_value=False)
        patch_mock = AsyncMock()
        with patch.object(AgentService, "patch", new=patch_mock), pytest.raises(DesignRunNotApplicable):
            await svc.apply(project_id=_PROJECT, run_id=uuid.uuid4(), actor_user_id=_USER, actor_ip=None)
        patch_mock.assert_not_awaited()

    async def test_a_run_of_another_project_is_not_found(self) -> None:
        from contexts.agents.domain.errors import DesignRunNotFound

        teacher, peer = _agent(), _agent()
        svc = _design_service(teacher, peer, run=_run(teacher, peer, project_id=_OTHER_PROJECT))
        with pytest.raises(DesignRunNotFound):
            await svc.apply(project_id=_PROJECT, run_id=uuid.uuid4(), actor_user_id=_USER, actor_ip=None)


# -- Worker (AC-18) ------------------------------------------------------------


class _FakeSession:
    def __init__(self, db: Any) -> None:
        self._db = db

    async def __aenter__(self) -> Any:
        return self._db

    async def __aexit__(self, *exc: object) -> bool:
        return False


class _WorkerHarness:
    def __init__(
        self,
        *,
        run: ClassroomDesignRun | None,
        design: Agent | None,
        result: ProviderCallResult | BaseException | None = None,
    ) -> None:
        self.agents = MagicMock()
        self.agents.begin_design_run = AsyncMock(return_value=run)
        self.agents.get_agent = AsyncMock(return_value=design)
        self.agents.record_design_success = AsyncMock(return_value=True)
        self.agents.record_design_failure = AsyncMock(return_value=True)
        self.agents.chat_model_capabilities = MagicMock(return_value={})
        self.agents.chat_model_catalog = MagicMock(return_value=())
        self.router = MagicMock()
        if isinstance(result, BaseException):
            self.router.call = AsyncMock(side_effect=result)
        else:
            self.router.call = AsyncMock(
                return_value=result or ProviderCallResult(http_status=200, body={"text": _reply()})
            )
        self.db = MagicMock()
        self.db.commit = AsyncMock()

    async def run(self, run_id: uuid.UUID) -> str:
        with ExitStack() as stack:
            stack.enter_context(
                patch("shared_kernel.db.session.async_session", return_value=_FakeSession(self.db))
            )
            stack.enter_context(
                patch("contexts.agents.interfaces.facade.AgentsFacade", return_value=self.agents)
            )
            stack.enter_context(
                patch("contexts.keys.infrastructure.adapters.build_router", return_value=self.router)
            )
            stack.enter_context(patch("shared_kernel.audit.flush_tail_events", new=AsyncMock()))
            return await worker.run_classroom_design({}, str(run_id))


class TestWorker:
    def _setup(self, **over: Any) -> tuple[ClassroomDesignRun, Agent]:
        design = _agent(prompt="I design.")
        teacher, peer = _agent(prompt="T0"), _agent(prompt="P0")
        run = _run(teacher, peer, design_agent_id=design.id, status=DesignRunStatus.RUNNING, **over)
        return run, design

    async def test_a_valid_reply_is_stored_as_the_proposal(self) -> None:
        run, design = self._setup()
        h = _WorkerHarness(run=run, design=design)

        assert await h.run(run.id) == "succeeded"

        proposal = h.agents.record_design_success.await_args.args[1]
        assert (proposal.teacher_system_prompt, proposal.peer_system_prompt) == ("T new", "P new")
        h.agents.record_design_failure.assert_not_awaited()

    async def test_the_call_bills_the_design_agent_with_no_tools(self) -> None:
        run, design = self._setup()
        h = _WorkerHarness(run=run, design=design)

        await h.run(run.id)

        kwargs = h.router.call.await_args.kwargs
        assert kwargs["group_id"] == design.key_group_id
        request = kwargs["request"]
        assert (request.agent_id, request.usage_context) == (design.id, "classroom_design")
        assert "tools" not in request.payload
        assert request.payload["system"].startswith("I design.")
        assert "T0" in request.payload["messages"][0]["content"]

    @pytest.mark.parametrize(
        ("result", "reason"),
        [
            (ProviderCallResult(http_status=200, body={"text": "nope"}), DESIGN_BAD_OUTPUT),
            (
                ProviderCallResult(
                    http_status=200, body={"text": _reply(peer_system_prompt="x" * (MAX_PROMPT_CHARS + 1))}
                ),
                DESIGN_TOO_LARGE,
            ),
            (ProviderCallResult(http_status=500, body={}), DESIGN_UNAVAILABLE),
            (KeyGroupExhausted(group_id=uuid.uuid4(), reason="errors"), DESIGN_UNAVAILABLE),
            (RuntimeError("provider said: secret prompt"), DESIGN_UNAVAILABLE),
        ],
    )
    async def test_each_failure_is_one_closed_reason(self, result: Any, reason: str) -> None:
        run, design = self._setup()
        h = _WorkerHarness(run=run, design=design, result=result)

        assert await h.run(run.id) == "failed"

        assert h.agents.record_design_failure.await_args.args == (run.id, reason)
        h.agents.record_design_success.assert_not_awaited()

    async def test_a_design_agent_moved_to_another_project_is_misconfigured(self) -> None:
        run, design = self._setup()
        h = _WorkerHarness(run=run, design=replace(design, project_id=_OTHER_PROJECT))

        assert await h.run(run.id) == "failed"

        assert h.agents.record_design_failure.await_args.args == (run.id, DESIGN_MISCONFIGURED)
        h.router.call.assert_not_awaited()

    async def test_a_run_that_is_not_pending_is_left_alone(self) -> None:
        h = _WorkerHarness(run=None, design=None)

        assert await h.run(uuid.uuid4()) == "not-pending"

        h.router.call.assert_not_awaited()
        h.agents.record_design_failure.assert_not_awaited()


# -- Route gates (AC-20) ---------------------------------------------------------


def _gate_capability(fn: Any) -> Any:
    import inspect

    gate = inspect.signature(fn).parameters["_"].default.dependency
    assert gate.__qualname__.startswith("require.")
    return next(c.cell_contents for c in gate.__closure__ or () if isinstance(c.cell_contents, Capability))


@pytest.mark.parametrize(
    "route",
    ["create_design_run", "list_design_runs", "get_design_run", "apply_design_run"],
)
def test_every_design_route_requires_the_edit_capability_on_the_path_project(route: str) -> None:
    from app.api.v1 import classroom_design as routes

    assert _gate_capability(getattr(routes, route)) is Capability.RESOURCE_CREATE_EDIT


class TestRevisionRouteGates:
    async def _call(self, monkeypatch: pytest.MonkeyPatch, route: str) -> MagicMock:
        from fastapi import HTTPException

        from app.api.v1 import agents as routes

        facade = MagicMock()
        facade.get_agent = AsyncMock(return_value=_agent(project_id=_OTHER_PROJECT))
        facade.list_prompt_revisions = AsyncMock(return_value=[])
        facade.restore_prompt_revision = AsyncMock()
        monkeypatch.setattr(routes, "AgentsFacade", MagicMock(return_value=facade))
        refuse = AsyncMock(side_effect=HTTPException(status_code=403))
        monkeypatch.setattr(routes, "assert_project_capability", refuse)
        principal = SimpleNamespace(user_id=_USER)
        call = (
            routes.list_prompt_revisions(
                agent_id=uuid.uuid4(), limit=50, offset=0, principal=principal, db=MagicMock()
            )
            if route == "list"
            else routes.restore_prompt_revision(
                agent_id=uuid.uuid4(),
                revision_id=uuid.uuid4(),
                if_match='"3"',
                ctx=SimpleNamespace(actor_ip=None, request_id=None),
                principal=principal,
                db=MagicMock(),
            )
        )
        with pytest.raises(HTTPException):
            await call
        assert refuse.await_args.kwargs["project_id"] == _OTHER_PROJECT
        assert refuse.await_args.kwargs["capability"] is Capability.RESOURCE_CREATE_EDIT
        return facade

    async def test_listing_is_refused_without_the_edit_capability(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        facade = await self._call(monkeypatch, "list")
        facade.list_prompt_revisions.assert_not_awaited()

    async def test_restoring_is_refused_without_the_edit_capability(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        facade = await self._call(monkeypatch, "restore")
        facade.restore_prompt_revision.assert_not_awaited()


def test_the_job_is_registered_single_attempt() -> None:
    from app.workers.main import WorkerSettings

    jobs = [f for f in WorkerSettings.functions if getattr(f, "name", None) == "run_classroom_design"]
    assert len(jobs) == 1
    assert jobs[0].max_tries == 1
    assert jobs[0].timeout_s == worker.DESIGN_JOB_TIMEOUT_S
