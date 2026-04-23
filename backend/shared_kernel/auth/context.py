"""Per-request auth context — populated by middleware, consumed by deps.

`RequestContext` is the single object the FastAPI dependencies hand around
so no handler has to plumb `user_id`, `actor_ip`, `session_id`, `request_id`
through its own arguments. `shared_kernel.auth.dependencies.current_context`
yields it.

It is carried on `request.state.auth_ctx`. Middlewares enrich it:

- `RequestIdMiddleware` — sets `request_id`
- `TrustedProxyMiddleware` — sets `actor_ip`
- `AuthMiddleware` — sets `principal`, `session_id`, `access_jti`
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from shared_kernel.auth.permissions import Principal


@dataclass(slots=True)
class RequestContext:
    request_id: uuid.UUID = field(default_factory=uuid.uuid4)
    actor_ip: str | None = None
    principal: Principal | None = None
    session_id: uuid.UUID | None = None
    access_jti: uuid.UUID | None = None
    access_exp: datetime | None = None
    impersonated_by: uuid.UUID | None = None

    @property
    def is_authenticated(self) -> bool:
        return self.principal is not None

    @property
    def is_impersonated(self) -> bool:
        return self.impersonated_by is not None

    def remaining_access_ttl(self, now: datetime) -> timedelta:
        if self.access_exp is None:
            return timedelta(seconds=0)
        delta = self.access_exp - now
        return delta if delta.total_seconds() > 0 else timedelta(seconds=0)


__all__ = ["RequestContext"]
