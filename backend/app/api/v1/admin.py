"""`/api/admin/*` — thin aggregator that mounts domain-specific sub-routers.

Each sub-router lives in its own module (admin_users, admin_orgs, etc.) and
handles one concern. This file re-exports a single ``router`` so that
``main.py`` keeps the same one-liner registration.

IP bans are in ``admin_ip_bans.py`` (separate since Phase C).
GraphRAG reset is in ``graphrag.py``.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import (
    admin_audit,
    admin_impersonation,
    admin_metrics,
    admin_orgs,
    admin_projects,
    admin_rate_limits,
    admin_users,
)

router = APIRouter(prefix="/api/admin", tags=["admin"])

router.include_router(admin_users.router)
router.include_router(admin_impersonation.router)
router.include_router(admin_orgs.router)
router.include_router(admin_projects.router)
router.include_router(admin_audit.router)
router.include_router(admin_metrics.router)
router.include_router(admin_rate_limits.router)

__all__ = ["router"]
