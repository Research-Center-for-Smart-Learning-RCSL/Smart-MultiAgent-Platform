"""AC-16 / AC-17 / AC-34 — bundled skill files.

The MinIO client is faked at `get_minio_client`, the seam `file_service` imports it
through. The fake is a real byte store rather than a mock recording calls: the tests that
matter here ask "what would `read_skill` get back", and a call-args assertion cannot
answer that.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from contexts.skills.application import file_service as fs
from contexts.skills.application.file_service import (
    MAX_SKILL_FILE_BYTES,
    SkillFileService,
    file_sha256,
    kind_for_path,
)
from contexts.skills.domain.errors import (
    SkillFileNotEditable,
    SkillFileNotFound,
    SkillFilePathTaken,
    SkillUnreadable,
)
from contexts.skills.domain.models import (
    Skill,
    SkillFile,
    SkillFileKind,
    SkillScanStatus,
    SkillScope,
    SkillSource,
)
from contexts.skills.domain.readability import assert_readable
from contexts.skills.domain.text_rules import ALLOWED_TOP_LEVEL_DIRS
from shared_kernel import audit
from tests.unit.skill_fakes import FakeSkillFileRepo

ACTOR = uuid.uuid4()


def _skill(name: str = "pdf-fill") -> Skill:
    return Skill(
        id=uuid.uuid4(),
        scope=SkillScope.PROJECT,
        agent_id=None,
        project_id=uuid.uuid4(),
        org_id=None,
        name=name,
        description="Fills PDFs.",
        body="# Body",
        body_sha256="0" * 64,
        source=SkillSource.AUTHORED,
        bundle_sha256=None,
        requires=(),
        allowed_tools=(),
        extra_frontmatter={},
        created_by=uuid.uuid4(),
        version=1,
        created_at=datetime.now(UTC),
        deleted_at=None,
    )


def _file(
    *,
    path: str = "references/guide.md",
    kind: SkillFileKind = SkillFileKind.REFERENCE,
    scan_status: SkillScanStatus = SkillScanStatus.CLEAN,
    skill_id: uuid.UUID | None = None,
) -> SkillFile:
    return SkillFile(
        id=uuid.uuid4(),
        skill_id=skill_id or uuid.uuid4(),
        path=path,
        kind=kind,
        mime="text/markdown",
        size_bytes=6,
        sha256="a" * 64,
        minio_key="k",
        scan_status=scan_status,
        extracted_chars=6,
        created_at=datetime.now(UTC),
    )


class FakeMinio:
    """A byte store with the two methods `file_service` uses."""

    skill_bundles_bucket = "skill-bundles"

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.content_types: dict[str, str] = {}

    async def put_object(self, *, bucket: str, key: str, data: bytes, content_type: str) -> None:
        assert bucket == self.skill_bundles_bucket
        self.objects[key] = data
        self.content_types[key] = content_type

    async def get_object(self, *, bucket: str, key: str) -> bytes:
        assert bucket == self.skill_bundles_bucket
        return self.objects[key]


@pytest.fixture
def minio(monkeypatch: pytest.MonkeyPatch) -> FakeMinio:
    fake = FakeMinio()
    monkeypatch.setattr(fs, "get_minio_client", lambda: fake)
    return fake


class TestKindDerivation:
    def test_every_allowed_directory_maps_to_a_kind(self) -> None:
        # The path rule and the kind map must agree exactly: a directory the rule admits
        # but the map misses is a KeyError at upload instead of a 422 at validation.
        # This is the invariant a module-level `assert` would have made, minus the
        # `-O` strip.
        for d in ALLOWED_TOP_LEVEL_DIRS:
            assert kind_for_path(f"{d}/x.txt") in SkillFileKind

    @pytest.mark.parametrize(
        ("path", "kind"),
        [
            ("references/g.md", SkillFileKind.REFERENCE),
            ("scripts/run.py", SkillFileKind.SCRIPT),
            ("assets/logo.png", SkillFileKind.ASSET),
            ("references/deep/nested/x.md", SkillFileKind.REFERENCE),
        ],
    )
    def test_the_directory_decides_the_kind(self, path: str, kind: SkillFileKind) -> None:
        # R31.18. Never the client: a client-chosen kind would let an uploader put a
        # script under assets/ and have it staged, or mark a binary `reference` and have
        # read_skill render its bytes into the prompt.
        assert kind_for_path(path) == kind


class TestReadGate:
    def test_all_clean_is_readable(self) -> None:
        assert_readable(_skill(), [_file(), _file(path="scripts/a.py", kind=SkillFileKind.SCRIPT)])

    def test_a_skill_with_no_files_is_readable(self) -> None:
        assert_readable(_skill(), [])

    @pytest.mark.parametrize(
        "status",
        [SkillScanStatus.QUARANTINED, SkillScanStatus.PENDING, SkillScanStatus.SKIPPED],
    )
    def test_any_non_clean_file_makes_the_whole_skill_unreadable(self, status: SkillScanStatus) -> None:
        # AC-34 / R31.20 fail-closed, knowingly stricter than the RAG precedent, which
        # treats SKIPPED as usable. A skill is executed, not read.
        with pytest.raises(SkillUnreadable) as exc:
            assert_readable(_skill(), [_file(scan_status=status)])
        assert exc.value.path == "references/guide.md"

    def test_one_bad_file_among_good_ones_still_blocks(self) -> None:
        # Q-18's rule at read time: a SKILL.md referencing a file the model cannot fetch
        # induces confabulation, so the skill is refused whole rather than in part.
        files = [
            _file(path="references/a.md"),
            _file(path="assets/b.bin", scan_status=SkillScanStatus.QUARANTINED),
        ]
        with pytest.raises(SkillUnreadable):
            assert_readable(_skill(), files)


class TestInitialScanStatus:
    def test_pending_when_a_scanner_will_run(self) -> None:
        assert fs._initial_scan_status(True) is SkillScanStatus.PENDING

    def test_clean_when_no_scanner_exists(self) -> None:
        # Mirrors rag_scan_document's first branch. Without this, a self-hosted
        # deployment with no ClamAV leaves every file `pending` forever and AC-34's
        # fail-closed gate becomes fail-shut: every skill permanently unreadable.
        assert fs._initial_scan_status(False) is SkillScanStatus.CLEAN


class TestExtraction:
    def test_markdown_is_forwarded_verbatim(self) -> None:
        text = fs._extract_text(b"# Title\n\n- a\n- b", "text/markdown")
        assert text == "# Title\n\n- a\n- b"

    def test_an_unsupported_mime_falls_back_to_a_lossy_decode(self) -> None:
        # Rather than refusing a file the author uploaded and SKILL.md references.
        assert fs._extract_text(b"\xff\xfe raw", "application/x-thing") == "�� raw"

    def test_only_reference_files_report_extracted_chars(self) -> None:
        assert fs._extracted_chars(b"hello", "text/plain", SkillFileKind.REFERENCE) == 5
        assert fs._extracted_chars(b"hello", "text/plain", SkillFileKind.SCRIPT) == 0
        assert fs._extracted_chars(b"hello", "text/plain", SkillFileKind.ASSET) == 0


class TestCaps:
    def test_the_per_file_cap_matches_the_multipart_cap(self) -> None:
        # Q-17's 32 MB per file. The coincidence with the RAG multipart cap is why
        # Phase 2 needs no tus path at all, so it is pinned rather than left to drift.
        from contexts.knowledge.application.ingest_service import MAX_MULTIPART_BYTES

        assert MAX_SKILL_FILE_BYTES == MAX_MULTIPART_BYTES == 32 * 1024 * 1024


class TestSha:
    def test_sha_is_over_the_bytes(self) -> None:
        # The only claim worth making here. The two tests that used to follow —
        # "identical bytes hash identically", "different bytes differ" — asserted
        # properties of hashlib, not of this function, and are subsumed by this one.
        import hashlib

        assert file_sha256(b"abc") == hashlib.sha256(b"abc").hexdigest()


class _Harness:
    """A SkillFileService wired to doubles, plus the audit events it emitted."""

    def __init__(self, minio: FakeMinio) -> None:
        self.files = FakeSkillFileRepo()
        self.minio = minio
        self.events: list[audit.AuditEvent] = []
        svc = SkillFileService.__new__(SkillFileService)
        svc._db = None  # type: ignore[attr-defined]
        svc._files = self.files  # type: ignore[attr-defined]
        self.svc = svc

    def actions(self) -> list[str]:
        return [e.action for e in self.events]


@pytest.fixture
def h(minio: FakeMinio, monkeypatch: pytest.MonkeyPatch) -> _Harness:
    harness = _Harness(minio)

    async def _capture(_db: object, event: audit.AuditEvent) -> None:
        harness.events.append(event)

    monkeypatch.setattr(audit, "emit", _capture)
    return harness


class TestAdd:
    async def test_an_authored_file_is_stored_and_rowed(self, h: _Harness) -> None:
        skill = _skill()
        f = await h.svc.add(
            skill=skill,
            path="references/guide.md",
            data=b"# Guide",
            mime="text/markdown",
            scan_enabled=False,
            actor_user_id=ACTOR,
        )
        assert f.kind is SkillFileKind.REFERENCE
        assert f.sha256 == file_sha256(b"# Guide")
        assert f.size_bytes == 7
        # The bytes really landed under the key the row points at — the row and the
        # object agreeing is the whole contract read_skill depends on.
        assert h.minio.objects[f.minio_key] == b"# Guide"

    async def test_an_uploaded_file_is_editable_afterwards(self, h: _Harness) -> None:
        # Q-20's real claim. An earlier version of this test called `add` twice with
        # identical arguments and asserted the two results matched — true of any
        # function, and it exercised neither route. What Q-20 actually promises is that
        # import-then-freeze was rejected: a file that arrived as opaque uploaded bytes
        # stays editable, because that is what lets someone localise an imported skill.
        skill = _skill()
        uploaded = await h.svc.add(
            skill=skill,
            path="references/a.md",
            data=b"bytes as they arrived from an upload",
            mime="application/octet-stream",
            scan_enabled=False,
            actor_user_id=ACTOR,
        )
        edited = await h.svc.update_content(
            skill=skill,
            file=uploaded,
            data=b"# now authored in the UI",
            scan_enabled=False,
            actor_user_id=ACTOR,
        )
        assert edited.sha256 == file_sha256(b"# now authored in the UI")
        assert edited.id == uploaded.id, "an edit re-points the same row, it does not add one"

    async def test_add_is_audited(self, h: _Harness) -> None:
        await h.svc.add(
            skill=_skill(),
            path="scripts/run.py",
            data=b"print(1)",
            mime="text/x-python",
            scan_enabled=False,
            actor_user_id=ACTOR,
        )
        assert h.actions() == ["skill.file_created"]
        assert h.events[0].metadata["path"] == "scripts/run.py"
        assert h.events[0].metadata["kind"] == "script"

    async def test_the_audit_resource_is_the_skill_not_the_file(self, h: _Harness) -> None:
        # A file has no independent lifecycle; keying the event on it would scatter one
        # skill's trail across ids that vanish on delete.
        skill = _skill()
        await h.svc.add(
            skill=skill,
            path="references/a.md",
            data=b"x",
            mime="text/markdown",
            scan_enabled=False,
            actor_user_id=ACTOR,
        )
        assert h.events[0].resource_type == "skill"
        assert h.events[0].resource_id == skill.id

    async def test_an_exact_path_collision_is_409(self, h: _Harness) -> None:
        skill = _skill()
        await h.svc.add(
            skill=skill,
            path="references/a.md",
            data=b"x",
            mime="text/markdown",
            scan_enabled=False,
            actor_user_id=ACTOR,
        )
        with pytest.raises(SkillFilePathTaken):
            await h.svc.add(
                skill=skill,
                path="references/a.md",
                data=b"y",
                mime="text/markdown",
                scan_enabled=False,
                actor_user_id=ACTOR,
            )

    async def test_a_case_insensitive_collision_is_409_naming_the_existing_path(self, h: _Harness) -> None:
        # The DB's UNIQUE is byte-exact and would accept both; a Windows dev box resolves
        # them to one file. This check is the only thing standing between the two.
        skill = _skill()
        await h.svc.add(
            skill=skill,
            path="assets/Logo.png",
            data=b"x",
            mime="image/png",
            scan_enabled=False,
            actor_user_id=ACTOR,
        )
        with pytest.raises(SkillFilePathTaken) as exc:
            await h.svc.add(
                skill=skill,
                path="assets/logo.png",
                data=b"y",
                mime="image/png",
                scan_enabled=False,
                actor_user_id=ACTOR,
            )
        assert exc.value.collides_with == "assets/Logo.png"

    async def test_the_same_path_on_a_different_skill_is_fine(self, h: _Harness) -> None:
        # The collision rule is per skill, not global.
        await h.svc.add(
            skill=_skill(),
            path="references/a.md",
            data=b"x",
            mime="text/markdown",
            scan_enabled=False,
            actor_user_id=ACTOR,
        )
        await h.svc.add(
            skill=_skill(),
            path="references/a.md",
            data=b"x",
            mime="text/markdown",
            scan_enabled=False,
            actor_user_id=ACTOR,
        )

    async def test_scan_enabled_leaves_the_file_pending_and_therefore_unreadable(self, h: _Harness) -> None:
        skill = _skill()
        f = await h.svc.add(
            skill=skill,
            path="references/a.md",
            data=b"x",
            mime="text/markdown",
            scan_enabled=True,
            actor_user_id=ACTOR,
        )
        assert f.scan_status is SkillScanStatus.PENDING
        # AC-34's gate and the upload path agree: a file is unreadable until scanned.
        with pytest.raises(SkillUnreadable):
            assert_readable(skill, [f])


class TestUpdate:
    async def test_editing_an_uploaded_file_changes_its_sha(self, h: _Harness) -> None:
        # AC-16: an uploaded file is editable, and editing changes sha256.
        skill = _skill()
        f = await h.svc.add(
            skill=skill,
            path="references/a.md",
            data=b"before",
            mime="text/markdown",
            scan_enabled=False,
            actor_user_id=ACTOR,
        )
        updated = await h.svc.update_content(
            skill=skill,
            file=f,
            data=b"after",
            scan_enabled=False,
            actor_user_id=ACTOR,
        )
        assert updated.sha256 == file_sha256(b"after") != f.sha256
        assert h.minio.objects[updated.minio_key] == b"after"

    async def test_an_asset_is_not_editable(self, h: _Harness) -> None:
        # AC-17: kind gates the editor. R31.18 defines an asset as opaque bytes.
        skill = _skill()
        f = await h.svc.add(
            skill=skill,
            path="assets/logo.png",
            data=b"\x89PNG",
            mime="image/png",
            scan_enabled=False,
            actor_user_id=ACTOR,
        )
        with pytest.raises(SkillFileNotEditable) as exc:
            await h.svc.update_content(
                skill=skill,
                file=f,
                data=b"evil",
                scan_enabled=False,
                actor_user_id=ACTOR,
            )
        assert exc.value.kind == "asset"

    async def test_a_script_is_editable(self, h: _Harness) -> None:
        # Only `asset` is gated — a script is text its author must be able to fix.
        skill = _skill()
        f = await h.svc.add(
            skill=skill,
            path="scripts/run.py",
            data=b"print(1)",
            mime="text/x-python",
            scan_enabled=False,
            actor_user_id=ACTOR,
        )
        updated = await h.svc.update_content(
            skill=skill,
            file=f,
            data=b"print(2)",
            scan_enabled=False,
            actor_user_id=ACTOR,
        )
        assert updated.sha256 == file_sha256(b"print(2)")

    async def test_an_edit_resets_the_scan_status(self, h: _Harness) -> None:
        # The file the scanner cleared is not the file now stored. Carrying `clean`
        # forward would let an edit smuggle content past AC-34's gate.
        skill = _skill()
        f = await h.svc.add(
            skill=skill,
            path="references/a.md",
            data=b"ok",
            mime="text/markdown",
            scan_enabled=True,
            actor_user_id=ACTOR,
        )
        await h.files.mark_scan(f.id, scan_status=SkillScanStatus.CLEAN)
        clean = await h.files.get(f.id)
        assert clean is not None
        assert clean.scan_status is SkillScanStatus.CLEAN

        updated = await h.svc.update_content(
            skill=skill,
            file=clean,
            data=b"evil",
            scan_enabled=True,
            actor_user_id=ACTOR,
        )
        assert updated.scan_status is SkillScanStatus.PENDING

    async def test_update_audits_the_sha_before_and_after(self, h: _Harness) -> None:
        skill = _skill()
        f = await h.svc.add(
            skill=skill,
            path="references/a.md",
            data=b"before",
            mime="text/markdown",
            scan_enabled=False,
            actor_user_id=ACTOR,
        )
        await h.svc.update_content(
            skill=skill,
            file=f,
            data=b"after",
            scan_enabled=False,
            actor_user_id=ACTOR,
        )
        assert h.actions() == ["skill.file_created", "skill.file_updated"]
        meta = h.events[-1].metadata
        assert meta["sha256_before"] == file_sha256(b"before")
        assert meta["sha256_after"] == file_sha256(b"after")


class TestDelete:
    async def test_delete_removes_the_row_and_audits(self, h: _Harness) -> None:
        skill = _skill()
        f = await h.svc.add(
            skill=skill,
            path="references/a.md",
            data=b"x",
            mime="text/markdown",
            scan_enabled=False,
            actor_user_id=ACTOR,
        )
        await h.svc.delete(skill=skill, file=f, actor_user_id=ACTOR)
        assert await h.files.list_for_skill(skill.id) == []
        assert h.actions() == ["skill.file_created", "skill.file_deleted"]

    async def test_deleting_frees_the_path(self, h: _Harness) -> None:
        skill = _skill()
        f = await h.svc.add(
            skill=skill,
            path="references/a.md",
            data=b"x",
            mime="text/markdown",
            scan_enabled=False,
            actor_user_id=ACTOR,
        )
        await h.svc.delete(skill=skill, file=f, actor_user_id=ACTOR)
        await h.svc.add(
            skill=skill,
            path="references/a.md",
            data=b"y",
            mime="text/markdown",
            scan_enabled=False,
            actor_user_id=ACTOR,
        )

    async def test_deleting_leaves_the_bytes(self, h: _Harness) -> None:
        # Two rows can share one content-addressed key, so removing the object here could
        # pull the bytes out from under a sibling that legitimately points at them.
        skill = _skill()
        f = await h.svc.add(
            skill=skill,
            path="references/a.md",
            data=b"x",
            mime="text/markdown",
            scan_enabled=False,
            actor_user_id=ACTOR,
        )
        await h.svc.delete(skill=skill, file=f, actor_user_id=ACTOR)
        assert h.minio.objects[f.minio_key] == b"x"

    async def test_deleting_twice_is_404(self, h: _Harness) -> None:
        skill = _skill()
        f = await h.svc.add(
            skill=skill,
            path="references/a.md",
            data=b"x",
            mime="text/markdown",
            scan_enabled=False,
            actor_user_id=ACTOR,
        )
        await h.svc.delete(skill=skill, file=f, actor_user_id=ACTOR)
        with pytest.raises(SkillFileNotFound):
            await h.svc.delete(skill=skill, file=f, actor_user_id=ACTOR)


class TestGetOwned:
    async def test_a_file_from_another_skill_is_404(self, h: _Harness) -> None:
        # §8 threat 3 (SEC-H1) one level down: a caller who owns skill A must not be able
        # to name A in the path, quote B's file id, and reach it.
        a, b = _skill("a"), _skill("b")
        mine = await h.svc.add(
            skill=a,
            path="references/a.md",
            data=b"x",
            mime="text/markdown",
            scan_enabled=False,
            actor_user_id=ACTOR,
        )
        theirs = await h.svc.add(
            skill=b,
            path="references/secret.md",
            data=b"s",
            mime="text/markdown",
            scan_enabled=False,
            actor_user_id=ACTOR,
        )
        assert (await h.svc.get_owned(a.id, mine.id)).id == mine.id
        with pytest.raises(SkillFileNotFound):
            await h.svc.get_owned(a.id, theirs.id)

    async def test_an_unknown_file_is_404(self, h: _Harness) -> None:
        with pytest.raises(SkillFileNotFound):
            await h.svc.get_owned(uuid.uuid4(), uuid.uuid4())


class TestReadText:
    async def test_reference_text_round_trips(self, h: _Harness) -> None:
        # AC-18: reference-file text is readable.
        skill = _skill()
        f = await h.svc.add(
            skill=skill,
            path="references/g.md",
            data="# 標題\n\nbody".encode(),
            mime="text/markdown",
            scan_enabled=False,
            actor_user_id=ACTOR,
        )
        assert await h.svc.read_text(f) == "# 標題\n\nbody"
