"""prompt_assistant_turn — worker-side model resolution and quota refund (§29).

Unit-level coverage of the branches fixed in review: a null model_id must
resolve to the pinned key's provider default (never invented by the adapter
itself), the request payload must be built in the shape resolve_model()
actually accepts, and quota charged in the web process must be refunded when
the worker finds the turn can't run before any provider call. Fakes for the
DB session, config/keys/agents facades, session store, router, and
publisher — no live Postgres/Redis required.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

import app.workers.tasks.prompt_assistant as worker_mod
from contexts.keys.application.provider_router import StreamComplete, TokenDelta
from contexts.prompt_studio.domain.models import AssistantConfig, AssistantSession, PromptScope

_NOW = datetime(2026, 7, 5, tzinfo=UTC)


class _NullCtx:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeDb(_NullCtx):
    async def commit(self) -> None:
        pass


def _fake_sessionmaker():
    return lambda: _FakeDb()


def _config(*, model_id: str | None, key_id: uuid.UUID | None) -> AssistantConfig:
    return AssistantConfig(
        id=uuid.uuid4(),
        scope=PromptScope.USER,
        org_id=None,
        user_id=uuid.uuid4(),
        system_prompt="be helpful",
        key_id=key_id,
        model_id=model_id,
        daily_request_limit_per_user=50,
        enabled=True,
        hide_platform_templates=False,
        version=1,
        created_at=_NOW,
        updated_at=_NOW,
    )


def _session() -> AssistantSession:
    return AssistantSession(
        session_id=uuid.uuid4(), user_id=uuid.uuid4(), project_id=uuid.uuid4(), messages=()
    )


class _FakeStore:
    def __init__(self, session: AssistantSession) -> None:
        self._session = session
        self.appended: list = []
        self.refunds: list[tuple] = []

    async def get(self, session_id):
        return self._session

    async def append_message(self, session_id, message):
        self.appended.append(message)
        return self._session

    async def decr_daily_quota(self, *, config_id, user_id, day):
        self.refunds.append((config_id, user_id, day))
        return 0


class _FakeConfigService:
    def __init__(self, config: AssistantConfig | None) -> None:
        self._config = config

    def __call__(self, _db):
        return self

    async def resolve_for_project(self, *, project_id, user_id):
        return self._config


class _FakeKeysFacade:
    def __init__(self, key) -> None:
        self._key = key

    def __call__(self, _db):
        return self

    async def get_key(self, _key_id):
        return self._key


class _FakeAgentsFacade:
    def __call__(self, _db):
        return self

    def chat_model_catalog(self):
        return (SimpleNamespace(provider="claude", default="claude-sonnet-4-6"),)


class _FakeConfigRepo:
    def __call__(self, _db):
        return self

    async def list_files(self, _config_id):
        return []


class _FakePublisher:
    def __init__(self, _channel) -> None:
        self.events: list[tuple[str, dict]] = []

    async def emit(self, event_type, data=None):
        self.events.append((event_type, data or {}))
        return 1


@dataclass
class _FakeRouter:
    captured_request: object = None

    def call_single_key_stream(self, *, key_id, request):
        self.captured_request = request

        async def _gen():
            yield TokenDelta(text="hi")
            yield StreamComplete(result=SimpleNamespace(body={}))

        return _gen()


@dataclass
class _RaisingRouter:
    captured_request: object = None

    def call_single_key_stream(self, *, key_id, request):
        self.captured_request = request

        async def _gen():
            yield TokenDelta(text="partial")
            raise RuntimeError("provider stream dropped")
            yield  # pragma: no cover -- unreachable, satisfies generator shape

        return _gen()


def _patch_common(monkeypatch, *, config, key, store, router) -> None:
    monkeypatch.setattr(worker_mod, "get_sessionmaker", _fake_sessionmaker)
    monkeypatch.setattr(worker_mod, "get_redis", lambda: object())
    monkeypatch.setattr(worker_mod, "SessionStore", lambda _redis: store)
    monkeypatch.setattr(worker_mod, "ConfigService", _FakeConfigService(config))
    monkeypatch.setattr(worker_mod, "KeysFacade", _FakeKeysFacade(key))
    monkeypatch.setattr(worker_mod, "AgentsFacade", _FakeAgentsFacade())
    monkeypatch.setattr(worker_mod, "AssistantConfigRepository", _FakeConfigRepo())
    monkeypatch.setattr(worker_mod, "build_router", lambda _db: router)
    monkeypatch.setattr(worker_mod, "Publisher", _FakePublisher)


@pytest.mark.asyncio
async def test_null_model_id_resolves_to_provider_default(monkeypatch) -> None:
    key = SimpleNamespace(provider=SimpleNamespace(value="claude"))
    key_id = uuid.uuid4()
    config = _config(model_id=None, key_id=key_id)
    session = _session()
    store = _FakeStore(session)
    router = _FakeRouter()

    _patch_common(monkeypatch, config=config, key=key, store=store, router=router)

    result = await worker_mod.prompt_assistant_turn({}, str(session.session_id), "")

    assert result == "ok"
    assert router.captured_request.payload["model"] == "claude-sonnet-4-6"
    # Correct payload shape: a single "model" string, not a "models" list
    # that resolve_model() can never match against.
    assert "models" not in router.captured_request.payload
    assert store.refunds == []


@pytest.mark.asyncio
async def test_explicit_model_id_is_used_verbatim(monkeypatch) -> None:
    key = SimpleNamespace(provider=SimpleNamespace(value="openai"))
    config = _config(model_id="gpt-5.4-mini", key_id=uuid.uuid4())
    session = _session()
    store = _FakeStore(session)
    router = _FakeRouter()

    _patch_common(monkeypatch, config=config, key=key, store=store, router=router)

    result = await worker_mod.prompt_assistant_turn({}, str(session.session_id), "")

    assert result == "ok"
    assert router.captured_request.payload["model"] == "gpt-5.4-mini"


@pytest.mark.asyncio
async def test_pinned_key_revoked_refunds_quota(monkeypatch) -> None:
    config = _config(model_id="claude-sonnet-4-6", key_id=uuid.uuid4())
    session = _session()
    store = _FakeStore(session)
    router = _FakeRouter()

    _patch_common(monkeypatch, config=config, key=None, store=store, router=router)

    result = await worker_mod.prompt_assistant_turn({}, str(session.session_id), "")

    assert result == "pinned key revoked"
    assert store.refunds == [(config.id, session.user_id, worker_mod.now().strftime("%Y%m%d"))]
    # Q-5: a marker is persisted so a refetch after this failure sees why,
    # instead of a history ending in the user's turn with no reply at all.
    assert [(m.role, m.content, m.error) for m in store.appended] == [
        ("assistant", "prompt-studio/unavailable", True)
    ]


@pytest.mark.asyncio
async def test_no_default_for_provider_refunds_quota(monkeypatch) -> None:
    # Pinned key's provider has no catalog entry (e.g. embed-only provider) --
    # null model_id can't resolve to anything.
    key = SimpleNamespace(provider=SimpleNamespace(value="voyage"))
    config = _config(model_id=None, key_id=uuid.uuid4())
    session = _session()
    store = _FakeStore(session)
    router = _FakeRouter()

    _patch_common(monkeypatch, config=config, key=key, store=store, router=router)

    result = await worker_mod.prompt_assistant_turn({}, str(session.session_id), "")

    assert result == "no model configured"
    assert store.refunds == [(config.id, session.user_id, worker_mod.now().strftime("%Y%m%d"))]
    assert [(m.role, m.content, m.error) for m in store.appended] == [
        ("assistant", "prompt-studio/no-model", True)
    ]


@pytest.mark.asyncio
async def test_no_resolvable_config_does_not_attempt_refund(monkeypatch) -> None:
    session = _session()
    store = _FakeStore(session)
    router = _FakeRouter()

    _patch_common(monkeypatch, config=None, key=None, store=store, router=router)

    result = await worker_mod.prompt_assistant_turn({}, str(session.session_id), "")

    assert result == "no resolvable config / key revoked"
    assert store.refunds == []
    assert [(m.role, m.content, m.error) for m in store.appended] == [
        ("assistant", "prompt-studio/unavailable", True)
    ]


@pytest.mark.asyncio
async def test_provider_stream_failure_persists_marker(monkeypatch) -> None:
    key = SimpleNamespace(provider=SimpleNamespace(value="claude"))
    config = _config(model_id="claude-sonnet-4-6", key_id=uuid.uuid4())
    session = _session()
    store = _FakeStore(session)
    router = _RaisingRouter()

    _patch_common(monkeypatch, config=config, key=key, store=store, router=router)

    result = await worker_mod.prompt_assistant_turn({}, str(session.session_id), "")

    assert result == "turn failed"
    # A mid-stream provider failure never reaches the success-path append at
    # the bottom of prompt_assistant_turn -- the marker is the only record.
    assert [(m.role, m.content, m.error) for m in store.appended] == [
        ("assistant", "prompt-studio/turn-failed", True)
    ]
