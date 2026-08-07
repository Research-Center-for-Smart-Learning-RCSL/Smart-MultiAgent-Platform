"""The request transaction must commit before the response reaches the client.

FastAPI registers yield-dependency teardown on ``fastapi_inner_astack``, which
unwinds only after ``await response(scope, receive, send)``. A commit placed
there returns 2xx while the rows are still uncommitted, so a client that reads
straight after its own write can miss it. ``db_session`` therefore commits on
``fastapi_function_astack`` instead. These tests pin that ordering — without
them the fix reverts silently, because every assertion about the response body
still passes either way.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import patch

import pytest
from fastapi import Depends, FastAPI
from fastapi.responses import PlainTextResponse
from fastapi.testclient import TestClient

from shared_kernel.db.session import db_session


class _RecordingSession:
    """Minimal AsyncSession stand-in that logs transaction calls in order."""

    def __init__(self, log: list[str]) -> None:
        self._log = log
        self.info: dict[str, Any] = {}

    async def commit(self) -> None:
        self._log.append("commit")

    async def rollback(self) -> None:
        self._log.append("rollback")

    async def close(self) -> None:
        self._log.append("close")


class _MarkingResponse(PlainTextResponse):
    """Records the moment the response bytes are handed to the ASGI server."""

    log: list[str]

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        self.log.append("send")
        await super().__call__(scope, receive, send)


def _app(log: list[str], *, fail: bool = False) -> FastAPI:
    session = _RecordingSession(log)

    @asynccontextmanager
    async def _sessionmaker_call() -> AsyncIterator[_RecordingSession]:
        try:
            yield session
        finally:
            await session.close()

    def _fake_get_sessionmaker() -> Any:
        return _sessionmaker_call

    app = FastAPI()

    class _Response(_MarkingResponse):
        pass

    _Response.log = log

    @app.get("/probe", response_class=_Response)
    async def _probe(db: Any = Depends(db_session)) -> str:
        assert db is session
        if fail:
            raise RuntimeError("endpoint blew up")
        return "ok"

    app.state.patcher = patch(
        "shared_kernel.db.session.get_sessionmaker",
        _fake_get_sessionmaker,
    )
    return app


def test_commit_precedes_response_send() -> None:
    log: list[str] = []
    app = _app(log)
    with app.state.patcher:
        response = TestClient(app).get("/probe")

    assert response.status_code == 200
    assert "commit" in log, log
    assert "send" in log, log
    # The whole point: the rows are durable before the client can act on the 200.
    assert log.index("commit") < log.index("send"), log


def test_endpoint_failure_rolls_back_before_send() -> None:
    log: list[str] = []
    app = _app(log, fail=True)
    with app.state.patcher, pytest.raises(RuntimeError):
        TestClient(app).get("/probe")

    assert "rollback" in log, log
    assert "commit" not in log, log
