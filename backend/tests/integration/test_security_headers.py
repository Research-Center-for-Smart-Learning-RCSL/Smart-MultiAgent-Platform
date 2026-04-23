"""§19a.2 header surface on JSON responses + CSP report-only toggle.

Mounts only `SecurityHeadersMiddleware` on a throwaway FastAPI app so the
test is independent of DB/Redis and of the broader middleware stack.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.middleware.security_headers import SecurityHeadersMiddleware
from app.config.settings import get_settings


def _fresh_client() -> TestClient:
    get_settings.cache_clear()  # type: ignore[attr-defined]
    app = FastAPI()
    app.add_middleware(SecurityHeadersMiddleware)

    @app.get("/ping")
    def _ping() -> dict[str, str]:
        return {"ok": "1"}

    return TestClient(app)


def test_default_csp_enforced(monkeypatch) -> None:
    monkeypatch.delenv("SMAP_CSP_REPORT_ONLY", raising=False)
    monkeypatch.delenv("SMAP_SEC_CSP_REPORT_ONLY", raising=False)
    with _fresh_client() as c:
        r = c.get("/ping")
    hdrs = {k.lower(): v for k, v in r.headers.items()}
    assert r.status_code == 200
    assert "content-security-policy" in hdrs
    assert "content-security-policy-report-only" not in hdrs
    csp = hdrs["content-security-policy"]
    # R19a.05 — img-src is intentionally broad (agents emit https: links).
    assert "img-src 'self' data: https:" in csp
    assert "frame-ancestors 'none'" in csp
    assert "object-src 'none'" in csp


def test_report_only_toggle(monkeypatch) -> None:
    monkeypatch.setenv("SMAP_CSP_REPORT_ONLY", "1")
    with _fresh_client() as c:
        r = c.get("/ping")
    hdrs = {k.lower() for k in r.headers}
    assert "content-security-policy-report-only" in hdrs
    assert "content-security-policy" not in hdrs


def test_full_r19a2_header_surface(monkeypatch) -> None:
    monkeypatch.delenv("SMAP_CSP_REPORT_ONLY", raising=False)
    with _fresh_client() as c:
        r = c.get("/ping")
    hdrs = {k.lower(): v for k, v in r.headers.items()}
    assert hdrs["strict-transport-security"].startswith("max-age=31536000")
    assert "includeSubDomains" in hdrs["strict-transport-security"]
    assert "preload" in hdrs["strict-transport-security"]
    assert hdrs["x-content-type-options"] == "nosniff"
    assert hdrs["x-frame-options"] == "DENY"
    assert hdrs["referrer-policy"] == "strict-origin-when-cross-origin"
    assert "camera=()" in hdrs["permissions-policy"]
    assert hdrs["cross-origin-opener-policy"] == "same-origin"
    assert hdrs["cross-origin-resource-policy"] == "same-origin"
