"""AC-18 / AC-30 / R31.19 — the file-path arm of the charset matrix.

AC-30's matrix names "each file path" as a column and Phase 1 left it vacuous: there was
no file surface to carry a path. This is that column, and it is not a copy of the
`description` tests — a path is structured text, so it carries the charset rule *plus*
layout, traversal, and cross-filesystem rules that free-form fields have no use for.

Every rejection is asserted by *reason*, not merely by falsiness, because
`skill_file_path_reason` returning "is empty" for a traversal attempt would pass a
truthiness test while telling the author something false.
"""

from __future__ import annotations

import unicodedata

import pytest

from contexts.skills.domain.text_rules import (
    ALLOWED_TOP_LEVEL_DIRS,
    INDEX_DELIMITER_MARKER,
    MAX_FILE_PATH_CHARS,
    path_collision_key,
    skill_file_path_reason,
)


class TestAcceptedPaths:
    @pytest.mark.parametrize(
        "path",
        [
            "references/guide.md",
            "scripts/run.py",
            "assets/logo.png",
            "references/nested/deep/notes.md",
            "assets/a-b_c.1.bin",
            # CJK is ordinary content in a zh-TW product, not an edge case.
            "references/說明.md",
            # Emoji is `So`, not `Cf` — the charset rule must not overreach into it.
            "assets/icon-🚀.png",
        ],
    )
    def test_an_ordinary_bundle_path_is_accepted(self, path: str) -> None:
        assert skill_file_path_reason(path) is None

    def test_every_allowed_top_level_dir_is_actually_allowed(self) -> None:
        # Guards against the constant and the predicate drifting apart.
        for d in ALLOWED_TOP_LEVEL_DIRS:
            assert skill_file_path_reason(f"{d}/x.txt") is None


class TestLayout:
    def test_skill_md_is_the_body_not_a_file(self) -> None:
        assert "body" in (skill_file_path_reason("SKILL.md") or "")

    def test_a_bare_filename_at_root_is_rejected(self) -> None:
        assert "must live under" in (skill_file_path_reason("loose.md") or "")

    def test_an_unknown_top_level_dir_is_rejected(self) -> None:
        # R31.19: only SKILL.md, references/, scripts/, assets/.
        assert "must live under" in (skill_file_path_reason("bin/run.sh") or "")

    def test_a_directory_that_only_prefixes_an_allowed_one_is_rejected(self) -> None:
        # `references_evil/` starts with "references" but is not it.
        assert "must live under" in (skill_file_path_reason("references_evil/x.md") or "")

    def test_empty_is_rejected(self) -> None:
        assert skill_file_path_reason("") == "is empty"


class TestTraversalAndAbsolute:
    @pytest.mark.parametrize(
        "path",
        [
            "references/../../../etc/passwd",
            "references/../secret",
            "../outside.md",
            "references/./x.md",
        ],
    )
    def test_traversal_segments_are_rejected(self, path: str) -> None:
        assert "traversal" in (skill_file_path_reason(path) or "")

    def test_absolute_is_rejected(self) -> None:
        assert skill_file_path_reason("/etc/passwd") == "is absolute"

    def test_a_backslash_is_rejected_before_it_can_be_read_as_a_separator(self) -> None:
        # Rejected outright: normalising it to `/` would make a Windows-authored path
        # mean something different from what its author typed.
        assert skill_file_path_reason("references\\x.md") == "contains a backslash"

    def test_a_double_slash_is_rejected(self) -> None:
        assert "empty path segment" in (skill_file_path_reason("references//x.md") or "")


class TestCharsetRuleAppliesToPaths:
    def test_a_newline_is_rejected(self) -> None:
        # §8 threat 8: the manifest renders one file per line, so a newline in a path
        # forges an entry exactly as a newline in a description forges an index line.
        reason = skill_file_path_reason("references/x.md\nassets/evil.sh")
        assert reason is not None
        assert "control character" in reason

    def test_the_index_delimiter_is_rejected(self) -> None:
        reason = skill_file_path_reason(f"references/{INDEX_DELIMITER_MARKER}.md")
        assert reason == "contains the skills index delimiter"

    @pytest.mark.parametrize(
        ("cp", "fragment"),
        [
            (0x202E, "bidirectional override"),  # RIGHT-TO-LEFT OVERRIDE
            (0x200B, "zero-width"),  # ZERO WIDTH SPACE
            (0xE0041, "tag character"),  # TAG LATIN A — the smuggling channel
            (0x00AD, "non-printing"),  # SOFT HYPHEN: Cf, caught by category not by list
        ],
    )
    def test_invisible_characters_are_rejected_with_a_precise_reason(self, cp: int, fragment: str) -> None:
        reason = skill_file_path_reason(f"references/x{chr(cp)}.md")
        assert reason is not None
        assert fragment in reason

    def test_over_length_is_rejected(self) -> None:
        path = "references/" + ("a" * MAX_FILE_PATH_CHARS)
        assert skill_file_path_reason(path) == f"exceeds {MAX_FILE_PATH_CHARS} characters"


class TestCrossFilesystemRules:
    def test_a_non_nfc_path_is_rejected_rather_than_normalised(self) -> None:
        # NFD "é" (e + U+0301). Byte-exact UNIQUE would let this coexist with the NFC
        # form while one filesystem sees them as one file.
        nfd = unicodedata.normalize("NFD", "references/café.md")
        assert nfd != unicodedata.normalize("NFC", nfd)  # fixture is genuinely NFD
        assert skill_file_path_reason(nfd) == "is not NFC-normalised"

    def test_the_nfc_form_of_the_same_path_is_accepted(self) -> None:
        assert skill_file_path_reason(unicodedata.normalize("NFC", "references/café.md")) is None

    @pytest.mark.parametrize("name", ["CON", "con", "NUL.py", "com1", "LPT9.txt"])
    def test_windows_reserved_names_are_rejected(self, name: str) -> None:
        assert "reserved" in (skill_file_path_reason(f"scripts/{name}") or "")

    def test_a_reserved_name_as_a_mere_prefix_is_fine(self) -> None:
        # `CONFIG` is not `CON`; over-rejecting here would fail ordinary files.
        assert skill_file_path_reason("scripts/CONFIG.py") is None
        assert skill_file_path_reason("scripts/console.py") is None

    @pytest.mark.parametrize("path", ["assets/x ", "assets/x.", "assets/ x", "references/dir /x.md"])
    def test_trailing_dots_and_spaces_are_rejected(self, path: str) -> None:
        # Windows strips both silently, so these collide with their stripped form there
        # and not on Linux.
        assert "trailing dot or space" in (skill_file_path_reason(path) or "")


class TestCollisionKey:
    def test_case_differing_paths_share_a_collision_key(self) -> None:
        # The DB's UNIQUE is byte-exact (Linux's rule); a Windows dev box resolves both
        # of these to one file.
        assert path_collision_key("assets/X.md") == path_collision_key("assets/x.md")

    def test_distinct_paths_do_not_collide(self) -> None:
        assert path_collision_key("assets/a.md") != path_collision_key("assets/b.md")

    def test_folding_covers_more_than_lower(self) -> None:
        # `.lower()` does not fold ẞ -> ss; casefold does. A rule built on `.lower()`
        # would miss this pair, which is the reason the helper exists at all.
        assert path_collision_key("assets/ẞ.md") == path_collision_key("assets/ss.md")
