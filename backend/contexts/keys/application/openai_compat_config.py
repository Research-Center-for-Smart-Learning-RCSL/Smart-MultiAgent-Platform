"""Pydantic model for ``openai_compat`` per-key config (R7.16, Q-10).

Validated at upload time so invalid config is rejected with 422 rather
than discovered at first call. The schema is strict: unknown fields are
forbidden so the JSONB column stays bounded.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator


PROXY_HEADER_BLOCKLIST: frozenset[str] = frozenset(
    {
        "authorization",
        "content-type",
        "content-length",
        "host",
        "transfer-encoding",
        "connection",
        "upgrade",
        "proxy-authorization",
    }
)


class OpenAICompatConfig(BaseModel, extra="forbid"):
    base_url: str = Field(..., min_length=1, max_length=2048)
    label: str = Field(default="OpenAI Compatible", max_length=100)
    timeout_s: int = Field(default=120, ge=10, le=3600)
    capabilities: list[str] = Field(default_factory=lambda: ["llm_chat", "embedding"])
    proxy_headers: dict[str, str] | None = Field(
        default=None,
        description="Extra headers merged into every outbound request (e.g. proxy auth).",
    )

    @field_validator("proxy_headers")
    @classmethod
    def validate_proxy_headers(cls, v: dict[str, str] | None) -> dict[str, str] | None:
        if v is None or len(v) == 0:
            return v
        if len(v) > 20:
            raise ValueError("proxy_headers: max 20 entries allowed")
        for name, value in v.items():
            if name.lower() in PROXY_HEADER_BLOCKLIST:
                raise ValueError(f"proxy_headers: header {name!r} is blocked")
            if len(name) > 128:
                raise ValueError("proxy_headers: header name exceeds 128 chars")
            if len(value) > 4096:
                raise ValueError("proxy_headers: header value exceeds 4096 chars")
            if "\r" in name or "\n" in name:
                raise ValueError("proxy_headers: header name contains CR/LF")
            if "\r" in value or "\n" in value:
                raise ValueError("proxy_headers: header value contains CR/LF")
        return v

    @field_validator("capabilities")
    @classmethod
    def validate_capabilities(cls, v: list[str]) -> list[str]:
        allowed = {"llm_chat", "embedding"}
        for cap in v:
            if cap not in allowed:
                raise ValueError(f"unknown capability {cap!r}; allowed: {sorted(allowed)}")
        if not v:
            raise ValueError("at least one capability is required")
        return v

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()
