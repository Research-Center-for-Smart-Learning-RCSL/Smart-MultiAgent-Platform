"""D.7 — router policy pure functions (classify_http, backoff_delay_ms)."""

from __future__ import annotations

import random

from contexts.keys.application.router_policy import (
    RotationReason,
    backoff_delay_ms,
    classify_http,
)
from contexts.keys.domain.groups import RotationPolicy


def test_classify_2xx_is_ok() -> None:
    policy = RotationPolicy()
    out = classify_http(200, policy)
    assert out.reason is RotationReason.OK


def test_classify_401_is_fatal_regardless_of_rotate_codes() -> None:
    # Even if someone put 401 into rotate_on_error_codes, unauthorized is fatal.
    policy = RotationPolicy(rotate_on_error_codes=(401,))
    out = classify_http(401, policy)
    assert out.reason is RotationReason.FATAL
    assert out.error_code == "provider_unauthorized"


def test_classify_429_retries_when_retry_on_error() -> None:
    policy = RotationPolicy(retry_on_error=True)
    out = classify_http(429, policy)
    assert out.reason is RotationReason.RETRY


def test_classify_429_rotates_when_retry_off() -> None:
    policy = RotationPolicy(retry_on_error=False)
    out = classify_http(429, policy)
    assert out.reason is RotationReason.ROTATE


def test_classify_unknown_status_rotates() -> None:
    # 418 is not in default rotate_on_error_codes but is non-2xx non-auth.
    policy = RotationPolicy()
    out = classify_http(418, policy)
    assert out.reason is RotationReason.ROTATE


def test_classify_request_rejections_abort_group() -> None:
    # 400/404/422 mean the request itself is bad — identical on every key, so
    # the whole group aborts rather than rotating and burning siblings.
    policy = RotationPolicy()
    for status in (400, 404, 422):
        out = classify_http(status, policy)
        assert out.reason is RotationReason.ABORT, status
        assert out.error_code == f"http_{status}"


def test_classify_400_honours_explicit_rotate_override() -> None:
    # An operator who lists 400 in rotate_on_error_codes wins over the ABORT
    # default (some self-hosted gateways return 400 for transient conditions).
    policy = RotationPolicy(rotate_on_error_codes=(400,), retry_on_error=False)
    assert classify_http(400, policy).reason is RotationReason.ROTATE


def test_backoff_grows_and_caps() -> None:
    # With zero jitter, attempt N gives exactly initial * multiplier^(N-1),
    # capped at max.
    policy = RotationPolicy(
        retry_initial_delay_ms=500,
        retry_multiplier=2,  # type: ignore[arg-type]  # Decimal(2) accepted via dataclass
        retry_max_delay_ms=3000,
        retry_jitter_pct=0,
    )
    assert backoff_delay_ms(1, policy) == 500
    assert backoff_delay_ms(2, policy) == 1000
    assert backoff_delay_ms(3, policy) == 2000
    # Next step would be 4000, capped at 3000.
    assert backoff_delay_ms(4, policy) == 3000


def test_backoff_jitter_stays_within_pct_band() -> None:
    policy = RotationPolicy(
        retry_initial_delay_ms=1000,
        retry_multiplier=1,  # type: ignore[arg-type]
        retry_max_delay_ms=1000,
        retry_jitter_pct=20,
    )
    rng = random.Random(0)
    samples = [backoff_delay_ms(1, policy, rng=rng) for _ in range(200)]
    # All samples must sit inside 1000 ± 20%.
    assert min(samples) >= 800
    assert max(samples) <= 1200
