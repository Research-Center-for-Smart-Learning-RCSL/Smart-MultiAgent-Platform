"""Pydantic model for ``openai_compat`` per-key config (R7.16, Q-10).

Validated at upload time so invalid config is rejected with 422 rather
than discovered at first call. The schema is strict: unknown fields are
forbidden so the JSONB column stays bounded.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator


class OpenAICompatConfig(BaseModel, extra="forbid"):
    base_url: str = Field(..., min_length=1, max_length=2048)
    label: str = Field(default="OpenAI Compatible", max_length=100)
    timeout_s: int = Field(default=120, ge=10, le=3600)
    capabilities: list[str] = Field(default_factory=lambda: ["llm_chat", "embedding"])

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
