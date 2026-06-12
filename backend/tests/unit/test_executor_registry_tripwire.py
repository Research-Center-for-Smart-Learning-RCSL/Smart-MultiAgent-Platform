"""K.7 deliverable 3 — executor-registry orphan tripwire.

Generic guard against the ``ff19610``-class regression: an automated lint pass
(or a human) deleting a side-effect ``import`` from an executor ``registry.py``
while the executor module still exists on disk. ``ff19610`` did exactly that —
it stripped 10 of 11 imports and left only ``trigger`` registered, and no test
failed because every executor test imported the module under test directly.

``tests/unit/test_workflow_k4.py::test_executor_completeness`` pins it for the
*workflow* ``NodeType`` enum. This test is the enum-agnostic form: any module
under any ``contexts/*/application/executors/`` package that self-registers
(``@register(``) MUST be imported by its sibling ``registry.py``. Deleting such
an import makes this test — and therefore CI — fail. Pure filesystem inspection,
no infra, so it runs in the fast unit job.
"""

from __future__ import annotations

import pathlib
import re

# tests/unit/<this>.py → parents[2] == backend root.
_BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[2]


def _executor_packages() -> list[pathlib.Path]:
    """Every ``contexts/<ctx>/application/executors`` package directory."""
    return sorted((_BACKEND_ROOT / "contexts").glob("*/application/executors"))


def test_executor_packages_discovered() -> None:
    # If this fails the glob is wrong (e.g. the test was relocated), which would
    # silently turn the real assertion below into a vacuous pass.
    assert _executor_packages(), "no contexts/*/application/executors packages found"


def test_every_self_registering_module_is_imported_by_its_registry() -> None:
    offenders: list[str] = []
    checked = 0
    for pkg in _executor_packages():
        registry = pkg / "registry.py"
        assert registry.exists(), f"{pkg.relative_to(_BACKEND_ROOT)} has no registry.py"
        registry_src = registry.read_text(encoding="utf-8")
        for module in sorted(pkg.glob("*.py")):
            if module.name in {"__init__.py", "registry.py"}:
                continue
            # Only modules that actually register an executor are required to be
            # imported by the registry; a pure helper/base module is exempt.
            if "@register(" not in module.read_text(encoding="utf-8"):
                continue
            checked += 1
            # Word-boundary match so e.g. an executor named ``end`` is not
            # satisfied by an unrelated ``import endpoint`` line.
            if not re.search(rf"\bimport\s+{re.escape(module.stem)}\b", registry_src):
                offenders.append(f"{pkg.parent.parent.name}/{module.stem}")

    assert checked, "no self-registering executor modules found — guard is vacuous"
    assert not offenders, (
        "executor module(s) exist on disk but are NOT imported by their "
        f"registry.py (ff19610-class regression): {sorted(offenders)}"
    )
