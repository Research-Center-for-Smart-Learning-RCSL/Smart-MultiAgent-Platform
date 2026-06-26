"""Unit tests for the Code-Interpreter live-kernel path.

Covers three layers without a Docker daemon:
- the in-image ``kernel.py`` ``_run`` helper (namespace persistence + artifact
  diff), loaded by path from ``deploy/`` since it ships in the sandbox image;
- ``DockerRunscSandbox._reply_to_result`` (kernel JSON reply -> ToolCallResult);
- the module-level reaper's empty-registry fast path.
"""

from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import uuid
from typing import Any

import pytest

_KERNEL_PY = (
    pathlib.Path(__file__).parents[3] / "deploy" / "sandbox" / "code-exec" / "kernel" / "kernel.py"
)


@pytest.fixture(autouse=True)
def _restore_cwd() -> Any:
    """The kernel's ``_run`` chdirs into the session dir; keep that out of the
    rest of the suite by restoring the process cwd after each test."""
    cwd = os.getcwd()
    try:
        yield
    finally:
        os.chdir(cwd)


def _load_kernel(workspace: pathlib.Path, room: str, monkeypatch: pytest.MonkeyPatch) -> Any:
    """Import a fresh copy of the in-image kernel module pointed at *workspace*."""
    monkeypatch.setenv("SMAP_KERNEL_WORKSPACE", str(workspace))
    monkeypatch.setenv("SMAP_KERNEL_ROOM", room)
    spec = importlib.util.spec_from_file_location(f"smap_kernel_{room}", _KERNEL_PY)
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_namespace_persists_across_calls(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    kernel = _load_kernel(tmp_path, "room-a", monkeypatch)
    first = kernel._run("import pandas_stub as _; total = 1 + 2", "", 5.0)
    # The import fails (no such module) but the assignment after must not run —
    # use a clean statement instead to assert persistence:
    second = kernel._run("total = 40 + 2", "", 5.0)
    third = kernel._run("print(total)", "", 5.0)
    assert second["ok"] is True
    assert third["ok"] is True
    assert third["stdout"].strip() == "42"
    # `first` failed on the bad import; that must not abort the kernel.
    assert first["ok"] is False


def test_stdin_is_readable(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    kernel = _load_kernel(tmp_path, "room-b", monkeypatch)
    res = kernel._run("import sys; print(sys.stdin.read().upper())", "hello", 5.0)
    assert res["ok"] is True
    assert res["stdout"].strip() == "HELLO"


def test_new_output_files_become_artifacts(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    kernel = _load_kernel(tmp_path, "room-c", monkeypatch)
    code = (
        "import pathlib\n"
        f"out = pathlib.Path(r'{kernel._OUTPUTS}')\n"
        "out.mkdir(parents=True, exist_ok=True)\n"
        "(out / 'chart.png').write_bytes(b'\\x89PNG fake')\n"
    )
    res = kernel._run(code, "", 5.0)
    assert res["ok"] is True
    arts = res["artifacts"]
    assert len(arts) == 1
    assert arts[0]["filename"] == "chart.png"
    assert arts[0]["mime"] == "image/png"
    assert arts[0]["b64"]  # small file inlined
    # A second call that produces nothing reports no new artifacts.
    again = kernel._run("x = 1", "", 5.0)
    assert again["artifacts"] == []


def test_error_is_captured_not_raised(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    kernel = _load_kernel(tmp_path, "room-d", monkeypatch)
    res = kernel._run("raise ValueError('boom')", "", 5.0)
    assert res["ok"] is False
    assert "boom" in res["stderr"]


# --------------------------------------------------------------------------- #
# Host-side reply parsing                                                      #
# --------------------------------------------------------------------------- #


def test_reply_to_result_parses_artifacts() -> None:
    from contexts.agents.infrastructure.sandbox.docker_runsc import DockerRunscSandbox

    art = {"filename": "f.png", "mime": "image/png", "size_bytes": 3, "rel_path": "/w/f.png", "b64": "AAA"}
    reply = {"ok": True, "stdout": "hi", "stderr": "", "artifacts": [art]}
    out = (0, json.dumps(reply).encode("utf-8"), b"")
    res = DockerRunscSandbox()._reply_to_result(out, restarted=False, start=0.0, session="a:b")
    assert res.ok is True
    assert res.stdout == "hi"
    assert res.metadata["artifacts"] == [art]
    assert res.metadata["session"] == "a:b"
    assert res.metadata["restarted"] is False


def test_reply_to_result_flags_restart_in_metadata() -> None:
    from contexts.agents.infrastructure.sandbox.docker_runsc import DockerRunscSandbox

    reply = {"ok": True, "stdout": "v", "stderr": "", "artifacts": []}
    out = (0, json.dumps(reply).encode("utf-8"), b"")
    res = DockerRunscSandbox()._reply_to_result(out, restarted=True, start=0.0, session="x")
    # The restart rides in metadata; stdout stays the kernel's clean output.
    assert res.stdout == "v"
    assert res.metadata["restarted"] is True


def test_reply_to_result_handles_non_json() -> None:
    from contexts.agents.infrastructure.sandbox.docker_runsc import DockerRunscSandbox

    out = (1, b"not json", b"traceback")
    res = DockerRunscSandbox()._reply_to_result(out, restarted=False, start=0.0, session="x")
    assert res.ok is False
    assert "non-JSON" in res.stderr


@pytest.mark.asyncio
async def test_reaper_noop_on_empty_registry() -> None:
    from contexts.agents.infrastructure.sandbox import docker_runsc as dr

    dr._KERNELS.clear()
    removed = await dr._reap_idle_kernels_once(idle_s=0.0)
    assert removed == 0


def test_kernel_container_name_is_deterministic() -> None:
    from contexts.agents.infrastructure.sandbox.docker_runsc import _kernel_container_name

    agent, room = uuid.uuid4(), uuid.uuid4()
    assert _kernel_container_name(agent, room) == f"smap-kernel-{agent}-{room}"


# --------------------------------------------------------------------------- #
# Attachment staging (Phase 2)                                                 #
# --------------------------------------------------------------------------- #


def test_safe_input_name_strips_paths_and_dots() -> None:
    from contexts.agents.infrastructure.sandbox.docker_runsc import _safe_input_name

    assert _safe_input_name("../../etc/passwd") == "passwd"
    assert _safe_input_name("data/sales.csv") == "sales.csv"
    assert _safe_input_name("..") == "file"
    assert _safe_input_name("C:\\tmp\\x.xlsx") == "x.xlsx"


def test_tar_staged_inputs_builds_dirs_and_files() -> None:
    import io
    import tarfile

    from contexts.agents.domain.mcp import StagedFile
    from contexts.agents.infrastructure.sandbox.docker_runsc import _SANDBOX_UID, _tar_staged_inputs

    files = [StagedFile(filename="a.csv", data=b"1,2,3"), StagedFile(filename="a.csv", data=b"4,5,6")]
    archive, staged = _tar_staged_inputs("sessions/room-1/inputs", files)
    # Collision-disambiguated, returned as inputs/-relative paths.
    assert staged[0] == "inputs/a.csv"
    assert staged[1] != "inputs/a.csv"
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        members = {m.name.rstrip("/"): m for m in tar.getmembers()}
    # Directory chain is present and owned by the sandbox uid.
    assert "sessions" in members
    inputs_dir = members["sessions/room-1/inputs"]
    assert inputs_dir.isdir()
    assert inputs_dir.uid == _SANDBOX_UID
    file_members = [m for m in members.values() if m.isfile()]
    assert len(file_members) == 2
    assert all(m.uid == _SANDBOX_UID and m.mode == 0o600 for m in file_members)
