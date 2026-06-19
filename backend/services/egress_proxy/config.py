"""Pydantic-settings model for the egress proxy environment variables.

Replaces scattered ``os.environ.get()`` calls in ``main.py`` with a
validated, typed settings object.
"""

from __future__ import annotations

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings


class EgressProxyEnvConfig(BaseSettings):
    """Environment-variable config for the egress proxy composition root."""

    model_config = {"env_prefix": ""}

    egress_proxy_shared_secret: str = Field(
        ...,
        alias="EGRESS_PROXY_SHARED_SECRET",
        description="Hex-encoded shared secret (>= 64 hex chars = 32 bytes).",
    )
    smap_db_dsn: str = Field(
        ...,
        alias="SMAP_DB_DSN",
        description="PostgreSQL async DSN (asyncpg dialect).",
    )
    smap_egress_upstream_timeout_s: float = Field(
        default=20.0,
        alias="SMAP_EGRESS_UPSTREAM_TIMEOUT_S",
        description="Upstream forward timeout in seconds.",
    )

    @field_validator("egress_proxy_shared_secret")
    @classmethod
    def _validate_secret(cls, v: str) -> str:
        try:
            decoded = bytes.fromhex(v)
        except ValueError as exc:
            raise ValueError(
                "EGRESS_PROXY_SHARED_SECRET must be a hex string"
                " (e.g. 64 hex chars = 32 bytes)"
            ) from exc
        if len(decoded) < 32:
            raise ValueError(
                "EGRESS_PROXY_SHARED_SECRET must be at least 32 bytes"
                " (64 hex chars). Generate with: openssl rand -hex 32"
            )
        return v

    @field_validator("smap_db_dsn")
    @classmethod
    def _validate_dsn(cls, v: str) -> str:
        if not v:
            raise ValueError("SMAP_DB_DSN is required")
        return v

    @property
    def shared_secret_bytes(self) -> bytes:
        return bytes.fromhex(self.egress_proxy_shared_secret)


__all__ = ["EgressProxyEnvConfig"]
