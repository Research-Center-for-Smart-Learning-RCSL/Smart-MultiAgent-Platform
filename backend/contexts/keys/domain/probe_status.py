"""Probe status domain enum.

The status of a key-validity probe is a domain concept (it lives on
`ApiKey` / `SearchKey`); only the HTTP transport that produces a probe
result lives in infrastructure. Keeping the enum here lets domain models
reference it without crossing the layer boundary.
"""

from __future__ import annotations

import enum


class ProbeStatus(str, enum.Enum):
    OK = "ok"
    FAILED = "failed"
    UNTESTED = "untested"


__all__ = ["ProbeStatus"]
