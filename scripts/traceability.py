#!/usr/bin/env python3
"""Generator + repo gate — `docs/traceability.csv` is derived from `REQUIREMENTS.md`.

§27 of the SRS has always instructed "re-run the extraction whenever new `[Rxx.yy]`
IDs are added", but no extraction tool existed: the file came from a one-off author
pass on 2026-04-25 and was hand-maintained per chapter afterwards, which is why some
chapters were complete and §30 had none of its rows. See
`docs/tasks/2026-08-24-traceability-extraction-gate/`.

The generator and the checker are the same code path on purpose. A checker that
reimplements "what is a requirement" is free to disagree with the writer, and the
disagreement is invisible: the analysis behind this script first used
`\\[R(\\d+\\.\\d+)\\]` and reported nine non-existent stale rows, because that pattern
cannot see `[R7.09a]`, `[R22.15.01]` or `[R19a.10]`. One parser, one definition.

Python rather than shell for the same reason as `check_no_lazy_prompt.py`: it runs on a
Windows dev box, where a missing tool exits non-zero in a *pass-shaped* way under a shell
without `set -e`.

What this gate does NOT verify, stated so the gap is not mistaken for coverage:

- that a requirement has a test, or any implementation at all. It checks that the index
  is complete and that citations resolve; it makes no claim about coverage.
- that `[Rxx.yy]` prose *inside* `REQUIREMENTS.md` resolves. That file is the definition
  source and is the first exclusion below; §9.2 legitimately names the ids it removed, and
  a self-citation of a removed ID is therefore not caught here.
- unbracketed mentions. Only `[R13.13]` is a citation; the bare `R13.13` form, which the
  SRS and `docs/implement/` both use freely, is indistinguishable from ordinary prose
  without a much looser pattern and its false positives.
- that `summary` reads well. It is a mechanical transform of the SRS sentence. The rule
  when a summary reads badly is to edit the SRS sentence, never to special-case this
  script — otherwise the column stops being derivable and starts drifting again.

Usage (from the repo root):
    python scripts/traceability.py            # regenerate docs/traceability.csv
    python scripts/traceability.py --check    # verify only; non-zero exit on drift
"""

from __future__ import annotations

import argparse
import csv
import io
import pathlib
import re
import subprocess
import sys
from typing import NamedTuple

REPO = pathlib.Path(__file__).resolve().parents[1]
SRS = REPO / "REQUIREMENTS.md"
CSV_PATH = REPO / "docs" / "traceability.csv"

HEADER = "requirement_id,section,summary"
# Spelled as an escape, never as a literal: a literal BOM in this source is invisible in
# every editor and would be indistinguishable from the defect it exists to report.
BOM = "\ufeff"

# Four ID shapes are in use and all four are load-bearing: plain `R30.15`, an item-letter
# suffix `R7.09a`, a three-part `R22.15.01`, and a *chapter*-letter suffix `R19a.10`
# (§19a is a real chapter; R11a.01-02 live under §11). Narrowing this pattern silently
# drops requirements from the index rather than failing, which is the failure this whole
# gate exists to prevent.
ID = r"R\d+[a-z]?\.\d+(?:\.\d+)?[a-z]?"

DEFINITION = re.compile(rf"\*\*\[({ID})\]\*\*")
CITATION = re.compile(rf"\[({ID})\]")
HEADING = re.compile(r"^##\s+(.+?)\s*$")

# A definition's text runs to the end of its own Markdown block. These end it. The
# nested-list case is not an edge case: 32 definitions are followed by sub-bullets that
# elaborate them, and the authored rows never included those, so neither does this.
BLOCK_END = re.compile(
    r"""
      ^\s*$                       # blank line
    | ^\#{1,6}\s                  # heading
    | ^\s*(?:-{3,}|\*{3,}|_{3,})\s*$   # thematic break
    | ^\s*(?:[-*+]|\d+[.)])\s     # a new list item, at any indent
    | ^\s*\|                      # a table row
    | ^\s*(?:```|~~~)             # a fenced code block
    """,
    re.VERBOSE,
)
# A bare-paragraph definition can sit directly under another one with no blank line
# between them (§3 does this four times), so a following marker also ends the block.
NEXT_DEFINITION = re.compile(rf"^\s*\*\*\[{ID}\]\*\*")

# Code spans are stashed behind placeholders before emphasis is stripped, for two
# reasons that pull in opposite directions. `*` occurs inside spans (`S*`, `on*`,
# `@slices/*`, `ceil(numerator * N / denominator)`), so an emphasis regex over the raw
# text pairs those asterisks across span boundaries and deletes what lies between them.
# But emphasis also *contains* spans (`**Slice `api/` folders wrap these**`), so merely
# splitting on spans leaves each half of that pair in a different fragment and neither
# is ever stripped. A placeholder is opaque to the emphasis regexes and keeps the
# surrounding text one string, which satisfies both. Underscores are deliberately never
# treated as emphasis: the SRS uses `_` inside identifiers and inside a redaction
# regex, and never as italics.
CODE_SPAN = re.compile(r"`([^`]*)`")
PLACEHOLDER = re.compile(r"\x00(\d+)\x00")
IMAGE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
BOLD = re.compile(r"\*\*(.+?)\*\*")
ITALIC = re.compile(r"\*([^*]+)\*")
WHITESPACE = re.compile(r"\s+")

# Every tracked file, minus the exclusions below — not a root list. A root list is the
# wrong shape for this check: `[Rxx.yy]` is cited wherever someone reaches for it, and
# the four roots the dossier specified missed four files that cite one today
# (`.github/workflows/ci.yml`, `deploy/sandbox/code-exec/Dockerfile`, that image's
# `kernel/kernel.py`, and `frontend/eslint.config.js`). A citation the gate does not
# read is a citation the gate cannot defend.
#
# Each exclusion is deliberate, and each is printed on every run so the scope stays
# visible rather than becoming folklore.
EXCLUSIONS: tuple[tuple[str, str], ...] = (
    ("REQUIREMENTS.md", "the definition source; its own prose is not a citation"),
    ("docs/tasks", "task dossiers are a historical record, not live documentation"),
    ("docs/audits", "audit findings are a historical record, not live documentation"),
    ("backend/alembic/versions", "a landed migration is immutable history"),
    (
        "docs/traceability.csv",
        "generated from REQUIREMENTS.md, which is itself the definition source",
    ),
    # Scoped to the one file whose whole content is a fixture SRS. Its ids are invented
    # on purpose — a shape like R7a.01 has to be spelled out to prove the parser sees
    # it — so every one of them dangles by construction. Same shape and same reasoning
    # as check_no_lazy_prompt.py's fourth exclusion.
    (
        "backend/tests/unit/test_traceability_extraction.py",
        "a fixture SRS; its invented ids are the point of the test",
    ),
)


class TraceabilityError(Exception):
    """The SRS cannot be parsed unambiguously; the caller prints this and exits 1."""


class Requirement(NamedTuple):
    id: str
    section: str
    summary: str
    line: int


def _strip_inline_markdown(text: str) -> str:
    """Reduce inline Markdown to its text, then collapse whitespace to single spaces."""
    spans: list[str] = []

    def _stash(match: re.Match[str]) -> str:
        spans.append(match.group(1))
        return f"\x00{len(spans) - 1}\x00"

    text = CODE_SPAN.sub(_stash, text)
    text = IMAGE.sub(r"\1", text)
    text = LINK.sub(r"\1", text)
    text = BOLD.sub(r"\1", text)
    text = ITALIC.sub(r"\1", text)
    text = PLACEHOLDER.sub(lambda match: spans[int(match.group(1))], text)
    return WHITESPACE.sub(" ", text).strip()


def parse_requirements(text: str) -> list[Requirement]:
    """Every `**[<id>]**` in document order, with its section and derived summary.

    Raises `TraceabilityError` on a duplicate ID rather than picking one: two rows
    claiming the same requirement, or one row silently winning, are both worse than a
    red build that names the line.
    """
    lines = text.split("\n")
    section = ""
    seen: dict[str, int] = {}
    found: list[Requirement] = []

    for index, line in enumerate(lines):
        heading = HEADING.match(line)
        if heading:
            section = heading.group(1)
            continue

        matches = list(DEFINITION.finditer(line))
        if not matches:
            continue
        if len(matches) > 1:
            # A summary runs to the end of its block, so a second marker's text would be
            # swallowed into the first requirement's row and the second would vanish from
            # the index with both the generator and the checker agreeing. Same reasoning
            # as the duplicate-ID rule below: fail rather than pick.
            named = ", ".join(f"[{m.group(1)}]" for m in matches)
            raise TraceabilityError(
                f"REQUIREMENTS.md:{index + 1} defines {named} on one line. "
                "Each definition needs a line of its own, or all but the first are lost."
            )

        match = matches[0]
        requirement_id = match.group(1)
        lineno = index + 1
        if requirement_id in seen:
            raise TraceabilityError(
                f"[{requirement_id}] is defined twice: "
                f"REQUIREMENTS.md:{seen[requirement_id]} and REQUIREMENTS.md:{lineno}. "
                "Every requirement must have exactly one definition."
            )
        seen[requirement_id] = lineno

        if not section:
            raise TraceabilityError(
                f"[{requirement_id}] at REQUIREMENTS.md:{lineno} precedes any "
                "'## ' heading, so it has no section to record."
            )

        block = [line[match.end() :]]
        for following in lines[index + 1 :]:
            if BLOCK_END.match(following) or NEXT_DEFINITION.match(following):
                break
            block.append(following)

        found.append(
            Requirement(
                id=requirement_id,
                section=section,
                summary=_strip_inline_markdown(" ".join(block)),
                line=lineno,
            )
        )

    return found


def _render_row(requirement: Requirement) -> str:
    """`id,"section","summary"` — the id bare, the two free-text columns always quoted.

    Always-quote keeps every row the same shape whether or not its text happens to
    contain a comma, so a diff on this file reads as a diff on the SRS. Escaping is the
    stdlib writer's, not hand-rolled.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer, quoting=csv.QUOTE_ALL, lineterminator="")
    writer.writerow([requirement.section, requirement.summary])
    return f"{requirement.id},{buffer.getvalue()}"


def render_csv(requirements: list[Requirement]) -> str:
    """The complete file: header, one row per requirement in SRS document order."""
    return "\n".join([HEADER, *(_render_row(r) for r in requirements)]) + "\n"


def _row_id(row: str) -> str:
    return row.split(",", 1)[0]


def report_row_drift(committed: str, generated: str) -> None:
    """Print what differs, row by row, so the reader knows what to regenerate.

    The comparison the caller made is byte-for-byte, so it can fail on things no row
    diff would show — a BOM an editor added, a changed header. Those are named
    explicitly: a gate that reports "row R3.01 differs" over two visually identical
    rows teaches the reader to stop believing it.
    """
    if committed.startswith(BOM):
        print("  the committed file carries a UTF-8 BOM; this writes it without one.")
        committed = committed.removeprefix(BOM)

    committed_lines = committed.split("\n")
    generated_lines = generated.split("\n")
    if committed_lines[0] != generated_lines[0]:
        print(f"  header differs: {committed_lines[0]!r} should be {generated_lines[0]!r}")

    committed_by_id = {_row_id(r): r for r in committed_lines[1:] if r}
    generated_by_id = {_row_id(r): r for r in generated_lines[1:] if r}

    for requirement_id, row in generated_by_id.items():
        if requirement_id not in committed_by_id:
            print(f"  missing row  {requirement_id}: {row}")
    for requirement_id in committed_by_id:
        if requirement_id not in generated_by_id:
            print(f"  stale row    {requirement_id}: not defined in REQUIREMENTS.md")
    for requirement_id, row in generated_by_id.items():
        other = committed_by_id.get(requirement_id)
        if other is not None and other != row:
            print(f"  differs      {requirement_id}")
            print(f"    committed: {other}")
            print(f"    generated: {row}")

    if committed_by_id == generated_by_id:
        print(
            "  every row matches by id and by text, so the difference is in row order, "
            "the header, or the line endings."
        )


def _excluded(rel: str) -> bool:
    return any(rel == path or rel.startswith(path + "/") for path, _ in EXCLUSIONS)


def _tracked_files() -> list[str]:
    """Every tracked file in the repository.

    `git ls-files` rather than a filesystem walk, so build output and tool caches
    (`dist/`, `node_modules/`, `.import_linter_cache`) stay out of scope without a
    hand-maintained skip list drifting out of date. Untracked files are out of scope for
    the same reason they are invisible to a reviewer: they are not part of the commit.
    """
    out = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=REPO,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout
    return [p for p in out.split("\0") if p]


def find_dangling_citations(defined: set[str]) -> tuple[list[str], int]:
    """Every `[Rxx.yy]` cited outside the SRS that names no defined requirement."""
    dangling: list[str] = []
    scanned = 0

    for rel in _tracked_files():
        if _excluded(rel):
            continue
        path = REPO / rel
        if not path.is_file():  # a deleted-but-staged path
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue  # binary or unreadable — nothing citable in it
        scanned += 1
        for lineno, line in enumerate(text.splitlines(), start=1):
            for match in CITATION.finditer(line):
                if match.group(1) not in defined:
                    dangling.append(f"{rel}:{lineno}: [{match.group(1)}]")

    return dangling, scanned


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify the committed file instead of rewriting it",
    )
    args = parser.parse_args(argv)

    try:
        requirements = parse_requirements(SRS.read_text(encoding="utf-8"))
    except TraceabilityError as exc:
        print("Gate FAILED — REQUIREMENTS.md is ambiguous:")
        print(f"  {exc}")
        return 1

    sections = len({r.section for r in requirements})
    print(
        f"Parsed {len(requirements)} requirement definitions across {sections} sections of REQUIREMENTS.md."
    )

    generated = render_csv(requirements)
    failed = False

    if args.check:
        committed = CSV_PATH.read_bytes().decode("utf-8") if CSV_PATH.exists() else ""
        if committed != generated:
            print("\nGate FAILED — docs/traceability.csv does not match REQUIREMENTS.md:")
            report_row_drift(committed, generated)
            print(
                "\nRegenerate it in the same change that touched the SRS:\n  python scripts/traceability.py"
            )
            failed = True
        else:
            print(f"docs/traceability.csv matches ({len(requirements)} rows).")
    else:
        CSV_PATH.write_text(generated, encoding="utf-8", newline="\n")
        print(f"Wrote docs/traceability.csv ({len(requirements)} rows).")

    dangling, scanned = find_dangling_citations({r.id for r in requirements})
    print(f"\nScanned {scanned} tracked files for citations.")
    for path, why in EXCLUSIONS:
        print(f"  excluded {path} — {why}")

    if dangling:
        print("\nGate FAILED — these citations name no requirement in REQUIREMENTS.md:")
        for hit in dangling:
            print(f"  {hit}")
        print(
            "\nA renumbered requirement needs its citations updated; a removed one "
            "needs them rewritten to name whatever superseded it."
        )
        failed = True
    else:
        print("Every cited [Rxx.yy] resolves.")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
