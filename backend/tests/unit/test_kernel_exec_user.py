"""The kernel exec must reach the daemon with a string user.

The Docker SDK stringifies ``user`` when it creates a container but passes it
through unchanged when it creates an exec, and the daemon decodes ``User`` into
a Go string: an int there is a 400 on every call, which the kernel path reports
as "kernel unavailable". Asserted against the SDK's real request body, because
a fake ``exec_run`` accepts whatever it is given.
"""

from __future__ import annotations

from typing import Any

import docker
import pytest
from docker.models.containers import Container

from contexts.agents.infrastructure.sandbox import docker_runsc as ds


class _Sent(Exception):
    pass


def test_the_kernel_exec_sends_its_user_as_a_string(monkeypatch: pytest.MonkeyPatch) -> None:
    body: dict[str, Any] = {}

    def capture(self: Any, url: str, data: Any = None, **_kw: Any) -> Any:
        body.update(data)
        raise _Sent

    monkeypatch.setattr(docker.APIClient, "_post_json", capture)
    client = docker.DockerClient.__new__(docker.DockerClient)
    client.api = docker.APIClient(base_url="tcp://127.0.0.1:1", version="1.43")
    container = Container(attrs={"Id": "kernel", "State": {"Running": True}}, client=client)
    monkeypatch.setattr(ds, "_get_container_quietly", lambda _client, _ref: container)

    with pytest.raises(ds._KernelGone):
        ds.DockerRunscSandbox()._kernel_exec_call(client, "kernel", "print(1)", "", 30.0)

    assert body["User"] == str(ds._SANDBOX_UID)
