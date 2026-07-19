"""Volume reconciliation for the tree-preserving stagers.

`2026-07-16-agent-workspace-volume-reconcile`. The agent-files and skills subtrees
of the per-agent volume are projections of a source of truth (the
`agent_workspace_files` rows; the bound-skill set). Staging used to `put_archive` the
survivors *over* the existing tree and cache a manifest sha in a per-process dict — an
overlay that could add but never remove (a deleted file kept its bytes, FU-19) behind a
cache that returned paths without touching Docker (so it could not notice a volume
removed out of band, FU-6). Staging now runs a real container that empties the subtree
and re-extracts the staged set.

Two layers are tested:

- The orchestration (`_stage_tree` over a fake Docker client): the container is created
  through `_create_verified`, started, waited on, and a non-zero exit raises; there is no
  cache, so every call spawns a container.
- The `_RECONCILE` script itself, run as a subprocess against a real temp filesystem: a
  file dropped from the set is removed (AC-1), and the clear cannot escape the subtree via
  a symlink or a `..` member (AC-6). The script is stdlib-only by design — the code_exec
  image has no SMAP package — so it is exercised directly rather than imported.
"""

from __future__ import annotations

import io
import os
import subprocess
import sys
import tarfile
import uuid

import pytest

from contexts.agents.domain.errors import SandboxReconcileError, SandboxRuntimeViolation
from contexts.agents.domain.mcp import StagedFile
from contexts.agents.infrastructure.sandbox import docker_runsc as ds

# asyncio_mode = "auto" (pyproject) runs bare `async def test_*` without a per-test mark;
# the subprocess and docstring tests below are deliberately synchronous.


# --- orchestration layer: _stage_tree over a fake Docker client --------------


class _FakeContainer:
    def __init__(self, *, runtime: str = "runsc", exit_code: int = 0, stderr: bytes = b"") -> None:
        self._runtime = runtime
        self._exit_code = exit_code
        self._stderr = stderr
        self.attrs: dict = {}
        self.archives: list[tuple[str, bytes]] = []
        self.started = False
        self.waited = False
        self.killed = False
        self.removed = False

    def reload(self) -> None:
        self.attrs = {"HostConfig": {"Runtime": self._runtime}}

    def put_archive(self, path: str, data: bytes) -> bool:
        self.archives.append((path, data))
        return True

    def start(self) -> None:
        self.started = True

    def wait(self, timeout: float | None = None) -> dict:
        self.waited = True
        return {"StatusCode": self._exit_code}

    def logs(self, *, stdout: bool = True, stderr: bool = False) -> bytes:
        return self._stderr if stderr else b""

    def kill(self) -> None:
        self.killed = True

    def remove(self, *, force: bool = False) -> None:
        self.removed = True


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


def _box(monkeypatch, container: _FakeContainer):
    """A real DockerRunscSandbox with only the daemon faked.

    `_create_verified`, `_assert_runsc`, `_await_reconcile` and `_remove_quietly` stay
    real — the fake container reports `runsc`, so the gVisor guard actually runs and a
    regression dropping it fails here."""
    client = _FakeClient(container)
    monkeypatch.setattr(ds.DockerRunscSandbox, "_client", lambda self: client)

    async def _ready(self):
        return None

    monkeypatch.setattr(ds.DockerRunscSandbox, "_ensure_runtime_ready", _ready)
    monkeypatch.setattr(ds.DockerRunscSandbox, "_base_host_config", lambda self: {})
    box = ds.DockerRunscSandbox(code_exec_image="img")
    return box, client


def _staged(name: str, data: bytes = b"x") -> StagedFile:
    return StagedFile(filename=name, data=data)


async def test_reconcile_runs_a_real_container_not_a_never_started_one(monkeypatch) -> None:
    """AC-3. The container is created, started, and waited on — the old `command=["true"]`
    container was never started, so an in-container failure was unobservable."""
    container = _FakeContainer()
    box, client = _box(monkeypatch, container)

    await box.stage_agent_workspace_files(
        agent_id=uuid.uuid4(), files=[_staged("a.csv")], manifest_sha="sha1"
    )

    assert client.create_kwargs is not None
    assert client.create_kwargs["command"] == ["python", "-c", ds._RECONCILE]
    assert container.started is True
    assert container.waited is True
    assert container.removed is True


async def test_the_command_overlays_files_and_a_manifest_on_the_volume(monkeypatch) -> None:
    """AC-1 (orchestration half) + regression for the tmpfs-shadow bug. Two put_archives,
    both to the **volume root** and never a tmpfs (put_archive runs before start, and a
    tmpfs mount does not exist until the container runs, so a /tmp file would be shadowed
    at start): the staged files overlay the live subtree, and a tiny manifest of the
    desired paths lands beside them for the prune to read. The prune itself is proven
    against a real FS below."""
    container = _FakeContainer()
    box, client = _box(monkeypatch, container)

    await box.stage_agent_workspace_files(
        agent_id=uuid.uuid4(), files=[_staged("a.csv")], manifest_sha="sha1"
    )

    env = client.create_kwargs["environment"]
    assert env["SMAP_RECONCILE_SUBDIR"] == "agent-files"
    assert env["SMAP_RECONCILE_ROOT"] == "/workspace"
    assert env["SMAP_RECONCILE_MANIFEST"].startswith("/workspace/.smap-reconcile-")
    assert env["SMAP_RECONCILE_MANIFEST"].endswith(".manifest")
    assert "SMAP_RECONCILE_ARCHIVE" not in env  # no staged-copy transport any more

    # Both put_archives go to the volume, never /tmp. First carries the files (overlay),
    # second the manifest.
    assert [path for path, _ in container.archives] == ["/workspace", "/workspace"]
    files_archive, manifest_archive = (data for _p, data in container.archives)
    assert _tar_file_names(files_archive) == ["agent-files/a.csv"]
    assert _tar_file_names(manifest_archive)[0].endswith(".manifest")


async def test_a_non_zero_exit_raises(monkeypatch) -> None:
    """AC-3. An in-container reconcile failure must surface as an error, so the caller's
    best-effort swallow runs the turn without the files rather than trusting a
    half-reconciled volume."""
    container = _FakeContainer(exit_code=1, stderr=b"reconcile: empty subdir")
    box, _client = _box(monkeypatch, container)

    with pytest.raises(SandboxReconcileError) as excinfo:
        await box.stage_skill_files(
            agent_id=uuid.uuid4(), files=[_staged("s/scripts/x.py")], manifest_sha="sha1"
        )

    assert "empty subdir" in str(excinfo.value)
    assert container.removed is True


async def test_a_timeout_kills_and_raises(monkeypatch) -> None:
    """AC-3. A container that never exits must not hang the turn: wait times out, the
    container is killed, and the error propagates."""
    container = _FakeContainer()

    def _hang(timeout: float | None = None):
        raise RuntimeError("wait timed out")

    container.wait = _hang  # type: ignore[method-assign]
    box, _client = _box(monkeypatch, container)

    with pytest.raises(SandboxReconcileError):
        await box.stage_agent_workspace_files(
            agent_id=uuid.uuid4(), files=[_staged("a.csv")], manifest_sha="sha1"
        )

    assert container.killed is True
    assert container.removed is True


async def test_staging_goes_through_create_verified(monkeypatch) -> None:
    """AC-4. gVisor is asserted before the workload starts: a container that lands on runc
    is removed without ever running the reconcile."""
    container = _FakeContainer(runtime="runc")
    box, _client = _box(monkeypatch, container)

    with pytest.raises(SandboxRuntimeViolation):
        await box.stage_agent_workspace_files(
            agent_id=uuid.uuid4(), files=[_staged("a.csv")], manifest_sha="sha1"
        )

    assert container.started is False
    assert container.removed is True


async def test_no_cache_every_stage_spawns_a_container(monkeypatch) -> None:
    """AC-5. Two consecutive stages of the identical manifest both create and start a
    container. The retired cache made the second a no-op that touched no Docker API — and
    a hit path that touches no Docker API can never notice a missing volume (FU-6)."""
    starts = {"n": 0}
    real_start = _FakeContainer.start

    def _counting_start(self):
        starts["n"] += 1
        real_start(self)

    monkeypatch.setattr(_FakeContainer, "start", _counting_start)

    container = _FakeContainer()
    box, _client = _box(monkeypatch, container)
    agent_id = uuid.uuid4()

    await box.stage_agent_workspace_files(agent_id=agent_id, files=[_staged("a.csv")], manifest_sha="s")
    await box.stage_agent_workspace_files(agent_id=agent_id, files=[_staged("a.csv")], manifest_sha="s")

    assert starts["n"] == 2


async def test_no_files_spawns_no_container(monkeypatch) -> None:
    container = _FakeContainer()
    box, client = _box(monkeypatch, container)

    out = await box.stage_agent_workspace_files(agent_id=uuid.uuid4(), files=[], manifest_sha="s")

    assert out == []
    assert client.create_kwargs is None
    assert container.started is False


def test_the_reconcile_docstring_describes_reconciliation_not_a_cache() -> None:
    """AC-8. The stager no longer claims idempotency via a cache; it describes making the
    subtree equal the staged set."""
    doc = (ds.DockerRunscSandbox._stage_tree.__doc__ or "").lower()
    assert "reconcile" in doc
    assert "idempotent" not in doc


# --- the _RECONCILE prune against a real filesystem --------------------------
#
# `_RECONCILE` runs AFTER the overlay put_archive: the staged files are already on the
# volume, and its job is to prune whatever the staged set no longer contains. These tests
# model that state directly — write the post-overlay tree, write the manifest of desired
# paths, run the stdlib-only script, and assert what survives. The script reads its target
# from the environment precisely so it can run here without a Docker daemon. Symlink cases
# skip where the host cannot create symlinks (unprivileged Windows).


def _tar_file_names(data: bytes) -> list[str]:
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        return [m.name for m in tar.getmembers() if m.isfile()]


def _run_prune(root, subdir: str, present: dict[str, bytes], desired: list[str]):
    """Simulate the state after the overlay put_archive and run the prune.

    *present* maps volume-root-relative paths to the bytes already on the volume (the staged
    files just overlaid, plus any stale leftovers); *desired* is the manifest of paths the
    staged set contains. Returns (CompletedProcess, manifest_path)."""
    root.mkdir(parents=True, exist_ok=True)
    for rel, payload in present.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(payload)
    manifest = root / ".smap-reconcile-test.manifest"
    manifest.write_text("\n".join(desired) + "\n", encoding="utf-8")
    env = {
        **os.environ,
        "SMAP_RECONCILE_ROOT": str(root),
        "SMAP_RECONCILE_SUBDIR": subdir,
        "SMAP_RECONCILE_MANIFEST": str(manifest),
    }
    result = subprocess.run(
        [sys.executable, "-c", ds._RECONCILE], env=env, capture_output=True, text=True, check=False
    )
    return result, manifest


def _run_prune_raw(root, subdir: str, manifest_lines: list[str]):
    """Like `_run_prune` but assumes the caller already built the on-disk tree (used by the
    symlink cases, which need os.symlink rather than write_bytes)."""
    root.mkdir(parents=True, exist_ok=True)
    manifest = root / ".smap-reconcile-test.manifest"
    manifest.write_text("\n".join(manifest_lines) + "\n", encoding="utf-8")
    env = {
        **os.environ,
        "SMAP_RECONCILE_ROOT": str(root),
        "SMAP_RECONCILE_SUBDIR": subdir,
        "SMAP_RECONCILE_MANIFEST": str(manifest),
    }
    return subprocess.run(
        [sys.executable, "-c", ds._RECONCILE], env=env, capture_output=True, text=True, check=False
    )


def _supports_symlink(tmp_path) -> bool:
    probe = tmp_path / "_probe"
    try:
        os.symlink(tmp_path, probe)
    except OSError:
        return False
    probe.unlink()
    return True


def test_a_dropped_file_is_removed_from_the_subtree(tmp_path) -> None:
    """AC-1. The headline: the volume held {a, b}, the staged set is {a}, and after the
    prune b's bytes are gone — not merely unnamed in the note. The old overlay left b."""
    root = tmp_path / "workspace"
    result, _m = _run_prune(
        root,
        "agent-files",
        present={"agent-files/a.csv": b"new-a", "agent-files/b.csv": b"b-should-vanish"},
        desired=["agent-files/a.csv"],
    )

    assert result.returncode == 0, result.stderr
    assert (root / "agent-files" / "a.csv").read_bytes() == b"new-a"
    assert not (root / "agent-files" / "b.csv").exists()


def test_the_manifest_is_removed_after_the_prune(tmp_path) -> None:
    """The manifest is a per-call file on the volume; the prune unlinks it in its finally so
    it never lingers to fill the quota or be mistaken for content."""
    root = tmp_path / "workspace"
    result, manifest = _run_prune(
        root, "agent-files", present={"agent-files/a.csv": b"a"}, desired=["agent-files/a.csv"]
    )

    assert result.returncode == 0, result.stderr
    assert not manifest.exists()


def test_the_subtree_is_never_emptied_only_extras_pruned(tmp_path) -> None:
    """AC + FU-6: in-place reconcile never clears first, so a desired file present on the
    volume is untouched while only the extras are removed — there is no window where the
    subtree is empty."""
    root = tmp_path / "workspace"
    result, _m = _run_prune(
        root,
        "agent-files",
        present={"agent-files/keep.csv": b"keep", "agent-files/drop.csv": b"drop"},
        desired=["agent-files/keep.csv"],
    )

    assert result.returncode == 0, result.stderr
    assert (root / "agent-files" / "keep.csv").read_bytes() == b"keep"
    assert not (root / "agent-files" / "drop.csv").exists()


def test_a_nested_dropped_file_is_removed_and_empty_dirs_pruned(tmp_path) -> None:
    """A dropped file inside a folder does not linger, and a folder left empty by the prune
    is removed, while a folder still holding a desired file is kept."""
    root = tmp_path / "workspace"
    result, _m = _run_prune(
        root,
        "agent-files",
        present={
            "agent-files/reports/q1.csv": b"keep",
            "agent-files/reports/q2.csv": b"drop",
            "agent-files/old/gone.csv": b"drop",
        },
        desired=["agent-files/reports/q1.csv"],
    )

    assert result.returncode == 0, result.stderr
    assert (root / "agent-files" / "reports" / "q1.csv").read_bytes() == b"keep"
    assert not (root / "agent-files" / "reports" / "q2.csv").exists()
    assert not (root / "agent-files" / "old").exists()  # wholly-stale folder pruned


def test_the_other_subtree_is_untouched(tmp_path) -> None:
    """Reconciling agent-files prunes only agent-files: the skills subtree and the file
    tool's own state on the same volume survive (AC-21, and the §9 danger this guards)."""
    root = tmp_path / "workspace"
    result, _m = _run_prune(
        root,
        "agent-files",
        present={
            "agent-files/a.csv": b"a",
            "agent-files/stale.csv": b"stale",
            "skills/s/keep.py": b"skill",
            "notes.txt": b"file-tool state",
        },
        desired=["agent-files/a.csv"],
    )

    assert result.returncode == 0, result.stderr
    assert (root / "skills" / "s" / "keep.py").read_bytes() == b"skill"
    assert (root / "notes.txt").read_bytes() == b"file-tool state"
    assert not (root / "agent-files" / "stale.csv").exists()


def test_a_manifest_path_cannot_make_the_prune_delete_outside_the_subtree(tmp_path) -> None:
    """AC-6. The prune only ever deletes entries it walks *under* the subtree; the manifest
    is a keep-set, never a list of paths to act on. A hostile manifest entry pointing
    outside agent-files therefore cannot cause a deletion there."""
    root = tmp_path / "workspace"
    result, _m = _run_prune(
        root,
        "agent-files",
        present={"agent-files/a.csv": b"a", "notes.txt": b"file-tool state"},
        desired=["agent-files/a.csv", "../notes.txt", "/etc/passwd"],
    )

    assert result.returncode == 0, result.stderr
    assert (root / "notes.txt").read_bytes() == b"file-tool state"
    assert (root / "agent-files" / "a.csv").read_bytes() == b"a"


def test_a_symlink_out_of_the_subtree_is_removed_not_followed(tmp_path) -> None:
    """AC-6. A symlink the agent's own code_exec left inside agent-files, pointing at the
    rest of the volume, is unlinked by the prune — never traversed, so its target is not
    deleted."""
    if not _supports_symlink(tmp_path):
        pytest.skip("host cannot create symlinks")

    root = tmp_path / "workspace"
    sub = root / "agent-files"
    sub.mkdir(parents=True)
    secret = root / "file-tool-secret.txt"
    secret.write_bytes(b"must survive")
    (sub / "a.csv").write_bytes(b"a")  # a desired file, overlaid
    os.symlink(secret, sub / "escape-link")  # agent-planted, not in the staged set

    result = _run_prune_raw(root, "agent-files", ["agent-files/a.csv"])

    assert result.returncode == 0, result.stderr
    assert secret.read_bytes() == b"must survive"
    assert not (sub / "escape-link").exists()
    assert (sub / "a.csv").read_bytes() == b"a"


def test_a_symlinked_subdir_is_not_descended_and_its_target_survives(tmp_path) -> None:
    """AC-6. A symlinked directory inside the subtree pointing at other volume state is
    removed as a link (os.walk does not descend into it), so the files behind it survive."""
    if not _supports_symlink(tmp_path):
        pytest.skip("host cannot create symlinks")

    root = tmp_path / "workspace"
    sub = root / "agent-files"
    sub.mkdir(parents=True)
    outside = root / "other-state"
    outside.mkdir()
    (outside / "keep.txt").write_bytes(b"must survive")
    (sub / "a.csv").write_bytes(b"a")
    os.symlink(outside, sub / "sneaky")  # symlinked dir, not in the staged set

    result = _run_prune_raw(root, "agent-files", ["agent-files/a.csv"])

    assert result.returncode == 0, result.stderr
    assert (outside / "keep.txt").read_bytes() == b"must survive"
    assert not (sub / "sneaky").exists()
    assert (sub / "a.csv").read_bytes() == b"a"


def test_the_subtree_itself_being_a_symlink_is_replaced_not_followed(tmp_path) -> None:
    """AC-6. If the subtree root is itself a symlink to elsewhere on the volume (the overlay
    having written the staged files through it), the prune removes the link and rebuilds a
    real directory rather than walking — and following — it, so the link's former target
    survives untouched."""
    if not _supports_symlink(tmp_path):
        pytest.skip("host cannot create symlinks")

    root = tmp_path / "workspace"
    root.mkdir(parents=True)
    elsewhere = root / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "keep.txt").write_bytes(b"untouched")
    os.symlink(elsewhere, root / "agent-files")

    result = _run_prune_raw(root, "agent-files", ["agent-files/a.csv"])

    assert result.returncode == 0, result.stderr
    assert not (root / "agent-files").is_symlink()
    assert (elsewhere / "keep.txt").read_bytes() == b"untouched"


# --- the legacy sessions purge (2026-07-19-session-dir-room-isolation, AC-7) --
#
# Session state moved to its own per-room volume, so nothing writes
# /workspace/sessions any more -- and therefore nothing cleans it up either. The
# repair reuses this same script with an empty manifest rather than adding a
# second deletion path, so what is tested here is that "reconcile to nothing"
# clears the subtree and only the subtree.


def _run_purge(root, subdir: str = "sessions"):
    """Drive `_RECONCILE` exactly as `purge_legacy_session_dirs` does: empty manifest."""
    root.mkdir(parents=True, exist_ok=True)
    manifest = root / ".smap-reconcile-purge.manifest"
    manifest.write_text("\n", encoding="utf-8")
    env = {
        **os.environ,
        "SMAP_RECONCILE_ROOT": str(root),
        "SMAP_RECONCILE_SUBDIR": subdir,
        "SMAP_RECONCILE_MANIFEST": str(manifest),
    }
    return subprocess.run(
        [sys.executable, "-c", ds._RECONCILE], env=env, capture_output=True, text=True, check=False
    )


def test_purge_clears_the_legacy_sessions_tree(tmp_path) -> None:
    """T-6/AC-7. Every room's leftovers go, however deep."""
    root = tmp_path / "workspace"
    for rel in (
        "sessions/room-a/inputs/secret.pdf",
        "sessions/room-a/outputs/chart.png",
        "sessions/room-b/inputs/other.csv",
    ):
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x")

    result = _run_purge(root)

    assert result.returncode == 0, result.stderr
    assert not (root / "sessions" / "room-a").exists()
    assert not (root / "sessions" / "room-b").exists()


def test_purge_leaves_every_other_region_of_the_volume_intact(tmp_path) -> None:
    """T-6/AC-7. The blast radius. `agent-files/` and `skills/` are projections of
    live tables and the volume root is the `file` tool's own state -- all three are
    Agent-scoped and stay shared across rooms by design (Q-1). A purge that took
    them would be a far worse bug than the leak it repairs."""
    root = tmp_path / "workspace"
    keep = {
        "notes.md": b"file-tool state",
        "agent-files/reports/q1.csv": b"designer upload",
        "skills/pdf-fill/scripts/fill.py": b"skill script",
    }
    for rel, payload in keep.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(payload)
    stale = root / "sessions" / "room-a" / "inputs" / "leak.pdf"
    stale.parent.mkdir(parents=True, exist_ok=True)
    stale.write_bytes(b"leak")

    result = _run_purge(root)

    assert result.returncode == 0, result.stderr
    assert not stale.exists()
    for rel, payload in keep.items():
        assert (root / rel).read_bytes() == payload


def test_purge_is_idempotent_on_a_volume_that_never_had_sessions(tmp_path) -> None:
    """AC-7. Most volumes are already clean; re-running must be a cheap no-op, not
    an error the sweep would have to special-case as a failure."""
    root = tmp_path / "workspace"
    (root / "agent-files").mkdir(parents=True, exist_ok=True)
    (root / "agent-files" / "a.csv").write_bytes(b"a")

    first = _run_purge(root)
    second = _run_purge(root)

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert (root / "agent-files" / "a.csv").read_bytes() == b"a"


def test_purge_does_not_follow_a_symlinked_sessions_root(tmp_path) -> None:
    """T-6/AC-7. The one that matters: the agent's own code_exec could have left
    `sessions` as a symlink to elsewhere on the volume, and a purge that walked it
    would delete the target's contents -- turning a repair into data loss. The
    script replaces a symlinked root rather than walking it."""
    if not _supports_symlink(tmp_path):
        pytest.skip("host cannot create symlinks")

    root = tmp_path / "workspace"
    elsewhere = root / "agent-files"
    elsewhere.mkdir(parents=True, exist_ok=True)
    (elsewhere / "precious.csv").write_bytes(b"must survive")
    os.symlink(elsewhere, root / "sessions")

    result = _run_purge(root)

    assert result.returncode == 0, result.stderr
    assert (elsewhere / "precious.csv").read_bytes() == b"must survive"
    assert not (root / "sessions").is_symlink()


def test_purge_does_not_follow_a_symlinked_room_dir(tmp_path) -> None:
    """T-6/AC-7. Same danger one level down: a room dir symlinked out of the
    subtree. os.walk does not descend into it and os.unlink removes the link, not
    what it points at."""
    if not _supports_symlink(tmp_path):
        pytest.skip("host cannot create symlinks")

    root = tmp_path / "workspace"
    elsewhere = root / "agent-files"
    elsewhere.mkdir(parents=True, exist_ok=True)
    (elsewhere / "precious.csv").write_bytes(b"must survive")
    sessions = root / "sessions"
    sessions.mkdir(parents=True, exist_ok=True)
    os.symlink(elsewhere, sessions / "room-a")

    result = _run_purge(root)

    assert result.returncode == 0, result.stderr
    assert (elsewhere / "precious.csv").read_bytes() == b"must survive"
    assert not (sessions / "room-a").exists()
