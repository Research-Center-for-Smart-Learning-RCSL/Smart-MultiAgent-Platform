"""K.7 — wiring tripwires. The permanent cross-context guard set.

The 2026-06-11 completeness audit's root cause: 451 unit tests, every one mocked
at the service boundary, so no test ever asked the cross-context question ("does
a message produce a reply?") and a lint commit could sever the executor registry
without a single failure. This tier closes that gap with the minimum permanent
set — a ``FakeAdapter`` for the LLM, but **real** Postgres, **real** Redis, and
the actual service/engine code paths (not mocked at the boundary).

It requires the ``compose.test.yml`` data plane (Postgres + Redis + MailHog) and
runs as the dedicated, required ``backend-wiring`` CI job from inside the compose
network (so ``postgres`` / ``redis`` / ``mailhog`` resolve). It is excluded from
the unit job via ``-m "not wiring"``. Each test pins one behaviour and cites the
requirement ID it guards (per §0.3).

Six checks (K.7):
    1. executor registry completeness ........ K.4 (ff19610 backstop)
    2. message -> streamed agent reply ....... K.2 / K.3  (R13.19)
    3. A2A call round-trip ................... K.3        (R9.15)
    4. workflow golden run ................... K.4        (§14.3)
    5. usage event per provider call ......... K.1        (R7.12)
    6. email round-trip via MailHog .......... K.6        (R6.02)
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import time
import uuid
from types import SimpleNamespace

import httpx
import pytest
import sqlalchemy as sa

from contexts.agents.application.runtime.turn_engine import TurnEngine
from contexts.agents.domain.models import AgentModelHint, ContextMode, PromptStrategy
from contexts.agents.infrastructure.repositories import AgentRepository
from contexts.conversation.application.message_service import MessageService
from contexts.conversation.application.triggers import evaluate_message_wakeups
from contexts.conversation.infrastructure.repositories import (
    ChatroomAgentRepository,
    ChatroomRepository,
    WorkspaceRepository,
)
from contexts.identity.infrastructure.repositories import UserRepository
from contexts.keys.application.provider_router import (
    ProviderCallResult,
    ProviderRequest,
    ProviderRouter,
    StreamComplete,
    TokenDelta,
)
from contexts.keys.domain.probe_status import ProbeStatus
from contexts.keys.domain.providers import ApiKeyProvider, ProviderCapability
from contexts.keys.infrastructure.group_repository import KeyGroupRepository
from contexts.keys.infrastructure.repositories import ApiKeyRepository
from contexts.orchestration.application.a2a_consumer import consume_once
from contexts.orchestration.application.a2a_handler import handle_envelope
from contexts.orchestration.application.a2a_service import A2AService
from contexts.orchestration.interfaces.facade import OrchestrationFacade
from contexts.tenancy.domain.models import OrgMemberRole, ProjectMemberRole
from contexts.tenancy.infrastructure.repositories import (
    OrgMemberRepository,
    OrgRepository,
    ProjectMemberRepository,
    ProjectRepository,
)
from contexts.workflow.application.run_engine import RunEngine
from contexts.workflow.domain.models import RunState
from contexts.workflow.infrastructure.repositories import (
    WorkflowRepository,
    WorkflowRunRepository,
)
from shared_kernel.auth.clients import get_redis
from shared_kernel.db.session import async_session
from shared_kernel.infra.vault import EnvelopeRecord
from shared_kernel.realtime.pubsub import Subscriber, room_channel

pytestmark = pytest.mark.wiring

# Deterministic reply text the fakes emit; the streamed deltas concatenate to it.
_REPLY = "Hello from the fake adapter."
_DELTAS = ("Hello ", "from ", "the fake adapter.")

_MAILHOG_API = os.environ.get("MAILHOG_API_URL", "http://mailhog:8025")

# Fires the every_n_messages trigger on the first message; allow_self_open lets
# it fire without live WS presence (the wiring tier has no socket clients).
_WAKEUP_EVERY_1 = {
    "allow_self_open": True,
    "triggers": {"every_n_messages": {"enabled": True, "n": 1}},
}


# --------------------------------------------------------------------------- #
# Fakes — the only mocked seam in this tier (the LLM provider, by design).     #
# --------------------------------------------------------------------------- #


def _ok_result() -> ProviderCallResult:
    return ProviderCallResult(
        http_status=200,
        body={"text": _REPLY, "tool_calls": []},
        input_tokens=5,
        output_tokens=7,
        request_ms=1,
    )


class _FakeRouter:
    """Stands in for the real ``ProviderRouter`` where a turn only needs a reply
    (message/A2A/workflow tiers). Injected via ``TurnEngine(router=...)`` or by
    patching ``turn_engine.build_router``; no keys, no Vault, no network."""

    def __init__(self, db: object | None = None) -> None:
        self._db = db

    async def call_stream(self, *, group_id, request):
        for chunk in _DELTAS:
            yield TokenDelta(chunk)
        yield StreamComplete(_ok_result())

    async def call(self, *, group_id, request):
        return _ok_result()


class _FakeAdapter:
    """A real-protocol provider adapter (unary + streaming) for the usage-event
    tier, where the **real** ``ProviderRouter`` accounting path must run."""

    def __init__(self, provider: ApiKeyProvider) -> None:
        self.provider = provider

    async def invoke(self, *, secret, request):
        return _ok_result()

    async def stream(self, *, secret, request):
        for chunk in _DELTAS:
            yield TokenDelta(chunk)
        yield StreamComplete(_ok_result())


# --------------------------------------------------------------------------- #
# Seeding — build a real object graph through the production repositories.     #
# --------------------------------------------------------------------------- #


async def _seed_agent_and_room(
    db, *, a2a_enabled: bool = True, wakeup_config: dict | None = None
) -> SimpleNamespace:
    u = uuid.uuid4().hex[:8]
    user = await UserRepository(db).insert(email=f"wire-{u}@smap.test", password_hash="x" * 16)
    org = await OrgRepository(db).create(name=f"org-{u}", creator_user_id=user.id)
    await OrgMemberRepository(db).add(
        org_id=org.id, user_id=user.id, role=OrgMemberRole.OWNER, is_original_creator=True
    )
    project = await ProjectRepository(db).create(
        name=f"proj-{u}",
        owner_user_id=None,
        owner_org_id=org.id,
        created_by_user_id=user.id,
    )
    await ProjectMemberRepository(db).add(
        project_id=project.id, user_id=user.id, role=ProjectMemberRole.OWNER
    )
    group = await KeyGroupRepository(db).create(project_id=project.id, name=f"kg-{u}")
    agent = await AgentRepository(db).create(
        project_id=project.id,
        name=f"agent-{u}",
        model_hint=AgentModelHint.CLAUDE,
        key_group_id=group.id,
        system_prompt="You are a deterministic test agent.",
        prompt_strategy=PromptStrategy.FULL,
        rag_config_id=None,
        graphrag_config_id=None,
        context_mode=ContextMode.GENERAL,
        context_token_cap=None,
        a2a_enabled=a2a_enabled,
        wakeup_config=wakeup_config or {},
        workflow_capabilities={},
    )
    workspace = await WorkspaceRepository(db).create(project_id=project.id, name=f"ws-{u}")
    room = await ChatroomRepository(db).create(workspace_id=workspace.id, name=f"room-{u}")
    await ChatroomAgentRepository(db).add(chatroom_id=room.id, agent_id=agent.id)
    return SimpleNamespace(
        user=user,
        org=org,
        project=project,
        group=group,
        agent=agent,
        workspace=workspace,
        room=room,
    )


@contextlib.asynccontextmanager
async def _serving(agent_id: uuid.UUID):
    """Run an A2A inbox consumer for ``agent_id`` for the duration of the block.

    The agent rows must be committed before entering — the consumer dispatches
    each envelope on its own ``async_session`` (see ``a2a_handler``)."""
    flag = {"on": True}

    async def _loop() -> None:
        while flag["on"]:
            with contextlib.suppress(Exception):
                await consume_once(agent_id, handle_envelope)
            await asyncio.sleep(0.02)

    task = asyncio.create_task(_loop())
    try:
        yield
    finally:
        flag["on"] = False
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


# --------------------------------------------------------------------------- #
# 1. Executor registry completeness (K.4 — ff19610 backstop).                  #
# --------------------------------------------------------------------------- #


def test_executor_registry_complete() -> None:
    from contexts.workflow.application.executors import get_executor
    from contexts.workflow.domain.models import NodeType

    for node_type in NodeType:
        assert get_executor(node_type) is not None, f"no executor for {node_type}"


# --------------------------------------------------------------------------- #
# 2. Message -> streamed agent reply (K.2 / K.3, R13.19).                      #
# --------------------------------------------------------------------------- #


async def test_message_produces_streamed_agent_reply() -> None:
    async with async_session() as db:
        env = await _seed_agent_and_room(db, wakeup_config=_WAKEUP_EVERY_1)
        await MessageService(db).send(
            chatroom_id=env.room.id,
            sender_user_id=env.user.id,
            content_md="Hello agent",
            actor_ip=None,
        )

        # K.3 link (a): the message dispatch must reach OrchestrationFacade
        # .on_message_created and fire the every_n_messages trigger. Deleting that
        # call (the audit's severed wiring) makes this return [] and fails CI.
        woke = await evaluate_message_wakeups(db, chatroom_id=env.room.id, sender_is_user=True)
        assert env.agent.id in woke

        collected: list[dict] = []
        async with Subscriber([room_channel(env.room.id)]) as sub:

            async def _collect() -> None:
                async for event in sub.events():
                    collected.append(event)
                    if event.get("type") == "agent.finished":
                        return

            engine = TurnEngine(db, router=_FakeRouter())  # type: ignore[arg-type]
            turn = asyncio.create_task(
                engine.run_turn(agent_id=env.agent.id, chatroom_id=env.room.id, trigger="manual")
            )
            await asyncio.wait_for(_collect(), timeout=15)
            result = await turn

        assert result.status == "completed", result.reason
        assert result.message_id is not None
        types = {e.get("type") for e in collected}
        assert "agent.token" in types
        assert "agent.finished" in types

        # The reply is a real, persisted sender_type=AGENT message.
        row = (
            await db.execute(
                sa.text("SELECT sender_type FROM messages WHERE id = :mid"),
                {"mid": result.message_id},
            )
        ).first()
        assert row is not None
        assert row[0] == "agent"


# --------------------------------------------------------------------------- #
# 3. A2A call round-trip (K.3, R9.15).                                         #
# --------------------------------------------------------------------------- #


async def test_a2a_call_round_trip(monkeypatch: pytest.MonkeyPatch) -> None:
    # The A2A handler builds its own TurnEngine internally — point its router at
    # the fake LLM so the served turn produces a deterministic reply.
    import contexts.agents.application.runtime.turn_engine as te

    monkeypatch.setattr(te, "build_router", lambda db, **_: _FakeRouter(db))

    async with async_session() as seed_db:
        env = await _seed_agent_and_room(seed_db, a2a_enabled=True)
        callee = await AgentRepository(seed_db).create(
            project_id=env.project.id,
            name="callee",
            model_hint=AgentModelHint.CLAUDE,
            key_group_id=env.group.id,
            system_prompt="callee",
            prompt_strategy=PromptStrategy.FULL,
            rag_config_id=None,
            graphrag_config_id=None,
            context_mode=ContextMode.GENERAL,
            context_token_cap=None,
            a2a_enabled=True,
            # The callee must opt into being A2A-callable: with no shared
            # invocation context between two distinct agents, R9.17 requires
            # call_only on the target (a2a_scope.is_call_only_enabled).
            wakeup_config={"triggers": {"call_only": {"enabled": True}}},
            workflow_capabilities={},
        )
        await seed_db.commit()

    caller_id = env.agent.id
    callee_id = callee.id

    async with _serving(callee_id), async_session() as call_db:
        reply = await asyncio.wait_for(
            A2AService(call_db).call(
                from_agent_id=caller_id,
                to_agent_id=callee_id,
                payload={"input": "ping"},
            ),
            timeout=30,
        )

    assert reply["payload"]["reply"] == _REPLY


# --------------------------------------------------------------------------- #
# 4. Workflow golden run (K.4, §14.3).                                         #
# --------------------------------------------------------------------------- #


async def _find_parked_approval(run_id: uuid.UUID) -> tuple[uuid.UUID, str]:
    """Locate the approval the gate executor parked on, via its Redis claim key.

    The approval_gate executor writes ``wf:approval:{approval_id}`` ->
    ``{run_id, node_id}`` (K.4); scan for the one belonging to this run."""
    redis = get_redis()
    target = str(run_id)
    async for key in redis.scan_iter(match="wf:approval:*"):
        raw = await redis.get(key)
        if not raw:
            continue
        data = json.loads(raw)
        if data.get("run_id") == target:
            return uuid.UUID(str(key).rsplit(":", 1)[-1]), str(data.get("node_id"))
    raise AssertionError(f"no parked approval found for run {run_id}")


async def test_workflow_golden_run(monkeypatch: pytest.MonkeyPatch) -> None:
    import contexts.agents.application.runtime.turn_engine as te

    monkeypatch.setattr(te, "build_router", lambda db, **_: _FakeRouter(db))

    async with async_session() as seed_db:
        env = await _seed_agent_and_room(seed_db, a2a_enabled=True)
        await seed_db.commit()

    agent_id = env.agent.id
    definition = {
        "entry_node_id": "trigger_1",
        "nodes": [
            {"id": "trigger_1", "type": "trigger", "config": {"trigger_type": "manual"}},
            {"id": "cond_1", "type": "condition", "config": {"default_port": "go"}},
            {
                "id": "agent_1",
                "type": "agent_invocation",
                "config": {
                    "agent_id": str(agent_id),
                    "input_template": "hello",
                    "output_variable": "reply",
                },
            },
            {
                "id": "gate_1",
                "type": "approval_gate",
                "config": {
                    "mode": "single",
                    "leader_agent_id": str(agent_id),
                    "approvers": [],
                    "timeout_seconds": 300,
                },
            },
            {"id": "end_1", "type": "end", "config": {"status": "success"}},
        ],
        "edges": [
            {"id": "e1", "from": "trigger_1", "to": "cond_1"},
            {"id": "e2", "from": "cond_1", "to": "agent_1", "from_port": "go"},
            {"id": "e3", "from": "agent_1", "to": "gate_1", "from_port": "success"},
            {"id": "e4", "from": "gate_1", "to": "end_1", "from_port": "approved"},
        ],
    }

    # resume_at_port reloads the workflow row's definition as the source of
    # truth (start_run's `definition` arg is the same document the API loads
    # from this row). The row MUST carry the real definition — an empty stub
    # leaves the resume path with no edges, so the run never reaches `end`.
    async with async_session() as wf_db:
        workflow = await WorkflowRepository(wf_db).insert(
            workspace_id=env.workspace.id, name="golden", definition=definition
        )
        await wf_db.commit()

    async with _serving(agent_id), async_session() as db:
        # start_run drives trigger -> condition -> agent_invocation (which makes a
        # synchronous A2A self-call served by the consumer above) and parks at the
        # approval gate (run -> WAITING).
        run_id = await RunEngine(db).start_run(
            project_id=env.project.id,
            workflow_id=workflow.id,
            definition=definition,
            trigger_type="manual",
            trigger_payload={},
        )

        approval_id, node_id = await _find_parked_approval(run_id)
        await OrchestrationFacade(db).cast_approval_vote(
            approval_id=approval_id, voter_agent_id=agent_id, vote=True
        )
        # The vote's resolution enqueues a resume task (covered by K.4 unit tests);
        # drive the resume directly here so the golden run is deterministic without
        # standing up an arq worker.
        await RunEngine(db).resume_at_port(run_id, node_id, "approved")
        await db.commit()

        run = await WorkflowRunRepository(db).get(run_id)
        assert run is not None
        assert run.state == RunState.SUCCEEDED


# --------------------------------------------------------------------------- #
# 5. Usage event written per provider call (K.1, R7.12).                       #
# --------------------------------------------------------------------------- #


async def test_usage_event_written_per_provider_call() -> None:
    async with async_session() as db:
        u = uuid.uuid4().hex[:8]
        user = await UserRepository(db).insert(email=f"key-{u}@smap.test", password_hash="x" * 16)
        key_id = uuid.uuid4()
        await ApiKeyRepository(db).insert(
            key_id=key_id,
            owner_user_id=user.id,
            provider=ApiKeyProvider.OPENAI,
            name=f"k-{u}",
            envelope=EnvelopeRecord(
                ciphertext=b"ct",
                nonce=b"no",
                dek_wrapped="vault:v1:zz",
                ciphertext_hmac=b"hm",
                # transit_key_version defaults to 0 ("unparsed/legacy"), which the
                # repository rejects as corrupted; set it to a valid version.
                transit_key_version=1,
                hmac_key_version=1,
            ),
            masked_preview="sk-...abcd",
            test_status=ProbeStatus.OK,
            test_error=None,
            last_test_at=None,
        )
        await db.commit()

        # Real ProviderRouter + a fake adapter so the router's own accounting path
        # runs; bypass Vault transit (no real DEK) by stubbing the unwrap step.
        router = ProviderRouter(db, {ApiKeyProvider.OPENAI: _FakeAdapter(ApiKeyProvider.OPENAI)})

        async def _fake_unwrap(_key_id: uuid.UUID) -> bytes:
            return b"dummy-secret"

        router._unwrap_secret = _fake_unwrap  # type: ignore[assignment]

        result = await router.call_single_key(
            key_id=key_id,
            request=ProviderRequest(
                capability=ProviderCapability.LLM_CHAT,
                payload={"models": {}, "messages": []},
            ),
        )
        assert result.http_status == 200

        count = (
            await db.execute(
                sa.text("SELECT count(*) FROM key_usage_events WHERE key_id = :k"),
                {"k": key_id},
            )
        ).scalar_one()
        assert count == 1


# --------------------------------------------------------------------------- #
# 6. Email round-trip via MailHog (K.6, R6.02).                                #
# --------------------------------------------------------------------------- #


def test_register_delivers_verification_email_via_smtp() -> None:
    from fastapi.testclient import TestClient

    from app.main import create_app

    # NB: a non-reserved domain — email-validator rejects reserved TLDs like
    # `.test` (used elsewhere only via the repository, which bypasses EmailStr).
    email = f"verify-{uuid.uuid4().hex[:8]}@example.com"
    with TestClient(create_app()) as client:
        resp = client.post(
            "/api/auth/register",
            json={"email": email, "password": "Str0ng-P@ssw0rd-1"},
        )
        assert resp.status_code in (200, 202), resp.text

    # MailHog ingests asynchronously; poll its HTTP API for the message.
    found = False
    for _ in range(40):
        r = httpx.get(f"{_MAILHOG_API}/api/v2/messages", timeout=5.0)
        if r.status_code == 200 and email in r.text:
            found = True
            break
        time.sleep(0.5)
    assert found, f"verification email for {email} not received by MailHog"
