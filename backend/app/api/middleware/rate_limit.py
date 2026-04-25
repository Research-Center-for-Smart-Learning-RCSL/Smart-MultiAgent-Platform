"""Path-prefix-based rate-limit middleware (R19.02 / R19.04 / R19.06).

Bucket selection:
  * `/api/auth/*`            → auth      (10 / min / IP)
  * `POST /api/chatrooms/.../messages` → chat-send (60 / min / user)
  * `POST /api/*attachments*|/api/tus*`→ upload (10 / min / user)
  * everything else          → other     (300 / min / user)

The auth bucket is IP-scoped per R19.02; the others resolve the caller's
`user_id` from the already-set `auth_ctx.principal` (so auth middleware must
run earlier for those buckets).
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from shared_kernel.auth import ratelimit
from shared_kernel.errors.problem import Problem, problem_type


def _bucket_for(path: str, method: str) -> ratelimit.Bucket:
    if path.startswith("/api/auth/"):
        return ratelimit.Bucket.AUTH
    if method == "POST" and "/messages" in path and path.startswith("/api/chatrooms/"):
        return ratelimit.Bucket.CHAT
    # tus PATCH (resumable chunk uploads) is explicitly 300/min/user per
    # R19.02 + F.5 — only the *Creation* POST and single-shot attachment POSTs
    # count against the 10/min/user upload bucket.
    if method == "POST" and (
        path.startswith("/api/tus")
        or "/attachments" in path
        or "/documents" in path
    ):
        return ratelimit.Bucket.UPLOAD
    return ratelimit.Bucket.OTHER


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:  # type: ignore[no-untyped-def]
        # Operator tooling + browser-driven reports are exempt: rate-limiting
        # them would either break monitoring (healthz/readyz/metrics) or make
        # anon CSP violation reports DoS the whole 'anon' OTHER bucket.
        #
        # /readyz stays exempt despite the audit's 2.18 advisory. Rationale:
        # 1) The 2-second result cache in readyz._load_results already bounds
        #    fan-out to <=1/sec/process — DoS amplification is already mitigated.
        # 2) Bucketing /readyz forces a Redis hop in the middleware. If Redis
        #    is the down dependency, rate-limit-check would fail FIRST and
        #    return 500 instead of letting /readyz return a clean 503 with
        #    `dependencies.redis == "down"` — that's a regression for the
        #    operator dashboard, not a fix.
        path = request.url.path
        if path in ("/healthz", "/readyz", "/metrics", "/api/csp-report"):
            return await call_next(request)

        bucket = _bucket_for(path, request.method)
        ctx = getattr(request.state, "auth_ctx", None)
        user_id = str(ctx.principal.user_id) if ctx and ctx.principal else None
        actor_ip = ctx.actor_ip if ctx else (
            request.client.host if request.client else "unknown"
        )
        decision = await ratelimit.check(
            bucket=bucket, user_id=user_id, actor_ip=actor_ip,
        )
        if not decision.allowed:
            problem = Problem(
                type=problem_type("rate-limited"),
                title="Too Many Requests",
                status=429,
                detail=f"Bucket {bucket.value} exceeded",
                extras={"bucket": bucket.value},
            )
            body = problem.dump()
            body["instance"] = path
            return JSONResponse(
                status_code=429,
                content=body,
                media_type="application/problem+json",
                headers={
                    "Retry-After": str(decision.retry_after_seconds),
                    "X-RateLimit-Limit": str(decision.limit),
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": str(decision.reset_ms // 1000),
                },
            )
        response = await call_next(request)
        response.headers.setdefault("X-RateLimit-Limit", str(decision.limit))
        response.headers.setdefault("X-RateLimit-Remaining", str(decision.remaining))
        response.headers.setdefault("X-RateLimit-Reset", str(decision.reset_ms // 1000))
        return response
