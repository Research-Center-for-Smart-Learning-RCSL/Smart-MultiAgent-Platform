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


async def test_the_command_stages_the_archive_on_the_durable_volume(monkeypatch) -> None:
    """AC-1 (orchestration half) + regression for the tmpfs-shadow bug. The reconcile
    command targets the caller's own subtree, and the staging tar is put to the **volume
    root**, never a tmpfs: put_archive runs before start, and a tmpfs mount does not exist
    until the container runs, so a /tmp file would be shadowed at start and the reconcile
    would fail to open it every turn. The clear-then-extract behaviour is proven against a
    real FS below."""
    container = _FakeContainer()
    box, client = _box(monkeypatch, container)

    await box.stage_agent_workspace_files(
        agent_id=uuid.uuid4(), files=[_staged("a.csv")], manifest_sha="sha1"
    )

    env = client.create_kwargs["environment"]
    assert env["SMAP_RECONCILE_SUBDIR"] == "agent-files"
    assert env["SMAP_RECONCILE_ROOT"] == "/workspace"
    # On the volume, outside the reconciled subtree, and never a /tmp (tmpfs) path.
    assert env["SMAP_RECONCILE_ARCHIVE"].startswith("/workspace/.smap-reconcile-")
    assert env["SMAP_RECONCILE_ARCHIVE"].endswith(".tar")
    assert not env["SMAP_RECONCILE_ARCHIVE"].startswith("/tmp")
    assert [path for path, _ in container.archives] == ["/workspace"]


async def test_a_non_zero_exit_raises(monkeypatch) -> None:
    """AC-3. An in-container reconcile failure must surface as an error, so the caller's
    best-effort swallow runs the turn without the files rather than trusting a
    half-reconciled volume."""
    container = _FakeContainer(exit_code=1, stderr=b"reconcile: member escapes root: ../x")
    box, _client = _box(monkeypatch, container)

    with pytest.raises(SandboxReconcileError) as excinfo:
        await box.stage_skill_files(
            agent_id=uuid.uuid4(), files=[_staged("s/scripts/x.py")], manifest_sha="sha1"
        )

    assert "member escapes root" in str(excinfo.value)
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


# --- the _RECONCILE script against a real filesystem -------------------------
#
# The script is stdlib-only and reads its target from the environment precisely so it can
# run here, on a real FS, without a Docker daemon. Symlink cases skip where the host cannot
# create symlinks (unprivileged Windows); the drop-file and `..`-escape cases run anywhere.


def _inner_archive(subdir: str, files: dict[str, bytes]) -> bytes:
    """The staging archive the container reads from the volume: the same shape
    `_tar_staged_inputs` produces — a DIRTYPE for the subtree plus its files."""
    archive, _staged = ds._tar_staged_inputs(
        rel_dir=subdir,
        files=[StagedFile(filename=name, data=data) for name, data in files.items()],
        preserve_tree=True,
    )
    return archive


def _run_reconcile(root, subdir: str, archive_bytes: bytes) -> subprocess.CompletedProcess:
    archive_path = root.parent / "reconcile.tar"
    archive_path.write_bytes(archive_bytes)
    env = {
        **os.environ,
        "SMAP_RECONCILE_ROOT": str(root),
        "SMAP_RECONCILE_SUBDIR": subdir,
        "SMAP_RECONCILE_ARCHIVE": str(archive_path),
    }
    return subprocess.run(
        [sys.executable, "-c", ds._RECONCILE],
        env=env,
        capture_output=True,
        text=True,
        check=False,
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
    """AC-1. The headline: stage {a, b}, then reconcile to {a}, and b's bytes are gone
    from the volume — not merely unnamed in the note. Overlay-staging left b behind."""
    root = tmp_path / "workspace"
    sub = root / "agent-files"
    sub.mkdir(parents=True)
    (sub / "a.csv").write_bytes(b"old-a")
    (sub / "b.csv").write_bytes(b"b-should-vanish")

    result = _run_reconcile(root, "agent-files", _inner_archive("agent-files", {"a.csv": b"new-a"}))

    assert result.returncode == 0, result.stderr
    assert (sub / "a.csv").read_bytes() == b"new-a"
    assert not (sub / "b.csv").exists()


def test_the_staging_tar_and_stale_siblings_are_cleaned_up(tmp_path) -> None:
    """The staging tar lives on the quota-limited volume, so the reconcile unlinks its own
    tar even on success, and sweeps an *aged* sibling orphaned by an earlier killed
    container. A fresh sibling (a concurrent worker's in-flight archive) is left alone — the
    age gate is what keeps concurrent stagers from deleting each other's archives."""
    import time

    root = tmp_path / "workspace"
    (root / "agent-files").mkdir(parents=True)

    aged_orphan = root / ".smap-reconcile-OLD.tar"
    aged_orphan.write_bytes(b"junk from a container killed long ago")
    old = time.time() - 3600
    os.utime(aged_orphan, (old, old))

    fresh_sibling = root / ".smap-reconcile-CONCURRENT.tar"
    fresh_sibling.write_bytes(b"another worker's in-flight archive")

    archive_path = root / ".smap-reconcile-CURRENT.tar"
    archive_path.write_bytes(_inner_archive("agent-files", {"a.csv": b"a"}))

    env = {
        **os.environ,
        "SMAP_RECONCILE_ROOT": str(root),
        "SMAP_RECONCILE_SUBDIR": "agent-files",
        "SMAP_RECONCILE_ARCHIVE": str(archive_path),
    }
    result = subprocess.run(
        [sys.executable, "-c", ds._RECONCILE], env=env, capture_output=True, text=True, check=False
    )

    assert result.returncode == 0, result.stderr
    assert (root / "agent-files" / "a.csv").read_bytes() == b"a"
    assert not archive_path.exists()  # our own staging tar is always removed
    assert not aged_orphan.exists()  # the aged orphan is swept
    assert fresh_sibling.exists()  # a concurrent worker's fresh archive is left intact


def test_a_nested_dropped_file_is_removed(tmp_path) -> None:
    """The subtree is emptied wholesale, so a survivor's folder is rebuilt and a dropped
    file inside a folder does not linger."""
    root = tmp_path / "workspace"
    sub = root / "agent-files"
    (sub / "reports").mkdir(parents=True)
    (sub / "reports" / "q1.csv").write_bytes(b"keep")
    (sub / "reports" / "q2.csv").write_bytes(b"drop")

    result = _run_reconcile(root, "agent-files", _inner_archive("agent-files", {"reports/q1.csv": b"keep"}))

    assert result.returncode == 0, result.stderr
    assert (sub / "reports" / "q1.csv").read_bytes() == b"keep"
    assert not (sub / "reports" / "q2.csv").exists()


def test_the_other_subtree_is_untouched(tmp_path) -> None:
    """Reconciling agent-files clears only agent-files: the skills subtree and the file
    tool's own state on the same volume survive (AC-21, and the §9 danger this guards)."""
    root = tmp_path / "workspace"
    (root / "agent-files").mkdir(parents=True)
    (root / "agent-files" / "stale.csv").write_bytes(b"stale")
    (root / "skills" / "s").mkdir(parents=True)
    (root / "skills" / "s" / "keep.py").write_bytes(b"skill")
    (root / "notes.txt").write_bytes(b"file-tool state")

    result = _run_reconcile(root, "agent-files", _inner_archive("agent-files", {"a.csv": b"a"}))

    assert result.returncode == 0, result.stderr
    assert (root / "skills" / "s" / "keep.py").read_bytes() == b"skill"
    assert (root / "notes.txt").read_bytes() == b"file-tool state"
    assert not (root / "agent-files" / "stale.csv").exists()


def test_a_member_escaping_the_root_is_refused(tmp_path) -> None:
    """AC-6. A crafted archive member with `..` cannot write outside the volume root. Our
    own tars never carry such a member, but the guard is defence against a bug upstream."""
    root = tmp_path / "workspace"
    (root / "agent-files").mkdir(parents=True)

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        info = tarfile.TarInfo(name="agent-files/../../escaped.txt")
        payload = b"escaped"
        info.size = len(payload)
        tar.addfile(info, io.BytesIO(payload))

    result = _run_reconcile(root, "agent-files", buf.getvalue())

    assert result.returncode != 0
    assert "escapes root" in result.stderr
    assert not (tmp_path / "escaped.txt").exists()


def test_a_symlink_out_of_the_subtree_is_removed_not_followed(tmp_path) -> None:
    """AC-6. A symlink the agent's own code_exec left inside agent-files, pointing at the
    rest of the volume, is unlinked by the clear — never traversed, so its target is not
    deleted."""
    if not _supports_symlink(tmp_path):
        pytest.skip("host cannot create symlinks")

    root = tmp_path / "workspace"
    sub = root / "agent-files"
    sub.mkdir(parents=True)
    secret = root / "file-tool-secret.txt"
    secret.write_bytes(b"must survive")
    os.symlink(secret, sub / "escape-link")

    result = _run_reconcile(root, "agent-files", _inner_archive("agent-files", {"a.csv": b"a"}))

    assert result.returncode == 0, result.stderr
    assert secret.read_bytes() == b"must survive"
    assert not (sub / "escape-link").exists()
    assert (sub / "a.csv").read_bytes() == b"a"


def test_the_subtree_itself_being_a_symlink_is_replaced_not_followed(tmp_path) -> None:
    """AC-6. If the subtree root is itself a symlink to elsewhere on the volume, the clear
    removes the link (rmtree is never called on it) and rebuilds a real directory, leaving
    the link's former target untouched."""
    if not _supports_symlink(tmp_path):
        pytest.skip("host cannot create symlinks")

    root = tmp_path / "workspace"
    root.mkdir(parents=True)
    elsewhere = root / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "keep.txt").write_bytes(b"untouched")
    os.symlink(elsewhere, root / "agent-files")

    result = _run_reconcile(root, "agent-files", _inner_archive("agent-files", {"a.csv": b"a"}))

    assert result.returncode == 0, result.stderr
    assert not (root / "agent-files").is_symlink()
    assert (root / "agent-files" / "a.csv").read_bytes() == b"a"
    assert (elsewhere / "keep.txt").read_bytes() == b"untouched"
