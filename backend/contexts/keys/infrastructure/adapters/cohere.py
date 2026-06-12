"""Cohere adapter — Rerank only (K.1). Auth via ``Authorization: Bearer``."""

from __future__ import annotations

from typing import Any

from contexts.keys.application.provider_router import ProviderCallResult, ProviderRequest
from contexts.keys.domain.providers import ApiKeyProvider, ProviderCapability
from contexts.keys.infrastructure.adapters import base

_URL = "https://api.cohere.com/v1/rerank"


class CohereAdapter:
    provider = ApiKeyProvider.COHERE

    async def invoke(self, *, secret: str, request: ProviderRequest) -> ProviderCallResult:
        if request.capability is not ProviderCapability.RERANK:
            raise ValueError(f"cohere does not serve {request.capability.value}")
        payload = request.payload
        body: dict[str, Any] = {
            "model": base.resolve_model(payload, ApiKeyProvider.COHERE),
            "query": payload["query"],
            "documents": payload["documents"],
        }
        if payload.get("top_n") is not None:
            body["top_n"] = payload["top_n"]
        headers = {"Authorization": f"Bearer {secret}", "content-type": "application/json"}
        async with base.new_client() as client:
            resp = await client.post(_URL, json=body, headers=headers)
        if resp.status_code != 200:
            return ProviderCallResult(http_status=resp.status_code, body=base.scrub_error(resp))
        data = resp.json()
        results = [
            {"index": r.get("index"), "relevance_score": r.get("relevance_score")}
            for r in data.get("results") or []
        ]
        meta = (data.get("meta") or {}).get("billed_units") or {}
        return ProviderCallResult(
            http_status=200,
            body={"results": results},
            input_tokens=int(meta.get("search_units", 0)),
        )


__all__ = ["CohereAdapter"]
