"""`/api/admin/*` shared dependencies.

Extracted so sub-routers can import `require_admin` without circular imports
back to the aggregator `admin.py`.
"""

from __future__ import annotations

from fastapi import Depends, HTTPException

from shared_kernel.auth.dependencies import current_principal
from shared_kernel.auth.permissions import Principal


async def require_admin(
    principal: Principal = Depends(current_principal),
) -> Principal:
    if not principal.is_admin:
        raise HTTPException(status_code=403, detail="Admin only")
    return principal
