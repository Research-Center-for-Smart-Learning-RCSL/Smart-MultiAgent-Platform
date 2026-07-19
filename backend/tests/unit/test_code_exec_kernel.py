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

# Read from the backend, not hardcoded: a bump on one side of the contract must
# not be able to leave these tests asserting the other side's old value.
from contexts.agents.infrastructure.sandbox.docker_runsc import _KERNEL_PROTOCOL_VERSION as _PROTOCOL

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
    """Import a fresh copy of the in-image kernel module with its own session dir.

    *room* only keeps the module name and the session dir distinct per test; the
    kernel no longer derives either from a room id, since isolation now comes
    from the mount rather than from a path segment ([R12.03b]).
    """
    session = workspace / room
    monkeypatch.setenv("SMAP_KERNEL_SESSION", str(session))
    spec = importlib.util.spec_from_file_location(f"smap_kernel_{room}", _KERNEL_PY)
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    # Harness guard: an env var the kernel stopped reading would silently send
    # every one of these tests to the real "/session" on the host, where they
    # would still pass. Caught exactly that way once.
    assert session == mod._SESSION_DIR
    return mod


def test_session_dir_comes_from_its_own_mount_not_the_workspace(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T-3. The session dir is a mount of its own, not a subdirectory of /workspace.

    Room isolation is established by what the container mounts
    (2026-07-19-session-dir-room-isolation): the per-agent volume carries no
    room-scoped data, so deriving the session dir from it would re-open the
    channel the mount split closes.
    """
    workspace = tmp_path / "workspace"
    session = tmp_path / "session"
    monkeypatch.setenv("SMAP_KERNEL_WORKSPACE", str(workspace))
    monkeypatch.setenv("SMAP_KERNEL_SESSION", str(session))
    spec = importlib.util.spec_from_file_location("smap_kernel_session_root", _KERNEL_PY)
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    assert session / "inputs" == mod._INPUTS
    assert session / "outputs" == mod._OUTPUTS
    assert workspace not in mod._INPUTS.parents
    assert workspace not in mod._OUTPUTS.parents


def test_session_dir_defaults_to_the_session_mount_point(monkeypatch: pytest.MonkeyPatch) -> None:
    """The default must match what `_create_kernel` binds, or nothing resolves."""
    monkeypatch.delenv("SMAP_KERNEL_SESSION", raising=False)
    monkeypatch.delenv("SMAP_KERNEL_WORKSPACE", raising=False)
    spec = importlib.util.spec_from_file_location("smap_kernel_default_session", _KERNEL_PY)
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    # Compare Path to Path: on a Windows host str() yields "\\session" for the
    # same posix path the Linux image will see.
    assert pathlib.Path("/session") == mod._SESSION_DIR


def test_kernel_and_backend_agree_on_the_protocol_version(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC-10. The two halves of the contract are versioned together.

    This is the assertion the stamp exists for: the image and the backend ship
    separately, and nothing in CI runs a container, so the repo itself is the
    only place the two can be compared. Bumping one side alone fails here.
    """
    kernel = _load_kernel(tmp_path, "protocol", monkeypatch)
    assert kernel.PROTOCOL_VERSION == _PROTOCOL
    # And the stamp is actually on the wire, not merely declared.
    assert kernel._run("pass", "", 5.0)["protocol"] == _PROTOCOL


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


def test_oversized_output_is_described_but_not_inlined(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T-1. Above the cap the kernel emits a descriptor with no payload.

    The branch had never been exercised, which is why nothing noticed that the
    second transport tier it hands off to was never built: `rel_path` and
    `size_bytes` are populated precisely so the host can go and fetch the file,
    and until now the host just dropped it.
    """
    kernel = _load_kernel(tmp_path, "oversized", monkeypatch)
    monkeypatch.setattr(kernel, "_ARTIFACT_B64_CAP", 1024)
    code = (
        "import pathlib\n"
        f"out = pathlib.Path(r'{kernel._OUTPUTS}')\n"
        "out.mkdir(parents=True, exist_ok=True)\n"
        "(out / 'big.bin').write_bytes(b'x' * 4096)\n"
    )

    res = kernel._run(code, "", 5.0)

    assert res["ok"] is True
    (art,) = res["artifacts"]
    assert art["b64"] is None
    # The fields the host needs to fetch it must survive, or the descriptor is
    # useless and the file is unrecoverable.
    assert art["size_bytes"] == 4096
    assert art["filename"] == "big.bin"
    assert art["rel_path"].endswith("big.bin")


class TestSingleMemberTarExtraction:
    """`_single_member_tar_bytes` unpacks what `get_archive` returns.

    The stream describes a path the agent's own code controls, so its shape is
    not a given: a symlink or a directory where a file was expected must yield
    nothing rather than something surprising.
    """

    @staticmethod
    def _tar(name: str, data: bytes, *, kind: int | None = None) -> list[bytes]:
        import io as _io
        import tarfile

        buf = _io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tar:
            info = tarfile.TarInfo(name)
            if kind is not None:
                info.type = kind
                info.size = 0
                tar.addfile(info)
            else:
                info.size = len(data)
                tar.addfile(info, _io.BytesIO(data))
        return [buf.getvalue()]

    def test_extracts_a_regular_file(self) -> None:
        from contexts.agents.infrastructure.sandbox.docker_runsc import _single_member_tar_bytes

        stream = self._tar("big.bin", b"payload")
        assert _single_member_tar_bytes(stream, 1024) == b"payload"

    def test_refuses_a_member_larger_than_the_ceiling(self) -> None:
        """Truncating would hand back a corrupt artifact that looks fine."""
        from contexts.agents.infrastructure.sandbox.docker_runsc import _single_member_tar_bytes

        stream = self._tar("big.bin", b"x" * 100)
        assert _single_member_tar_bytes(stream, 10) is None

    def test_refuses_a_path_outside_the_session_mount(self) -> None:
        """The descriptor naming this path comes from a container that runs
        agent-authored code in the kernel's own process, so the agent can dictate
        it. Without this the host would `get_archive` any path the agent names and
        upload it into the room as an attachment."""
        from contexts.agents.infrastructure.sandbox.docker_runsc import _safe_session_path

        for hostile in (
            "/etc/passwd",
            "/workspace/agent-files/other.csv",
            "/session/../workspace/secret",
            "outputs/relative.png",
            "",
            "/session/ok\x00.png",
        ):
            assert _safe_session_path(hostile) is None, hostile

    def test_allows_a_genuine_session_output(self) -> None:
        from contexts.agents.infrastructure.sandbox.docker_runsc import _safe_session_path

        assert _safe_session_path("/session/outputs/chart.png") == "/session/outputs/chart.png"
        # Normalised, not merely accepted.
        assert _safe_session_path("/session/outputs/./chart.png") == "/session/outputs/chart.png"

    def test_ignores_a_non_file_member(self) -> None:
        import tarfile

        from contexts.agents.infrastructure.sandbox.docker_runsc import _single_member_tar_bytes

        stream = self._tar("outputs", b"", kind=tarfile.DIRTYPE)
        assert _single_member_tar_bytes(stream, 1024) is None


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
    reply = {"protocol": _PROTOCOL, "ok": True, "stdout": "hi", "stderr": "", "artifacts": [art]}
    out = (0, json.dumps(reply).encode("utf-8"), b"")
    res = DockerRunscSandbox()._reply_to_result(out, restarted=False, start=0.0, session="a:b")
    assert res.ok is True
    assert res.stdout == "hi"
    assert res.metadata["artifacts"] == [art]
    assert res.metadata["session"] == "a:b"
    assert res.metadata["restarted"] is False


def test_reply_to_result_flags_restart_in_metadata() -> None:
    from contexts.agents.infrastructure.sandbox.docker_runsc import DockerRunscSandbox

    reply = {"protocol": _PROTOCOL, "ok": True, "stdout": "v", "stderr": "", "artifacts": []}
    out = (0, json.dumps(reply).encode("utf-8"), b"")
    res = DockerRunscSandbox()._reply_to_result(out, restarted=True, start=0.0, session="x")
    # The restart rides in metadata; stdout stays the kernel's clean output.
    assert res.stdout == "v"
    assert res.metadata["restarted"] is True


@pytest.mark.parametrize(
    ("stamp", "case"),
    [
        (None, "old image: predates the stamp entirely"),
        (_PROTOCOL - 1, "old image: stages inputs where this kernel does not look"),
        (_PROTOCOL + 1, "new image: expects a mount this backend does not bind"),
    ],
)
def test_reply_to_result_refuses_a_mismatched_kernel(stamp: int | None, case: str) -> None:
    """AC-10. A backend/image mismatch fails loudly instead of reading wrong paths.

    Both directions are silent without this: attachments staged where the kernel
    never looks, or a session mount that was never bound. Both reach the user as
    "the agent ignored my file", which is indistinguishable from a model failure.
    """
    from contexts.agents.infrastructure.sandbox.docker_runsc import DockerRunscSandbox

    reply: dict[str, Any] = {"ok": True, "stdout": "leaked", "stderr": "", "artifacts": []}
    if stamp is not None:
        reply["protocol"] = stamp
    out = (0, json.dumps(reply).encode("utf-8"), b"")
    res = DockerRunscSandbox()._reply_to_result(out, restarted=False, start=0.0, session="x")

    assert res.ok is False, case
    assert res.metadata["protocol_mismatch"] is True
    # The kernel's output must not be passed off as a successful result.
    assert "leaked" not in res.stdout
    # The message has to point at the deployment, or the operator debugs the model.
    assert "image" in res.stderr


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


def test_a_file_does_not_take_a_name_another_file_needs_as_a_folder() -> None:
    """Both `reports` and `reports/q1.csv` are legal, distinct uploads. Emitting a
    file member and a directory member for the same path makes extraction fail, and
    staging faults are swallowed to protect the turn — so this would have dropped
    every workspace file for the agent, silently, over one odd upload.
    """
    import io
    import tarfile

    from contexts.agents.domain.mcp import StagedFile
    from contexts.agents.infrastructure.sandbox.docker_runsc import _tar_staged_inputs

    files = [
        StagedFile(filename="reports", data=b"file-not-folder"),
        StagedFile(filename="reports/q1.csv", data=b"in-folder"),
    ]
    archive, staged = _tar_staged_inputs("agent-files", files, preserve_tree=True)

    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        kinds = {m.name.rstrip("/"): ("dir" if m.isdir() else "file") for m in tar.getmembers()}
    # No path is claimed as both, and every reported path exists as a file.
    assert kinds["agent-files/reports"] == "dir"
    assert staged[1] == "agent-files/reports/q1.csv"
    assert staged[0] != "agent-files/reports"
    assert kinds[staged[0]] == "file"
    assert tar_bytes(archive, staged[0]) == b"file-not-folder"
    assert tar_bytes(archive, staged[1]) == b"in-folder"


def test_attachments_still_flatten_to_a_basename() -> None:
    """AC-13's other half. Attachment names carry no meaningful tree and are less
    trusted, so `preserve_tree` stays off for them and a nested name collapses."""
    from contexts.agents.domain.mcp import StagedFile
    from contexts.agents.infrastructure.sandbox.docker_runsc import _tar_staged_inputs

    files = [StagedFile(filename="../../etc/passwd", data=b"x")]
    _, staged = _tar_staged_inputs("sessions/r1/inputs", files)

    assert staged == ["sessions/r1/inputs/passwd"]


@pytest.mark.parametrize(
    "bad",
    ["../../etc/passwd", "/etc/passwd", "reports/../../../etc/passwd", "a\x00b", "a\nb.csv"],
)
def test_the_shared_rule_rejects_unstageable_paths(bad: str) -> None:
    """AC-13. Preserving the tree means the basename shortcut no longer strips
    traversal, so the rule must reject it outright rather than rewrite it into
    something plausible. `a\\nb.csv` is here because these paths are interpolated
    into the one-line note the model reads: a newline would let an upload forge a
    prompt section."""
    from shared_kernel.storage.sanitize import safe_workspace_relpath

    with pytest.raises(ValueError):
        safe_workspace_relpath(bad)


def test_one_unstageable_path_costs_one_file_not_all_of_them() -> None:
    """The rule raises; staging must not. Callers swallow staging faults to keep
    the turn alive (`turn_engine.py:778`), so a raised batch would turn one odd
    row into every workspace file silently vanishing for that agent, every turn,
    forever. One bad row costs one file.
    """
    from contexts.agents.domain.mcp import StagedFile
    from contexts.agents.infrastructure.sandbox.docker_runsc import _tar_staged_inputs

    files = [
        StagedFile(filename="good.csv", data=b"a"),
        StagedFile(filename="../../etc/passwd", data=b"b"),
        StagedFile(filename="reports/also-good.csv", data=b"c"),
    ]
    _, staged = _tar_staged_inputs("agent-files", files, preserve_tree=True)

    assert staged == ["agent-files/good.csv", "agent-files/reports/also-good.csv"]


def test_no_accepted_path_can_escape_the_staging_dir() -> None:
    """The containment property itself, not a list of blocked strings.

    Staging builds `posixpath.join(rel_dir, name)`, which silently discards
    `rel_dir` if `name` is absolute, and resolves upward on any `..`. So for every
    path the rule accepts, the member must stay under `rel_dir` — and no component
    may be '', '.', or '..'. Enumerated over an adversarial alphabet rather than
    spot-checked, because the exact strings that break it are the ones nobody
    thought to list.
    """
    import itertools
    import posixpath

    from shared_kernel.storage.sanitize import safe_workspace_relpath

    # The zero-width space earns its place: it makes a component look ordinary to
    # normpath while reading as ".." to anything that strips it. The rule rejects
    # it outright now, which is why nothing downstream has to launder it.
    alphabet = ["..", ".", "/", "a", " ", "\\", "...", "..a", "%2e", "\u200b"]
    accepted = 0
    for n in range(1, 4):
        for combo in itertools.product(alphabet, repeat=n):
            try:
                out = safe_workspace_relpath("".join(combo))
            except ValueError:
                continue
            accepted += 1
            resolved = posixpath.normpath(posixpath.join("/workspace", "agent-files", out))
            assert resolved.startswith("/workspace/agent-files/"), f"{combo} escaped to {resolved}"
            assert not any(c in ("", ".", "..") for c in out.split("/")), out
    # Guard the guard: an alphabet that rejected everything would prove nothing.
    assert accepted > 100


def test_the_stored_path_is_the_staged_path() -> None:
    """A designer's stored path is shown in the UI and reported to the model, so
    staging must not quietly rewrite it — that is this dossier's own defect in a
    new place. Per-component `safe_input_name` did: it strips leading dots, so
    `.config/app.json` staged as `config/app.json`, a path the designer never saw.
    """
    from shared_kernel.storage.sanitize import safe_workspace_relpath, validate_workspace_relpath

    for path in (".config/app.json", ".env", "reports/2024/q1.csv", "a-b_c.d.csv"):
        assert safe_workspace_relpath(path) == path
        # And what upload stores is exactly what staging re-validates.
        assert validate_workspace_relpath(path) == path


@pytest.mark.parametrize("raw", ["/ /x.csv", "  reports//q1.csv ", "a/./b.csv", "x.csv"])
def test_upload_normalisation_is_idempotent(raw: str) -> None:
    """The stored path is re-validated at staging, so a path upload accepts must
    survive a second pass unchanged. It did not: whitespace was stripped before
    slashes, so `/ /x.csv` stored as ` /x.csv`, which re-validates to `/x.csv` and
    is then rejected as absolute — one ordinary upload permanently and silently
    breaking every agent-files staging for that agent.
    """
    from shared_kernel.storage.sanitize import safe_workspace_relpath, validate_workspace_relpath

    once = validate_workspace_relpath(raw)
    assert validate_workspace_relpath(once) == once
    # The stored form must also survive the stricter staging rule.
    assert safe_workspace_relpath(once) == once


def test_file_tool_reads_both_path_forms_identically() -> None:
    """AC-9. The `file` tool is untouched by this fix and must stay that way: the
    absolute form the note now carries has to mean what the relative form meant."""
    from contexts.agents.application.tools.file_tool import _safe_relpath

    assert _safe_relpath("agent-files/x") == _safe_relpath("/workspace/agent-files/x")
    assert _safe_relpath("agent-files/x") == "/workspace/agent-files/x"
