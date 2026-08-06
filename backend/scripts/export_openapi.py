"""Dump the FastAPI OpenAPI document to stdout.

Used by the frontend codegen pipeline (`make openapi-types` and the
`check:openapi-drift` CI gate). Writes a compact-but-deterministic JSON so
diffs stay reviewable.
"""

from __future__ import annotations

import json
import sys
from typing import Any

from app.main import create_app


def _mirror_const_to_enum(node: Any) -> None:
    """Restate every ``const`` as a single-member ``enum``.

    pydantic <=2.12 emitted both for a one-value ``Literal``; 2.13 emits only
    ``const``. openapi-typescript-codegen 0.31.0 (the current release) reads
    ``enum`` and ignores ``const``, so without this the generated client widens
    `TokenPairOut.token_type` from `'Bearer'` to `string`, and the slices that
    declare the literal by hand stop compiling.

    Both keywords carry the same constraint and JSON Schema allows them
    together, so this restores the pre-2.13 output rather than inventing one.
    Drop it once the generator understands ``const``.
    """
    if isinstance(node, dict):
        if "const" in node and "enum" not in node:
            node["enum"] = [node["const"]]
        for value in node.values():
            _mirror_const_to_enum(value)
    elif isinstance(node, list):
        for item in node:
            _mirror_const_to_enum(item)


def main() -> None:
    app = create_app()
    spec = app.openapi()
    _mirror_const_to_enum(spec)
    json.dump(spec, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
