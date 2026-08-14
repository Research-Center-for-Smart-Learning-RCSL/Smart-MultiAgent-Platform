"""The shipped catalogues must ship as package data.

`packages.find` collects `.py` files only, so without an explicit
`package-data` entry a non-editable install produces an empty course catalogue:
it works from a source checkout and from the container image (which copies the
tree wholesale) and fails only from a wheel. No other test in the suite can see
that, because every one of them runs from the repo root.

Building a wheel per test run is too slow for the unit tier, so this pins the
declaration instead. The end-to-end check -- build a wheel, load a course from
it outside the source tree -- is AC-6 of
docs/tasks/2026-08-08-activity-example-catalogue/spec.md and AC-13 of
docs/tasks/2026-08-09-platform-example-activity-types/spec.md.

The key is asserted against the catalogue module's own package rather than
hard-coded, so moving the catalogue again cannot leave a stale declaration
passing this test while shipping nothing.
"""

from __future__ import annotations

import fnmatch
import tomllib
from importlib.resources.abc import Traversable
from pathlib import Path

import pytest

from contexts.activities.infrastructure.examples import catalogue as course_catalogue
from contexts.activities.infrastructure.examples.catalogue import COURSES_DIRNAME, courses_root
from contexts.agents.infrastructure.examples import catalogue as pack_catalogue
from contexts.agents.infrastructure.examples.catalogue import PACKS_DIRNAME, packs_root

PYPROJECT = Path(__file__).parents[2] / "pyproject.toml"

# (package, directory name, directory) per shipped catalogue. Both are covered by
# the same rules because both fail the same way: silently, and only from a wheel.
CATALOGUES: list[tuple[str, str, Traversable]] = [
    (str(course_catalogue.__package__), COURSES_DIRNAME, courses_root()),
    (str(pack_catalogue.__package__), PACKS_DIRNAME, packs_root()),
]
CATALOGUE_IDS = [dirname for _, dirname, _ in CATALOGUES]


def _package_data_patterns(package: str) -> list[str]:
    config = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    package_data = config["tool"]["setuptools"].get("package-data", {})
    return list(package_data.get(package, []))


@pytest.mark.parametrize(("package", "dirname", "root"), CATALOGUES, ids=CATALOGUE_IDS)
def test_the_catalogue_is_declared_as_package_data(package: str, dirname: str, root: Traversable) -> None:
    assert _package_data_patterns(package), (
        f"{package} declares no package-data; a wheel would ship an empty {dirname} catalogue"
    )


@pytest.mark.parametrize(("package", "dirname", "root"), CATALOGUES, ids=CATALOGUE_IDS)
def test_every_shipped_file_is_covered_by_a_pattern(package: str, dirname: str, root: Traversable) -> None:
    """A file the patterns miss is invisible from an installed artifact."""
    patterns = _package_data_patterns(package)

    uncovered = [
        entry.name
        for entry in root.iterdir()
        if not any(fnmatch.fnmatch(f"{dirname}/{entry.name}", p) for p in patterns)
    ]

    assert uncovered == [], f"not matched by {patterns}: {uncovered}"
