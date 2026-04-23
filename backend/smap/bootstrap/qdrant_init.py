"""`smap.bootstrap qdrant-init` — readiness probe + version print (B.9).

Collection creation is deferred to Phase E (rag_{project_id} / graphrag_*)
because they are per-project and driven by the product, not by bootstrap.
"""

from __future__ import annotations

import httpx

from app.config.settings import Settings

from ._common import BootstrapReport


def run(settings: Settings) -> BootstrapReport:
    report = BootstrapReport(subcommand="qdrant-init")
    base = settings.qdrant.url.rstrip("/")
    headers: dict[str, str] = {}
    if settings.qdrant.api_key:
        headers["api-key"] = settings.qdrant.api_key
    with httpx.Client(timeout=3.0) as http:
        ready = http.get(f"{base}/readyz", headers=headers)
        if ready.status_code != 200:
            raise RuntimeError(f"Qdrant not ready: status={ready.status_code}")
        version = http.get(f"{base}/", headers=headers).json()
    report.did("qdrant:ready", str(version.get("version", "<unknown>")))
    return report
