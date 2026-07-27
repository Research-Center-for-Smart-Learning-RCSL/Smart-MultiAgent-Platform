"""Additive merge for free-form JSONB config columns.

Columns such as ``agents.wakeup_config`` and ``agents.workflow_capabilities`` are
free-form by design: a designer may write keys no model knows about, and a client
sends back only the keys it renders. Assigning the submitted payload to the column
therefore deletes everything the sender did not happen to model — which is how
designer-set ``soft_bounds`` (R15.08) were being erased by unrelated UI edits.

Merge semantics:

* a key absent from the patch keeps its stored value;
* dict-vs-dict recurses, so a partial nested object does not replace its siblings;
* any other value (scalar, list, type change) replaces wholesale — lists are values,
  not collections to merge;
* an explicit ``None`` **deletes** the key. This mirrors the "explicit null clears,
  omitted means no change" convention the agents endpoint already applies to its
  scalar fields, and it is the only way to remove a key under an additive write.

The stored dict is never mutated: callers hand in a value loaded from the ORM, and
the pre-merge state must stay intact for anything else holding the same object.
Nested containers the patch does not touch are shared by reference rather than
deep-copied, since the merged result is written straight to the database.
"""

from __future__ import annotations

from typing import Any


def merge_json_config(stored: dict[str, Any] | None, patch: dict[str, Any]) -> dict[str, Any]:
    """Return ``stored`` with ``patch`` applied additively. See module docstring."""
    merged: dict[str, Any] = dict(stored or {})
    for key, value in patch.items():
        if value is None:
            merged.pop(key, None)
            continue
        existing = merged.get(key)
        if isinstance(existing, dict) and isinstance(value, dict):
            merged[key] = merge_json_config(existing, value)
        else:
            merged[key] = value
    return merged


__all__ = ["merge_json_config"]
