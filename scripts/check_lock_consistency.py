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

Usage (from the repo root):  python scripts/check_lock_consistency.py
"""

from __future__ import annotations

import pathlib
import re
import sys
import tomllib

REPO = pathlib.Path(__file__).resolve().parents[1]
PYPROJECT = REPO / "backend" / "pyproject.toml"
LOCK = REPO / "backend" / "requirements.lock"

# "name==1.2.*", "name>=1.2,<2", "name[extra]==1.2.3" — capture name and specifier.
REQ = re.compile(r"^\s*(?P<name>[A-Za-z0-9._-]+)\s*(?:\[[^\]]*\])?\s*(?P<spec>.*)$")
# utf-8-sig above strips a leading BOM; both files are read that way so a
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


def satisfies(version: str, spec: str) -> bool:
    """True if *version* meets *spec*, for the pin styles this repo actually uses.

    Only `==` forms are decided here — `==1.2.*` and `==1.2.3`. Range specs
    (`>=1.2,<2`) are reported as unchecked rather than guessed at, because
    getting them subtly wrong would make this gate lie. The repo pins exactly
    by convention, so ranges are the rare case.
    """
    spec = spec.strip()
    if not spec.startswith("=="):
        return True
    want = spec[2:].strip()
    if want.endswith(".*"):
        prefix = want[:-2]
        return version == prefix or version.startswith(prefix + ".")
    return version == want


def main() -> int:
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8-sig"))
    project = data.get("project", {})

    # Only runtime dependencies: the lock is the *runtime* install list, so dev
    # and optional extras are legitimately absent from it.
    declared: list[str] = list(project.get("dependencies", []))

    pins = parse_lock()
    missing: list[str] = []
    mismatched: list[tuple[str, str, str]] = []

    for raw in declared:
        # Environment markers ("pkg==1.0 ; sys_platform == 'win32'") are not
        # evaluated here; compare the requirement half only.
        req = raw.split(";", 1)[0].strip()
        m = REQ.match(req)
        if not m:
            continue
        name = normalize(m["name"])
        spec = m["spec"]

        locked = pins.get(name)
        if locked is None:
            missing.append(f"{name} (declared {spec or 'unpinned'})")
        elif not satisfies(locked, spec):
            mismatched.append((name, spec, locked))

    if not missing and not mismatched:
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
    return 1


if __name__ == "__main__":
    sys.exit(main())
