"""Unit tests for the prompt_studio session service (§29 / R29.07-R29.09, AC-8).

DB-free: a fake session store + fake config service + monkeypatched enqueue
drive ownership, message-cap, daily-quota and enqueue paths without Redis.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

import contexts.prompt_studio.application.session_service as session_mod
from contexts.prompt_studio.application.session_service import SessionService
from contexts.prompt_studio.domain.errors import (
    AssistantUnavailable,
    DailyQuotaExceeded,
    SessionLimitReached,
    SessionNotFound,
)
from contexts.prompt_studio.domain.models import (
    AssistantConfig,
    AssistantSession,
    PromptScope,
    SessionMessage,
)

_NOW = datetime(2026, 7, 5, tzinfo=UTC)


def _config(limit: int = 50, key: uuid.UUID | None = None) -> AssistantConfig:
    return AssistantConfig(
        id=uuid.uuid4(),
        scope=PromptScope.USER,
        org_id=None,
        user_id=uuid.uuid4(),
        system_prompt="",
        key_id=key or uuid.uuid4(),
        model_id="m",
        daily_request_limit_per_user=limit,
        enabled=True,
        hide_platform_templates=False,
        version=1,
        created_at=_NOW,
        updated_at=_NOW,
    )


class _FakeStore:
    def __init__(self, *, session=None, cap=False, quota=1):
        self._session = session
        self._cap = cap
        self._quota = quota
        self.appended: list[SessionMessage] = []

    async def get(self, session_id):
        return self._session

    def at_message_cap(self, session):
        return self._cap

    async def incr_daily_quota(self, *, config_id, user_id, day):
        return self._quota

    async def append_message(self, session, message):
        self.appended.append(message)
        return AssistantSession(
            session_id=session.session_id,
            user_id=session.user_id,
            project_id=session.project_id,
            messages=(*session.messages, message),
        )

    async def create(self, *, user_id, project_id):
        return AssistantSession(session_id=uuid.uuid4(), user_id=user_id, project_id=project_id, messages=())


class _FakeConfigs:
    def __init__(self, config):
        self._config = config

    async def resolve_for_project(self, *, project_id, user_id):
        return self._config


def _make_service(*, store, configs, monkeypatch, enqueued=None):
    async def _fake_enqueue(*args, **kwargs):
        if enqueued is not None:
            enqueued.append((args, kwargs))

    monkeypatch.setattr(session_mod, "enqueue", _fake_enqueue)
    svc = SessionService.__new__(SessionService)
    svc._db = object()
    svc._store = store
    svc._configs = configs
    return svc


def _session(user_id) -> AssistantSession:
    return AssistantSession(session_id=uuid.uuid4(), user_id=user_id, project_id=uuid.uuid4(), messages=())


@pytest.mark.asyncio
async def test_post_message_unknown_session(monkeypatch) -> None:
    svc = _make_service(
        store=_FakeStore(session=None), configs=_FakeConfigs(_config()), monkeypatch=monkeypatch
    )
    with pytest.raises(SessionNotFound):
        await svc.post_message(
            session_id=uuid.uuid4(), actor_user_id=uuid.uuid4(), content="hi", editor_draft=None
        )


@pytest.mark.asyncio
async def test_post_message_cross_user_is_not_found(monkeypatch) -> None:
    owner, attacker = uuid.uuid4(), uuid.uuid4()
    svc = _make_service(
        store=_FakeStore(session=_session(owner)), configs=_FakeConfigs(_config()), monkeypatch=monkeypatch
    )
    with pytest.raises(SessionNotFound):
        await svc.post_message(
            session_id=uuid.uuid4(), actor_user_id=attacker, content="hi", editor_draft=None
        )


@pytest.mark.asyncio
async def test_post_message_at_cap(monkeypatch) -> None:
    user = uuid.uuid4()
    svc = _make_service(
        store=_FakeStore(session=_session(user), cap=True),
        configs=_FakeConfigs(_config()),
        monkeypatch=monkeypatch,
    )
    with pytest.raises(SessionLimitReached):
        await svc.post_message(session_id=uuid.uuid4(), actor_user_id=user, content="hi", editor_draft=None)


@pytest.mark.asyncio
async def test_post_message_quota_exceeded(monkeypatch) -> None:
    user = uuid.uuid4()
    # limit 50, this call is the 51st -> reject.
    svc = _make_service(
        store=_FakeStore(session=_session(user), quota=51),
        configs=_FakeConfigs(_config(limit=50)),
        monkeypatch=monkeypatch,
    )
    with pytest.raises(DailyQuotaExceeded):
        await svc.post_message(session_id=uuid.uuid4(), actor_user_id=user, content="hi", editor_draft=None)


@pytest.mark.asyncio
async def test_post_message_no_config(monkeypatch) -> None:
    user = uuid.uuid4()
    svc = _make_service(
        store=_FakeStore(session=_session(user)), configs=_FakeConfigs(None), monkeypatch=monkeypatch
    )
    with pytest.raises(AssistantUnavailable):
        await svc.post_message(session_id=uuid.uuid4(), actor_user_id=user, content="hi", editor_draft=None)


@pytest.mark.asyncio
async def test_post_message_happy_enqueues(monkeypatch) -> None:
    user = uuid.uuid4()
    enqueued: list = []
    store = _FakeStore(session=_session(user), quota=1)
    svc = _make_service(
        store=store, configs=_FakeConfigs(_config(limit=50)), monkeypatch=monkeypatch, enqueued=enqueued
    )
    await svc.post_message(
        session_id=uuid.uuid4(), actor_user_id=user, content="draft me a prompt", editor_draft="x"
    )
    assert len(enqueued) == 1
    assert enqueued[0][0][0] == "prompt_assistant_turn"
    assert store.appended[0].role == "user"


@pytest.mark.asyncio
async def test_create_session_requires_resolvable_config(monkeypatch) -> None:
    svc = _make_service(store=_FakeStore(), configs=_FakeConfigs(None), monkeypatch=monkeypatch)
    with pytest.raises(AssistantUnavailable):
        await svc.create_session(user_id=uuid.uuid4(), project_id=uuid.uuid4())
