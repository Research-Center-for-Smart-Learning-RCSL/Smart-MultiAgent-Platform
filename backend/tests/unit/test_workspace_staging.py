"""Workspace staging: the agent's persisted files (AC-12) and a skill's scripts (AC-21).

AC-12 — ``_stage_persisted_files`` hashed *every* persisted file into ``manifest_sha``
but staged only the ``_MAX_AGENT_FILES_BYTES`` prefix, so the sandbox's cache key
described bytes that were never written to the volume.

AC-21 — bound skills' ``scripts/`` are staged under ``/workspace/skills/{name}/``,
reported absolutely, gated on the same scan status ``read_skill`` enforces, and tracked
by a manifest cache of their own so the two file sets never evict each other.

AC-40 — the three stagers disagree on prefix **by design**; see the test of that name,
and D-37 for why the AC no longer reads as approved.

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
from contexts.skills.application.binding_service import BoundSet
from contexts.skills.domain.models import SkillScanStatus
from tests.unit.skill_fakes import make_skill, make_skill_file

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
        # Mirrors the real method's contract (docker_runsc.stage_agent_workspace_files):
        # absolute, tree-preserving. Until 2026-07-17 this fake returned
        # `agent-files/{name}` — the same unresolvable path production returned —
        # so the suite stayed green over a 100%-reproducible bug. A fake that
        # encodes the implementation's mistake tests nothing.
        return [f"/workspace/agent-files/{f.filename}" for f in files]


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
    assert out == ["/workspace/agent-files/a.csv", "/workspace/agent-files/b.csv"]


async def test_manifest_matches_the_staged_prefix_not_the_full_set() -> None:
    # 100 MiB + 100 MiB overruns the 128 MiB cap, so only `a` is staged.
    staged = _wf("a.bin", "sha-a", 100 * _MIB)
    dropped = _wf("b.bin", "sha-b", 100 * _MIB)

    runner, out = await _stage([staged, dropped])

    assert runner.calls[0]["filenames"] == ["a.bin"]
    assert out == ["/workspace/agent-files/a.bin"]
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
    assert out == ["/workspace/agent-files/a.bin", "/workspace/agent-files/c.csv"]
    # AC-12 still holds across the change: the manifest describes the set that
    # was actually staged, so skipping `b` cannot make the cache key lie.
    assert runner.calls[0]["manifest_sha"] == _expected_manifest([a, c])


class _NoteRunner:
    """All three staging methods, returning what the real ones now return (absolute)."""

    def __init__(self) -> None:
        self.skill_calls: list[dict] = []

    async def stage_agent_workspace_files(self, *, agent_id, files, manifest_sha):
        return [f"/workspace/agent-files/{f.filename}" for f in files]

    async def stage_kernel_inputs(self, *, agent_id, chatroom_id, files):
        return [f"/workspace/sessions/{chatroom_id}/inputs/{f.filename}" for f in files]

    async def stage_skill_files(self, *, agent_id, files, manifest_sha):
        self.skill_calls.append({"manifest_sha": manifest_sha, "filenames": [f.filename for f in files]})
        return [f"/workspace/skills/{f.filename}" for f in files]


def _async_return(value):
    async def _f(*_a, **_kw):
        return value

    return _f


async def _note(monkeypatch, ws_files, attachments, bound=None, read_bytes=None) -> str | None:
    """Drive `_stage_workspace_inputs` unbound, stubbing its lazy imports."""
    import contexts.agents.application.runtime.turn_engine as mod
    from contexts.agents.domain.models import AgentToolType

    engine = TurnEngine.__new__(TurnEngine)
    engine._db = None
    tools = [SimpleNamespace(enabled=True, tool_type=AgentToolType.HOSTED_CODE_INTERPRETER)]

    monkeypatch.setattr(
        mod,
        "AgentsFacade",
        lambda _db: SimpleNamespace(
            list_agent_tools=_async_return(tools),
            list_workspace_files=_async_return(ws_files),
        ),
    )
    monkeypatch.setattr(
        mod,
        "ConversationFacade",
        lambda _db: SimpleNamespace(read_attachments_bytes=_async_return([b"x" for _ in attachments])),
    )
    monkeypatch.setattr(
        "contexts.agents.infrastructure.sandbox.docker_runsc.docker_runsc_sandbox_from_settings",
        lambda: _NoteRunner(),
    )
    monkeypatch.setattr(
        "contexts.skills.interfaces.facade.SkillsFacade",
        lambda _db: SimpleNamespace(read_skill_file_bytes=read_bytes or _async_return(b"print(1)")),
    )
    return await TurnEngine._stage_workspace_inputs(
        engine, SimpleNamespace(id=uuid.uuid4()), uuid.uuid4(), attachments, bound or BoundSet(skills=())
    )


async def test_the_note_the_model_reads_carries_only_absolute_paths(monkeypatch) -> None:
    """AC-5. The note is the whole user-visible surface of this bug: the model is
    told where its files are, and until 2026-07-17 it was told `agent-files/x`,
    which resolves under the kernel's cwd (`/workspace/sessions/{room}`) where
    nothing is. Two staging trees at different depths feed one sentence, so the
    only form that can be true for both is absolute.
    """
    note = await _note(
        monkeypatch,
        ws_files=[_wf("reports/q1.csv", "sha-a", 10)],
        attachments=[SimpleNamespace(filename="upload.csv", size_bytes=10)],
    )

    assert note is not None
    assert "/workspace/agent-files/reports/q1.csv" in note
    assert "/workspace/sessions/" in note
    # No bare relative form survives. Asserted by scanning for the old prefix
    # rather than by splitting the note on "," — a filename may contain a comma,
    # so comma-splitting is not a real invariant of this format (FU-7).
    assert "agent-files/" in note
    assert " agent-files/" not in note
    assert ": agent-files/" not in note


def test_cap_is_the_documented_128_mib() -> None:
    # The manifest fix is only meaningful against a real cut; pin the constant
    # the truncation cases above are sized against.
    assert te._MAX_AGENT_FILES_BYTES == 128 * _MIB


# --- AC-21 / AC-40: skill script staging ------------------------------------


class _SkillRunner:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def stage_skill_files(self, *, agent_id, files, manifest_sha):
        self.calls.append(
            {
                "manifest_sha": manifest_sha,
                "staged": [(f.filename, f.data) for f in files],
            }
        )
        return [f"/workspace/skills/{f.filename}" for f in files]


async def _stage_skills(monkeypatch, bound) -> tuple[_SkillRunner, list[str]]:
    """Drive `_stage_skill_scripts` unbound over stubs."""
    monkeypatch.setattr(
        "contexts.skills.interfaces.facade.SkillsFacade",
        lambda _db: SimpleNamespace(
            read_skill_file_bytes=lambda f: _async_return(f"bytes:{f.path}".encode())()
        ),
    )
    engine = TurnEngine.__new__(TurnEngine)
    engine._db = None
    runner = _SkillRunner()
    out: list[str] = []
    await TurnEngine._stage_skill_scripts(engine, SimpleNamespace(id=uuid.uuid4()), runner, bound, out)
    return runner, out


def _bound(*pairs):
    """A BoundSet from (skill, files) pairs, as `resolve_bound_set` would return it."""
    return BoundSet(
        skills=tuple(s for s, _ in pairs),
        files={s.id: tuple(fs) for s, fs in pairs},
    )


async def test_scripts_stage_under_the_skill_name_preserving_the_bundle_layout(monkeypatch) -> None:
    """AC-21. The staged name is `{skill}/{path}`, so the file lands at
    `/workspace/skills/pdf-fill/scripts/fill.py` and SKILL.md's own relative
    "run scripts/fill.py" resolves from the skill root."""
    skill = make_skill(name="pdf-fill")
    script = make_skill_file(skill.id, path="scripts/fill.py")

    runner, out = await _stage_skills(monkeypatch, _bound((skill, [script])))

    assert runner.calls[0]["staged"] == [("pdf-fill/scripts/fill.py", b"bytes:scripts/fill.py")]
    assert out == ["/workspace/skills/pdf-fill/scripts/fill.py"]


async def test_only_scripts_stage_never_references_or_assets(monkeypatch) -> None:
    """R31.18 and §8's item 9: a reference is served as text by `read_skill`, and an
    asset is opaque bytes the note explicitly forbids staging."""
    skill = make_skill(name="s")
    files = [
        make_skill_file(skill.id, path="scripts/run.py"),
        make_skill_file(skill.id, path="references/guide.md"),
        make_skill_file(skill.id, path="assets/logo.png"),
    ]

    runner, out = await _stage_skills(monkeypatch, _bound((skill, files)))

    assert [n for n, _ in runner.calls[0]["staged"]] == ["s/scripts/run.py"]
    assert out == ["/workspace/skills/s/scripts/run.py"]


async def test_a_skill_with_no_scripts_stages_nothing(monkeypatch) -> None:
    skill = make_skill(name="s")
    runner, out = await _stage_skills(
        monkeypatch, _bound((skill, [make_skill_file(skill.id, path="references/g.md")]))
    )

    assert runner.calls == []
    assert out == []


async def test_an_unreadable_skill_stages_no_scripts(monkeypatch) -> None:
    """AC-34's gate on the staging channel. `read_skill` refusing the body does not stop
    the bytes reaching the volume, and the staged note *names the path* — so without this
    the model is handed the absolute path of a quarantined script to run."""
    skill = make_skill(name="evil")
    files = [
        make_skill_file(skill.id, path="scripts/run.py", scan_status=SkillScanStatus.QUARANTINED),
    ]

    runner, out = await _stage_skills(monkeypatch, _bound((skill, files)))

    assert runner.calls == []
    assert out == []


async def test_one_quarantined_reference_withholds_the_skills_clean_scripts(monkeypatch) -> None:
    """Whole-skill, not per-file (Q-18). A skill whose SKILL.md the model cannot read has
    no business having its scripts on disk, even the clean ones."""
    skill = make_skill(name="s")
    files = [
        make_skill_file(skill.id, path="scripts/run.py", scan_status=SkillScanStatus.CLEAN),
        make_skill_file(skill.id, path="references/g.md", scan_status=SkillScanStatus.QUARANTINED),
    ]

    runner, out = await _stage_skills(monkeypatch, _bound((skill, files)))

    assert runner.calls == []
    assert out == []


async def test_a_pending_scan_withholds_the_script_too(monkeypatch) -> None:
    """D-27's fail-closed rule reaches this channel as well: `clean` is the only status
    that serves, so a scan still in flight does not stage."""
    skill = make_skill(name="s")
    files = [make_skill_file(skill.id, path="scripts/run.py", scan_status=SkillScanStatus.PENDING)]

    runner, out = await _stage_skills(monkeypatch, _bound((skill, files)))

    assert runner.calls == []


async def test_an_unreadable_skill_does_not_suppress_a_readable_one(monkeypatch) -> None:
    """Per skill, like every other skills failure path (AC-7): one quarantined skill must
    not cost the agent the rest of its bound set."""
    bad = make_skill(name="bad")
    good = make_skill(name="good")
    bound = _bound(
        (bad, [make_skill_file(bad.id, path="scripts/x.py", scan_status=SkillScanStatus.QUARANTINED)]),
        (good, [make_skill_file(good.id, path="scripts/y.py")]),
    )

    runner, out = await _stage_skills(monkeypatch, bound)

    assert [n for n, _ in runner.calls[0]["staged"]] == ["good/scripts/y.py"]
    assert out == ["/workspace/skills/good/scripts/y.py"]


async def test_the_manifest_covers_exactly_the_staged_scripts(monkeypatch) -> None:
    """AC-12's rule on this channel: the manifest is the cache key for what is on the
    volume, so it must not describe a skill that was skipped."""
    good = make_skill(name="good")
    bad = make_skill(name="bad")
    g = make_skill_file(good.id, path="scripts/y.py", sha256="b" * 64)
    bound = _bound(
        (good, [g]),
        (bad, [make_skill_file(bad.id, path="scripts/x.py", scan_status=SkillScanStatus.QUARANTINED)]),
    )

    runner, _out = await _stage_skills(monkeypatch, bound)

    expected = hashlib.sha256(f"good/scripts/y.py:{'b' * 64}".encode()).hexdigest()
    assert runner.calls[0]["manifest_sha"] == expected


async def test_editing_a_script_changes_the_manifest(monkeypatch) -> None:
    """The cache key must move when the bytes move, or an edited script never re-stages
    and the volume serves the old one forever."""
    skill = make_skill(name="s")
    before, _ = await _stage_skills(
        monkeypatch, _bound((skill, [make_skill_file(skill.id, path="scripts/x.py", sha256="a" * 64)]))
    )
    after, _ = await _stage_skills(
        monkeypatch, _bound((skill, [make_skill_file(skill.id, path="scripts/x.py", sha256="c" * 64)]))
    )

    assert before.calls[0]["manifest_sha"] != after.calls[0]["manifest_sha"]


async def test_a_skill_whose_scripts_overrun_the_budget_is_skipped_whole(monkeypatch) -> None:
    """Whole skills, not whole files: a half-staged skill is Q-18's failure — the model
    reads a SKILL.md, finds one of two scripts missing, and confabulates."""
    skill = make_skill(name="big")
    files = [
        make_skill_file(skill.id, path="scripts/a.py", size_bytes=20 * _MIB),
        make_skill_file(skill.id, path="scripts/b.py", size_bytes=20 * _MIB),
    ]

    runner, out = await _stage_skills(monkeypatch, _bound((skill, files)))

    assert runner.calls == []
    assert out == []


async def test_an_oversized_skill_does_not_drop_the_smaller_ones_behind_it(monkeypatch) -> None:
    """`continue`, not `break` — the same rule `_stage_persisted_files` follows, and the
    reason its asymmetry was a defect rather than a policy."""
    big = make_skill(name="big")
    small = make_skill(name="small")
    bound = _bound(
        (big, [make_skill_file(big.id, path="scripts/a.py", size_bytes=40 * _MIB)]),
        (small, [make_skill_file(small.id, path="scripts/b.py", size_bytes=10)]),
    )

    runner, out = await _stage_skills(monkeypatch, bound)

    assert [n for n, _ in runner.calls[0]["staged"]] == ["small/scripts/b.py"]
    assert out == ["/workspace/skills/small/scripts/b.py"]


async def test_the_note_names_skill_scripts_by_absolute_path(monkeypatch) -> None:
    """AC-21's reporting half, through the real `_stage_workspace_inputs`. Relative would
    be wrong here for a reason of its own: the kernel's cwd is the session dir, so a
    relative skill path would have to be `../../skills/{name}/x` — per-room, for a file
    that is per-agent."""
    skill = make_skill(name="pdf-fill")
    bound = _bound((skill, [make_skill_file(skill.id, path="scripts/fill.py")]))

    note = await _note(monkeypatch, ws_files=[], attachments=[], bound=bound)

    assert note == "[Files available in the code_exec workspace: /workspace/skills/pdf-fill/scripts/fill.py]"


async def test_skill_staging_failure_does_not_abort_the_turn_or_the_other_paths(monkeypatch) -> None:
    """The whole staging path is best-effort: a fault must cost the scripts, not the turn.
    The agent-files note must survive a skills failure."""
    skill = make_skill(name="s")
    bound = _bound((skill, [make_skill_file(skill.id, path="scripts/x.py")]))

    note = await _note(
        monkeypatch,
        ws_files=[_wf("a.csv", "sha-a", 10)],
        attachments=[],
        bound=bound,
        read_bytes=_boom,
    )

    assert note == "[Files available in the code_exec workspace: /workspace/agent-files/a.csv]"


async def _boom(*_a, **_kw):
    raise RuntimeError("minio down")


def test_the_skill_script_budget_is_its_own_constant() -> None:
    """Not shared with `_MAX_AGENT_FILES_BYTES`: sharing one budget would let a large
    upload silently unstage a bound skill's scripts."""
    assert te._MAX_SKILL_SCRIPT_BYTES == 32 * _MIB
    assert te._MAX_SKILL_SCRIPT_BYTES != te._MAX_AGENT_FILES_BYTES


def test_the_two_manifest_caches_are_distinct_objects() -> None:
    """AC-21's "skills staging does not evict agent-files staging". One dict keyed by
    agent_id would make each set's manifest evict the other's on every change, so binding
    a skill would re-stage every agent file and vice versa."""
    from contexts.agents.infrastructure.sandbox import docker_runsc as ds

    assert ds._SKILL_MANIFESTS is not ds._WORKSPACE_MANIFESTS


def test_the_three_stagers_disagree_on_prefix_by_design() -> None:
    """AC-40, rewritten — see D-37.

    The AC as approved asserted that `stage_kernel_inputs` "still returns `inputs/x`" and
    that `test_code_exec_kernel.py:164-185` "passes untouched". Both were overtaken by
    `ac4339a`, which fixed FU-15 independently: `_tar_staged_inputs` no longer hardcodes a
    report prefix, `_fix_paths` is gone, and every stager now reports absolute paths
    through `_workspace_abspath`. The `report_prefix` parameter §6 designed is therefore
    unnecessary — `stage_skill_files` just passes its own `rel_dir`.

    What the AC was protecting is still real, so it is asserted here instead: the three
    stagers write into three disjoint subtrees of one volume, and nothing in this task
    made them share one. A regression that collapsed two of these prefixes would let one
    file set overwrite another's on the agent's persistent volume.
    """
    import inspect

    from contexts.agents.infrastructure.sandbox import docker_runsc as ds

    kernel = inspect.getsource(ds.DockerRunscSandbox.stage_kernel_inputs)
    workspace = inspect.getsource(ds.DockerRunscSandbox.stage_agent_workspace_files)
    skills = inspect.getsource(ds.DockerRunscSandbox.stage_skill_files)

    assert 'rel_dir = f"sessions/{chatroom_id}/inputs"' in kernel
    assert 'rel_dir="agent-files"' in workspace
    assert 'rel_dir="skills"' in skills
