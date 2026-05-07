"""Dump the FastAPI OpenAPI document to stdout.

Used by the frontend codegen pipeline (`make openapi-types` and the
`check:openapi-drift` CI gate). Writes a compact-but-deterministic JSON so
diffs stay reviewable.
"""

from __future__ import annotations

import json
import sys

from app.main import create_app


def main() -> None:
    app = create_app()
    spec = app.openapi()
    json.dump(spec, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
