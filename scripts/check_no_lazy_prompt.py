#!/usr/bin/env python3
"""Repo gate — the §9.2 lazy prompt mechanism stays removed (AC-10).

`prompt_strategy` / `load_prompt_section` / the lazy `SectionCache` were removed on
2026-07-16 and superseded by Agent Skills (§31); see
`docs/tasks/2026-07-16-agent-skills/`. This gate fails if any of it reappears.

Expressed in Python rather than the dossier's literal `rg` command so it runs on a
Windows dev box, where ripgrep is frequently absent — under a shell without `set -e`
a missing `rg` exits non-zero in a *pass-shaped* way, which is how a dead gate goes
unnoticed. Python is on every runner and every dev machine here.

Usage (from the repo root):  python scripts/check_no_lazy_prompt.py
"""

from __future__ import annotations

import pathlib
import re
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]

PATTERN = re.compile(
    r"prompt_strategy|load_prompt_section|SectionCache|lazy_prompt|insertLazyTemplate|promptStrategy",
    re.IGNORECASE,
)

SEARCH_ROOTS = ("backend", "frontend/src", "frontend/tests", "docs")

# Each exclusion is deliberate; see AC-10.
EXCLUSIONS: tuple[tuple[str, str], ...] = (
    ("backend/alembic/versions", "a landed migration is immutable history"),
    ("backend/openapi.json", "generated; cleared on regeneration"),
    ("docs/tasks", "task dossiers are a historical record, not live documentation"),
    # AC-10 lists the three above. This fourth is forced by AC-10's own second half:
    # it requires an explicit test for the two nested i18n keys this grep structurally
    # cannot see, and a test asserting a name is absent has to spell that name out.
    # Scoped to the single file whose entire purpose is to assert the removal.
    (
        "frontend/src/slices/agents/__tests__/promptStrategyRemoved.test.ts",
        "asserts the removal; naming the removed keys is its job",
    ),
)


def _excluded(rel: str) -> bool:
    return any(rel == path or rel.startswith(path + "/") for path, _ in EXCLUSIONS)


def _tracked_files() -> list[str]:
    """Tracked files under the search roots.

    `git ls-files` rather than a filesystem walk: the dossier's `rg` command honours
    .gitignore, so a walk would additionally scan build output and tool caches
    (`.import_linter_cache`, `dist/`, `node_modules/`) and fail on generated content
    that is not the codebase. Tracked-files-only reproduces rg's effective scope
    without a hand-maintained skip list drifting out of date.
    """
    out = subprocess.run(
        ["git", "ls-files", "-z", "--", *SEARCH_ROOTS],
        cwd=REPO,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return [p for p in out.split("\0") if p]


def main() -> int:
    hits: list[str] = []
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
            continue  # binary or unreadable — not source we can regress in
        scanned += 1
        for lineno, line in enumerate(text.splitlines(), start=1):
            if PATTERN.search(line):
                hits.append(f"{rel}:{lineno}: {line.strip()[:120]}")

    print(f"Scanned {scanned} files across {', '.join(SEARCH_ROOTS)}.")
    for path, why in EXCLUSIONS:
        print(f"  excluded {path}/ — {why}")

    if hits:
        print("\nGate FAILED — the §9.2 lazy prompt mechanism has reappeared:")
        for h in hits:
            print(f"  {h}")
        print(
            "\nSkills (§31) is the supported replacement for on-demand prompt "
            "retrieval. See docs/tasks/2026-07-16-agent-skills/."
        )
        return 1

    print("\nGate passed: no §9.2 lazy prompt residue.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
