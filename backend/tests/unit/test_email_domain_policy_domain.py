"""Email-domain policy — the domain layer (R19a.13).

Covers `docs/tasks/2026-08-30-identity-onboarding-policy-hardening`:

* AC-2's normalisation half — what a policy entry may and may not be;
* AC-5's matching contract — the three modes, and exact-domain matching with no
  implied subdomains.
"""

from __future__ import annotations

import pytest

from contexts.identity.domain.email_domain_policy import (
    MAX_DOMAINS_PER_LIST,
    EmailDomainPolicy,
    EmailDomainPolicyMode,
    EmailDomainPolicyRolloutState,
    domain_of,
    normalise_domain,
    normalise_domain_list,
)
from contexts.identity.domain.errors import InvalidEmailDomain

# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("example.edu", "example.edu"),
        ("  example.edu  ", "example.edu"),
        ("EXAMPLE.EDU", "example.edu"),
        ("Dept.Example.Edu", "dept.example.edu"),
        # UTS-46 width folding: the full-width form of an ASCII domain must land
        # on the same entry, or a policy could be bypassed by typing it wide.
        ("ｅｘａｍｐｌｅ.edu", "example.edu"),
        # A U-label and its A-label are the same domain and must not become two
        # separate list entries.
        ("例子.测试", "xn--fsqu00a.xn--0zwm56d"),
        ("xn--fsqu00a.xn--0zwm56d", "xn--fsqu00a.xn--0zwm56d"),
    ],
)
def test_a_bare_domain_normalises_to_its_lower_cased_a_label(raw: str, expected: str) -> None:
    assert normalise_domain(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        # Everything that is not a bare domain. A stored entry of any of these
        # shapes can never match a domain extracted from an address, so it would
        # be a policy that looks configured and admits everybody.
        "user@example.edu",
        "https://example.edu",
        "example.edu/path",
        "example.edu:25",
        "example.edu?q=1",
        "example.edu#frag",
        "*.example.edu",
        "exam ple.edu",
        # Wildcard/suffix matching is a non-goal, so a leading dot is not a
        # shorthand for "and its subdomains" — it is a rejected entry.
        ".example.edu",
        "example.edu.",
        "a..b.edu",
        "-bad.edu",
        "bad-.edu",
        # A bare label has no deployment in which it is an email domain, and an
        # allow entry that can never match is a silently dead rule.
        "localhost",
        "a" * 64 + ".example.edu",
        ".".join(["a" * 60] * 5) + ".edu",
    ],
)
def test_anything_that_is_not_a_bare_domain_is_rejected(raw: str) -> None:
    with pytest.raises(InvalidEmailDomain):
        normalise_domain(raw)


def test_a_list_is_normalised_and_de_duplicated() -> None:
    """Two spellings of one domain are one entry, not two."""
    assert normalise_domain_list(["Example.EDU", "example.edu", " example.edu "]) == frozenset(
        {"example.edu"}
    )


def test_a_list_is_bounded_before_de_duplication_collapses_it() -> None:
    """Counting after de-duplication would let an unbounded array of one repeated
    domain through — the array is what the request body costs, not the set."""
    with pytest.raises(InvalidEmailDomain):
        normalise_domain_list(["example.edu"] * (MAX_DOMAINS_PER_LIST + 1))


def test_one_invalid_entry_rejects_the_whole_list() -> None:
    """Partial acceptance would silently produce a policy narrower or wider than
    the one the Admin submitted."""
    with pytest.raises(InvalidEmailDomain):
        normalise_domain_list(["example.edu", "user@elsewhere.test"])


# ---------------------------------------------------------------------------
# Address -> domain
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("email", "expected"),
    [
        ("user@example.edu", "example.edu"),
        ("USER@EXAMPLE.EDU", "example.edu"),
        # The last `@` wins: a quoted local part may legally contain one.
        ('"a@b"@example.edu', "example.edu"),
        ("user@ｅｘａｍｐｌｅ.edu", "example.edu"),
        ("no-at-sign", ""),
        ("user@", ""),
        ("user@localhost", ""),
        ("user@exam ple.edu", ""),
    ],
)
def test_domain_of_normalises_and_returns_empty_rather_than_raising(email: str, expected: str) -> None:
    """This runs on attacker-supplied addresses at registration, where an
    unparsable address is a refusal rather than a server error."""
    assert domain_of(email) == expected


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------


def _policy(
    mode: EmailDomainPolicyMode,
    *,
    allow: set[str] | None = None,
    deny: set[str] | None = None,
) -> EmailDomainPolicy:
    return EmailDomainPolicy(
        mode=mode,
        allow=frozenset(allow or ()),
        deny=frozenset(deny or ()),
        version=1,
        rollout_state=EmailDomainPolicyRolloutState.ACTIVE,
    )


def test_allow_mode_admits_only_listed_domains() -> None:
    policy = _policy(EmailDomainPolicyMode.ALLOW, allow={"example.edu"})
    assert policy.admits("user@example.edu")
    assert not policy.admits("user@elsewhere.test")


def test_deny_mode_refuses_only_listed_domains() -> None:
    policy = _policy(EmailDomainPolicyMode.DENY, deny={"disposable.test"})
    assert not policy.admits("user@disposable.test")
    assert policy.admits("user@example.edu")


def test_off_mode_applies_no_restriction_even_with_dormant_lists() -> None:
    """`off` may retain the lists an operator intends to re-enable, and must not
    enforce them while it does."""
    policy = _policy(EmailDomainPolicyMode.OFF, allow={"example.edu"}, deny={"example.edu"})
    assert policy.admits("user@anything.test")


def test_an_empty_allow_list_is_deny_all_and_an_empty_deny_list_is_allow_all() -> None:
    """Redis cannot tell a missing empty set from an intentional one; the mode
    decides what an empty list means, and both meanings are legal (Q-10)."""
    assert not _policy(EmailDomainPolicyMode.ALLOW).admits("user@example.edu")
    assert _policy(EmailDomainPolicyMode.DENY).admits("user@example.edu")


@pytest.mark.parametrize("mode", list(EmailDomainPolicyMode))
def test_an_address_with_no_parsable_domain_is_refused_in_every_mode(
    mode: EmailDomainPolicyMode,
) -> None:
    """ "No restriction on which domain" is not "no domain required"."""
    assert not _policy(mode).admits("not-an-address")


def test_a_listed_parent_does_not_admit_its_subdomains() -> None:
    """Exact matching after normalisation. Widening this needs explicit
    precedence and public-suffix semantics, which is a separate change."""
    policy = _policy(EmailDomainPolicyMode.ALLOW, allow={"example.edu"})
    assert not policy.admits("user@dept.example.edu")

    denied = _policy(EmailDomainPolicyMode.DENY, deny={"example.edu"})
    assert denied.admits("user@dept.example.edu")
