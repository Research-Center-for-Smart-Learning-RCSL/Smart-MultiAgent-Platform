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
        self.started = False
        self.attrs: dict[str, object] = {}

    def reload(self) -> None:
        if self._reload_raises:
            raise RuntimeError("inspect failed")
        self.attrs = {"HostConfig": {"Runtime": self._runtime}}

    def kill(self) -> None:
        self.killed = True

    def remove(self, *, force: bool = False) -> None:
        self.removed = True

    def start(self) -> None:
        self.started = True


class _FakeClient:
    def __init__(self, container: _FakeContainer) -> None:
        self._container = container
        self.create_kwargs: dict | None = None

    class _Containers:
        def __init__(self, outer: _FakeClient) -> None:
            self._outer = outer

        def create(self, **kwargs):
            self._outer.create_kwargs = kwargs
            return self._outer._container

    @property
    def containers(self) -> _FakeClient._Containers:
        return _FakeClient._Containers(self)


@pytest.mark.asyncio
async def test_runsc_runtime_passes() -> None:
    container = _FakeContainer("runsc")
    await DockerRunscSandbox._assert_runsc(container)  # must not raise
    assert container.killed is False


@pytest.mark.asyncio
async def test_runc_fallback_is_killed_and_rejected() -> None:
    container = _FakeContainer("runc")
    with pytest.raises(SandboxRuntimeViolation):
        await DockerRunscSandbox._assert_runsc(container)
    assert container.killed is True
    assert container.removed is True


@pytest.mark.asyncio
async def test_missing_runtime_is_rejected() -> None:
    container = _FakeContainer(None)
    with pytest.raises(SandboxRuntimeViolation):
        await DockerRunscSandbox._assert_runsc(container)
    assert container.killed is True


@pytest.mark.asyncio
async def test_uninspectable_container_fails_closed() -> None:
    container = _FakeContainer("runsc", reload_raises=True)
    with pytest.raises(SandboxRuntimeViolation):
        await DockerRunscSandbox._assert_runsc(container)
    assert container.killed is True


@pytest.mark.asyncio
async def test_create_verified_never_starts_on_wrong_runtime() -> None:
    # SEC-M5 / #32: the gVisor check happens BEFORE start, so a container that
    # lands on the wrong runtime is removed without ever executing the workload.
    container = _FakeContainer("runc")
    client = _FakeClient(container)
    with pytest.raises(SandboxRuntimeViolation):
        await DockerRunscSandbox()._create_verified(client, image="x", command=["c"])
    assert container.started is False
    assert container.removed is True


@pytest.mark.asyncio
async def test_create_verified_returns_unstarted_container_when_runsc() -> None:
    container = _FakeContainer("runsc")
    client = _FakeClient(container)
    out = await DockerRunscSandbox()._create_verified(client, image="x", command=["c"])
    # Verified but NOT started — the caller starts it after any pre-start setup.
    assert out is container
    assert container.started is False
    assert container.killed is False
