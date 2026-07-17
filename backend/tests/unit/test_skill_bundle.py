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
from types import SimpleNamespace

import pytest

from contexts.skills.application import bundle_service
from contexts.skills.application.bundle_service import (
    MAX_BUNDLE_COMPRESSED_BYTES,
    MAX_BUNDLE_ENTRIES,
    MAX_BUNDLE_UNCOMPRESSED_BYTES,
    authored_digest,
    read_bundle,
)
from contexts.skills.domain.errors import BundleInvalid, BundleQuarantined
from contexts.skills.domain.models import SkillScope, SkillSource
from contexts.skills.domain.text_rules import skill_file_path_reason

SKILL_MD = "---\nname: pdf-fill\ndescription: Fills PDF forms.\n---\n\n# PDF Fill\n\nProse.\n"


def build_zip(entries: dict[str, bytes | str], *, compression: int = zipfile.ZIP_DEFLATED) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression) as zf:
        for path, content in entries.items():
            zf.writestr(path, content)
    return buf.getvalue()


def valid_bundle(**extra: bytes | str) -> bytes:
    return build_zip({"SKILL.md": SKILL_MD, **extra})


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
        assert exc.value.path == "assets/big.bin" or "per-file" in exc.value.reason

    def test_an_honest_small_but_highly_compressible_bundle_is_not_a_bomb(self) -> None:
        # The false positive the floor exists to prevent: ordinary repetitive prose beats
        # 100:1 easily at small sizes. Ratio is a property of text, not of malice.
        text = "The quick brown fox. " * 4000  # ~84 KB, deflates to well under 1 KB
        data = build_zip({"SKILL.md": SKILL_MD, "references/notes.md": text})
        assert len(text) > len(data) * 100  # over 100:1, and legitimate
        assert read_bundle(data).files[0].data.decode() == text

    def test_too_many_entries_is_rejected(self) -> None:
        entries: dict[str, bytes | str] = {"SKILL.md": SKILL_MD}
        entries.update({f"references/f{i}.md": "x" for i in range(MAX_BUNDLE_ENTRIES)})
        with pytest.raises(BundleInvalid) as exc:
            read_bundle(build_zip(entries))
        assert str(MAX_BUNDLE_ENTRIES) in exc.value.reason

    def test_an_over_cap_compressed_bundle_is_rejected_before_it_is_opened(self) -> None:
        with pytest.raises(BundleInvalid) as exc:
            read_bundle(b"x" * (MAX_BUNDLE_COMPRESSED_BYTES + 1))
        assert "compressed" in exc.value.reason

    def test_a_bundle_cannot_install_more_files_than_the_per_file_api_allows(self) -> None:
        # The entry cap counts SKILL.md, which is not a `skill_files` row — so a full
        # bundle installs at most 499 files, strictly under the API's own 500. The
        # inequality is one-directional and load-bearing: the exporter must never emit a
        # skill its own importer would reject. Asserted rather than commented, because a
        # comment cannot fail when someone raises one number and not the other.
        from contexts.skills.application.file_service import MAX_SKILL_FILES

        assert MAX_BUNDLE_ENTRIES - 1 <= MAX_SKILL_FILES

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

    async def add(self, **kwargs: object) -> object:
        self.added.append(kwargs)
        return SimpleNamespace(id=uuid.uuid4())


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

        skill, warnings = await bundle_service.BundleService(None).import_bundle(  # type: ignore[arg-type]
            data=data,
            scope=SkillScope.PROJECT,
            owner_id=uuid.uuid4(),
            actor_user_id=uuid.uuid4(),
        )

        assert skill.name == "pdf-fill"
        assert warnings == ()
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
        _, warnings = await bundle_service.BundleService(None).import_bundle(  # type: ignore[arg-type]
            data=valid_bundle(**{"scripts/f.py": "import requests"}),
            scope=SkillScope.PROJECT,
            owner_id=uuid.uuid4(),
            actor_user_id=uuid.uuid4(),
        )
        assert any("network" in w for w in warnings)


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
        skill, _ = await bundle_service.BundleService(None).import_bundle(  # type: ignore[arg-type]
            data=valid_bundle(),
            scope=SkillScope.PROJECT,
            owner_id=uuid.uuid4(),
            actor_user_id=uuid.uuid4(),
        )
        assert skill.name == "pdf-fill"
        assert w.scanner.scanned == []


# Zip records each entry twice: a local header before the payload and a central-directory
# header at the end. `zipfile` reads sizes and flags from the central one, but a patch that
# touched only it would leave a self-inconsistent archive, so both are rewritten. Offsets
# per APPNOTE 4.3.7 / 4.3.12: (signature, name offset, name-length offset, flags offset,
# uncompressed-size offset).
_HEADERS = (
    (b"PK\x01\x02", 46, 28, 8, 24),
    (b"PK\x03\x04", 30, 26, 6, 22),
)


def _patch_entry_headers(
    data: bytes, *, entry: bytes, size: int | None = None, flags: int | None = None
) -> bytes:
    """Rewrite one entry's declared size and/or flag bits in both of its headers.

    **`zipfile` normalizes on write, which is why these fixtures cannot be built with
    `writestr`** (D-55): it rewrites a backslash to `/`, drops the encryption flag, and
    forces the UTF-8 name flag on for a non-ASCII name. Every hostile shape below is one a
    real packer can emit and Python's writer refuses to, so the bytes are patched after the
    fact. In code rather than as a checked-in binary because a fixture whose entire content
    is a lie about one field is one a reviewer can read here and cannot see in a blob.
    """
    out = bytearray(data)
    for signature, name_off, namelen_off, flag_off, size_off in _HEADERS:
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
            pos += 4
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
