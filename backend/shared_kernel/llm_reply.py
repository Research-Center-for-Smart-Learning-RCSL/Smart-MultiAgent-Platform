"""Reading a JSON object out of a model's text reply.

Models asked for "one JSON object and nothing else" still wrap it in a code
fence or a sentence often enough that a strict ``json.loads`` would fail runs a
lenient read recovers. Callers validate the shape themselves.
"""

from __future__ import annotations

import json
import re
from typing import Any, Final

_FENCE: Final = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.S)
_OBJECT_SPAN: Final = re.compile(r"\{[\s\S]*\}")


class ReplyNotJsonObject(ValueError):
    """The reply holds no parseable JSON object."""


def load_json_object(text: str) -> dict[str, Any]:
    """The JSON object in ``text``, unwrapping one code fence or surrounding prose."""
    body = text.strip()
    fenced = _FENCE.match(body)
    if fenced:
        body = fenced.group(1)
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        span = _OBJECT_SPAN.search(body)
        if span is None:
            raise ReplyNotJsonObject("reply is not JSON") from None
        try:
            data = json.loads(span.group(0))
        except json.JSONDecodeError:
            raise ReplyNotJsonObject("reply is not JSON") from None
    if not isinstance(data, dict):
        raise ReplyNotJsonObject("reply is not a JSON object")
    return data


__all__ = ["ReplyNotJsonObject", "load_json_object"]
