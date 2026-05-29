"""Post-create gVisor runtime assertion for the Docker sandbox (SEC-M5).

`runtime: runsc` in the host-config is only a *request*. These tests pin the
behaviour of `_assert_runsc`, the guard that refuses to run untrusted workloads
when the spawned container did not actually land on gVisor (e.g. the daemon's
default runtime was changed, or runsc is mis-registered).
"""

from __future__ import annotations

import pytest

from contexts.agents.domain.errors import SandboxRuntimeViolation
from contexts.agents.infrastructure.sandbox.docker_runsc import DockerRunscSandbox


class _FakeContainer:
    def __init__(self, runtime: str | None, *, reload_raises: bool = False) -> None:
        self._runtime = runtime
        self._reload_raises = reload_raises
        self.killed = False
        self.removed = False
        self.attrs: dict[str, object] = {}

    def reload(self) -> None:
        if self._reload_raises:
            raise RuntimeError("inspect failed")
        self.attrs = {"HostConfig": {"Runtime": self._runtime}}

    def kill(self) -> None:
        self.killed = True

    def remove(self, *, force: bool = False) -> None:
        self.removed = True


def test_runsc_runtime_passes() -> None:
    container = _FakeContainer("runsc")
    DockerRunscSandbox._assert_runsc(container)  # must not raise
    assert container.killed is False


def test_runc_fallback_is_killed_and_rejected() -> None:
    container = _FakeContainer("runc")
    with pytest.raises(SandboxRuntimeViolation):
        DockerRunscSandbox._assert_runsc(container)
    assert container.killed is True
    assert container.removed is True


def test_missing_runtime_is_rejected() -> None:
    container = _FakeContainer(None)
    with pytest.raises(SandboxRuntimeViolation):
        DockerRunscSandbox._assert_runsc(container)
    assert container.killed is True


def test_uninspectable_container_fails_closed() -> None:
    container = _FakeContainer("runsc", reload_raises=True)
    with pytest.raises(SandboxRuntimeViolation):
        DockerRunscSandbox._assert_runsc(container)
    assert container.killed is True
