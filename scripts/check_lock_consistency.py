#!/usr/bin/env python3
"""Repo gate — backend/requirements.lock must satisfy backend/pyproject.toml.

The runtime image installs from `requirements.lock` and nothing else
(`backend/Dockerfile:19`), while `pyproject.toml` is what humans read and what
Dependabot edits. Nothing tied the two together, so a dependency bump could go
fully green while changing nothing that ships — which is exactly what happened
on the weasyprint 68 -> 69 PR: it updated pyproject.toml and the since-removed
backend/uv.lock, both of which the image ignores, and left the lock pinning
68.1.

That failure mode is silent in both directions. A pin raised for a CVE looks
applied while the vulnerable version keeps shipping.

This gate is a *satisfaction* check, not a re-resolve. Re-resolving would need
the same platform and package index as whoever generated the lock, and would
drag every transitive dependency forward as a side effect. Comparing declared
pins against locked versions needs neither, so it runs identically on a laptop
and on CI.

Transitive dependencies are deliberately out of scope: they are not declared in
pyproject.toml, so there is nothing to compare them against here.

Specifiers are evaluated with `packaging`, the same implementation pip uses.
An earlier version decided only `==` forms and passed every range silently;
once Dependabot had rewritten most pins as ranges, that covered 24 of 38
runtime dependencies, including a pyjwt CVE floor.

Usage (from the repo root):  python scripts/check_lock_consistency.py
Requires `packaging` (installed by the repo-gates CI job).
"""

from __future__ import annotations

import pathlib
import re
import sys
import tomllib

from packaging.requirements import InvalidRequirement, Requirement

REPO = pathlib.Path(__file__).resolve().parents[1]
PYPROJECT = REPO / "backend" / "pyproject.toml"
LOCK = REPO / "backend" / "requirements.lock"

# utf-8-sig strips a leading BOM; both files are read that way so a
# BOM-prefixed first entry cannot silently read as "declared but not locked".
# Lock lines are exact pins: "name==1.2.3" or "name==1.2.3 ; marker".
LOCK_PIN = re.compile(r"^(?P<name>[A-Za-z0-9._-]+)==(?P<version>[^\s;]+)")


def normalize(name: str) -> str:
    """PEP 503 name normalization — `types-bleach` and `types_bleach` are one package."""
    return re.sub(r"[-_.]+", "-", name).lower()


def parse_lock() -> dict[str, str]:
    pins: dict[str, str] = {}
    for line in LOCK.read_text(encoding="utf-8-sig").splitlines():
        if not line or line.startswith(("#", " ", "\t")):
            continue
        m = LOCK_PIN.match(line)
        if m:
            pins[normalize(m["name"])] = m["version"]
    return pins


def main() -> int:
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8-sig"))
    project = data.get("project", {})

    # Only runtime dependencies: the lock is the *runtime* install list, so dev
    # and optional extras are legitimately absent from it.
    declared: list[str] = list(project.get("dependencies", []))

    pins = parse_lock()
    missing: list[str] = []
    mismatched: list[tuple[str, str, str]] = []
    unparsable: list[str] = []

    for raw in declared:
        try:
            req = Requirement(raw)
        except InvalidRequirement:
            unparsable.append(raw)
            continue
        # Environment markers are not evaluated; the lock is resolved for the
        # single runtime platform, so the specifier is compared regardless.
        name = normalize(req.name)
        spec = str(req.specifier)

        locked = pins.get(name)
        if locked is None:
            missing.append(f"{name} (declared {spec or 'unpinned'})")
        elif not req.specifier.contains(locked, prereleases=True):
            mismatched.append((name, spec, locked))

    if not missing and not mismatched and not unparsable:
        print(f"Lock consistency OK: {len(declared)} runtime dependencies satisfied.")
        return 0

    print("FAIL: backend/requirements.lock does not match backend/pyproject.toml.")
    print("The runtime image installs from the lock, so what ships is NOT what")
    print("pyproject declares. Update backend/requirements.lock to match.")
    print()
    for entry in missing:
        print(f"  missing from lock:  {entry}")
    for name, spec, locked in mismatched:
        print(f"  version mismatch:   {name} declared {spec}, locked at {locked}")
    for raw in unparsable:
        print(f"  unparsable:         {raw}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
