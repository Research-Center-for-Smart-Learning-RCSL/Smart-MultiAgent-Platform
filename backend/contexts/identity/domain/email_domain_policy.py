"""Email-domain allow/deny policy — the domain model (R19a.13).

The policy is a versioned singleton: one mode, one allow list, one deny list, and
a rollout state that says which store is currently authoritative for it. The
rollout state lives on the same aggregate as the policy deliberately (Q-8a): a
reader has to know the phase and the policy together, and splitting them lets a
reader enforce one version's list under another version's authority.

Matching is **exact after normalisation**. A listed parent domain does not imply
its subdomains, which is why ``dept.example.edu`` must be listed separately from
``example.edu``. Widening that is a separate change with its own precedence and
public-suffix rules (the dossier's FU-2), not a quiet relaxation here.

``idna`` is the one third-party import in this layer. UTS-46 is a specification,
not a framework, and the stdlib ``"idna"`` codec implements the older IDNA2003
rules with per-label quirks that would make the *same* address resolve
differently here than in the validators around it.
"""

from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Final

import idna

from contexts.identity.domain.errors import InvalidEmailDomain

#: DNS caps a fully-qualified name at 253 octets. ``idna`` enforces this (and the
#: 63-octet label cap) during encoding; the constant exists so the API layer can
#: bound each string *before* it reaches normalisation.
MAX_DOMAIN_LENGTH: Final = 253
#: Bounds one request body and one row. Comfortably above any real institution
#: list while keeping a hostile PUT from writing an unbounded array.
MAX_DOMAINS_PER_LIST: Final = 1000

#: Characters that mean the caller pasted something other than a bare domain —
#: an address, a URL, a port, or a wildcard pattern this policy does not support.
_REJECTED_CHARS: Final = frozenset("@/\\:?#*% \t\r\n")


class EmailDomainPolicyMode(str, enum.Enum):
    """Which list governs.

    ``ALLOW`` admits only listed domains, so an empty allow list is a legal
    deny-all. ``DENY`` refuses only listed domains, so an empty deny list is a
    legal allow-all. ``OFF`` applies no restriction and may retain dormant lists
    an operator intends to re-enable.
    """

    ALLOW = "allow"
    DENY = "deny"
    OFF = "off"


class EmailDomainPolicyRolloutState(str, enum.Enum):
    """Which store a new reader must treat as authoritative.

    ``COMPATIBILITY`` — the legacy Redis triple still governs, because replicas
    that know only those three keys may still be serving. Admin writes are fenced.

    ``ACTIVE`` — PostgreSQL governs, with a disposable Redis mirror in front of
    it. The only phase in which an Admin may write.

    ``ROLLBACK_FROZEN`` — PostgreSQL governs and is frozen, while the legacy
    triple is rewritten beneath it so old images can be started. Writes are
    fenced so the verified mirror cannot go stale the moment it is taken.
    """

    COMPATIBILITY = "compatibility"
    ACTIVE = "active"
    ROLLBACK_FROZEN = "rollback_frozen"


@dataclass(frozen=True, slots=True)
class EmailDomainPolicy:
    """One immutable policy snapshot.

    The lists are ``frozenset`` rather than sequences because order carries no
    meaning and duplicate entries are a normalisation artefact, not data.
    """

    mode: EmailDomainPolicyMode
    allow: frozenset[str] = field(default_factory=frozenset)
    deny: frozenset[str] = field(default_factory=frozenset)
    version: int = 0
    rollout_state: EmailDomainPolicyRolloutState = EmailDomainPolicyRolloutState.COMPATIBILITY
    legacy_mirrored_version: int | None = None
    updated_at: datetime | None = None
    updated_by_user_id: uuid.UUID | None = None

    def admits(self, email: str) -> bool:
        """Whether this policy admits ``email``'s domain.

        An address with no parsable domain is refused in every mode, including
        ``OFF``: "no restriction on which domain" is not "no domain required".
        """
        domain = domain_of(email)
        if not domain:
            return False
        if self.mode is EmailDomainPolicyMode.DENY:
            return domain not in self.deny
        if self.mode is EmailDomainPolicyMode.ALLOW:
            return domain in self.allow
        return True


def domain_of(email: str) -> str:
    """The normalised domain of ``email``, or ``""`` when there is none.

    Returns empty rather than raising: this runs on attacker-supplied addresses
    on the registration path, where an unparsable address is a refusal, not a
    server error.
    """
    if "@" not in email:
        return ""
    _, _, raw = email.rpartition("@")
    try:
        return normalise_domain(raw)
    except InvalidEmailDomain:
        return ""


def normalise_domain(raw: str) -> str:
    """Trim, UTS-46-normalise and lower-case one policy entry.

    Raises :class:`InvalidEmailDomain` for anything that is not a bare domain.
    The rejections are deliberate rather than best-effort: a stored
    ``https://example.edu/`` or ``@example.edu`` can never match a domain
    extracted from an address, so accepting one would silently produce a policy
    that looks configured and admits everybody.
    """
    candidate = raw.strip()
    if not candidate:
        raise InvalidEmailDomain("domain is empty")
    # `idna` passes a trailing root dot straight through, and it is legal DNS —
    # but an address's domain part never carries one, so a stored entry with one
    # could never match anything. Rejected here because nothing below will.
    if candidate.startswith(".") or candidate.endswith("."):
        raise InvalidEmailDomain(f"domain has a leading or trailing dot: {candidate!r}")
    # Checked ahead of `idna` only for the message: it reports a codepoint
    # offset, and "you pasted a URL" is the actionable form of that.
    rejected = _REJECTED_CHARS.intersection(candidate)
    if rejected:
        raise InvalidEmailDomain(f"domain contains {sorted(rejected)!r}: {candidate!r}")
    # A bare label ("localhost", or a typo'd "example") cannot be an email
    # domain in any deployment this policy governs, and admitting one as an
    # allow entry would be a silently dead rule.
    if "." not in candidate:
        raise InvalidEmailDomain(f"domain has no dot: {candidate!r}")
    try:
        # uts46=True folds case and width and applies the mapping table; the
        # result is the A-label form, which is what an address's domain part
        # normalises to as well, so the two are comparable byte for byte. It also
        # owns the empty-label, hyphen, 63-octet-label and 253-octet-domain
        # rules, so those are not re-checked below.
        encoded = idna.encode(candidate, uts46=True, transitional=False).decode("ascii")
    except (idna.IDNAError, UnicodeError) as exc:
        raise InvalidEmailDomain(f"domain is not a valid IDN: {candidate!r} ({exc})") from exc
    return encoded.lower()


def normalise_domain_list(raws: list[str]) -> frozenset[str]:
    """Normalise and de-duplicate one list, bounded by :data:`MAX_DOMAINS_PER_LIST`.

    The count is checked on the *input*, before normalisation collapses
    duplicates — otherwise a caller could send an unbounded array of one repeated
    domain and have it pass.
    """
    if len(raws) > MAX_DOMAINS_PER_LIST:
        raise InvalidEmailDomain(f"more than {MAX_DOMAINS_PER_LIST} domains in one list")
    return frozenset(normalise_domain(raw) for raw in raws)


__all__ = [
    "MAX_DOMAINS_PER_LIST",
    "MAX_DOMAIN_LENGTH",
    "EmailDomainPolicy",
    "EmailDomainPolicyMode",
    "EmailDomainPolicyRolloutState",
    "domain_of",
    "normalise_domain",
    "normalise_domain_list",
]
