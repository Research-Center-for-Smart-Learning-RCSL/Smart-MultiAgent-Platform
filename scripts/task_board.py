#!/usr/bin/env python3
"""Generator + repo gate: `docs/tasks/BOARD.md` is derived from the dossiers' frontmatter.

The board answers "what can I pick up next". It was hand-maintained until 2026-11-01, and
by then it had grown to 2,400 lines of completion history around five live rows, with
rows in the wrong section (an in-progress dossier under Ready now, another buried in the
history). The frontmatter was always the source of truth (`docs/tasks/README.md`), so the
board is now generated from it and this gate fails when the committed file drifts. The
hand-written history is frozen in `docs/tasks/archive/board-history-to-2026-11.md`.

The generator and the checker are one code path, as in `traceability.py`, so they cannot
disagree about what a dossier is.

The gate also fails on frontmatter the board cannot place: a status outside the lifecycle
in `docs/tasks/README.md`, a missing `status`, or a `depends_on` entry naming no dossier.

What it does NOT verify: that a dossier's status is true (an `in-progress` dossier whose
code already shipped stays on the board until someone flips it), or anything below the
frontmatter.

Usage (from the repo root):
    python scripts/task_board.py            # regenerate docs/tasks/BOARD.md
    python scripts/task_board.py --check    # verify only; non-zero exit on drift
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys
from typing import NamedTuple

REPO = pathlib.Path(__file__).resolve().parents[1]
TASKS = REPO / "docs" / "tasks"
BOARD = TASKS / "BOARD.md"
#: Folders under docs/tasks/ that are not dossiers.
NOT_DOSSIERS = {"archive"}

LIFECYCLE = {"draft", "approved", "in-progress", "implemented", "superseded", "abandoned"}
CLOSED = {"implemented", "superseded", "abandoned"}

KEY = re.compile(r"^([a-z_]+):\s*(.*)$")
ITEM = re.compile(r"^\s+-\s+(.+?)\s*$")
TITLE = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)


class FrontmatterError(Exception):
    pass


class Dossier(NamedTuple):
    slug: str
    type: str
    status: str
    title: str
    depends_on: tuple[str, ...]


def _value(raw: str) -> str | list[str]:
    raw = raw.strip()
    if raw.startswith("[") and raw.endswith("]"):
        return [v.strip() for v in raw[1:-1].split(",") if v.strip()]
    return raw


def parse_frontmatter(text: str, slug: str) -> dict[str, str | list[str]]:
    """The flat YAML subset the templates use: `key: value`, `key: [a, b]` and `key:` + `- a` lines."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise FrontmatterError(f"{slug}: spec.md does not open with a frontmatter block")
    fields: dict[str, str | list[str]] = {}
    current: str | None = None
    for line in lines[1:]:
        if line.strip() == "---":
            return fields
        if (m := ITEM.match(line)) and current is not None:
            existing = fields[current]
            fields[current] = [*(existing if isinstance(existing, list) else []), m.group(1)]
            continue
        if m := KEY.match(line):
            current = m.group(1)
            fields[current] = _value(m.group(2)) if m.group(2).strip() else []
            continue
        if line.strip():
            raise FrontmatterError(f"{slug}: unreadable frontmatter line {line!r}")
    raise FrontmatterError(f"{slug}: frontmatter block is never closed")


def load_dossiers() -> list[Dossier]:
    dossiers = []
    for folder in sorted(p for p in TASKS.iterdir() if p.is_dir() and p.name not in NOT_DOSSIERS):
        spec = folder / "spec.md"
        if not spec.is_file():
            raise FrontmatterError(f"{folder.name}: no spec.md")
        # utf-8-sig: one 2026-07 dossier was saved with a BOM, which is harmless to readers.
        text = spec.read_text(encoding="utf-8-sig")
        fields = parse_frontmatter(text, folder.name)
        status = fields.get("status")
        if not isinstance(status, str) or status not in LIFECYCLE:
            raise FrontmatterError(f"{folder.name}: status {status!r} is not one of {sorted(LIFECYCLE)}")
        depends = fields.get("depends_on", [])
        title = TITLE.search(text)
        dossiers.append(
            Dossier(
                slug=folder.name,
                type=str(fields.get("type", "")),
                status=status,
                title=title.group(1) if title else "",
                depends_on=tuple(depends if isinstance(depends, list) else [depends]),
            )
        )
    return dossiers


def render(dossiers: list[Dossier]) -> str:
    by_slug = {d.slug: d for d in dossiers}
    dangling = [(d.slug, dep) for d in dossiers for dep in d.depends_on if dep not in by_slug]
    if dangling:
        raise FrontmatterError(
            "depends_on names no dossier: " + ", ".join(f"{s} -> {dep}" for s, dep in dangling)
        )

    def unmet(d: Dossier) -> list[str]:
        return [dep for dep in d.depends_on if by_slug[dep].status != "implemented"]

    active = [d for d in dossiers if d.status not in CLOSED]
    in_progress = [d for d in active if d.status == "in-progress"]
    ready = [d for d in active if d.status != "in-progress" and not unmet(d)]
    blocked = [d for d in active if d.status != "in-progress" and unmet(d)]

    def esc(text: str) -> str:
        return text.replace("|", "\\|")

    def table(rows: list[Dossier], *, waiting: bool) -> list[str]:
        if not rows:
            return ["None.", ""]
        head = "| Dossier | Type | Status | Title |" + (" Waiting on |" if waiting else "")
        rule = "|---|---|---|---|" + ("---|" if waiting else "")
        out = [head, rule]
        for d in rows:
            cells = [f"[`{d.slug}`]({d.slug}/spec.md)", d.type, d.status, esc(d.title)]
            if waiting:
                cells.append(", ".join(f"`{dep}` ({by_slug[dep].status})" for dep in unmet(d)))
            out.append("| " + " | ".join(cells) + " |")
        return [*out, ""]

    lines = [
        "# Task Board",
        "",
        "Generated by `python scripts/task_board.py` from the frontmatter of every dossier under",
        "`docs/tasks/` that is not `implemented`, `superseded` or `abandoned`. Do not edit it by",
        "hand: change the dossier's `status` or `depends_on` and regenerate. CI fails when this",
        "file does not match. The grouping rules are in `README.md`; the hand-written board kept",
        "until 2026-11-01 is frozen in `archive/board-history-to-2026-11.md`.",
        "",
        "## In progress",
        "",
        *table(in_progress, waiting=False),
        "## Ready now",
        "",
        *table(ready, waiting=False),
        "## Blocked",
        "",
        *table(blocked, waiting=True),
    ]
    return "\n".join(lines).rstrip("\n") + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="verify only; exit 1 on drift")
    args = parser.parse_args(argv)

    try:
        generated = render(load_dossiers())
    except FrontmatterError as exc:
        print(f"Gate FAILED: {exc}")
        return 1

    if args.check:
        committed = BOARD.read_text(encoding="utf-8") if BOARD.is_file() else ""
        if committed.replace("\r\n", "\n") != generated:
            print("Gate FAILED: docs/tasks/BOARD.md does not match the dossiers' frontmatter.")
            print("Run `python scripts/task_board.py` and commit the result.")
            return 1
        print("docs/tasks/BOARD.md matches the dossiers' frontmatter.")
        return 0

    BOARD.write_text(generated, encoding="utf-8", newline="\n")
    print("Wrote docs/tasks/BOARD.md.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
