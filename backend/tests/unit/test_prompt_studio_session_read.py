"""Route behavior for the prompt-assistant session read endpoint (F-13 fix).

Ownership/not-found logic itself is exercised end-to-end here through a real
`SessionService.require_owned_session` (backed by a fake store), not mocked
away -- this is "the gap" the spec calls out: there was no test anywhere
under backend/tests/ exercising the session HTTP routes before this fix. The
route's own job -- calling that method and mapping the result to the
documented response shape -- is what these tests pin.

"unauthenticated -> 401" is not exercised here: it is enforced by the shared
current_principal FastAPI dependency, which every authenticated route uses
identically and which direct function-call tests (this project's convention
for tests/unit/ route tests, see test_chatroom_approvals_read.py) bypass by
construction.
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest

from app.api.v1 import prompt_studio
from contexts.prompt_studio.application.session_service import SessionService
from contexts.prompt_studio.domain.errors import SessionNotFound
from contexts.prompt_studio.domain.models import AssistantSession, SessionMessage
from shared_kernel.auth.permissions import Principal


class _FakeStore:
    """Minimal SessionStore stand-in: require_owned_session only reaches .get()."""

    def __init__(self, session: AssistantSession | None) -> None:
        self._session = session

    async def get(self, session_id: uuid.UUID) -> AssistantSession | None:
        return self._session


def _service(session: AssistantSession | None) -> SessionService:
    svc = SessionService.__new__(SessionService)
    svc._db = object()
    svc._store = _FakeStore(session)
    return svc


def _principal(user_id: uuid.UUID) -> Principal:
    return Principal(user_id=user_id, is_admin=False, email_verified=True)


async def test_owner_reads_own_session_in_order(monkeypatch: pytest.MonkeyPatch) -> None:
    owner = uuid.uuid4()
    session_id = uuid.uuid4()
    session = AssistantSession(
        session_id=session_id,
        user_id=owner,
        project_id=uuid.uuid4(),
        messages=(
            SessionMessage(role="user", content="draft me a prompt"),
            SessionMessage(role="assistant", content="here you go"),
        ),
    )
    monkeypatch.setattr(prompt_studio, "SessionService", lambda _db: _service(session))

    result = await prompt_studio.get_session(
        session_id=session_id, principal=_principal(owner), db=MagicMock()
    )

    assert result.session_id == session_id
    assert [(m.role, m.content, m.error) for m in result.messages] == [
        ("user", "draft me a prompt", False),
        ("assistant", "here you go", False),
    ]


async def test_error_marker_surfaces_its_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    owner = uuid.uuid4()
    session_id = uuid.uuid4()
    session = AssistantSession(
        session_id=session_id,
        user_id=owner,
        project_id=uuid.uuid4(),
        messages=(
            SessionMessage(role="user", content="draft me a prompt"),
            SessionMessage(role="assistant", content="prompt-studio/turn-failed", error=True),
        ),
    )
    monkeypatch.setattr(prompt_studio, "SessionService", lambda _db: _service(session))

    result = await prompt_studio.get_session(
        session_id=session_id, principal=_principal(owner), db=MagicMock()
    )

    assert result.messages[-1].error is True
    assert result.messages[-1].content == "prompt-studio/turn-failed"


async def test_non_owner_is_not_found_not_forbidden(monkeypatch: pytest.MonkeyPatch) -> None:
    owner, attacker = uuid.uuid4(), uuid.uuid4()
    session_id = uuid.uuid4()
    session = AssistantSession(session_id=session_id, user_id=owner, project_id=uuid.uuid4(), messages=())
    monkeypatch.setattr(prompt_studio, "SessionService", lambda _db: _service(session))

    # Pins the deliberate not-found/wrong-owner collapse (session_service.py:80-81):
    # a non-owner gets the identical SessionNotFound a nonexistent session would,
    # mapped to 404 (never 403) by error_mapping.py:64 -- so this route can never
    # become an existence oracle for another user's session. A platform admin
    # gets no special treatment either (Q-4): the principal here is a plain,
    # non-admin user, and the route never checks an is_admin flag at all.
    with pytest.raises(SessionNotFound):
        await prompt_studio.get_session(session_id=session_id, principal=_principal(attacker), db=MagicMock())


async def test_expired_session_is_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    # An expired session is indistinguishable from one that never existed --
    # store.get() returns None once its TTL elapses (session_store.py:96).
    monkeypatch.setattr(prompt_studio, "SessionService", lambda _db: _service(None))

    with pytest.raises(SessionNotFound):
        await prompt_studio.get_session(
            session_id=uuid.uuid4(), principal=_principal(uuid.uuid4()), db=MagicMock()
        )


async def test_response_carries_no_key_material(monkeypatch: pytest.MonkeyPatch) -> None:
    # Guards R29.14 against a future widening of the response model: the
    # projection is explicit (role/content/error only), so a new field added
    # to AssistantSession or SessionMessage cannot leak by default.
    owner = uuid.uuid4()
    session_id = uuid.uuid4()
    session = AssistantSession(
        session_id=session_id,
        user_id=owner,
        project_id=uuid.uuid4(),
        messages=(SessionMessage(role="assistant", content="reply"),),
    )
    monkeypatch.setattr(prompt_studio, "SessionService", lambda _db: _service(session))

    result = await prompt_studio.get_session(
        session_id=session_id, principal=_principal(owner), db=MagicMock()
    )

    assert set(result.model_fields.keys()) == {"session_id", "messages"}
    assert set(result.messages[0].model_fields.keys()) == {"role", "content", "error"}
