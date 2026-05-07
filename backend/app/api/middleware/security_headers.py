"""Echo §19a.2 response headers on JSON responses (Nginx owns HTML).

CSP is report-only when `SMAP_SEC_CSP_REPORT_ONLY=1`. Browsers enforce
whichever *-Report-Only: Content-Security-Policy header they see — we flip
the header name, not just the policy body.
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.config.settings import get_settings

_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'wasm-unsafe-eval'; "
    "style-src 'self'; "
    "img-src 'self' data: https:; "
    "font-src 'self' data:; "
    "connect-src 'self' wss:; "
    "frame-ancestors 'none'; "
    "base-uri 'self'; "
    "form-action 'self'; "
    "object-src 'none'"
)

_HSTS = "max-age=31536000; includeSubDomains; preload"
_PERMISSIONS = "camera=(), microphone=(), geolocation=(), payment=()"


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:  # type: ignore[no-untyped-def]
        response = await call_next(request)
        settings = get_settings()
        csp_header = (
            "Content-Security-Policy-Report-Only"
            if settings.security.csp_report_only
            else "Content-Security-Policy"
        )
        # Keep an existing value if Nginx already set one — we only fill in
        # headers that aren't present yet.
        response.headers.setdefault(csp_header, _CSP + "; report-uri /api/csp-report")
        response.headers.setdefault("Strict-Transport-Security", _HSTS)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("Permissions-Policy", _PERMISSIONS)
        response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        response.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
        return response
