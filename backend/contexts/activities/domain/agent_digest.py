"""Agent-visible submission digest (agent-visibility follow-up to Chapter §30).

A validator's ``ValidationResult.detail`` (already parsed out of MCP/webhook JSON
responses in ``validators/base.py``, and settable by an in-process scorer) is the
preferred human/agent-readable description of a submission. When no validator
supplies one, a compact, length-capped JSON dump of the raw payload stands in —
this covers a schema-form-driven activity type for free with no validator changes,
while giving a high-freedom plugin type (e.g. a canvas) an escape hatch to
describe itself in words instead of dumping raw coordinates.

In ``domain`` rather than ``application`` because both sides of the layer need it:
``submission_service`` builds the digest on write, and ``submission_repo``
reconstructs the fallback on read to answer whether a stored digest came from a
validator ``detail`` or from the payload. A pure, framework-free rendering rule
over a submission belongs here anyway; the alternative was an infrastructure
module importing an application one.
"""

from __future__ import annotations

import json
from typing import Any

_MAX_DIGEST_CHARS = 480
_TRUNCATION_SUFFIX = "…(truncated)"


def build_agent_digest(*, payload: dict[str, Any], detail: str | None) -> str:
    text = (
        detail if detail else json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    )
    if len(text) > _MAX_DIGEST_CHARS:
        text = text[:_MAX_DIGEST_CHARS] + _TRUNCATION_SUFFIX
    return text


__all__ = ["build_agent_digest"]
