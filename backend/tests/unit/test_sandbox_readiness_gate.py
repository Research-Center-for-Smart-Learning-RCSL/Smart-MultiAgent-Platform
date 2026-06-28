"""Sandbox runtime readiness gate (R12.05).

Before spawning a gVisor container the runner asks the mcp-sandbox-supervisor
whether the ``runsc`` runtime is registered host-wide, turning a missing runtime
into one clear, doc-pointing error instead of a cryptic Docker ``APIError`` per
spawn. These tests pin the verdict mapping and the deliberate posture: the gate
is *not* the security boundary (``_assert_runsc`` already fails closed per
container), so an unreachable supervisor fails **open** and only an explicit
``503`` fails **closed**.
"""

from __future__ import annotations

from collections.abc import Iterator

import httpx
import pytest

import contexts.agents.infrastructure.sandbox.docker_runsc as dr
from contexts.agents.domain.errors import SandboxRuntimeViolation
from contexts.agents.infrastructure.sandbox.docker_runsc import DockerRunscSandbox


@pytest.fixture(autouse=True)
def _reset_gate_cache() -> Iterator[None]:
    """The healthy/skipped verdict is cached process-wide; isolate each test."""
    dr._runtime_ready_at = None
    yield
    dr._runtime_ready_at = None


def _mock_httpx(monkeypatch: pytest.MonkeyPatch, handler: object) -> None:
    """Route the gate's ad-hoc AsyncClient through a MockTransport."""
    transport = httpx.MockTransport(handler)  # type: ignore[arg-type]
    real_client = httpx.AsyncClient

    def factory(*args: object, **kwargs: object) -> httpx.AsyncClient:
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(httpx, "AsyncClient", factory)


# --- pure verdict mapping --------------------------------------------------- #


def test_classify_200_is_healthy() -> None:
    assert DockerRunscSandbox._classify_supervisor_status(200, "") == (True, "ok")


def test_classify_503_is_fail_closed_with_detail() -> None:
    verdict, detail = DockerRunscSandbox._classify_supervisor_status(503, "runsc missing")
    assert verdict is False
    assert "runsc missing" in detail


def test_classify_503_without_detail_has_actionable_fallback() -> None:
    verdict, detail = DockerRunscSandbox._classify_supervisor_status(503, "")
    assert verdict is False
    assert detail  # non-empty so the operator gets something to act on


def test_classify_unexpected_status_fails_open() -> None:
    verdict, _ = DockerRunscSandbox._classify_supervisor_status(404, "")
    assert verdict is None  # misrouted URL must not wedge sandboxing


# --- gate behaviour --------------------------------------------------------- #


@pytest.mark.asyncio
async def test_gate_disabled_when_unconfigured(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _boom(self: DockerRunscSandbox) -> tuple[bool | None, str]:
        raise AssertionError("must not probe when supervisor_url is empty")

    monkeypatch.setattr(DockerRunscSandbox, "_probe_supervisor", _boom)
    await DockerRunscSandbox(supervisor_url="")._ensure_runtime_ready()  # no raise


@pytest.mark.asyncio
async def test_gate_passes_and_caches_when_healthy(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _ok(self: DockerRunscSandbox) -> tuple[bool | None, str]:
        return True, "ok"

    monkeypatch.setattr(DockerRunscSandbox, "_probe_supervisor", _ok)
    await DockerRunscSandbox(supervisor_url="http://sup:9090")._ensure_runtime_ready()
    assert dr._runtime_ready_at is not None


@pytest.mark.asyncio
async def test_gate_blocks_on_explicit_unhealthy(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _unhealthy(self: DockerRunscSandbox) -> tuple[bool | None, str]:
        return False, "gVisor runsc runtime not registered"

    monkeypatch.setattr(DockerRunscSandbox, "_probe_supervisor", _unhealthy)
    with pytest.raises(SandboxRuntimeViolation) as exc:
        await DockerRunscSandbox(supervisor_url="http://sup:9090")._ensure_runtime_ready()
    assert "runsc" in str(exc.value)
    # Not cached: an operator installing gVisor is picked up on the next spawn.
    assert dr._runtime_ready_at is None


@pytest.mark.asyncio
async def test_gate_fails_open_on_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _unreachable(self: DockerRunscSandbox) -> tuple[bool | None, str]:
        return None, "connection refused"

    monkeypatch.setattr(DockerRunscSandbox, "_probe_supervisor", _unreachable)
    await DockerRunscSandbox(supervisor_url="http://sup:9090")._ensure_runtime_ready()  # no raise
    # Cached so a down supervisor is not hammered on every spawn.
    assert dr._runtime_ready_at is not None


@pytest.mark.asyncio
async def test_gate_caches_probe_within_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    async def _count(self: DockerRunscSandbox) -> tuple[bool | None, str]:
        calls["n"] += 1
        return True, "ok"

    monkeypatch.setattr(DockerRunscSandbox, "_probe_supervisor", _count)
    sb = DockerRunscSandbox(supervisor_url="http://sup:9090")
    await sb._ensure_runtime_ready()
    await sb._ensure_runtime_ready()
    assert calls["n"] == 1


# --- HTTP probe (transport-level) ------------------------------------------ #


@pytest.mark.asyncio
async def test_probe_maps_200_to_healthy(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_httpx(monkeypatch, lambda req: httpx.Response(200, json={"status": "ok"}))
    verdict, detail = await DockerRunscSandbox(supervisor_url="http://sup:9090")._probe_supervisor()
    assert (verdict, detail) == (True, "ok")


@pytest.mark.asyncio
async def test_probe_maps_503_and_extracts_detail(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_httpx(
        monkeypatch,
        lambda req: httpx.Response(503, json={"status": "error", "detail": "runsc not registered"}),
    )
    verdict, detail = await DockerRunscSandbox(supervisor_url="http://sup:9090")._probe_supervisor()
    assert verdict is False
    assert "runsc not registered" in detail


@pytest.mark.asyncio
async def test_probe_fails_open_on_connect_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def _refuse(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    _mock_httpx(monkeypatch, _refuse)
    verdict, _ = await DockerRunscSandbox(supervisor_url="http://sup:9090")._probe_supervisor()
    assert verdict is None
