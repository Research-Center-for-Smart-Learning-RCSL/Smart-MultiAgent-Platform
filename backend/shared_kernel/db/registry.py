"""Backward-compat re-export -- moved to app.db_registry.

DEPRECATED: import from app.db_registry instead.

This module re-triggers the same side-effect imports so existing
``from shared_kernel.db import registry`` sites keep working.
"""

from app.db_registry import *  # noqa: F401, F403

__all__: list[str] = []
