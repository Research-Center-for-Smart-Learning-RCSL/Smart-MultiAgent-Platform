"""Arq worker entrypoint.

Task registry:
  - `noop`                        — liveness smoke
  - `file_scan_requested`         — F.5 attachment AV pass (no-op by default)
  - `retention_purge`             — F.8 nightly 5-year sweep (cron)
  - `chat_export`                 — F.10 chat export → JSON manifest in MinIO
  - `key_usage_threshold_sample`  — D.8 80% hourly-limit sampler (every 30 s)
"""

from __future__ import annotations

import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from typing import Any

from arq import cron
from arq.connections import RedisSettings

from app.config.settings import get_settings
from app.workers.tasks.conversation import (
    chat_export,
    file_scan_requested,
    retention_purge,
)
from app.workers.tasks.orchestration import (
    evaluate_silence,
    wakeup_agent,
    wakeup_refresh,
)
from app.workers.tasks.advisory import daily_org_advisory_snapshot
from app.workers.tasks.retention import retention_sweep
from app.workers.tasks.graphrag import graphrag_build
from app.workers.tasks.workflow import (
    archive_workflow_runs,
    retry_workflow_node,
    run_workflow_step,
    workflow_cron_scheduler,
    workflow_event_timeout,
    workflow_subagent_timeout,
)
from contexts.keys.application.threshold_worker import sample_once as _threshold_sample_once
from shared_kernel.db import registry as _db_registry  # noqa: F401 — table imports
from shared_kernel.db.session import get_sessionmaker
from shared_kernel.logging.setup import configure_logging


async def noop(ctx: dict[str, Any]) -> str:
    return "ok"


async def key_usage_threshold_sample(ctx: dict[str, Any]) -> int:
    """D.8 — 80% hourly-limit sampler. Runs every 30 s via cron."""
    _ = ctx
    sm = get_sessionmaker()
    async with sm() as session:
        return await _threshold_sample_once(session)


def _redis_alive() -> bool:
    """Sync Redis PING — fails fast if Arq's broker is unreachable.

    Arq jobs cannot be dequeued without Redis, so a process that's running
    but disconnected from Redis is *not* healthy. The previous healthcheck
    only verified the sidecar HTTP server, which masked broker outages.
    """
    try:
        import redis as _redis_sync  # type: ignore[import-not-found]
        client = _redis_sync.Redis.from_url(
            get_settings().redis.dsn, socket_timeout=2,
        )
        return bool(client.ping())
    except Exception:
        return False


class _HealthzHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 — stdlib signature
        if self.path != "/healthz":
            self.send_response(404); self.end_headers(); return
        if not _redis_alive():
            self.send_response(503)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"redis-unreachable"}')
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"status":"ok"}')

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        return


def _start_healthz_sidecar() -> None:
    port = int(os.environ.get("SMAP_WORKER_HEALTHZ_PORT", "8001"))
    server = ThreadingHTTPServer(("0.0.0.0", port), _HealthzHandler)
    Thread(target=server.serve_forever, daemon=True, name="worker-healthz").start()


async def _startup(ctx: dict[str, Any]) -> None:
    configure_logging(get_settings().logging)
    _start_healthz_sidecar()


class WorkerSettings:
    functions = [
        noop, file_scan_requested, retention_purge, chat_export,
        wakeup_agent, evaluate_silence, wakeup_refresh,
        run_workflow_step, retry_workflow_node,
        workflow_event_timeout, workflow_subagent_timeout,
        archive_workflow_runs, workflow_cron_scheduler,
        retention_sweep,
        daily_org_advisory_snapshot,
        key_usage_threshold_sample,
        graphrag_build,
    ]
    on_startup = _startup
    redis_settings = RedisSettings.from_dsn(get_settings().redis.dsn)
    job_timeout = 600
    max_jobs = 50
    keep_result = 3600
    cron_jobs = [
        # 03:10 UTC daily — retention sweep (R13.15).
        cron(retention_purge, hour=3, minute=10, run_at_startup=False),
        # 03:30 UTC daily — comprehensive retention sweep (I.4).
        cron(retention_sweep, hour=3, minute=30, run_at_startup=False),
        # Every 30 seconds — silence trigger sweep (G.3 / R15.02).
        cron(evaluate_silence, second={0, 30}, run_at_startup=False),
        # Hourly — wakeup config refresh to authored values (G.5 / R15.09).
        cron(wakeup_refresh, minute=0, run_at_startup=False),
        # 02:00 UTC daily — per-tenant advisory snapshot (I.8).
        cron(daily_org_advisory_snapshot, hour=2, minute=0, run_at_startup=False),
        # 04:00 UTC daily — 90-day workflow run archive (H.6).
        cron(archive_workflow_runs, hour=4, minute=0, run_at_startup=False),
        # Every minute — cron trigger scheduler (H.4).
        cron(workflow_cron_scheduler, minute=set(range(60)), run_at_startup=False),
        # Every 30 seconds — D.8 80% hourly-limit sampler (R7.11).
        cron(key_usage_threshold_sample, second={0, 30}, run_at_startup=False),
    ]
