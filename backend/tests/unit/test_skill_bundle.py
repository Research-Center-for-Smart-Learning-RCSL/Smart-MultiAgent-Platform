"""AC-23 / AC-24 / AC-25 / AC-27 / AC-32 — bundle import.

The parser has its own file (`test_skill_md.py`, D-54); this one is about the zip: the
layout rules, the streaming counters, and the quarantine gate.

**Fixtures are built, not stored.** §12 calls for `backend/tests/fixtures/skill-bundles/`.
Every bundle here is assembled in memory instead (D-55), and for the case that matters
most it is the only option: AC-32's lying-header zip has to be *malformed on purpose* —
its central directory must disagree with its payload — and a checked-in binary whose whole
point is a forged size field is a fixture no reviewer can read and no linter can check. A
builder that forges the field in three lines is auditable; the bomb is described by the
code that makes it.
"""

from __future__ import annotations

import io
import os
import struct
import uuid
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from contexts.skills.application import bundle_service
from contexts.skills.application.bundle_service import (
    MAX_BUNDLE_COMPRESSED_BYTES,
    MAX_BUNDLE_ENTRIES,
    MAX_BUNDLE_UNCOMPRESSED_BYTES,
    authored_digest,
    manifest_of,
    read_bundle,
)
from contexts.skills.application.skill_md import (
    SkillManifest,
    parse_skill_md,
    render_skill_md,
)
from contexts.skills.domain.errors import BundleInvalid, BundleQuarantined, SkillUnreadable
from contexts.skills.domain.models import (
    Skill,
    SkillFile,
    SkillFileKind,
    SkillScanStatus,
    SkillScope,
    SkillSource,
)
from contexts.skills.domain.text_rules import skill_file_path_reason
from contexts.skills.interfaces import error_mapping
from contexts.skills.interfaces import facade as facade_module

SKILL_MD = "---\nname: pdf-fill\ndescription: Fills PDF forms.\n---\n\n# PDF Fill\n\nProse.\n"


def doc(frontmatter: str, body: str = "# Title\n\nProse.\n") -> str:
    return f"---\n{frontmatter}\n---\n{body}"


def build_zip(entries: dict[str, bytes | str], *, compression: int = zipfile.ZIP_DEFLATED) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression) as zf:
        for path, content in entries.items():
            zf.writestr(path, content)
    return buf.getvalue()


def valid_bundle(**extra: bytes | str) -> bytes:
    return build_zip({"SKILL.md": SKILL_MD, **extra})


class TestParsedBundleShape:
    def test_skill_md_bytes_is_required_not_defaulted(self) -> None:
        # `_assert_clean` feeds this field to the scanner as the SKILL.md payload, so a
        # `b""` default would let a construction path that omitted it scan nothing and pass
        # the gate on the one file the model is most certain to read. Required by
        # construction; this pins it so a future default re-addition is caught here rather
        # than by a silently-unscanned import.
        with pytest.raises(TypeError):
            bundle_service.ParsedBundle(manifest=None, files=(), warnings=())  # type: ignore[call-arg]


class TestAValidBundle:
    def test_it_imports_and_the_frontmatter_populates_the_manifest(self) -> None:
        # AC-23.
        parsed = read_bundle(valid_bundle())
        assert parsed.manifest.name == "pdf-fill"
        assert parsed.manifest.description == "Fills PDF forms."
        assert parsed.manifest.body.strip().startswith("# PDF Fill")

    def test_the_three_allowed_directories_all_carry_files(self) -> None:
        parsed = read_bundle(
            valid_bundle(
                **{
                    "references/guide.md": "# Guide",
                    "scripts/run.py": "print('hi')",
                    "assets/logo.png": b"\x89PNG\r\n\x1a\n",
                }
            )
        )
        assert [f.path for f in parsed.files] == [
            "assets/logo.png",
            "references/guide.md",
            "scripts/run.py",
        ]

    def test_files_are_sorted_so_the_order_does_not_depend_on_the_zip(self) -> None:
        # The zip's entry order is the packer's choice; a deterministic export (Q-19)
        # cannot rest on it.
        parsed = read_bundle(
            valid_bundle(**{"scripts/z.py": "z", "references/a.md": "a", "assets/m.bin": b"m"})
        )
        assert [f.path for f in parsed.files] == sorted(f.path for f in parsed.files)

    def test_the_bytes_are_the_inflated_bytes(self) -> None:
        parsed = read_bundle(valid_bundle(**{"references/guide.md": "# Guide\n"}))
        assert parsed.files[0].data == b"# Guide\n"

    def test_a_stored_uncompressed_bundle_is_fine(self) -> None:
        parsed = read_bundle(build_zip({"SKILL.md": SKILL_MD}, compression=zipfile.ZIP_STORED))
        assert parsed.manifest.name == "pdf-fill"

    def test_a_directory_entry_is_not_a_file(self) -> None:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("SKILL.md", SKILL_MD)
            zf.writestr("references/", b"")
        assert read_bundle(buf.getvalue()).files == ()


class TestTheLayoutMatrix:
    """AC-24. Every arm rejects, and each names the entry so an author can fix it."""

    @pytest.mark.parametrize(
        "path",
        [
            "../escape.md",
            "references/../../escape.md",
            "/absolute.md",
            "references/./x.md",
        ],
    )
    def test_traversal_and_absolute_paths_are_rejected(self, path: str) -> None:
        with pytest.raises(BundleInvalid) as exc:
            read_bundle(valid_bundle(**{path: "x"}))
        assert exc.value.path == path

    @pytest.mark.parametrize("path", ["notes.md", "docs/notes.md", "src/run.py"])
    def test_an_unknown_top_level_directory_is_rejected(self, path: str) -> None:
        # R31.19 allows exactly references/, scripts/, assets/. A root file is rejected by
        # the same rule: there is no "root" kind for it to be.
        with pytest.raises(BundleInvalid) as exc:
            read_bundle(valid_bundle(**{path: "x"}))
        assert "must live under" in exc.value.reason

    def test_a_missing_skill_md_is_rejected(self) -> None:
        with pytest.raises(BundleInvalid) as exc:
            read_bundle(build_zip({"references/guide.md": "# Guide"}))
        assert "SKILL.md" in exc.value.reason

    def test_a_nested_skill_md_does_not_satisfy_the_root_requirement(self) -> None:
        with pytest.raises(BundleInvalid):
            read_bundle(build_zip({"references/SKILL.md": SKILL_MD}))

    def test_an_empty_zip_is_rejected(self) -> None:
        with pytest.raises(BundleInvalid) as exc:
            read_bundle(build_zip({}))
        assert "empty" in exc.value.reason

    def test_a_non_zip_is_rejected_rather_than_raising(self) -> None:
        with pytest.raises(BundleInvalid) as exc:
            read_bundle(b"this is not a zip file at all")
        assert "zip" in exc.value.reason

    def test_a_case_insensitive_collision_is_rejected(self) -> None:
        # Imports cleanly on Linux prod, destroys one file on a Windows dev box.
        with pytest.raises(BundleInvalid) as exc:
            read_bundle(valid_bundle(**{"references/Guide.md": "a", "references/guide.md": "b"}))
        assert "collide" in exc.value.reason

    def test_a_non_nfc_path_is_rejected(self) -> None:
        # U+0065 U+0301 (e + combining acute) rather than U+00E9. The two render
        # identically and collide in `UNIQUE (skill_id, path)` on one filesystem only.
        decomposed = "references/caf" + chr(0x65) + chr(0x301) + ".md"
        with pytest.raises(BundleInvalid) as exc:
            read_bundle(valid_bundle(**{decomposed: "x"}))
        assert "NFC" in exc.value.reason

    def test_a_newline_in_a_path_is_rejected(self) -> None:
        # read_skill renders the file manifest into the model's context, so a newline here
        # forges a manifest entry exactly as one in a description forges an index line.
        with pytest.raises(BundleInvalid) as exc:
            read_bundle(valid_bundle(**{"references/a\nb.md": "x"}))
        assert exc.value.path is not None

    def test_a_windows_reserved_name_is_rejected(self) -> None:
        with pytest.raises(BundleInvalid) as exc:
            read_bundle(valid_bundle(**{"scripts/CON.py": "x"}))
        assert "reserved" in exc.value.reason

    @pytest.mark.skipif(
        os.sep != "/",
        reason=(
            "zipfile normalizes backslashes to '/' on a backslash-separator OS — "
            "`ZipInfo.__init__` does `filename.replace(os.sep, '/')`, and the *reader* "
            "builds ZipInfos too. So on a Windows dev box no backslash path can reach "
            "`read_bundle` at all, and a fixture asserting one would be asserting "
            "zipfile's behaviour rather than ours. On Linux — prod — os.sep is '/', the "
            "replacement is a no-op, and the path arrives intact for the rule to reject."
        ),
    )
    def test_a_backslash_path_is_rejected(self) -> None:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("SKILL.md", SKILL_MD)
            info = zipfile.ZipInfo("placeholder")
            info.filename = "references\\guide.md"
            zf.writestr(info, b"x")
        with pytest.raises(BundleInvalid) as exc:
            read_bundle(buf.getvalue())
        assert "backslash" in exc.value.reason

    def test_the_backslash_rule_itself_holds_on_every_platform(self) -> None:
        # The platform-independent half of the case above: the predicate the importer
        # calls rejects it, whatever this OS's zipfile does to the path on the way in.
        assert "backslash" in (skill_file_path_reason("references\\guide.md") or "")

    def test_a_reserved_frontmatter_key_rejects_the_bundle(self) -> None:
        # The parser's rule, reached through the importer — the wiring is the assertion.
        body = "---\nname: t\ndescription: d\nscope: platform\n---\n"
        with pytest.raises(BundleInvalid) as exc:
            read_bundle(build_zip({"SKILL.md": body}))
        assert exc.value.key == "scope"

    def test_an_unrecognized_frontmatter_key_does_not_reject_the_bundle(self) -> None:
        # AC-24's list ends with this exception, and it is the one Q-29 was rewritten for.
        body = "---\nname: t\ndescription: d\nx-vendor-foo: bar\nversion: 1.2.0\n---\n"
        parsed = read_bundle(build_zip({"SKILL.md": body}))
        assert parsed.manifest.extra_frontmatter == {"x-vendor-foo": "bar", "version": "1.2.0"}

    def test_a_skill_md_that_is_not_utf8_is_rejected(self) -> None:
        with pytest.raises(BundleInvalid) as exc:
            read_bundle(build_zip({"SKILL.md": b"\xff\xfe\x00binary"}))
        assert "UTF-8" in exc.value.reason


class TestLinksAndEncryption:
    def _zip_with_mode(self, path: str, mode: int, content: bytes) -> bytes:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("SKILL.md", SKILL_MD)
            info = zipfile.ZipInfo(path)
            info.create_system = 3  # Unix, which is what makes external_attr a mode
            info.external_attr = mode << 16
            zf.writestr(info, content)
        return buf.getvalue()

    def test_a_symlink_entry_is_rejected(self) -> None:
        # Its "content" is the target path. Honoring it follows a link out of the bundle;
        # treating it as a file stores the target string as if it were the file.
        data = self._zip_with_mode("references/link.md", 0o120777, b"/etc/passwd")
        with pytest.raises(BundleInvalid) as exc:
            read_bundle(data)
        assert "symlink" in exc.value.reason

    def test_an_ordinary_unix_mode_file_is_not_mistaken_for_a_link(self) -> None:
        data = self._zip_with_mode("references/plain.md", 0o100644, b"# Plain")
        assert read_bundle(data).files[0].path == "references/plain.md"

    def test_an_encrypted_entry_is_rejected_because_it_cannot_be_scanned(self) -> None:
        # `writestr` drops the encryption flag, so this is patched in afterwards. Bytes we
        # cannot read are bytes ClamAV cannot scan, and AC-25's gate would pass them by
        # never seeing them — so this rejects before anything tries to decompress.
        data = _patch_entry_headers(
            build_zip({"SKILL.md": SKILL_MD, "references/secret.md": b"ciphertext"}),
            entry=b"references/secret.md",
            flags=0x1,
        )
        with pytest.raises(BundleInvalid) as exc:
            read_bundle(data)
        assert "encrypted" in exc.value.reason

    def test_a_non_utf8_flagged_non_ascii_path_is_rejected_rather_than_guessed(self) -> None:
        # zipfile forces the UTF-8 name flag on when it writes a non-ASCII name, so the
        # flag is cleared afterwards to produce what a legacy packer emits. Reading it
        # back, zipfile decodes the raw bytes as CP437 and *guesses* — in a zh-TW product,
        # importing the guess means storing mojibake as the path SKILL.md references.
        name = "references/" + chr(0x8AAA) + chr(0x660E) + ".md"
        data = build_zip({"SKILL.md": SKILL_MD, name: b"x"})
        data = _patch_entry_headers(data, entry=name.encode("utf-8"), flags=0)
        with zipfile.ZipFile(io.BytesIO(data)) as check:
            stored = next(n for n in check.namelist() if n != "SKILL.md")
        # The guess really happened: the name came back neither as written nor as ASCII.
        assert stored != name
        assert not stored.isascii()
        with pytest.raises(BundleInvalid) as exc:
            read_bundle(data)
        assert "UTF-8" in exc.value.reason


class TestTheStreamingCounters:
    """AC-32 and the size half of AC-24. Never a header, always the inflated byte."""

    def test_an_honest_bomb_is_rejected_by_the_ratio_rule(self) -> None:
        # Declares what it is and delivers it: ~64 MB of zeros from a tiny archive.
        #
        # **The reason is asserted, not merely the rejection**, and that is the whole
        # lesson of this class. The size rules mask one another — a bomb this size trips
        # the ratio rule *and* the per-file cap *and* the total budget — so a test that
        # only asserts `raises(BundleInvalid)` stays green with any one of the three
        # deleted. Probed: it did. Naming the rule makes each one load-bearing.
        data = build_zip({"SKILL.md": SKILL_MD, "assets/bomb.bin": b"\x00" * (64 * 1024 * 1024)})
        with pytest.raises(BundleInvalid) as exc:
            read_bundle(data)
        assert "ratio" in exc.value.reason

    def test_a_lying_header_bundle_is_rejected(self) -> None:
        # **The AC-32 case, and it does not happen the way AC-32 says** (D-56). The central
        # directory declares 1 MB over a ~200 MB payload. AC-32 predicts the streaming
        # counter catches it mid-inflate; measured, CPython's `zipfile` bounds the read at
        # the *declared* size, so the 200 MB never inflates and the counter never sees a
        # byte — the CRC check fails against the truncated stream instead.
        #
        # What is asserted is therefore the outcome that is both true and load-bearing:
        # the bundle is rejected as `BundleInvalid` (a 422), not with a `BadZipFile`
        # escaping as a 500 — which is what happened before the fix this test drove.
        payload = b"\x00" * (200 * 1024 * 1024)
        data = _patch_entry_headers(
            build_zip({"SKILL.md": SKILL_MD, "assets/bomb.bin": payload}),
            entry=b"assets/bomb.bin",
            size=1024 * 1024,
        )
        # The forgery is real: zipfile reports the lie rather than the truth.
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            assert zf.getinfo("assets/bomb.bin").file_size == 1024 * 1024

        with pytest.raises(BundleInvalid) as exc:
            read_bundle(data)
        assert exc.value.path == "assets/bomb.bin"

    def test_a_lying_header_never_yields_bytes_the_counter_did_not_count(self) -> None:
        # The invariant behind the case above, stated so a future decompressor change is
        # caught: whatever a forged header claims, nothing reaches a caller unmeasured. If
        # some future zipfile *stops* bounding reads by the declared size, the 200 MB then
        # flows through `_read_entry` and the budget rejects it — this test stays green
        # through that change, and its sibling above would need rewording, not repair.
        payload = b"\x00" * (8 * 1024 * 1024)
        data = _patch_entry_headers(
            build_zip({"SKILL.md": SKILL_MD, "assets/x.bin": payload}),
            entry=b"assets/x.bin",
            size=64,
        )
        with pytest.raises(BundleInvalid):
            read_bundle(data)

    def test_a_corrupt_deflate_payload_is_a_422_not_a_500(self) -> None:
        # **The regression test for a bug the self-audit found.** A corrupt payload does
        # not raise `BadZipFile` — it raises `zlib.error` straight out of the decompressor,
        # which zipfile does not wrap. The first version of this handler caught
        # `ValueError` on suspicion, which catches nothing zipfile emits, so every
        # corrupt-payload bundle escaped as a 500. Measured before it was fixed.
        data = bytearray(valid_bundle(**{"references/x.md": "x" * 5000}))
        pos = data.find(b"PK\x03\x04")
        while pos != -1:
            nlen = struct.unpack_from("<H", data, pos + 26)[0]
            elen = struct.unpack_from("<H", data, pos + 28)[0]
            if bytes(data[pos + 30 : pos + 30 + nlen]) == b"references/x.md":
                csize = struct.unpack_from("<I", data, pos + 18)[0]
                start = pos + 30 + nlen + elen
                for i in range(start, min(start + csize, len(data))):
                    data[i] = 0xFF
                break
            pos = data.find(b"PK\x03\x04", pos + 4)

        with pytest.raises(BundleInvalid) as exc:
            read_bundle(bytes(data))
        assert exc.value.path == "references/x.md"

    def test_an_invalid_utf8_name_under_the_utf8_flag_is_a_422_not_a_500(self) -> None:
        # **Found by the security gate, and it is a two-byte header edit.** `zipfile`'s
        # `_RealGetContents` does a bare `filename.decode('utf-8')` when flag 0x800 is set,
        # with no error handling — so this crashes the *constructor*, and every rule in
        # `read_bundle` is unreachable. `_entry_path`'s non-UTF-8 arm was written for this
        # attack and never sees it.
        original = "references/xxxxx.md"  # 19 bytes
        forged = b"references/\xff\xfe\xfd\xfc\xfb.md"  # the same 19, and not UTF-8
        assert len(forged) == len(original.encode())  # a length mismatch skips the patch
        data = _patch_entry_headers(
            build_zip({"SKILL.md": SKILL_MD, original: b"x"}),
            entry=original.encode(),
            flags=0x800,
            name_bytes=forged,
        )
        with pytest.raises(BundleInvalid) as exc:
            read_bundle(data)
        assert "zip archive" in exc.value.reason

    def test_an_unsupported_compression_method_is_a_422_not_a_500(self) -> None:
        # `_check_compression` raises NotImplementedError, which is not a BadZipFile.
        # Another two-byte edit, another 500 before this arm existed.
        data = _patch_entry_headers(
            build_zip({"SKILL.md": SKILL_MD, "references/x.md": b"x"}),
            entry=b"references/x.md",
            method=99,
        )
        with pytest.raises(BundleInvalid) as exc:
            read_bundle(data)
        assert exc.value.path == "references/x.md"

    def test_a_truncated_archive_is_a_422_not_a_500(self) -> None:
        whole = valid_bundle(**{"references/x.md": "x" * 5000})
        with pytest.raises(BundleInvalid):
            read_bundle(whole[: len(whole) // 2])

    def test_the_ratio_rule_rejects_below_every_absolute_cap(self) -> None:
        # 20 MB of zeros: under the 32 MB per-file cap and the 128 MB total, so neither
        # absolute rule can fire and the ratio is the only thing standing between this
        # bundle and 20 MB of allocation from a ~20 KB upload.
        data = build_zip({"SKILL.md": SKILL_MD, "assets/z.bin": b"\x00" * (20 * 1024 * 1024)})
        assert len(data) * 100 < 20 * 1024 * 1024  # the ratio really is over 100:1
        with pytest.raises(BundleInvalid) as exc:
            read_bundle(data)
        assert "ratio" in exc.value.reason

    def test_the_per_file_cap_rejects_below_the_ratio_and_total_rules(self) -> None:
        # The per-file cap in isolation. Incompressible bytes, stored rather than
        # deflated, so the ratio is ~1:1 and cannot fire; 33 MB is far under the 128 MB
        # total, so that cannot fire either. Only the per-file cap is left — and with it
        # deleted this bundle imports cleanly, which is what the probe proved.
        payload = os.urandom(33 * 1024 * 1024)
        data = build_zip({"SKILL.md": SKILL_MD, "assets/big.bin": payload}, compression=zipfile.ZIP_STORED)
        with pytest.raises(BundleInvalid) as exc:
            read_bundle(data)
        assert "per-file" in exc.value.reason
        # A plain `==`, not `path == X or "per-file" in reason`: the right disjunct was
        # already asserted above, so the `or` form could never fail and left `path`
        # untested — point the per-file raise at the wrong entry and it stayed green.
        assert exc.value.path == "assets/big.bin"

    def test_an_honest_small_but_highly_compressible_bundle_is_not_a_bomb(self) -> None:
        # The false positive the floor exists to prevent: ordinary repetitive prose beats
        # 100:1 easily at small sizes. Ratio is a property of text, not of malice.
        text = "The quick brown fox. " * 4000  # ~84 KB, deflates to well under 1 KB
        data = build_zip({"SKILL.md": SKILL_MD, "references/notes.md": text})
        assert len(text) > len(data) * 100  # over 100:1, and legitimate
        assert read_bundle(data).files[0].data.decode() == text

    def test_too_many_entries_is_rejected(self) -> None:
        # MAX_BUNDLE_ENTRIES files *plus* SKILL.md is one entry over the cap.
        entries: dict[str, bytes | str] = {"SKILL.md": SKILL_MD}
        entries.update({f"references/f{i}.md": "x" for i in range(MAX_BUNDLE_ENTRIES)})
        with pytest.raises(BundleInvalid) as exc:
            read_bundle(build_zip(entries))
        assert str(MAX_BUNDLE_ENTRIES) in exc.value.reason

    def test_an_over_cap_compressed_bundle_is_rejected_before_it_is_opened(self) -> None:
        with pytest.raises(BundleInvalid) as exc:
            read_bundle(b"x" * (MAX_BUNDLE_COMPRESSED_BYTES + 1))
        assert "compressed" in exc.value.reason

    def test_a_maximal_skills_export_is_importable(self) -> None:
        # **The invariant that matters, and the one the first version of this test missed.**
        # It asserted `MAX_BUNDLE_ENTRIES - 1 <= MAX_SKILL_FILES` — true, and irrelevant:
        # it checks that a bundle cannot *over*-fill a skill, while the defect ran the
        # other way. `_assert_path_free` refuses an add at `>= MAX_SKILL_FILES`, so a skill
        # holds exactly 500 files and exports 501 entries — which a flat 500 rejected. The
        # exporter emitted precisely what its own importer refused, and the comment above
        # the constant asserted the opposite.
        from contexts.skills.application.file_service import MAX_SKILL_FILES

        entries_a_full_skill_exports = MAX_SKILL_FILES + 1  # its files, plus SKILL.md
        assert entries_a_full_skill_exports <= MAX_BUNDLE_ENTRIES

    def test_the_boundary_bundle_actually_round_trips(self) -> None:
        # The arithmetic above stated as behaviour: a bundle at exactly the cap survives
        # the packer and the reader. Cheap because the files are empty — the point is the
        # count, not the bytes.
        from contexts.skills.application.file_service import MAX_SKILL_FILES

        files = [(f"references/f{i:04d}.md", b"x") for i in range(MAX_SKILL_FILES)]
        packed = bundle_service.write_bundle(skill_md=SKILL_MD, files=files)
        with zipfile.ZipFile(io.BytesIO(packed)) as zf:
            assert len(zf.namelist()) == MAX_SKILL_FILES + 1
        assert len(read_bundle(packed).files) == MAX_SKILL_FILES

    def test_one_entry_past_the_cap_is_still_rejected(self) -> None:
        from contexts.skills.application.file_service import MAX_SKILL_FILES

        files = [(f"references/f{i:04d}.md", b"x") for i in range(MAX_SKILL_FILES + 1)]
        with pytest.raises(BundleInvalid) as exc:
            read_bundle(bundle_service.write_bundle(skill_md=SKILL_MD, files=files))
        assert str(MAX_BUNDLE_ENTRIES) in exc.value.reason

    def test_the_per_file_ceiling_is_the_per_file_api_ceiling(self) -> None:
        from contexts.skills.application.file_service import MAX_SKILL_FILE_BYTES

        assert bundle_service.MAX_SKILL_FILE_BYTES == MAX_SKILL_FILE_BYTES

    def test_the_uncompressed_ceiling_is_the_agent_files_volume_ceiling(self) -> None:
        # Q-17 pins this to `_MAX_AGENT_FILES_BYTES` rather than choosing a number: a
        # bundle's files land on the volume that constant bounds.
        from contexts.agents.application.runtime.turn_engine import _MAX_AGENT_FILES_BYTES

        assert MAX_BUNDLE_UNCOMPRESSED_BYTES == _MAX_AGENT_FILES_BYTES


class TestTheInflationBudget:
    """The counter itself, tested directly.

    It needs its own tests because the three size rules mask each other end-to-end: the
    128 MB total is **unreachable through `read_bundle` with incompressible data** — you
    cannot inflate past 128 MB without either exceeding the 64 MB compressed cap or
    beating the 100:1 ratio — so an integration test for it necessarily trips a different
    rule first. Testing the unit is the only way to state the rule honestly.
    """

    def test_it_counts_up_to_the_total_and_then_rejects(self) -> None:
        b = bundle_service._InflationBudget(compressed_bytes=MAX_BUNDLE_UNCOMPRESSED_BYTES)
        b.consume(MAX_BUNDLE_UNCOMPRESSED_BYTES)  # exactly at the cap is fine
        assert b.inflated == MAX_BUNDLE_UNCOMPRESSED_BYTES
        with pytest.raises(BundleInvalid) as exc:
            b.consume(1)
        assert "inflates past" in exc.value.reason

    def test_the_total_accumulates_across_entries(self) -> None:
        # The rule a per-file cap cannot express: many legal files, one illegal set.
        b = bundle_service._InflationBudget(compressed_bytes=MAX_BUNDLE_UNCOMPRESSED_BYTES)
        chunk = MAX_BUNDLE_UNCOMPRESSED_BYTES // 4
        for _ in range(4):
            b.consume(chunk)
        with pytest.raises(BundleInvalid):
            b.consume(chunk)

    def test_the_ratio_is_measured_against_the_compressed_size_we_were_handed(self) -> None:
        # Never a header field. `compressed_bytes` is `len(data)` — bytes we hold.
        b = bundle_service._InflationBudget(compressed_bytes=1024)
        with pytest.raises(BundleInvalid) as exc:
            b.consume(1024 * 100 * 100)
        assert "ratio" in exc.value.reason

    def test_a_high_ratio_under_the_floor_is_not_a_bomb(self) -> None:
        # The false positive the floor exists to prevent, at the unit level: a 1 KB
        # archive inflating to 1 MB is 1000:1 and is a text file.
        b = bundle_service._InflationBudget(compressed_bytes=1024)
        b.consume(1024 * 1024)
        assert b.inflated == 1024 * 1024

    def test_the_floor_does_not_disable_the_total(self) -> None:
        # A ratio under 100:1 must still not inflate forever.
        b = bundle_service._InflationBudget(compressed_bytes=MAX_BUNDLE_UNCOMPRESSED_BYTES)
        with pytest.raises(BundleInvalid) as exc:
            b.consume(MAX_BUNDLE_UNCOMPRESSED_BYTES + 1)
        assert "inflates past" in exc.value.reason

    def test_it_is_wired_into_the_reader(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # The claim the unit tests above cannot make: `_read_entry` actually calls
        # `consume` for every chunk it inflates. Probed by deleting that one line — which
        # left the whole suite green before this test existed, because the per-file cap
        # silently covered for it.
        monkeypatch.setattr(bundle_service, "MAX_BUNDLE_UNCOMPRESSED_BYTES", 32)
        with pytest.raises(BundleInvalid) as exc:
            read_bundle(valid_bundle(**{"references/a.md": "x" * 500}))
        assert "inflates past" in exc.value.reason


class TestTheNetworkWarning:
    """AC-27 / Q-10. A warning, never a rejection — the author decides, not the importer."""

    @pytest.mark.parametrize(
        "code",
        [
            "import requests\nrequests.get('https://x')",
            "from urllib.request import urlopen\nurlopen('https://x')",
            "import socket\ns = socket.socket()",
            "import httpx",
        ],
    )
    def test_a_network_using_script_imports_with_a_warning(self, code: str) -> None:
        parsed = read_bundle(valid_bundle(**{"scripts/fetch.py": code}))
        assert parsed.files  # imported, not rejected
        assert any("network" in w for w in parsed.warnings)
        assert any("scripts/fetch.py" in w for w in parsed.warnings)

    def test_an_offline_script_warns_about_nothing(self) -> None:
        parsed = read_bundle(valid_bundle(**{"scripts/calc.py": "print(1 + 1)"}))
        assert parsed.warnings == ()

    def test_a_reference_file_mentioning_requests_is_not_a_script_warning(self) -> None:
        # Only `scripts/` runs. Prose about `import requests` in a guide is documentation.
        parsed = read_bundle(valid_bundle(**{"references/guide.md": "Use `import requests`"}))
        assert parsed.warnings == ()

    def test_a_binary_under_scripts_does_not_crash_the_hint_scan(self) -> None:
        parsed = read_bundle(valid_bundle(**{"scripts/blob.py": b"\xff\xfe\x00\x01"}))
        assert parsed.warnings == ()


class TestTheAuthoredDigest:
    """Q-30's byte set, and the number a "diverged" badge compares against (AC-16)."""

    def _digest(self, **over: object) -> str:
        base: dict[str, object] = {
            "name": "pdf-fill",
            "description": "Fills forms.",
            "requires": (),
            "allowed_tools": (),
            "body": "# Body",
            "file_digests": {"references/a.md": "aa"},
        }
        base.update(over)
        return authored_digest(**base)  # type: ignore[arg-type]

    def test_it_is_stable_across_calls(self) -> None:
        assert self._digest() == self._digest()

    @pytest.mark.parametrize(
        "change",
        [
            {"name": "other"},
            {"description": "Different."},
            {"body": "# Other"},
            {"requires": ("code_exec",)},
            {"allowed_tools": ("Read",)},
            {"file_digests": {"references/a.md": "bb"}},
            {"file_digests": {"references/b.md": "aa"}},
        ],
    )
    def test_every_member_of_the_byte_set_changes_it(self, change: dict[str, object]) -> None:
        assert self._digest(**change) != self._digest()

    def test_field_boundaries_cannot_be_impersonated(self) -> None:
        # Length-prefixed framing: without it, moving a character across a boundary would
        # collide two different skills onto one digest.
        assert self._digest(name="ab", description="c") != self._digest(name="a", description="bc")

    def test_file_order_does_not_change_it(self) -> None:
        a = self._digest(file_digests={"references/a.md": "1", "scripts/b.py": "2"})
        b = self._digest(file_digests={"scripts/b.py": "2", "references/a.md": "1"})
        assert a == b


class FakeScanResult:
    def __init__(self, clean: bool, threat: str | None = None) -> None:
        self.clean = clean
        self.threat_name = threat


class FakeScanner:
    """Verdicts keyed by the bytes handed in, so a test can quarantine one entry."""

    def __init__(self, quarantine: set[bytes] | None = None) -> None:
        self.scanned: list[bytes] = []
        self._quarantine = quarantine or set()

    async def scan(self, data: bytes) -> FakeScanResult:
        self.scanned.append(data)
        if data in self._quarantine:
            return FakeScanResult(False, "Eicar-Test-Signature")
        return FakeScanResult(True)


class FakeSkillService:
    def __init__(self, _db: object) -> None:
        self.created: list[dict[str, object]] = []

    async def create(self, **kwargs: object) -> object:
        self.created.append(kwargs)
        return SimpleNamespace(id=uuid.uuid4(), name=kwargs["name"])


class FakeSkillFileService:
    def __init__(self, _db: object) -> None:
        self.added: list[dict[str, object]] = []

    async def add_many(self, *, files: object, **_kwargs: object) -> list[object]:
        created: list[object] = []
        for path, _data, mime in files:  # type: ignore[attr-defined]
            self.added.append({"path": path, "mime": mime})
            # `sha256` is not decoration: the facade enqueues `(file_id, sha256)`, and
            # `skill_scan_file` conditions every verdict on that sha so a scan of replaced
            # bytes cannot land. A fake without it hides whether the facade passes it.
            created.append(SimpleNamespace(id=uuid.uuid4(), sha256="a" * 64))
        return created


@dataclass
class Wiring:
    skills: FakeSkillService
    files: FakeSkillFileService
    scanner: FakeScanner


def wire(
    monkeypatch: pytest.MonkeyPatch,
    *,
    scan_enabled: bool = True,
    quarantine: set[bytes] | None = None,
) -> Wiring:
    skills = FakeSkillService(None)
    files = FakeSkillFileService(None)
    scanner = FakeScanner(quarantine)

    monkeypatch.setattr(bundle_service, "SkillService", lambda _db: skills)
    monkeypatch.setattr(bundle_service, "SkillFileService", lambda _db: files)

    class _Settings:
        security = SimpleNamespace(file_scan_enabled=scan_enabled)

    monkeypatch.setattr(bundle_service, "get_settings", lambda: _Settings())
    # The **source module**, not `bundle_service.get_scanner`: the import is function-local
    # (matching the worker's lazy load), so patching a name on `bundle_service` would bind
    # nothing and the fake would never be reached. D-47's lesson — a test that only works
    # because of where an import sits is pinning an artifact, not a behaviour.
    monkeypatch.setattr("shared_kernel.scanning.get_scanner", lambda: scanner)
    return Wiring(skills=skills, files=files, scanner=scanner)


class TestImportOrchestration:
    async def test_a_clean_bundle_creates_the_skill_and_its_files(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        w = wire(monkeypatch)
        data = valid_bundle(**{"references/guide.md": "# Guide", "scripts/run.py": "print(1)"})

        result = await bundle_service.BundleService(None).import_bundle(  # type: ignore[arg-type]
            data=data,
            scope=SkillScope.PROJECT,
            owner_id=uuid.uuid4(),
            actor_user_id=uuid.uuid4(),
        )

        assert result.skill.name == "pdf-fill"
        assert result.warnings == ()
        assert [f["path"] for f in w.files.added] == ["references/guide.md", "scripts/run.py"]

    async def test_it_is_recorded_as_imported_with_an_authored_digest(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        w = wire(monkeypatch)
        await bundle_service.BundleService(None).import_bundle(  # type: ignore[arg-type]
            data=valid_bundle(),
            scope=SkillScope.PROJECT,
            owner_id=uuid.uuid4(),
            actor_user_id=uuid.uuid4(),
        )
        created = w.skills.created[0]
        assert created["source"] is SkillSource.IMPORTED
        # `bundle_sha256` is the authored-byte-set digest (Q-30), never a hash of the zip:
        # zip bytes carry timestamps, so hashing them would make every skill diverged
        # forever the moment it was exported.
        assert created["bundle_sha256"] == authored_digest(
            name="pdf-fill",
            description="Fills PDF forms.",
            requires=(),
            allowed_tools=(),
            body=read_bundle(valid_bundle()).manifest.body,
            file_digests={},
        )

    async def test_scope_and_owner_come_from_the_caller_never_from_frontmatter(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # §8 threat 2. The frontmatter cannot say `scope:` at all (the parser rejects it),
        # and this is the other half: what the importer copies is named one field at a
        # time, so an unknown key has no route to a column even if the parser gained one.
        w = wire(monkeypatch)
        owner = uuid.uuid4()
        await bundle_service.BundleService(None).import_bundle(  # type: ignore[arg-type]
            data=valid_bundle(),
            scope=SkillScope.ORG,
            owner_id=owner,
            actor_user_id=uuid.uuid4(),
        )
        created = w.skills.created[0]
        assert created["scope"] is SkillScope.ORG
        assert created["owner_id"] == owner

    async def test_tolerated_frontmatter_reaches_extra_frontmatter(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        w = wire(monkeypatch)
        body = "---\nname: t\ndescription: d\nversion: 2.1.0\n---\nBody\n"
        await bundle_service.BundleService(None).import_bundle(  # type: ignore[arg-type]
            data=build_zip({"SKILL.md": body}),
            scope=SkillScope.PLATFORM,
            owner_id=None,
            actor_user_id=uuid.uuid4(),
        )
        assert w.skills.created[0]["extra_frontmatter"] == {"version": "2.1.0"}

    async def test_the_network_warning_reaches_the_caller(self, monkeypatch: pytest.MonkeyPatch) -> None:
        wire(monkeypatch)
        result = await bundle_service.BundleService(None).import_bundle(  # type: ignore[arg-type]
            data=valid_bundle(**{"scripts/f.py": "import requests"}),
            scope=SkillScope.PROJECT,
            owner_id=uuid.uuid4(),
            actor_user_id=uuid.uuid4(),
        )
        assert any("network" in w for w in result.warnings)


class TestTheFacadeSeam:
    """**The test that was missing, and its absence was a Critical.**

    `SkillFileService.add` does not enqueue anything — only `SkillsFacade` does, at
    `add_file` and here. `BundleService.import_bundle` called `add` directly, so an
    imported file sat `pending` forever, and under AC-34's fail-closed gate that is a skill
    that never becomes readable. Nothing caught it: every import test drove the service,
    which is the layer with the hole in it, and the service's own docstring asserted a
    re-scan that no code performed (D-48's class, and D-12's).
    """

    async def test_the_facade_enqueues_a_scan_for_every_imported_file(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        w = wire(monkeypatch)
        enqueued: list[tuple[uuid.UUID, str]] = []

        async def _capture(*, file_id: uuid.UUID, sha256: str) -> None:
            enqueued.append((file_id, sha256))

        monkeypatch.setattr(facade_module, "enqueue_skill_scan", _capture)
        db = _CommitSpy()
        monkeypatch.setattr(facade_module, "BundleService", lambda _db: _FakeBundleService(w))

        result = await facade_module.SkillsFacade(db).import_bundle(  # type: ignore[arg-type]
            data=valid_bundle(**{"references/a.md": "a", "scripts/b.py": "b"}),
            scope=SkillScope.PROJECT,
            owner_id=uuid.uuid4(),
            actor_user_id=uuid.uuid4(),
        )
        assert [f.id for f in result.files] == [fid for fid, _ in enqueued]
        assert len(enqueued) == 2

    async def test_it_commits_before_it_enqueues(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Load-bearing, and the reason `add_file`'s docstring says so: `skill_scan_file`
        # reads the row on its own connection, so an enqueue inside an open transaction is
        # a race the worker can win — and its not-found arm returns rather than retries, so
        # losing it strands the file `pending` forever. Exactly the outcome the missing
        # enqueue produced, arrived at from the other direction.
        w = wire(monkeypatch)
        order: list[str] = []
        db = _CommitSpy(order)

        async def _capture(*, file_id: uuid.UUID, sha256: str) -> None:
            order.append("enqueue")

        monkeypatch.setattr(facade_module, "enqueue_skill_scan", _capture)
        monkeypatch.setattr(facade_module, "BundleService", lambda _db: _FakeBundleService(w))

        await facade_module.SkillsFacade(db).import_bundle(  # type: ignore[arg-type]
            data=valid_bundle(**{"references/a.md": "a"}),
            scope=SkillScope.PROJECT,
            owner_id=uuid.uuid4(),
            actor_user_id=uuid.uuid4(),
        )
        assert order == ["commit", "enqueue"]

    def test_the_service_is_not_the_entry_point_and_says_so(self) -> None:
        # A docstring test, which this file would normally not write. It earns its place:
        # the defect was that the only *correct* way to import is invisible from the
        # service, and the service previously documented the opposite.
        doc = bundle_service.BundleService.import_bundle.__doc__ or ""
        assert "SkillsFacade.import_bundle" in doc


class _CommitSpy:
    def __init__(self, order: list[str] | None = None) -> None:
        self.order = order if order is not None else []

    async def commit(self) -> None:
        self.order.append("commit")


class _FakeBundleService:
    """Runs the real import against the wired fakes, so the facade's own steps are what
    this class tests rather than the import it delegates."""

    def __init__(self, wiring: Wiring) -> None:
        self._w = wiring

    async def import_bundle(self, **kwargs: object) -> bundle_service.ImportResult:
        return await bundle_service.BundleService(None).import_bundle(**kwargs)  # type: ignore[arg-type]


class TestTheQuarantineGate:
    """AC-25 / Q-18. One bad file rejects the bundle whole, and "whole" means *before*
    any row exists — a skill created and then found unreadable is the partial import
    Q-18 refuses, not a rejection."""

    async def test_one_quarantined_file_rejects_the_bundle(self, monkeypatch: pytest.MonkeyPatch) -> None:
        w = wire(monkeypatch, quarantine={b"infected"})
        data = valid_bundle(**{"references/ok.md": "fine", "assets/bad.bin": b"infected"})

        with pytest.raises(BundleQuarantined) as exc:
            await bundle_service.BundleService(None).import_bundle(  # type: ignore[arg-type]
                data=data,
                scope=SkillScope.PROJECT,
                owner_id=uuid.uuid4(),
                actor_user_id=uuid.uuid4(),
            )
        assert exc.value.path == "assets/bad.bin"
        # The clean sibling does not sneak in on the back of the rejected one.
        assert w.files.added == []

    async def test_no_partial_skill_is_created(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # The load-bearing assertion, and the reason the scan cannot be left to the async
        # worker: it runs *after* rows exist, so it could only ever un-create a skill.
        w = wire(monkeypatch, quarantine={b"infected"})
        with pytest.raises(BundleQuarantined):
            await bundle_service.BundleService(None).import_bundle(  # type: ignore[arg-type]
                data=valid_bundle(**{"assets/bad.bin": b"infected"}),
                scope=SkillScope.PROJECT,
                owner_id=uuid.uuid4(),
                actor_user_id=uuid.uuid4(),
            )
        assert w.skills.created == []
        assert w.files.added == []

    async def test_the_skill_md_itself_is_scanned(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # It never becomes a `skill_files` row, and it is the part of the bundle the model
        # is most certain to read.
        w = wire(monkeypatch, quarantine={SKILL_MD.encode()})
        with pytest.raises(BundleQuarantined) as exc:
            await bundle_service.BundleService(None).import_bundle(  # type: ignore[arg-type]
                data=valid_bundle(),
                scope=SkillScope.PROJECT,
                owner_id=uuid.uuid4(),
                actor_user_id=uuid.uuid4(),
            )
        assert exc.value.path == "SKILL.md"
        assert w.skills.created == []

    async def test_every_byte_the_bundle_carries_is_offered_to_the_scanner(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        w = wire(monkeypatch)
        await bundle_service.BundleService(None).import_bundle(  # type: ignore[arg-type]
            data=valid_bundle(**{"references/a.md": "aaa", "assets/b.bin": b"bbb"}),
            scope=SkillScope.PROJECT,
            owner_id=uuid.uuid4(),
            actor_user_id=uuid.uuid4(),
        )
        assert set(w.scanner.scanned) == {SKILL_MD.encode(), b"aaa", b"bbb"}

    async def test_with_no_scanner_deployed_imports_still_work(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # `_initial_scan_status` already treats a scanner-less deployment as CLEAN for
        # every other path into `skill_files`. Refusing imports here would make Skills the
        # one feature that requires ClamAV to exist.
        w = wire(monkeypatch, scan_enabled=False, quarantine={SKILL_MD.encode()})
        result = await bundle_service.BundleService(None).import_bundle(  # type: ignore[arg-type]
            data=valid_bundle(),
            scope=SkillScope.PROJECT,
            owner_id=uuid.uuid4(),
            actor_user_id=uuid.uuid4(),
        )
        assert result.skill.name == "pdf-fill"
        assert w.scanner.scanned == []


class TestTheRoundTrip:
    """AC-26 — export → import → export is byte-identical over Q-30's byte set."""

    def _round_trip(self, data: bytes) -> bytes:
        parsed = read_bundle(data)
        return bundle_service.write_bundle(
            skill_md=render_skill_md(parsed.manifest),
            files=[(f.path, f.data) for f in parsed.files],
        )

    def test_export_import_export_is_byte_identical(self) -> None:
        first = self._round_trip(
            valid_bundle(
                **{
                    "references/guide.md": "# Guide\n",
                    "scripts/run.py": "print(1)\n",
                    "assets/logo.png": b"\x89PNG\r\n\x1a\n",
                }
            )
        )
        assert self._round_trip(first) == first

    def test_it_is_identical_across_separate_exports_of_the_same_input(self) -> None:
        # Determinism proper: not "stable under round trip" but "a function of the skill".
        # A zip records an mtime per entry, so without a pinned timestamp two exports one
        # second apart differ for a reason the skill had no part in.
        data = valid_bundle(**{"references/a.md": "x"})
        assert self._round_trip(data) == self._round_trip(data)

    def test_entry_order_does_not_depend_on_the_input_zips_order(self) -> None:
        a = valid_bundle(**{"references/a.md": "1", "scripts/z.py": "2"})
        b = valid_bundle(**{"scripts/z.py": "2", "references/a.md": "1"})
        assert self._round_trip(a) == self._round_trip(b)

    def test_skill_md_leads_and_the_rest_sort(self) -> None:
        out = self._round_trip(valid_bundle(**{"scripts/z.py": "z", "references/a.md": "a"}))
        with zipfile.ZipFile(io.BytesIO(out)) as zf:
            assert zf.namelist() == ["SKILL.md", "references/a.md", "scripts/z.py"]

    @pytest.mark.parametrize(
        "frontmatter",
        [
            "name: t\ndescription: d",
            "name: t\ndescription: d\nallowed-tools: [Read, Grep]",
            "name: t\ndescription: d\nallowed-tools: Read, Grep, Bash(git:*)",
            "name: t\ndescription: d\nallowed-tools:\n  - Read\n  - Grep",
            "name: t\ndescription: d\nx-smap-requires: [code_exec]",
            "name: t\ndescription: d\nlicense: Complete terms in LICENSE.txt",
            "name: t\ndescription: d\nversion: 1.2.0\nuser-invocable: true",
            'name: t\ndescription: "Does a thing: carefully"',
            "name: t\ndescription: Fills forms, checks them, and signs.",
            'name: t\ndescription:\n  "Folded across\n  two lines."',
        ],
    )
    def test_every_recognized_and_tolerated_shape_survives(self, frontmatter: str) -> None:
        # AC-29's "every one, both list syntaxes". The two list syntaxes converge on one
        # emitted form, which is what makes the *second* export identical to the first.
        first = self._round_trip(build_zip({"SKILL.md": doc(frontmatter)}))
        assert self._round_trip(first) == first

    def test_the_values_survive_and_not_just_the_bytes(self) -> None:
        # Byte-identity between two exports would also hold if both dropped a field, so
        # the semantic claim is asserted separately.
        first = read_bundle(
            self._round_trip(
                build_zip(
                    {
                        "SKILL.md": doc(
                            "name: pdf-fill\ndescription: Fills forms.\n"
                            "allowed-tools: Read, Bash(git:*)\nx-smap-requires: [code_exec]\n"
                            "license: MIT\nversion: 2.0.0"
                        )
                    }
                )
            )
        ).manifest
        assert first.name == "pdf-fill"
        assert first.description == "Fills forms."
        assert first.allowed_tools == ("Read", "Bash(git:*)")
        assert first.requires == ("code_exec",)
        assert first.license == "MIT"
        assert first.extra_frontmatter == {"version": "2.0.0"}

    def test_a_body_with_thematic_breaks_survives(self) -> None:
        body = "# Title\n\n---\n\nMiddle.\n\n---\n\nEnd.\n"
        out = read_bundle(self._round_trip(build_zip({"SKILL.md": doc("name: t\ndescription: d", body)})))
        assert out.manifest.body == body

    def test_server_assigned_state_is_never_emitted(self) -> None:
        # AC-26's exclusion list. It holds by construction — SkillManifest cannot express
        # these — so this asserts the construction, which is the thing that could regress.
        text = render_skill_md(read_bundle(valid_bundle()).manifest)
        head = text.split("---")[1]
        for key in ("source:", "version:", "scope:", "created_by:", "bundle_sha256:"):
            assert key not in head


class TestDeterminismIsAFunctionOfTheSkill:
    """The parts of Q-19 that "two exports match" cannot see.

    Every test here exists because a mutation survived without it: comparing two exports
    taken microseconds apart passes with a live clock in the timestamp, and feeding
    already-sorted input to the packer passes with the sort deleted. A determinism claim
    has to be tested against the thing that would vary, not against a repeat of itself.
    """

    def test_every_entry_carries_the_pinned_epoch_rather_than_a_clock(self) -> None:
        # **The literal, not the constant.** Asserting `== bundle_service._ZIP_EPOCH` reads
        # whatever the module currently says and passes even with a live clock substituted
        # — which the probe confirmed. The zip epoch is the contract, so it is written out
        # here; if someone changes the constant, this is meant to fail.
        out = bundle_service.write_bundle(skill_md=SKILL_MD, files=[("references/a.md", b"x")])
        with zipfile.ZipFile(io.BytesIO(out)) as zf:
            for info in zf.infolist():
                assert info.date_time == (1980, 1, 1, 0, 0, 0)

    def test_every_entry_carries_a_fixed_regular_file_mode(self) -> None:
        out = bundle_service.write_bundle(skill_md=SKILL_MD, files=[("scripts/run.py", b"x")])
        with zipfile.ZipFile(io.BytesIO(out)) as zf:
            for info in zf.infolist():
                assert info.external_attr == (0o100644 << 16)
                # And not a link — the shape `_assert_not_a_link` reads on the way back in.
                assert info.create_system == 3

    def test_the_packer_sorts_input_it_is_handed_unsorted(self) -> None:
        # `read_bundle` already sorts, so the packer's own sort is unreachable through the
        # round trip and was deletable without reddening anything. `export_bundle` is a
        # second caller, and the guarantee belongs to the packer.
        out = bundle_service.write_bundle(
            skill_md=SKILL_MD,
            files=[("scripts/z.py", b"z"), ("assets/m.bin", b"m"), ("references/a.md", b"a")],
        )
        with zipfile.ZipFile(io.BytesIO(out)) as zf:
            assert zf.namelist() == ["SKILL.md", "assets/m.bin", "references/a.md", "scripts/z.py"]

    def test_the_packer_is_a_pure_function_of_its_arguments(self) -> None:
        args = {"skill_md": SKILL_MD, "files": [("references/a.md", b"x")]}
        assert bundle_service.write_bundle(**args) == bundle_service.write_bundle(**args)  # type: ignore[arg-type]

    def test_tolerated_keys_are_emitted_sorted_not_in_dict_order(self) -> None:
        # `extra_frontmatter` is a JSONB round trip; nobody controls its key order. Built
        # in deliberately reverse order, because an insertion-ordered dict makes a sorted
        # emitter and an unsorted one agree by accident.
        m = SkillManifest(
            name="t",
            description="d",
            body="",
            extra_frontmatter={"z-last": "1", "m-mid": "2", "a-first": "3"},
        )
        emitted = [ln.split(":")[0] for ln in render_skill_md(m).splitlines() if ":" in ln]
        assert emitted == ["name", "description", "a-first", "m-mid", "z-last"]

    def test_an_empty_list_is_omitted_rather_than_emitted_as_brackets(self) -> None:
        # Absence is what an empty list already means to the reader, so emitting `[]` would
        # add a key the author never wrote to every skill in the corpus.
        rendered = render_skill_md(read_bundle(valid_bundle()).manifest)
        assert "allowed-tools" not in rendered
        assert "x-smap-requires" not in rendered


def _stored_skill(**over: object) -> Skill:
    base: dict[str, Any] = {
        "id": uuid.uuid4(),
        "scope": SkillScope.ORG,
        "agent_id": None,
        "project_id": None,
        "org_id": uuid.uuid4(),
        "name": "pdf-fill",
        "description": "Fills forms.",
        "body": "# Body\n",
        "body_sha256": "a" * 64,
        "source": SkillSource.IMPORTED,
        "bundle_sha256": None,
        "requires": (),
        "allowed_tools": (),
        "extra_frontmatter": {},
        "created_by": uuid.uuid4(),
        "version": 1,
        "created_at": datetime.now(UTC),
        "deleted_at": None,
    }
    base.update(over)
    return Skill(**base)


def _stored_file(path: str, scan_status: SkillScanStatus) -> SkillFile:
    return SkillFile(
        id=uuid.uuid4(),
        skill_id=uuid.uuid4(),
        path=path,
        kind=SkillFileKind.REFERENCE,
        mime="text/markdown",
        size_bytes=3,
        sha256="b" * 64,
        minio_key="k",
        scan_status=scan_status,
        extracted_chars=3,
        created_at=datetime.now(UTC),
    )


class TestExportRefusesUnreadableFiles:
    """Finding 1 — `export_bundle` must apply the AC-34 fail-closed gate `read_skill` does.

    A quarantined (or still-pending) file makes a skill unreadable; an export that shipped
    those bytes anyway is a side door for exactly what the gate exists to stop — and an
    org/platform skill is exported by members who never uploaded it, to a presigned URL.
    """

    def _wire(self, monkeypatch: pytest.MonkeyPatch, files: list[SkillFile], *, read_bytes: bytes = b"x"):
        reads: list[str] = []

        class _Files:
            def __init__(self, _db: object) -> None:
                pass

            async def list_for_skill(self, _skill_id: object) -> list[SkillFile]:
                return files

            async def read_bytes(self, f: SkillFile) -> bytes:
                reads.append(f.path)
                return read_bytes

        monkeypatch.setattr(bundle_service, "SkillFileService", lambda db: _Files(db))
        return reads

    @pytest.mark.parametrize(
        "status",
        [SkillScanStatus.QUARANTINED, SkillScanStatus.PENDING, SkillScanStatus.SKIPPED],
    )
    async def test_a_non_clean_file_blocks_export(
        self, monkeypatch: pytest.MonkeyPatch, status: SkillScanStatus
    ) -> None:
        bad = _stored_file("references/x.md", status)
        reads = self._wire(monkeypatch, [bad])
        with pytest.raises(SkillUnreadable):
            await bundle_service.BundleService(None).export_bundle(_stored_skill())  # type: ignore[arg-type]
        # Fail-closed *before* fetching: the condemned bytes are never even read.
        assert reads == []

    async def test_a_clean_skill_exports(self, monkeypatch: pytest.MonkeyPatch) -> None:
        clean = _stored_file("references/x.md", SkillScanStatus.CLEAN)
        self._wire(monkeypatch, [clean], read_bytes=b"# guide")
        data = await bundle_service.BundleService(None).export_bundle(_stored_skill())  # type: ignore[arg-type]
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            assert set(zf.namelist()) == {"SKILL.md", "references/x.md"}
            assert zf.read("references/x.md") == b"# guide"


class TestTheStoredSkillRoundTrips:
    """`manifest_of` — the seam between a `skills` row and a bundle, D-57 included.

    It had no test at all: every round-trip case above goes `read_bundle` → `render`, and
    never through the row. Deleting the `license` pop left the suite green.
    """

    def _skill(self, **over: object) -> Skill:
        base: dict[str, Any] = {
            "id": uuid.uuid4(),
            "scope": SkillScope.PROJECT,
            "agent_id": None,
            "project_id": uuid.uuid4(),
            "org_id": None,
            "name": "pdf-fill",
            "description": "Fills forms.",
            "body": "# Body\n",
            "body_sha256": "a" * 64,
            "source": SkillSource.IMPORTED,
            "bundle_sha256": None,
            "requires": ("code_exec",),
            "allowed_tools": ("Read",),
            "extra_frontmatter": {},
            "created_by": uuid.uuid4(),
            "version": 3,
            "created_at": datetime.now(UTC),
            "deleted_at": None,
        }
        base.update(over)
        return Skill(**base)

    def test_a_row_renders_to_a_bundle_that_parses_back_to_it(self) -> None:
        m = parse_skill_md(render_skill_md(manifest_of(self._skill())))
        assert (m.name, m.description, m.body) == ("pdf-fill", "Fills forms.", "# Body\n")
        assert m.requires == ("code_exec",)
        assert m.allowed_tools == ("Read",)

    def test_manifest_of_lifts_license_out_of_the_jsonb_into_its_field(self) -> None:
        # **Asserted on `manifest_of` directly, not through a render.** The round-trip test
        # below launders this: with the lift deleted, `license` stays in
        # `extra_frontmatter`, gets emitted by the tolerated-key loop, and parses back into
        # `.license` anyway — so the round trip is green either way and says nothing about
        # D-57's seam. The probe proved it.
        m = manifest_of(self._skill(extra_frontmatter={"license": "MIT", "version": "1.0.0"}))
        assert m.license == "MIT"
        assert m.extra_frontmatter == {"version": "1.0.0"}

    def test_license_rides_in_extra_frontmatter_and_survives(self) -> None:
        # D-57: there is no `skills.license` column, so it round-trips through the JSONB.
        skill = self._skill(extra_frontmatter={"license": "MIT", "version": "1.0.0"})
        m = parse_skill_md(render_skill_md(manifest_of(skill)))
        assert m.license == "MIT"
        assert m.extra_frontmatter == {"version": "1.0.0"}

    def test_license_is_emitted_once_in_its_declared_slot(self) -> None:
        # Once, because a duplicate key is `BundleInvalid` on re-import — an export its own
        # importer rejects. In its declared slot, because that is what the lift buys over
        # leaving it among the sorted vendor keys.
        skill = self._skill(extra_frontmatter={"license": "MIT", "a-vendor-key": "v"})
        keys = [ln.split(":")[0] for ln in render_skill_md(manifest_of(skill)).splitlines() if ":" in ln]
        assert keys.count("license") == 1
        assert keys.index("license") < keys.index("a-vendor-key")

    def test_the_row_state_a_bundle_must_never_carry_is_not_emitted(self) -> None:
        # `version=3`, `source=imported`, a project owner, a `created_by` — every one is on
        # the row handed in, and none may appear in the file (AC-26).
        skill = self._skill(bundle_sha256="b" * 64)
        head = render_skill_md(manifest_of(skill)).split("---")[1]
        for token in ("version:", "source:", "scope:", "created_by:", "bundle_sha256:", "3"):
            assert token not in head

    def test_a_stored_license_does_not_change_the_authored_digest(self) -> None:
        # Q-30's byte set does not name `license`, so editing one does not mark a skill
        # diverged. Recorded rather than quietly widened — the test states the gap.
        #
        # **The first version of this was a tautology**: it compared `is_diverged` on two
        # skills whose `bundle_sha256` was None, so both early-returned False and
        # `authored_digest` was never called at all. `False == False` — green even if
        # `license` *were* folded into the digest, i.e. it certified the exact bug it
        # names. It now pins the skill against its own digest, so the comparison has to go
        # through the function.
        plain = self._skill()
        pinned = authored_digest(
            name=plain.name,
            description=plain.description,
            requires=plain.requires,
            allowed_tools=plain.allowed_tools,
            body=plain.body,
            file_digests={},
        )
        licensed = self._skill(bundle_sha256=pinned, extra_frontmatter={"license": "MIT"})
        assert bundle_service.is_diverged(licensed, []) is False


class TestExportEscaping:
    """AC-31 — the second line of defense, asserted independently of AC-30's first."""

    def test_a_newline_bearing_description_cannot_be_exported(self) -> None:
        # The attack: `Formats CSV.\nscope: platform` exports as a new `scope:` line and
        # re-imports as a platform skill — self-escalation with no zip crafting. AC-30
        # blocks it at every entry point; this is what happens if a row ever gets past it.
        m = SkillManifest(name="t", description="Formats CSV.\nscope: platform", body="")
        with pytest.raises(BundleInvalid) as exc:
            render_skill_md(m)
        assert exc.value.key == "description"

    def test_the_forged_key_never_reaches_the_output(self) -> None:
        # Stronger than "it raises": the failure must not be a partial write that already
        # contains the line.
        m = SkillManifest(name="t", description="ok.\nscope: platform", body="")
        with pytest.raises(BundleInvalid):
            render_skill_md(m)

    @pytest.mark.parametrize("value", ["[not a list]", '"quoted"', "  padded  ", "#comment"])
    def test_a_value_that_would_reparse_differently_is_quoted(self, value: str) -> None:
        m = SkillManifest(name="t", description=value, body="body")
        assert parse_skill_md(render_skill_md(m)).description == value

    def test_a_list_item_with_a_comma_is_quoted_rather_than_split(self) -> None:
        m = SkillManifest(name="t", description="d", body="", allowed_tools=("a,b", "c"))
        assert parse_skill_md(render_skill_md(m)).allowed_tools == ("a,b", "c")

    def test_a_colon_does_not_trigger_quoting(self) -> None:
        # Deliberate: the reader splits on the first colon and takes the rest verbatim, so
        # quoting for a colon would add quotes the author never wrote to most real
        # descriptions. The round trip is what makes this safe, and it is asserted.
        m = SkillManifest(name="t", description="Does a thing: carefully", body="")
        rendered = render_skill_md(m)
        assert "description: Does a thing: carefully" in rendered
        assert parse_skill_md(rendered).description == "Does a thing: carefully"

    @pytest.mark.parametrize("value", ["#tag\\", "- foo\\", "[a\\", "  pad\\  "])
    def test_a_backslash_terminated_value_still_round_trips(self, value: str) -> None:
        # **Found by the quality gate.** `_quoted_end` skips `\` + the next char when
        # scanning for a closing `"`, and the emitter escapes nothing — so these exported
        # double-quoted and re-imported as "unterminated quoted value": an export its own
        # importer rejects, and AC-26 broken for it. Single-quoting has no escape rule and
        # carries the backslash literally.
        m = SkillManifest(name="t", description=value, body="body")
        assert parse_skill_md(render_skill_md(m)).description == value

    def test_a_forged_key_in_extra_frontmatter_cannot_be_exported(self) -> None:
        # **Found by the security gate, and it refutes a claim this module made.** Values
        # go through `_emit_scalar`; keys did not — and a key is emitted at column 0, so a
        # newline in one writes a `scope:` line into a file SMAP itself signs. Unreachable
        # through the parser (`_KEY_RE` shapes every key it produces), but
        # `extra_frontmatter` is an untyped JSONB dict and the invariant was advertised as
        # structural, so it is made structural.
        m = SkillManifest(
            name="ok",
            description="d",
            body="",
            extra_frontmatter={"a: b\nscope": "platform"},
        )
        with pytest.raises(BundleInvalid) as exc:
            render_skill_md(m)
        assert "not a valid key" in exc.value.reason

    def test_a_key_containing_a_colon_cannot_be_exported(self) -> None:
        # **The code-review finding.** The guard used `_KEY_RE.match(f"{key}:")`, a whole-
        # line parser whose `(.*)` tail swallowed the embedded colon — so `a:b` passed and
        # re-imported as key `a`, value `b: ...`: silent corruption, no exception, of the
        # exact untyped-dict writer the guard exists to defend against.
        m = SkillManifest(name="ok", description="d", body="", extra_frontmatter={"a:b": "v"})
        with pytest.raises(BundleInvalid) as exc:
            render_skill_md(m)
        assert "not a valid key" in exc.value.reason

    @pytest.mark.parametrize("codepoint", [0x200B, 0x202E, 0xE0041])
    def test_a_format_char_in_a_value_cannot_be_exported(self, codepoint: int) -> None:
        # **The code-review finding.** `_emit_scalar` rejected `Cc/Zl/Zp` but not `Cf`, so
        # a zero-width/bidi/tag character exported into a file its own importer then
        # rejected. Now it runs the same charset rule the parser does on input.
        m = SkillManifest(name="t", description=f"safe{chr(codepoint)}text", body="body")
        with pytest.raises(BundleInvalid) as exc:
            render_skill_md(m)
        assert exc.value.key == "description"
        # And it really is symmetric: nothing SMAP emits is something SMAP would refuse to
        # re-read. (The positive direction is covered by the 42-file round-trip corpus.)

    def test_an_empty_tolerated_list_survives_the_round_trip(self) -> None:
        # The author wrote this key, so dropping it loses what they wrote — unlike a
        # recognized empty list, where absence *is* the meaning.
        m = SkillManifest(name="t", description="d", body="", extra_frontmatter={"x-tools": []})
        assert parse_skill_md(render_skill_md(m)).extra_frontmatter == {"x-tools": []}

    def test_a_value_needing_quotes_that_holds_both_quote_characters_is_refused(self) -> None:
        # The reader does not unescape, so no quoting style round-trips this. Refusing is
        # loud; emitting an escape the reader will not undo would break it silently.
        m = SkillManifest(name="t", description="[\"a\" and 'b']", body="")
        with pytest.raises(BundleInvalid) as exc:
            render_skill_md(m)
        assert "without escaping" in exc.value.reason


class TestDivergence:
    """Q-20 / AC-16's badge — the clause that has had no definition since Phase 2."""

    def _skill(self, **over: object) -> object:
        base: dict[str, object] = {
            "name": "t",
            "description": "d",
            "body": "# Body",
            "requires": (),
            "allowed_tools": (),
            "bundle_sha256": None,
        }
        base.update(over)
        return SimpleNamespace(**base)

    def _digest_of(self, skill: object, files: list[object]) -> str:
        return authored_digest(
            name=skill.name,  # type: ignore[attr-defined]
            description=skill.description,  # type: ignore[attr-defined]
            requires=skill.requires,  # type: ignore[attr-defined]
            allowed_tools=skill.allowed_tools,  # type: ignore[attr-defined]
            body=skill.body,  # type: ignore[attr-defined]
            file_digests={f.path: f.sha256 for f in files},  # type: ignore[attr-defined]
        )

    def test_an_authored_skill_is_never_diverged(self) -> None:
        # It was never a bundle, so it has nothing to diverge from. "Imported and has a
        # hash" — the only thing Phase 1 could compute — is true of every import forever,
        # which is why Phase 1 returned False rather than guess.
        assert bundle_service.is_diverged(self._skill(), []) is False  # type: ignore[arg-type]

    def test_a_freshly_imported_skill_is_not_diverged(self) -> None:
        s = self._skill()
        s = self._skill(bundle_sha256=self._digest_of(s, []))
        assert bundle_service.is_diverged(s, []) is False  # type: ignore[arg-type]

    def test_an_edited_body_diverges(self) -> None:
        s = self._skill()
        pinned = self._digest_of(s, [])
        assert bundle_service.is_diverged(  # type: ignore[arg-type]
            self._skill(bundle_sha256=pinned, body="# Edited"), []
        )

    def test_an_edited_file_diverges(self) -> None:
        f = SimpleNamespace(path="references/a.md", sha256="aa")
        s = self._skill()
        pinned = self._digest_of(s, [f])
        edited = SimpleNamespace(path="references/a.md", sha256="bb")
        assert bundle_service.is_diverged(self._skill(bundle_sha256=pinned), [edited])  # type: ignore[arg-type]


class TestTheRejectionsReachTheClientAsTheirSlugs:
    """AC-24 and AC-25 are worded as slugs, not as exceptions.

    `skills/bundle-invalid` and `skills/bundle-quarantined` are the contract; the
    exception classes are an implementation detail behind them. Asserted against the real
    registration map rather than a hand-written expectation, so a rename that misses the
    map is caught here rather than by a client.
    """

    def test_bundle_invalid_maps_to_its_slug_and_422(self) -> None:
        slug, status_code, _ = error_mapping._MAP[BundleInvalid]
        assert (slug, status_code) == ("skills/bundle-invalid", 422)

    def test_bundle_quarantined_maps_to_its_slug_and_422(self) -> None:
        slug, status_code, _ = error_mapping._MAP[BundleQuarantined]
        assert (slug, status_code) == ("skills/bundle-quarantined", 422)

    def test_the_offending_key_travels_with_the_problem(self) -> None:
        # §10's mitigation for invisible frontmatter errors is a rejection "naming the
        # offending key". Without this the author sees a 422 over a zip and no field.
        exc = BundleInvalid("nope", key="scope")
        assert error_mapping._extras(exc) == {"key": "scope", "path": None}

    def test_the_offending_path_travels_with_the_problem(self) -> None:
        exc = BundleInvalid("nope", path="references/x.md")
        assert error_mapping._extras(exc)["path"] == "references/x.md"

    def test_a_whole_bundle_rejection_names_no_entry_rather_than_a_wrong_one(self) -> None:
        # A size cap or a missing SKILL.md is nobody's fault in particular.
        assert error_mapping._extras(BundleInvalid("too big")) == {"key": None, "path": None}

    def test_the_quarantined_path_travels(self) -> None:
        assert error_mapping._extras(BundleQuarantined("assets/x.bin")) == {"path": "assets/x.bin"}

    def test_a_real_rejection_carries_a_key_the_mapping_can_surface(self) -> None:
        # End to end over the two layers: the importer's own error, through the extras
        # function the handler calls.
        with pytest.raises(BundleInvalid) as exc:
            read_bundle(build_zip({"SKILL.md": doc("name: t\ndescription: d\nscope: platform")}))
        assert error_mapping._extras(exc.value)["key"] == "scope"


# Zip records each entry twice: a local header before the payload and a central-directory
# header at the end. `zipfile` reads sizes and flags from the central one, but a patch that
# touched only it would leave a self-inconsistent archive, so both are rewritten. Offsets
# per APPNOTE 4.3.7 / 4.3.12: (signature, name offset, name-length offset, flags offset,
# uncompressed-size offset).
_HEADERS = (
    (b"PK\x01\x02", 46, 28, 8, 24, 10),
    (b"PK\x03\x04", 30, 26, 6, 22, 8),
)


def _patch_entry_headers(
    data: bytes,
    *,
    entry: bytes,
    size: int | None = None,
    flags: int | None = None,
    method: int | None = None,
    name_bytes: bytes | None = None,
) -> bytes:
    """Rewrite one entry's declared size and/or flag bits in both of its headers.

    **`zipfile` normalizes on write, which is why these fixtures cannot be built with
    `writestr`** (D-55): it rewrites a backslash to `/`, drops the encryption flag, and
    forces the UTF-8 name flag on for a non-ASCII name. Every hostile shape below is one a
    real packer can emit and Python's writer refuses to, so the bytes are patched after the
    fact. In code rather than as a checked-in binary because a fixture whose entire content
    is a lie about one field is one a reviewer can read here and cannot see in a blob.
    """
    if name_bytes is not None and len(name_bytes) != len(entry):
        # A length mismatch would silently skip the rewrite and hand back an *inert*
        # fixture — a test then asserting "rejected" would be asserting nothing. Raising
        # beats returning something that looks hostile and is not.
        raise AssertionError(
            f"name_bytes must be {len(entry)} bytes to patch in place, got {len(name_bytes)}"
        )
    out = bytearray(data)
    patched = 0
    for signature, name_off, namelen_off, flag_off, size_off, method_off in _HEADERS:
        pos = 0
        while True:
            pos = out.find(signature, pos)
            if pos == -1:
                break
            name_len = struct.unpack_from("<H", out, pos + namelen_off)[0]
            if bytes(out[pos + name_off : pos + name_off + name_len]) == entry:
                if size is not None:
                    struct.pack_into("<I", out, pos + size_off, size)
                if flags is not None:
                    struct.pack_into("<H", out, pos + flag_off, flags)
                if method is not None:
                    struct.pack_into("<H", out, pos + method_off, method)
                if name_bytes is not None:
                    out[pos + name_off : pos + name_off + name_len] = name_bytes
                patched += 1
            pos += 4
    if patched != len(_HEADERS):
        raise AssertionError(f"expected to patch both headers for {entry!r}, patched {patched}")
    return bytes(out)


class TestThePatchHelperItself:
    """The fixture builder is test infrastructure, so its own claims get tests.

    D-47's lesson, applied before it bit: a patcher that silently matched nothing would
    leave the tests above asserting that *ordinary* bundles are rejected — green, and
    testing nothing they name.
    """

    def test_it_actually_changes_the_declared_size(self) -> None:
        data = build_zip({"SKILL.md": SKILL_MD, "assets/x.bin": b"\x00" * 5000})
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            assert zf.getinfo("assets/x.bin").file_size == 5000
        forged = _patch_entry_headers(data, entry=b"assets/x.bin", size=17)
        with zipfile.ZipFile(io.BytesIO(forged)) as zf:
            assert zf.getinfo("assets/x.bin").file_size == 17

    def test_it_actually_changes_the_flag_bits(self) -> None:
        data = build_zip({"SKILL.md": SKILL_MD, "assets/x.bin": b"payload"})
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            before = zf.getinfo("assets/x.bin").flag_bits
        forged = _patch_entry_headers(data, entry=b"assets/x.bin", flags=before | 0x1)
        with zipfile.ZipFile(io.BytesIO(forged)) as zf:
            assert zf.getinfo("assets/x.bin").flag_bits & 0x1

    def test_it_leaves_other_entries_alone(self) -> None:
        data = build_zip({"SKILL.md": SKILL_MD, "assets/x.bin": b"\x00" * 5000})
        forged = _patch_entry_headers(data, entry=b"assets/x.bin", size=17)
        with zipfile.ZipFile(io.BytesIO(forged)) as zf:
            assert zf.getinfo("SKILL.md").file_size == len(SKILL_MD.encode())
