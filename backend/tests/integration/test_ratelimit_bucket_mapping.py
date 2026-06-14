"""Path/method → rate-limit bucket mapping (R19.02 bucket assignment).

Actual Redis-backed sliding window is exercised by a separate stack-up E2E;
this test covers the pure routing layer so a renamed endpoint or reshuffled
prefix does not silently land requests in the wrong bucket.
"""

from __future__ import annotations

import pytest

from app.api.middleware.rate_limit import _bucket_for
from shared_kernel.auth.ratelimit import Bucket, Scope, default_policies


@pytest.mark.parametrize(
    ("method", "path", "expected"),
    [
        # Auth bucket — IP-scoped, 10/min.
        ("POST", "/api/auth/login", Bucket.AUTH),
        ("POST", "/api/auth/register", Bucket.AUTH),
        ("POST", "/api/auth/refresh", Bucket.AUTH),
        # Account-recovery flows get their own IP bucket (API-9) so reset-email
        # flooding cannot starve the login bucket.
        ("POST", "/api/auth/request-password-reset", Bucket.AUTH_RECOVERY),
        ("POST", "/api/auth/reset-password", Bucket.AUTH_RECOVERY),
        ("POST", "/api/auth/verify-email", Bucket.AUTH_RECOVERY),
        ("GET", "/api/auth/verify-email", Bucket.AUTH_RECOVERY),
        # Chat-send — 60/min/user.
        ("POST", "/api/chatrooms/abc/messages", Bucket.CHAT),
        # Upload — 10/min/user for tus Creation + attachment POSTs.
        ("POST", "/api/tus", Bucket.UPLOAD),
        ("POST", "/api/chatrooms/abc/attachments", Bucket.UPLOAD),
        ("POST", "/api/rag/documents", Bucket.UPLOAD),
        # Other default — everything that isn't one of the above.
        ("GET", "/api/orgs", Bucket.OTHER),
        ("POST", "/api/orgs", Bucket.OTHER),
        ("GET", "/api/projects/123", Bucket.OTHER),
        ("PATCH", "/api/invites/xyz/accept", Bucket.OTHER),
        # tus PATCH (chunk uploads) must NOT land in the UPLOAD bucket — F.5
        # carves them out to 300/min/user.
        ("PATCH", "/api/tus/abc", Bucket.OTHER),
    ],
)
def test_bucket_for_path(method: str, path: str, expected: Bucket) -> None:
    assert _bucket_for(path, method) is expected


def test_default_bucket_budgets_match_spec() -> None:
    policies = default_policies()
    assert policies[Bucket.AUTH].max_count == 10
    assert policies[Bucket.AUTH].window_sec == 60
    assert policies[Bucket.AUTH].scope is Scope.IP
    assert policies[Bucket.AUTH_RECOVERY].max_count == 10
    assert policies[Bucket.AUTH_RECOVERY].scope is Scope.IP
    assert policies[Bucket.CHAT].max_count == 60
    assert policies[Bucket.CHAT].scope is Scope.USER
    assert policies[Bucket.UPLOAD].max_count == 10
    assert policies[Bucket.UPLOAD].scope is Scope.USER
    assert policies[Bucket.OTHER].max_count == 300
    assert policies[Bucket.OTHER].scope is Scope.USER


def test_every_bucket_has_a_default_policy() -> None:
    # prime_policies() seeds + mirrors one row per Bucket keyed by Bucket.value,
    # and the limiter reads config:ratelimit:{Bucket.value}. A Bucket without a
    # default would be seeded inconsistently / silently skipped — pin the 1:1.
    assert {b.value for b in Bucket} == {b.value for b in default_policies()}
