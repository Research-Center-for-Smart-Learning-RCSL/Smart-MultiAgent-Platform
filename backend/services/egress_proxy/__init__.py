"""SMAP Egress Proxy (R12.04).

Standalone FastAPI HTTPS forward-proxy that every sandboxed MCP / URL-MCP
must traverse. Importing this package does not start a server; call
:func:`services.egress_proxy.app.create_app` explicitly.
"""

from __future__ import annotations

from services.egress_proxy.app import create_app
from services.egress_proxy.ip_policy import is_blocked_ip

__all__ = ["create_app", "is_blocked_ip"]
