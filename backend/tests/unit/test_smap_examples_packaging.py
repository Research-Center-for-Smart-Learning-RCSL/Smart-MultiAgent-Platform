"""The course catalogue must ship as package data.

`packages.find` collects `.py` files only, so without an explicit
`package-data` entry a non-editable install produces a seeder CLI whose
catalogue directory is empty: it works from a source checkout and from the
container image (which copies the tree wholesale) and fails only from a wheel.
No other test in the suite can see that, because every one of them runs from
the repo root.

Building a wheel per test run is too slow for the unit tier, so this pins the
declaration instead. The end-to-end check -- build a wheel, load a course from
it outside the source tree -- is AC-6 of
docs/tasks/2026-08-08-activity-example-catalogue/spec.md.
"""

from __future__ import annotations

import fnmatch
import tomllib
from pathlib import Path

from smap.examples._catalogue import COURSES_DIRNAME, courses_root

PYPROJECT = Path(__file__).parents[2] / "pyproject.toml"


def _package_data_patterns() -> list[str]:
    config = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    package_data = config["tool"]["setuptools"].get("package-data", {})
    return list(package_data.get("smap.examples", []))


def test_the_catalogue_is_declared_as_package_data() -> None:
    assert (
        _package_data_patterns()
    ), "smap.examples declares no package-data; a wheel would ship an empty course catalogue"


def test_every_shipped_course_file_is_covered_by_a_pattern() -> None:
    """A course file the patterns miss is invisible from an installed artifact."""
    patterns = _package_data_patterns()

    uncovered = [
        entry.name
        for entry in courses_root().iterdir()
        if not any(fnmatch.fnmatch(f"{COURSES_DIRNAME}/{entry.name}", p) for p in patterns)
    ]

    assert uncovered == [], f"not matched by {patterns}: {uncovered}"
