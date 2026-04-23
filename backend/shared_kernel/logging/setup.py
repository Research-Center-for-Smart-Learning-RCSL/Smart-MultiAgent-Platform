"""Configure loguru + stdlib logging to emit JSON with O1.2 fields.

Required log fields (per `docs/operations.md` §1.2):
  ts, level, service, request_id, session_id, user_id, route, latency_ms,
  event, msg, error

Unknown fields pass through under `extra` after redaction.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import orjson
from loguru import logger

from shared_kernel.logging.context import request_id_var, session_id_var, user_id_var
from shared_kernel.logging.redaction import redact

_REQUIRED_FIELDS = (
    "ts", "level", "service", "request_id", "session_id", "user_id",
    "route", "latency_ms", "event", "msg", "error",
)


def _sink_factory(service_name: str):  # noqa: ANN202 - loguru sink signature
    def _sink(message: Any) -> None:
        record = message.record
        payload: dict[str, Any] = {
            "ts": record["time"].isoformat(),
            "level": record["level"].name.lower(),
            "service": service_name,
            "request_id": request_id_var.get(),
            "session_id": session_id_var.get(),
            "user_id": user_id_var.get(),
            "route": record["extra"].get("route"),
            "latency_ms": record["extra"].get("latency_ms"),
            "event": record["extra"].get("event"),
            "msg": record["message"],
            "error": None,
        }
        if record["exception"]:
            exc = record["exception"]
            payload["error"] = {
                "type": exc.type.__name__ if exc.type else None,
                "value": str(exc.value) if exc.value else None,
            }
        extras = {k: v for k, v in record["extra"].items() if k not in payload}
        if extras:
            payload["extra"] = redact(extras)

        sys.stdout.buffer.write(orjson.dumps(redact(payload)) + b"\n")
        sys.stdout.buffer.flush()

    return _sink


class _StdlibInterceptHandler(logging.Handler):
    """Bridge stdlib logging (uvicorn, sqlalchemy, arq) into loguru."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno
        frame, depth = logging.currentframe(), 2
        while frame.f_code.co_filename == logging.__file__ and frame.f_back:
            frame = frame.f_back
            depth += 1
        logger.opt(depth=depth, exception=record.exc_info).log(level, record.getMessage())


def configure_logging(settings: Any) -> None:
    """Wire loguru and redirect stdlib loggers. Idempotent."""
    logger.remove()
    logger.add(
        _sink_factory(settings.service_name),
        level=settings.level.upper(),
        backtrace=False,
        diagnose=False,
        enqueue=False,
    )

    logging.basicConfig(handlers=[_StdlibInterceptHandler()], level=0, force=True)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "fastapi", "sqlalchemy.engine"):
        lg = logging.getLogger(name)
        lg.handlers = [_StdlibInterceptHandler()]
        lg.propagate = False


__all__ = ["configure_logging", "_REQUIRED_FIELDS"]
