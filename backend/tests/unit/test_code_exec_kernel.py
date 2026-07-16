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

_KERNEL_PY = pathlib.Path(__file__).parents[3] / "deploy" / "sandbox" / "code-exec" / "kernel" / "kernel.py"


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


def test_new_output_files_become_artifacts(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
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


def test_error_is_captured_not_raised(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
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


def tar_bytes(archive: bytes, member: str) -> bytes:
    """Content of *member* in *archive* — what the guest would actually read."""
    import io
    import tarfile

    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        f = tar.extractfile(member)
        assert f is not None, f"{member!r} not in archive"
        return f.read()


def test_tar_staged_inputs_builds_dirs_and_files() -> None:
    import io
    import tarfile

    from contexts.agents.domain.mcp import StagedFile
    from contexts.agents.infrastructure.sandbox.docker_runsc import _SANDBOX_UID, _tar_staged_inputs

    files = [StagedFile(filename="a.csv", data=b"1,2,3"), StagedFile(filename="a.csv", data=b"4,5,6")]
    archive, staged = _tar_staged_inputs("sessions/room-1/inputs", files)
    # Volume-relative, and collision-disambiguated. This asserted `inputs/a.csv`
    # until 2026-07-17: the helper hardcoded that prefix regardless of `rel_dir`,
    # so it reported a path it had not written, and this test pinned it. See
    # `test_tar_staged_inputs_returns_the_paths_it_wrote` below for the contract.
    assert staged[0] == "sessions/room-1/inputs/a.csv"
    assert staged[1] != "sessions/room-1/inputs/a.csv"
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


@pytest.mark.parametrize("rel_dir", ["agent-files", "sessions/r1/inputs"])
def test_tar_staged_inputs_returns_the_paths_it_wrote(rel_dir: str) -> None:
    """The helper's docstring promises "staged_relative_paths". It must mean it.

    The 2026-07-17 defect in one assertion: the tar member name respected
    `rel_dir` while the returned path hardcoded `inputs/`, so for
    `rel_dir="agent-files"` the caller was handed `inputs/x` for a file written
    to `agent-files/x`. `stage_kernel_inputs` was unharmed only by coincidence —
    its `rel_dir` ends in `inputs` — which is why this is parametrized over both
    callers rather than just the broken one.
    """
    import io
    import tarfile

    from contexts.agents.domain.mcp import StagedFile
    from contexts.agents.infrastructure.sandbox.docker_runsc import _tar_staged_inputs

    files = [StagedFile(filename="data.csv", data=b"1,2,3"), StagedFile(filename="notes.txt", data=b"hi")]
    archive, staged = _tar_staged_inputs(rel_dir, files)

    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        written = [m.name for m in tar.getmembers() if m.isfile()]

    assert staged == written


def test_workspace_staging_preserves_the_designers_tree() -> None:
    """AC-12. `reports/q1.csv` is the designer's layout, not a name to flatten."""
    import io
    import tarfile

    from contexts.agents.domain.mcp import StagedFile
    from contexts.agents.infrastructure.sandbox.docker_runsc import _SANDBOX_UID, _tar_staged_inputs

    files = [
        StagedFile(filename="reports/q1.csv", data=b"a"),
        StagedFile(filename="archive/q1.csv", data=b"b"),
    ]
    archive, staged = _tar_staged_inputs("agent-files", files, preserve_tree=True)

    assert staged == ["agent-files/reports/q1.csv", "agent-files/archive/q1.csv"]
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        members = {m.name.rstrip("/"): m for m in tar.getmembers()}
    # Same basename in two folders: distinct files, and neither renamed. Flattening
    # collided these into q1.csv / q1-1.csv, silently mixing up whose data is whose.
    assert tar_bytes(archive, "agent-files/reports/q1.csv") == b"a"
    assert tar_bytes(archive, "agent-files/archive/q1.csv") == b"b"
    # Intermediate dirs exist and are sandbox-owned, or extraction lands them as root.
    for d in ("agent-files", "agent-files/reports", "agent-files/archive"):
        assert members[d].isdir()
        assert members[d].uid == _SANDBOX_UID


def test_attachments_still_flatten_to_a_basename() -> None:
    """AC-13's other half. Attachment names carry no meaningful tree and are less
    trusted, so `preserve_tree` stays off for them and a nested name collapses."""
    from contexts.agents.domain.mcp import StagedFile
    from contexts.agents.infrastructure.sandbox.docker_runsc import _tar_staged_inputs

    files = [StagedFile(filename="../../etc/passwd", data=b"x")]
    _, staged = _tar_staged_inputs("sessions/r1/inputs", files)

    assert staged == ["sessions/r1/inputs/passwd"]


@pytest.mark.parametrize("bad", ["../../etc/passwd", "/etc/passwd", "reports/../../../etc/passwd", "a\x00b"])
def test_workspace_staging_rejects_traversal_rather_than_flattening_it(bad: str) -> None:
    """AC-13. Preserving the tree means the basename shortcut no longer contains
    traversal, so staging must reject it outright. The API boundary already does
    (`workspace_service._safe_workspace_path`), but the sandbox does not trust a
    DB row to have come through it."""
    from contexts.agents.domain.mcp import StagedFile
    from contexts.agents.infrastructure.sandbox.docker_runsc import _tar_staged_inputs

    with pytest.raises(ValueError):
        _tar_staged_inputs("agent-files", [StagedFile(filename=bad, data=b"x")], preserve_tree=True)


def test_file_tool_reads_both_path_forms_identically() -> None:
    """AC-9. The `file` tool is untouched by this fix and must stay that way: the
    absolute form the note now carries has to mean what the relative form meant."""
    from contexts.agents.application.tools.file_tool import _safe_relpath

    assert _safe_relpath("agent-files/x") == _safe_relpath("/workspace/agent-files/x")
    assert _safe_relpath("agent-files/x") == "/workspace/agent-files/x"
