"""K.7 deliverable 2 — make the integration marker real.

The audit found the marker filter was a no-op: ``-m "not integration"`` excluded
nothing because no test under ``tests/integration/`` carried the marker — the
"integration" suite was a directory convention only, so the fast CI job silently
ran the whole tree and the filter protected nothing.

Rather than annotate each file by hand (and re-annotate every new one), this
conftest applies ``@pytest.mark.integration`` to every test physically located
under ``tests/integration/`` at collection time. After this, ``-m "not
integration"`` genuinely excludes this directory and the dedicated
``backend-integration`` CI job (``-m integration``) owns running it.
"""

from __future__ import annotations

import pathlib

import pytest

_INTEGRATION_DIR = pathlib.Path(__file__).parent.resolve()


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    for item in items:
        path = getattr(item, "path", None)
        if path is None:
            continue
        if _INTEGRATION_DIR in pathlib.Path(path).resolve().parents:
            item.add_marker(pytest.mark.integration)
