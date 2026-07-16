"""AC-12 — the workspace-file manifest covers exactly the file set that is staged.

``_stage_persisted_files`` hashed *every* persisted file into ``manifest_sha`` but
staged only the ``_MAX_AGENT_FILES_BYTES`` prefix, so the sandbox's cache key
described bytes that were never written to the volume.

Building a real TurnEngine needs settings/router/qdrant wiring, so these drive the
method unbound over stubs — the house pattern (see test_turn_engine_observer_activity.py).
"""

from __future__ import annotations

import hashlib
import uuid
from types import SimpleNamespace

import pytest

import contexts.agents.application.runtime.turn_engine as te
from contexts.agents.application.runtime.turn_engine import TurnEngine

_MIB = 1024 * 1024


def _wf(path: str, sha: str, size: int):
    return SimpleNamespace(path=path, sha256=sha, size_bytes=size, minio_key=f"k/{path}")


def _expected_manifest(files) -> str:
    return hashlib.sha256("\n".join(sorted(f"{wf.path}:{wf.sha256}" for wf in files)).encode()).hexdigest()


class _Runner:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def stage_agent_workspace_files(self, *, agent_id, files, manifest_sha):
        self.calls.append({"manifest_sha": manifest_sha, "filenames": [f.filename for f in files]})
        return [f"agent-files/{f.filename}" for f in files]


class _Storage:
    def __init__(self) -> None:
        self._cfg = SimpleNamespace(bucket_agent_workspace="agent-workspace")

    async def get_object(self, *, bucket, key):
        return b"data"


def _facade(ws_files):
    async def _list(_agent_id):
        return ws_files

    return SimpleNamespace(list_workspace_files=_list)


@pytest.fixture(autouse=True)
def _patch_storage(monkeypatch):
    import shared_kernel.storage as storage_mod

    monkeypatch.setattr(storage_mod, "get_minio_client", lambda: _Storage())


async def _stage(ws_files) -> tuple[_Runner, list[str]]:
    engine = TurnEngine.__new__(TurnEngine)
    runner = _Runner()
    out: list[str] = []
    await TurnEngine._stage_persisted_files(
        engine, SimpleNamespace(id=uuid.uuid4()), runner, _facade(ws_files), out
    )
    return runner, out


# --------------------------------------------------------------------------- #


async def test_manifest_covers_the_whole_set_when_nothing_is_truncated() -> None:
    files = [_wf("a.csv", "sha-a", 10), _wf("b.csv", "sha-b", 20)]

    runner, out = await _stage(files)

    assert runner.calls[0]["filenames"] == ["a.csv", "b.csv"]
    assert runner.calls[0]["manifest_sha"] == _expected_manifest(files)
    assert out == ["agent-files/a.csv", "agent-files/b.csv"]


async def test_manifest_matches_the_staged_prefix_not_the_full_set() -> None:
    # 100 MiB + 100 MiB overruns the 128 MiB cap, so only `a` is staged.
    staged = _wf("a.bin", "sha-a", 100 * _MIB)
    dropped = _wf("b.bin", "sha-b", 100 * _MIB)

    runner, out = await _stage([staged, dropped])

    assert runner.calls[0]["filenames"] == ["a.bin"]
    assert out == ["agent-files/a.bin"]
    # The manifest describes what is on the volume...
    assert runner.calls[0]["manifest_sha"] == _expected_manifest([staged])
    # ...and specifically NOT the set that was asked for.
    assert runner.calls[0]["manifest_sha"] != _expected_manifest([staged, dropped])


async def test_editing_a_file_past_the_cut_does_not_change_the_manifest() -> None:
    # The dropped file is not on the volume, so its content cannot make the
    # volume stale. Under the old whole-set hash this re-staged an identical
    # prefix on every edit of a file the sandbox never received.
    staged = _wf("a.bin", "sha-a", 100 * _MIB)

    first, _ = await _stage([staged, _wf("b.bin", "sha-b", 100 * _MIB)])
    second, _ = await _stage([staged, _wf("b.bin", "sha-b-EDITED", 100 * _MIB)])

    assert first.calls[0]["manifest_sha"] == second.calls[0]["manifest_sha"]


async def test_editing_a_staged_file_does_change_the_manifest() -> None:
    # The other direction: the manifest must still invalidate on a real change,
    # or the fix above would be a cache that never refreshes.
    dropped = _wf("b.bin", "sha-b", 100 * _MIB)

    first, _ = await _stage([_wf("a.bin", "sha-a", 100 * _MIB), dropped])
    second, _ = await _stage([_wf("a.bin", "sha-a-EDITED", 100 * _MIB), dropped])

    assert first.calls[0]["manifest_sha"] != second.calls[0]["manifest_sha"]


async def test_a_different_truncation_point_restages() -> None:
    # A set that truncates to a different staged prefix is a different volume
    # state and must re-stage.
    first, _ = await _stage([_wf("a.bin", "sha-a", 100 * _MIB), _wf("b.bin", "sha-b", 100 * _MIB)])
    second, _ = await _stage([_wf("a.bin", "sha-a", 10), _wf("b.bin", "sha-b", 20)])

    assert first.calls[0]["filenames"] == ["a.bin"]
    assert second.calls[0]["filenames"] == ["a.bin", "b.bin"]
    assert first.calls[0]["manifest_sha"] != second.calls[0]["manifest_sha"]


async def test_no_persisted_files_stages_nothing() -> None:
    runner, out = await _stage([])

    assert runner.calls == []
    assert out == []


async def test_first_file_over_the_cap_stages_nothing() -> None:
    runner, out = await _stage([_wf("huge.bin", "sha-h", 200 * _MIB)])

    assert runner.calls == []
    assert out == []


async def test_a_file_that_fits_after_an_overrun_is_still_staged() -> None:
    # FU-18. Selection keeps packing past a file it cannot fit, rather than
    # stopping at it: `b` overruns the 128 MiB cap, but `c` fits in what is left
    # and the designer uploaded it, so it must reach the volume.
    #
    # The attachment path 45 lines up does exactly this (`continue`, not `break`)
    # for the same reason against the same shape of cap, which is what makes the
    # asymmetry a defect rather than two deliberate policies.
    #
    # None of the truncation cases above can see this: every one of them puts the
    # overrunning file last, so `break` and `continue` agree on all of them.
    a = _wf("a.bin", "sha-a", 100 * _MIB)
    b = _wf("b.bin", "sha-b", 100 * _MIB)
    c = _wf("c.csv", "sha-c", 10)

    runner, out = await _stage([a, b, c])

    assert runner.calls[0]["filenames"] == ["a.bin", "c.csv"]
    assert out == ["agent-files/a.bin", "agent-files/c.csv"]
    # AC-12 still holds across the change: the manifest describes the set that
    # was actually staged, so skipping `b` cannot make the cache key lie.
    assert runner.calls[0]["manifest_sha"] == _expected_manifest([a, c])


def test_cap_is_the_documented_128_mib() -> None:
    # The manifest fix is only meaningful against a real cut; pin the constant
    # the truncation cases above are sized against.
    assert te._MAX_AGENT_FILES_BYTES == 128 * _MIB
