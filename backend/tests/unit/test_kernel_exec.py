"""The session kernel's exec: what reaches the daemon, and what a failure means.

`code_exec` in a chat room never ran from 2026-06-27 to 2026-10-11 because the
exec user was an int (a 400) and the socket proxy denied `/exec` (a 403), and
both surfaced only as "kernel unavailable": every exec error was read as a dead
kernel, with nothing logged. See
docs/tasks/2026-07-19-large-artifacts-silently-dropped/spec.md D-11, D-13.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, ClassVar

import docker
import pytest
import requests
from docker.models.containers import Container

from contexts.agents.infrastructure.sandbox import docker_runsc as ds


class _Sent(Exception):
    pass


def test_the_kernel_exec_sends_its_user_as_a_string(monkeypatch: pytest.MonkeyPatch) -> None:
    """Asserted against the SDK's real request body: the SDK stringifies
    `user` on container create but not on exec create, and the daemon decodes
    the exec `User` into a Go string. Every fake `exec_run` accepts any type."""
    body: dict[str, Any] = {}

    def capture(self: Any, url: str, data: Any = None, **_kw: Any) -> Any:
        body.update(data)
        raise _Sent

    monkeypatch.setattr(docker.APIClient, "_post_json", capture)
    client = docker.DockerClient.__new__(docker.DockerClient)
    client.api = docker.APIClient(base_url="tcp://127.0.0.1:1", version="1.43")
    container = Container(attrs={"Id": "kernel", "State": {"Running": True}}, client=client)
    monkeypatch.setattr(ds, "_get_container_quietly", lambda _client, _ref: container)

    with pytest.raises(ds._KernelExecFailed):
        ds.DockerRunscSandbox()._kernel_exec_call(client, "kernel", "print(1)", "", 30.0)

    assert body, "exec create no longer goes through APIClient._post_json; re-point this capture"
    assert body["User"] == str(ds._SANDBOX_UID)


def _api_error(status: int) -> docker.errors.APIError:
    response = requests.Response()
    response.status_code = status
    cls = docker.errors.NotFound if status == 404 else docker.errors.APIError
    return cls(f"{status} from the daemon", response=response)


class _Container:
    attrs: ClassVar[dict[str, Any]] = {"State": {"Running": True}}
    status = "running"

    def __init__(self, error: Exception) -> None:
        self._error = error

    def exec_run(self, *_a: Any, **_kw: Any) -> Any:
        raise self._error


@pytest.mark.parametrize("status", [404, 409])
def test_a_missing_or_stopped_kernel_is_gone(monkeypatch: pytest.MonkeyPatch, status: int) -> None:
    monkeypatch.setattr(ds, "_get_container_quietly", lambda _c, _r: _Container(_api_error(status)))

    with pytest.raises(ds._KernelGone):
        ds.DockerRunscSandbox()._kernel_exec_call(object(), "kernel", "print(1)", "", 30.0)


@pytest.mark.parametrize("status", [400, 403, 500])
def test_a_refused_exec_is_not_a_dead_kernel(monkeypatch: pytest.MonkeyPatch, status: int) -> None:
    monkeypatch.setattr(ds, "_get_container_quietly", lambda _c, _r: _Container(_api_error(status)))

    with pytest.raises(ds._KernelExecFailed):
        ds.DockerRunscSandbox()._kernel_exec_call(object(), "kernel", "print(1)", "", 30.0)


class _Session:
    """Drives `_run_code_exec_kernel` with scripted kernel lookups and exec outcomes."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, statuses: list[str], outcomes: list[Any]) -> None:
        self.lookups = 0
        self.discarded = 0
        sandbox = ds.DockerRunscSandbox

        async def get_or_create(_self: Any, _client: Any, **_kw: Any) -> Any:
            status = statuses[self.lookups]
            self.lookups += 1
            return ds._KernelHandle("cid", 0.0), status

        def exec_call(_self: Any, *_a: Any) -> Any:
            outcome = outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        async def discard(_self: Any, *_a: Any) -> None:
            self.discarded += 1

        monkeypatch.setattr(sandbox, "_client", lambda _self: object())
        monkeypatch.setattr(sandbox, "_get_or_create_kernel", get_or_create)
        monkeypatch.setattr(sandbox, "_kernel_exec_call", exec_call)
        monkeypatch.setattr(sandbox, "_discard_kernel_key", discard)

    async def run(self) -> Any:
        return await ds.DockerRunscSandbox()._run_code_exec_kernel(
            agent_id=uuid.uuid4(), chatroom_id=uuid.uuid4(), source="print(1)", stdin="", timeout_s=30.0
        )


_REPLY = (0, b'{"protocol": %d, "ok": true, "stdout": "1\\n"}' % ds._KERNEL_PROTOCOL_VERSION, b"")


async def test_a_refused_exec_is_logged_with_its_cause_and_not_retried(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A proxy 403 is deterministic: retrying it as a dead kernel only doubled
    the cost, and reporting it as "kernel unavailable" with nothing logged is
    how two deployment faults stayed invisible for three and a half months."""
    cause = _api_error(403)
    session = _Session(monkeypatch, ["reused"], [ds._KernelExecFailed("cid", cause)])

    with caplog.at_level(logging.WARNING, logger="smap.sandbox"):
        res = await session.run()

    assert res.ok is False
    assert res.stderr != "kernel unavailable"
    assert "not a problem with your code" in res.stderr
    assert session.lookups == 1
    assert session.discarded == 0
    assert any("403" in r.getMessage() for r in caplog.records)


async def test_readopting_the_same_live_kernel_is_not_a_restart(monkeypatch: pytest.MonkeyPatch) -> None:
    """The retry looks the kernel up by name; finding it still running means its
    state survived, and telling the model otherwise makes it redo its setup."""
    session = _Session(monkeypatch, ["reused", "reused"], [ds._KernelGone("cid"), _REPLY])

    res = await session.run()

    assert res.ok is True
    assert res.metadata["restarted"] is False


async def test_a_kernel_replaced_after_dying_is_a_restart(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _Session(monkeypatch, ["reused", "created"], [ds._KernelGone("cid"), _REPLY])

    res = await session.run()

    assert res.metadata["restarted"] is True
