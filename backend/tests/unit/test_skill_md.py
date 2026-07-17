"""AC-29 / AC-23 (parse half) — the dedicated `SKILL.md` parser.

§12 files AC-29 under `test_skill_bundle.py`. The parser gets its own file instead
(D-54): it is pure and has no zip, no IO, and no service in it, and the zip file will
carry the streaming counters and the quarantine rules. Splitting it is the same call
Phase 2 made when `test_skill_file_paths.py` came out of the sanitization matrix.

**The corpus is the specification.** Q-29's whole argument is a measurement — 42 real
`SKILL.md` files, against which a flat allowlist rejects 40% — so the syntax cases below
are the shapes those files actually use, not a tour of YAML. `TestTheRealCorpusShapes`
names the file count each shape was observed in, re-measured 2026-07-17.
"""

from __future__ import annotations

import pytest

from contexts.skills.application.skill_md import (
    SkillManifest,
    parse_skill_md,
    split_frontmatter,
)
from contexts.skills.domain.errors import BundleInvalid
from contexts.skills.domain.models import MAX_DESCRIPTION_CHARS
from contexts.skills.domain.text_rules import INDEX_DELIMITER_MARKER


def doc(frontmatter: str, body: str = "# Title\n\nProse.") -> str:
    return f"---\n{frontmatter}\n---\n{body}"


MINIMAL = doc("name: pdf-fill\ndescription: Fills PDF forms.")


class TestTheRealCorpusShapes:
    """Every syntax here was observed in the 42-file corpus (Q-29, re-measured 2026-07-17)."""

    def test_the_two_keys_every_file_has(self) -> None:
        m = parse_skill_md(MINIMAL)
        assert m.name == "pdf-fill"
        assert m.description == "Fills PDF forms."

    def test_comma_separated_bare_scalars_are_a_list_not_one_string(self) -> None:
        # `allowed-tools: Read, Grep, Bash(git:*)` — 7 of 42. The last element carries a
        # colon and parentheses, so this is also the case a naive split(",") survives but
        # a naive "does it look like YAML" check does not.
        m = parse_skill_md(doc("name: t\ndescription: d\nallowed-tools: Read, Grep, Bash(git:*)"))
        assert m.allowed_tools == ("Read", "Grep", "Bash(git:*)")

    def test_a_description_with_commas_stays_one_string(self) -> None:
        # The inverse of the case above, and the reason comma-splitting is per-key rather
        # than global: prose commas are ordinary, and a `description` shredded into a list
        # would render as list punctuation in the index the model reads.
        m = parse_skill_md(doc("name: t\ndescription: Fills forms, checks them, and signs."))
        assert m.description == "Fills forms, checks them, and signs."

    def test_a_flow_sequence_is_a_list(self) -> None:
        m = parse_skill_md(doc("name: t\ndescription: d\nallowed-tools: [Read, Grep]"))
        assert m.allowed_tools == ("Read", "Grep")

    def test_a_block_sequence_is_a_list(self) -> None:
        m = parse_skill_md(doc("name: t\ndescription: d\nallowed-tools:\n  - Read\n  - Grep"))
        assert m.allowed_tools == ("Read", "Grep")

    def test_an_empty_list_value_is_empty_not_one_empty_string(self) -> None:
        # 6 of 42 write `allowed-tools:` with nothing after it. `[""]` would render an
        # empty tool name into the UI and `[]` is what the author meant.
        m = parse_skill_md(doc("name: t\ndescription: d\nallowed-tools:"))
        assert m.allowed_tools == ()

    @pytest.mark.parametrize("field", ["allowed_tools", "requires"])
    def test_an_absent_list_key_is_empty(self, field: str) -> None:
        # **The regression test for a bug this file's own tests missed.** Every case above
        # writes the key out as present-but-empty; absence is the far commoner shape (35
        # of 42 files carry no `allowed-tools` at all) and took a different code path,
        # where the `()` default stringified to "()" and split into a tool named `"()"`.
        # Found by the importer's digest test, not here — an empty-vs-absent distinction
        # is exactly the kind a parser test writes past.
        m = parse_skill_md(MINIMAL)
        assert getattr(m, field) == ()

    def test_the_minimal_document_carries_no_phantom_values(self) -> None:
        # The same bug stated as a whole-object claim, which is what a caller relies on.
        m = parse_skill_md(MINIMAL)
        assert (m.allowed_tools, m.requires, m.license, m.extra_frontmatter) == ((), (), None, {})

    def test_a_quoted_description_keeps_its_colon(self) -> None:
        m = parse_skill_md(doc('name: t\ndescription: "Does a thing: carefully"'))
        assert m.description == "Does a thing: carefully"

    def test_an_indented_multi_line_quoted_description_folds_to_one_line(self) -> None:
        # The official marketplace writes these (§6). The index renders one skill per
        # line, so folding is what makes them importable at all.
        m = parse_skill_md(doc('name: t\ndescription: "Fills PDF forms.\n  Use when a form needs values."'))
        assert m.description == "Fills PDF forms. Use when a form needs values."

    def test_an_indented_quoted_block_written_below_its_key(self) -> None:
        # The shape that rejected the first version of this parser: the key's line ends
        # at the colon and the quote opens on the next. One file in 42 writes it, which
        # is the whole argument for measuring the corpus instead of reasoning about the
        # format — every same-line variant above passed while this one 422'd.
        m = parse_skill_md(doc('name: t\ndescription:\n  "Solve problems with adversarial\n  verification."'))
        assert m.description == "Solve problems with adversarial verification."

    def test_an_indented_plain_block_written_below_its_key(self) -> None:
        m = parse_skill_md(doc("name: t\ndescription:\n  Solve problems\n  carefully."))
        assert m.description == "Solve problems carefully."

    def test_a_multi_line_flow_sequence(self) -> None:
        m = parse_skill_md(doc("name: t\ndescription: d\nallowed-tools: [\n  Read,\n  Grep,\n]"))
        assert m.allowed_tools == ("Read", "Grep")

    def test_license_is_a_bare_sentence(self) -> None:
        # The one `license:` in the corpus is prose, not an SPDX id.
        m = parse_skill_md(doc("name: t\ndescription: d\nlicense: Complete terms in LICENSE.txt"))
        assert m.license == "Complete terms in LICENSE.txt"

    @pytest.mark.parametrize(
        ("line", "key"),
        [
            ("version: 0.1.0", "version"),
            ("user-invocable: true", "user-invocable"),
            ("disable-model-invocation: false", "disable-model-invocation"),
            ("argument-hint: [file]", "argument-hint"),
        ],
    )
    def test_every_unrecognized_key_in_the_corpus_imports(self, line: str, key: str) -> None:
        # These four are why the allowlist was measured and abandoned: together they
        # appear in 17 of 42 files. A parser that rejects them cannot import 40% of the
        # format it claims to interoperate with.
        m = parse_skill_md(doc(f"name: t\ndescription: d\n{line}"))
        assert key in m.extra_frontmatter


class TestVersionIsToleratedNotReserved:
    """D-53. The regression test for a decision that reverses three ACs' literal text.

    AC-24/26/29 name `version` as reserved server-assigned state. Measured, `version:`
    is in **13 of 42** real files and is always an author's semver — never SMAP's row
    counter. Rejecting it by name rejects 31% of the corpus.
    """

    def test_an_author_semver_is_preserved_rather_than_rejected(self) -> None:
        m = parse_skill_md(doc("name: t\ndescription: d\nversion: 0.1.0"))
        assert m.extra_frontmatter["version"] == "0.1.0"

    def test_it_cannot_reach_the_row_version_because_no_such_field_exists(self) -> None:
        # The structural half of the argument, and the reason a blocklist was not needed:
        # §8 threat 2 asks for server-assigned fields to be *unreachable from parsed
        # input*, which a DTO without them delivers for keys nobody enumerated.
        fields = set(SkillManifest.__dataclass_fields__)
        assert fields.isdisjoint({"version", "scope", "source", "created_by", "bundle_sha256", "id"})
        assert not any(f.endswith("_id") for f in fields)


class TestReservedKeys:
    @pytest.mark.parametrize(
        "key",
        ["scope", "id", "created_by", "source", "agent_id", "project_id", "org_id", "bundle_sha256"],
    )
    def test_a_reserved_key_is_rejected_by_name(self, key: str) -> None:
        # §8 threat 2: `scope: platform` in a file anyone can write is self-escalation to
        # the tier Q-7 exists to gate.
        with pytest.raises(BundleInvalid) as exc:
            parse_skill_md(doc(f"name: t\ndescription: d\n{key}: whatever"))
        assert exc.value.key == key
        assert "server-assigned" in exc.value.reason

    def test_an_owner_prefixed_key_is_reserved_by_prefix(self) -> None:
        with pytest.raises(BundleInvalid) as exc:
            parse_skill_md(doc("name: t\ndescription: d\nowner_org_id: 123"))
        assert exc.value.key == "owner_org_id"


class TestTheBodyIsVerbatim:
    def test_a_thematic_break_in_the_body_is_not_a_frontmatter_delimiter(self) -> None:
        # `prompt_loader._SEP_RE` was `^---$` MULTILINE and split on every one of these,
        # which is the concrete reason this parser exists (Q-29).
        body = "# Title\n\nIntro.\n\n---\n\n## Next\n\n---\n\nEnd."
        m = parse_skill_md(doc("name: t\ndescription: d", body))
        assert m.body == body

    def test_a_key_value_line_in_the_body_stays_in_the_body(self) -> None:
        body = "# Title\n\nSet `scope: platform` in your config.\n"
        m = parse_skill_md(doc("name: t\ndescription: d", body))
        assert m.body == body

    def test_the_blank_line_after_the_fence_is_body_bytes(self) -> None:
        # Verbatim means no leading-blank strip: `---\n\n# Title` must export back to
        # itself byte-for-byte (AC-26).
        _, body = split_frontmatter("---\nname: t\n---\n\n# Title\n")
        assert body == "\n# Title\n"

    def test_an_empty_body_is_empty(self) -> None:
        m = parse_skill_md("---\nname: t\ndescription: d\n---\n")
        assert m.body == ""


class TestLexicalRules:
    def test_a_utf8_bom_is_stripped(self) -> None:
        # `chr()`, never a literal — the rule the module under test follows for the same
        # Trojan-Source reason.
        m = parse_skill_md(chr(0xFEFF) + MINIMAL)
        assert m.name == "pdf-fill"

    def test_crlf_is_normalized(self) -> None:
        m = parse_skill_md(MINIMAL.replace("\n", "\r\n"))
        assert m.name == "pdf-fill"
        assert "\r" not in m.body

    def test_a_comment_line_is_ignored(self) -> None:
        m = parse_skill_md(doc("# a note\nname: t\ndescription: d"))
        assert m.name == "t"

    def test_a_hash_inside_a_value_is_text_not_a_comment(self) -> None:
        # The parser has no business editing an author's prose.
        m = parse_skill_md(doc("name: t\ndescription: Use # for headings"))
        assert m.description == "Use # for headings"

    def test_a_duplicate_key_is_rejected(self) -> None:
        # `prompt_loader` silently last-wins. Two descriptions mean the author does not
        # know which one the model reads, and neither do we.
        with pytest.raises(BundleInvalid) as exc:
            parse_skill_md(doc("name: t\ndescription: one\ndescription: two"))
        assert exc.value.key == "description"
        assert "duplicate" in exc.value.reason

    def test_blank_lines_between_keys_are_fine(self) -> None:
        assert parse_skill_md(doc("name: t\n\ndescription: d")).name == "t"


class TestStructuralRejections:
    def test_a_document_with_no_frontmatter_is_rejected(self) -> None:
        with pytest.raises(BundleInvalid):
            parse_skill_md("# Just a document\n")

    def test_an_unclosed_frontmatter_block_is_rejected(self) -> None:
        with pytest.raises(BundleInvalid) as exc:
            parse_skill_md("---\nname: t\ndescription: d\n")
        assert "never closed" in exc.value.reason

    @pytest.mark.parametrize("missing", ["name", "description"])
    def test_both_required_keys_are_required(self, missing: str) -> None:
        keys = {"name": "name: t", "description": "description: d"}
        del keys[missing]
        with pytest.raises(BundleInvalid) as exc:
            parse_skill_md(doc("\n".join(keys.values())))
        assert exc.value.key == missing

    def test_an_unparseable_line_is_rejected_with_its_number(self) -> None:
        with pytest.raises(BundleInvalid) as exc:
            parse_skill_md(doc("name: t\ndescription: d\nthis is not a key"))
        assert "line 3" in exc.value.reason

    def test_an_unterminated_quote_is_rejected(self) -> None:
        with pytest.raises(BundleInvalid) as exc:
            parse_skill_md(doc('name: t\ndescription: "never closed'))
        assert "unterminated" in exc.value.reason

    def test_an_unterminated_flow_sequence_is_rejected(self) -> None:
        with pytest.raises(BundleInvalid) as exc:
            parse_skill_md(doc("name: t\ndescription: d\nallowed-tools: [Read, Grep"))
        assert "unterminated" in exc.value.reason

    def test_a_scalar_key_given_a_list_is_rejected_naming_the_key(self) -> None:
        with pytest.raises(BundleInvalid) as exc:
            parse_skill_md(doc("name: t\ndescription:\n  - one\n  - two"))
        assert exc.value.key == "description"

    @pytest.mark.parametrize(
        "name",
        [
            "Has-Capitals",
            "has spaces",
            "-leading-hyphen",
            "under_score",
            "a" * 65,
            "",
        ],
    )
    def test_an_invalid_name_is_rejected(self, name: str) -> None:
        # The name is the directory under /workspace/skills/, so it is filesystem-safe by
        # construction or not at all ([R31.01]).
        with pytest.raises(BundleInvalid) as exc:
            parse_skill_md(doc(f'name: "{name}"\ndescription: d'))
        assert exc.value.key == "name"

    def test_an_empty_description_is_rejected(self) -> None:
        # `description` decides whether the model ever selects the skill (Q-14).
        with pytest.raises(BundleInvalid) as exc:
            parse_skill_md(doc('name: t\ndescription: "   "'))
        assert exc.value.key == "description"


class TestTheCharsetRuleReachesImport:
    """AC-30's third entry point. A rule enforced at only some entry points is not a rule,
    and import is precisely the one whose author is a stranger (Q-2)."""

    def test_a_description_at_the_cap_is_accepted(self) -> None:
        m = parse_skill_md(doc(f"name: t\ndescription: {'x' * MAX_DESCRIPTION_CHARS}"))
        assert len(m.description) == MAX_DESCRIPTION_CHARS

    def test_an_over_cap_description_is_rejected(self) -> None:
        with pytest.raises(BundleInvalid) as exc:
            parse_skill_md(doc(f"name: t\ndescription: {'x' * (MAX_DESCRIPTION_CHARS + 1)}"))
        assert exc.value.key == "description"
        assert "exceeds" in exc.value.reason

    @pytest.mark.parametrize(
        ("codepoint", "expected"),
        [
            (0x202E, "bidirectional"),  # RIGHT-TO-LEFT OVERRIDE
            (0x200B, "zero-width"),  # ZERO WIDTH SPACE
            (0xE0041, "tag character"),  # TAG LATIN CAPITAL A
            (0x2028, "non-printing"),  # LINE SEPARATOR — str.splitlines() splits on it
        ],
    )
    def test_an_invisible_character_in_a_description_is_rejected(self, codepoint: int, expected: str) -> None:
        with pytest.raises(BundleInvalid) as exc:
            parse_skill_md(doc(f"name: t\ndescription: safe{chr(codepoint)}text"))
        assert expected in exc.value.reason

    def test_the_index_delimiter_in_a_description_is_rejected(self) -> None:
        with pytest.raises(BundleInvalid) as exc:
            parse_skill_md(doc(f"name: t\ndescription: d {INDEX_DELIMITER_MARKER} x"))
        assert "delimiter" in exc.value.reason

    @pytest.mark.parametrize("key", ["allowed-tools", "x-smap-requires"])
    def test_the_rule_covers_list_elements_too(self, key: str) -> None:
        # AC-30's matrix is over every model-or-UI-facing field, `description` included
        # but not alone. `allowed_tools` is display-only (Q-8), which makes it a
        # display-injection surface, not an exempt one.
        with pytest.raises(BundleInvalid) as exc:
            parse_skill_md(doc(f"name: t\ndescription: d\n{key}: [ok, bad{chr(0x202E)}]"))
        assert exc.value.key == key

    def test_the_rule_covers_license(self) -> None:
        with pytest.raises(BundleInvalid) as exc:
            parse_skill_md(doc(f"name: t\ndescription: d\nlicense: MIT{chr(0x200B)}"))
        assert exc.value.key == "license"

    def test_the_rule_covers_a_tolerated_key_and_its_value(self) -> None:
        # A tolerated key is re-emitted on export, so an unchecked newline here would
        # forge a key in a file SMAP itself writes (AC-31's second line of defense).
        with pytest.raises(BundleInvalid):
            parse_skill_md(doc(f"name: t\ndescription: d\nx-vendor: bad{chr(0x202E)}"))


class TestFrontmatterCannotBeForgedFromAValue:
    """§8 threat 2, the direction that needs no zip crafting at all."""

    def test_a_forged_key_inside_a_quoted_value_stays_part_of_the_value(self) -> None:
        # Greedy quote-gathering is the safe direction: `scope:` on a continuation line
        # inside an open quote must remain description text and never become a key.
        m = parse_skill_md(doc('name: t\ndescription: "Fills forms.\n  scope: platform"'))
        assert m.description == "Fills forms. scope: platform"

    def test_a_newline_bearing_description_cannot_survive_to_be_re_emitted(self) -> None:
        # The round trip is what weaponizes it (§8): a description holding a newline
        # exports into frontmatter as a new `scope:` line and re-imports as a platform
        # skill. Folding removes the newline; had it not, the charset rule rejects it.
        m = parse_skill_md(doc('name: t\ndescription: "one\n  two"'))
        assert "\n" not in m.description


class TestWarnings:
    def test_unrecognized_keys_produce_exactly_one_warning_naming_them(self) -> None:
        # §6: "surfaced as one import warning" — one, not one per key, so a bundle with
        # eight vendor keys does not bury the compatibility warnings that matter (Q-10).
        m = parse_skill_md(doc("name: t\ndescription: d\nversion: 1.0.0\nuser-invocable: true"))
        assert len(m.warnings) == 1
        assert "user-invocable" in m.warnings[0]
        assert "version" in m.warnings[0]

    def test_a_fully_recognized_document_warns_about_nothing(self) -> None:
        m = parse_skill_md(
            doc("name: t\ndescription: d\nallowed-tools: [Read]\nlicense: MIT\nx-smap-requires: [code_exec]")
        )
        assert m.warnings == ()
        assert m.extra_frontmatter == {}
        assert m.requires == ("code_exec",)
