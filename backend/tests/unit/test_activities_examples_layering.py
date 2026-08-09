"""AC-11: the catalogue is readable from HTTP without crossing a layer boundary.

Two directions, both static scans, because neither is enforced by a tool that runs
in CI: ``import-linter``'s ``root_packages`` is ``["app", "contexts",
"shared_kernel"]``, so it structurally cannot see an ``app -> smap`` edge, and its
two contracts only concern domain purity. Ruff selects ``TID`` but declares no
``banned-api`` section. Until then the rule lives in ``backend/CLAUDE.md`` and in
``smap/__init__.py``'s docstring -- prose, which is what this file replaces.

The reason it matters now: promoting the example catalogue into the context is
what lets an admin install a course over HTTP. Doing it by importing ``smap`` from
a route would have "worked", and would have made a CLI package -- whose own
docstring says nothing in it is served over HTTP -- part of the request path.
"""

from __future__ import annotations

import ast
import pathlib

_BACKEND = pathlib.Path(__file__).resolve().parents[2]
_APP = _BACKEND / "app"
_ACTIVITIES = _BACKEND / "contexts" / "activities"


def _imported_modules(py: pathlib.Path) -> list[str]:
    """Every dotted module name the file imports, from either import form."""
    tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            # `level > 0` is a relative import, which cannot leave the package.
            if node.level == 0 and node.module:
                names.append(node.module)
        elif isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
    return names


def _offenders(root: pathlib.Path, forbidden_prefix: str) -> list[str]:
    found: list[str] = []
    for py in root.rglob("*.py"):
        for module in _imported_modules(py):
            if module == forbidden_prefix or module.startswith(f"{forbidden_prefix}."):
                found.append(f"{py.relative_to(_BACKEND)}: {module}")
    return found


def test_app_does_not_import_the_smap_cli_package() -> None:
    """The catalogue must reach the API through the context, not through the CLI.

    ``smap/__init__.py``: "Nothing in this namespace is served over HTTP." The
    seeder CLI legitimately imports *downward* into app/contexts; this asserts the
    edge does not run the other way.
    """
    offenders = _offenders(_APP, "smap")

    assert not offenders, f"app/ must not import the smap CLI package: {offenders}"


def test_activities_does_not_import_app() -> None:
    """A ``contexts -> app`` inversion, which the catalogue used to carry.

    Before the move, ``_catalogue.py`` imported ``validate_filled_count_config``
    from ``app.plugins.activity_validators``. Inside the ``smap`` package that was
    merely unusual; inside ``contexts/`` it inverts the layer direction, so the
    rule the validator expresses now travels through the registry's
    ``schema_config_validator`` hook instead.
    """
    offenders = _offenders(_ACTIVITIES, "app")

    assert not offenders, f"contexts/activities must not import app: {offenders}"
