"""AC-3, AC-4, AC-5: `scripts/traceability.py` parses every shape the SRS uses.

The fixture below is a miniature SRS, never `REQUIREMENTS.md` itself. A test that read
the live document would rewrite its own expectations every time a requirement was added,
which is a test that asserts nothing; the gate's job is to compare the live document
against the committed CSV, and that comparison runs in CI, not here.

What the fixture is built to cover, because each of these is a real form in the SRS and
each has already broken one implementation of this parser:

- all four ID shapes, including the chapter-letter suffix (`R7a.01`) that a
  `R\\d+\\.\\d+`-shaped pattern cannot see;
- all three definition forms — bullet, bare paragraph, numbered list item;
- two bare paragraph definitions on adjacent lines, which §3 does and which makes "the
  next line" an unreliable block terminator on its own;
- a definition whose block is ended by a nested list, by a fenced code block, and by a
  wrapped continuation line that must be joined rather than dropped;
- emphasis wrapped *around* a code span, and an underscore that is part of an identifier
  rather than italics.

The ids below are invented, so every bracketed one in this file dangles against the real
SRS. That is why `scripts/traceability.py` excludes this path from its citation scan by
name — the exclusion and this fixture have to move together.
"""

from __future__ import annotations

import csv
import importlib.util
import io
import pathlib

import pytest

_REPO = pathlib.Path(__file__).resolve().parents[3]
_SCRIPT = _REPO / "scripts" / "traceability.py"

_spec = importlib.util.spec_from_file_location("_traceability_extraction", _SCRIPT)
assert _spec is not None
assert _spec.loader is not None
traceability = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(traceability)


FIXTURE_SRS = """\
# Fixture SRS

Preamble prose that defines nothing.

## 3. High-level Architecture

**[R3.01]** A bare-paragraph definition.
**[R3.02]** A second bare paragraph directly beneath, with no blank line between them.

- **[R3.03]** A bullet with **bold**, *italic*, `code`, a **bold `span` inside** and a
  continuation line that wraps.
- **[R3.04]** Followed by nested detail:
  - This sub-bullet elaborates it and is not part of the summary.

5. **[R3.05]** A numbered-list definition.

- **[R3.06]** Followed by a fenced block:
```json
{"id": "uuid"}
```

- **[R3.07]** He said "quoted", and a_snake_case_name keeps its underscores.

## 7a. Chapter With A Letter Suffix

- **[R7a.01]** A chapter-suffix identifier.
- **[R7.09a]** An item-suffix identifier.
- **[R22.15.01]** A three-part identifier.
"""

EXPECTED_ROWS = [
    ("R3.01", "3. High-level Architecture", "A bare-paragraph definition."),
    (
        "R3.02",
        "3. High-level Architecture",
        "A second bare paragraph directly beneath, with no blank line between them.",
    ),
    (
        "R3.03",
        "3. High-level Architecture",
        "A bullet with bold, italic, code, a bold span inside and a continuation line that wraps.",
    ),
    ("R3.04", "3. High-level Architecture", "Followed by nested detail:"),
    ("R3.05", "3. High-level Architecture", "A numbered-list definition."),
    ("R3.06", "3. High-level Architecture", "Followed by a fenced block:"),
    (
        "R3.07",
        "3. High-level Architecture",
        'He said "quoted", and a_snake_case_name keeps its underscores.',
    ),
    ("R7a.01", "7a. Chapter With A Letter Suffix", "A chapter-suffix identifier."),
    ("R7.09a", "7a. Chapter With A Letter Suffix", "An item-suffix identifier."),
    ("R22.15.01", "7a. Chapter With A Letter Suffix", "A three-part identifier."),
]


@pytest.fixture(scope="module")
def parsed() -> list:
    return traceability.parse_requirements(FIXTURE_SRS)


def test_every_definition_form_and_id_shape_produces_one_row(parsed: list) -> None:
    assert [(r.id, r.section, r.summary) for r in parsed] == EXPECTED_ROWS


def test_rows_are_emitted_in_document_order(parsed: list) -> None:
    assert [r.line for r in parsed] == sorted(r.line for r in parsed)


@pytest.mark.parametrize(
    "requirement_id",
    ["R3.01", "R7a.01", "R7.09a", "R22.15.01"],
    ids=["plain", "chapter-suffix", "item-suffix", "three-part"],
)
def test_all_four_id_shapes_round_trip(parsed: list, requirement_id: str) -> None:
    """A pattern that cannot see a shape drops it silently, which is the whole defect."""
    assert requirement_id in {r.id for r in parsed}
    assert traceability.CITATION.fullmatch(f"[{requirement_id}]") is not None


def test_a_duplicated_id_fails_and_names_both_definitions() -> None:
    source = "## 3. A\n\n- **[R3.01]** First.\n- **[R3.02]** Second.\n- **[R3.01]** Again.\n"
    with pytest.raises(traceability.TraceabilityError) as excinfo:
        traceability.parse_requirements(source)
    message = str(excinfo.value)
    assert "[R3.01]" in message
    assert "REQUIREMENTS.md:3" in message
    assert "REQUIREMENTS.md:5" in message


def test_two_definitions_on_one_line_fail_and_name_both() -> None:
    """A summary runs to the end of its block, so the second would be swallowed silently."""
    source = "## 3. A\n\n| **[R3.01]** First. | **[R3.02]** Second. |\n"
    with pytest.raises(traceability.TraceabilityError) as excinfo:
        traceability.parse_requirements(source)
    message = str(excinfo.value)
    assert "[R3.01]" in message
    assert "[R3.02]" in message
    assert "REQUIREMENTS.md:3" in message


def test_a_definition_before_any_heading_fails() -> None:
    with pytest.raises(traceability.TraceabilityError) as excinfo:
        traceability.parse_requirements("- **[R3.01]** Homeless.\n")
    assert "no section" in str(excinfo.value)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("**bold**", "bold"),
        ("*italic*", "italic"),
        ("`code`", "code"),
        # Emphasis wrapping a code span: splitting on spans puts the two `**` markers in
        # different fragments, so neither is ever stripped.
        ("**Slice `api/` folders**", "Slice api/ folders"),
        # An asterisk inside a code span is not emphasis and must survive.
        ("`*Container.vue`", "*Container.vue"),
        ("`S*` and `on*`", "S* and on*"),
        # Underscores are identifiers here, never italics.
        ("`api[_-]?key|private[_-]?key`", "api[_-]?key|private[_-]?key"),
        ("a_snake_case_name", "a_snake_case_name"),
        ("[link text](https://example.invalid)", "link text"),
        ("![alt text](image.png)", "alt text"),
        # A bracketed citation is not a link and must not be rewritten.
        ("see [R13.13] for the rule", "see [R13.13] for the rule"),
        ("collapses    all\n  whitespace", "collapses all whitespace"),
    ],
)
def test_inline_markdown_is_reduced_to_text(raw: str, expected: str) -> None:
    assert traceability._strip_inline_markdown(raw) == expected


def test_rendered_csv_has_a_header_and_one_row_per_requirement(parsed: list) -> None:
    rendered = traceability.render_csv(parsed)
    lines = rendered.split("\n")
    assert lines[0] == traceability.HEADER
    assert lines[-1] == ""  # the file ends with a newline
    assert len(lines) == len(parsed) + 2


def test_rendered_csv_quotes_the_free_text_columns_and_not_the_id(parsed: list) -> None:
    row = traceability.render_csv(parsed).split("\n")[1]
    assert row.startswith('R3.01,"')


def test_this_file_is_excluded_from_the_citation_scan() -> None:
    """Renaming this fixture without moving its exclusion turns the real gate red."""
    rel = pathlib.Path(__file__).resolve().relative_to(_REPO).as_posix()
    assert traceability._excluded(rel)


@pytest.mark.parametrize(
    "rel",
    [
        ".github/workflows/ci.yml",
        "deploy/sandbox/code-exec/Dockerfile",
        "deploy/sandbox/code-exec/kernel/kernel.py",
        "frontend/eslint.config.js",
        "frontend/tests",
        "scripts/traceability.py",
    ],
)
def test_the_citation_scan_reaches_outside_backend_frontend_src_and_docs(rel: str) -> None:
    """Each of these cites an `[Rxx.yy]` (or is a sibling gate's root) and was unreachable
    under the four-root scope the dossier specified. A root list cannot be kept honest;
    every tracked file minus a printed exclusion list can."""
    assert not traceability._excluded(rel)


def test_the_definition_source_is_excluded() -> None:
    """§9.2 legitimately names the ids it removed, so scanning the SRS would fail on it."""
    assert traceability._excluded("REQUIREMENTS.md")


def test_a_bom_is_named_rather_than_reported_as_a_row_diff(
    parsed: list, capsys: pytest.CaptureFixture[str]
) -> None:
    """A byte comparison fails on a BOM, and a row diff cannot show why."""
    generated = traceability.render_csv(parsed)
    traceability.report_row_drift(traceability.BOM + generated, generated)
    out = capsys.readouterr().out
    assert "BOM" in out
    assert "differs      R3.01" not in out


def test_a_changed_header_is_named_rather_than_reported_as_a_row_diff(
    parsed: list, capsys: pytest.CaptureFixture[str]
) -> None:
    generated = traceability.render_csv(parsed)
    committed = generated.replace(traceability.HEADER, "id,section,summary", 1)
    traceability.report_row_drift(committed, generated)
    out = capsys.readouterr().out
    assert "header differs" in out
    assert "missing row" not in out


def test_rendered_csv_parses_back_to_the_rows_it_was_built_from(parsed: list) -> None:
    """The quoting is hand-shaped, so prove it is still valid CSV — R3.07 embeds quotes."""
    rendered = traceability.render_csv(parsed)
    read_back = list(csv.reader(io.StringIO(rendered)))
    assert read_back[0] == traceability.HEADER.split(",")
    assert [tuple(row) for row in read_back[1:]] == EXPECTED_ROWS
