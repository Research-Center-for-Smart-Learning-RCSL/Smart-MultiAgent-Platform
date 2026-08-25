"""The consent fraction an activity type declares, and what it requires ([R30.40]).

Two pure rules the platform enforces without choosing: a type says what fraction
of a group must approve, and this module says what that means for a group of a
given size. The platform does not hard-code unanimity -- `1/1` expresses it and
is one value among many.

THE FRACTION IS TWO INTEGERS, NOT A FLOAT. `ceil(2 * 3 / 3)` is a fact;
`ceil(0.667 * 3)` is an argument about how many digits of 2/3 were stored. Every
required-approval count this module produces is exact integer arithmetic over the
numerator and denominator as authored.
"""

from __future__ import annotations

import math
from typing import Any

#: Upper bound on the denominator. A fraction finer than hundredths cannot change
#: the required count for any group a room can hold, so the only thing a larger
#: denominator adds is a number nobody can reason about in a settings form.
MAX_DENOMINATOR = 100

_CONSENT_KEY = "consent"
_NUMERATOR_KEY = "numerator"
_DENOMINATOR_KEY = "denominator"


def required_approvals(*, numerator: int, denominator: int, group_size: int) -> int:
    """How many approvals a pinned group of ``group_size`` must produce.

    ``ceil(numerator * group_size / denominator)``, clamped into ``1..group_size``.

    The clamp is not defensive tidying, it is the rule. The floor of 1 stops a
    tiny fraction over a small group from requiring zero approvals, which would
    make a proposal accept itself. The ceiling of ``group_size`` stops a fraction
    of exactly 1 from ever needing more people than the group has -- and it is
    what makes ``1/1`` mean "everyone" rather than "everyone, and one more" for
    any group whose size the fraction does not divide.

    A ``group_size`` of zero has no answer and never reaches here: a proposal
    pins a non-empty voter set (its proposer is always in it).
    """
    if group_size <= 0:  # pragma: no cover -- the caller pins a non-empty set
        raise ValueError("group_size must be positive")
    raw = math.ceil(numerator * group_size / denominator)
    return max(1, min(group_size, raw))


def is_unreachable(*, approvals: int, undecided: int, required: int) -> bool:
    """Whether the threshold can no longer be reached (Q-4).

    A proposal fails when the votes still outstanding cannot carry it, NOT on the
    first rejection. With a 2/3 threshold those are different events, and treating
    one dissent as fatal would silently implement unanimity -- making the
    configured fraction mean something other than what it says. Under `1/1` they
    coincide, which is correct: there, one rejection really does end it.
    """
    return approvals + undecided < required


def parse_group_config(raw: Any) -> tuple[int, int] | None:
    """The ``(numerator, denominator)`` a stored ``group_config`` declares.

    ``None`` for a type that is individual-only, which is what ``NULL`` means.
    Raises :class:`ValueError` on anything else, so a malformed row is a loud
    failure at the point of use rather than a silent fallback to some default
    threshold -- there is no safe default for a consent rule.
    """
    if raw is None:
        return None
    validate_group_config(raw)
    consent = raw[_CONSENT_KEY]
    return int(consent[_NUMERATOR_KEY]), int(consent[_DENOMINATOR_KEY])


def _require_int(value: Any, label: str) -> int:
    """One consent field as an ``int``, or a ``ValueError`` naming it.

    ``bool`` is excluded explicitly: it is an ``int`` in Python, and
    ``{"numerator": true}`` would otherwise register as the fraction 1/N.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"group_config.consent.{label} must be an integer")
    return value


def validate_group_config(raw: Any) -> None:
    """Refuse a ``group_config`` that is not a usable consent fraction.

    Checked at registration and at edit ([R30.02], [R30.23]) alongside the schema
    and validator checks.
    """
    if not isinstance(raw, dict):
        raise ValueError("group_config must be an object")
    consent = raw.get(_CONSENT_KEY)
    if not isinstance(consent, dict):
        raise ValueError("group_config.consent must be an object")
    unknown = sorted(set(raw) - {_CONSENT_KEY})
    if unknown:
        raise ValueError(f"group_config has unknown keys: {', '.join(unknown)}")
    unknown_consent = sorted(set(consent) - {_NUMERATOR_KEY, _DENOMINATOR_KEY})
    if unknown_consent:
        raise ValueError(f"group_config.consent has unknown keys: {', '.join(unknown_consent)}")

    numerator = _require_int(consent.get(_NUMERATOR_KEY), _NUMERATOR_KEY)
    denominator = _require_int(consent.get(_DENOMINATOR_KEY), _DENOMINATOR_KEY)
    if numerator < 1:
        raise ValueError("group_config.consent.numerator must be at least 1")
    if numerator > denominator:
        raise ValueError("group_config.consent.numerator must not exceed the denominator")
    if denominator > MAX_DENOMINATOR:
        raise ValueError(f"group_config.consent.denominator must not exceed {MAX_DENOMINATOR}")


__all__ = [
    "MAX_DENOMINATOR",
    "is_unreachable",
    "parse_group_config",
    "required_approvals",
    "validate_group_config",
]
