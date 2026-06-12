"""Voyage adapter — Embeddings only (K.1). Auth via ``Authorization: Bearer``."""

from __future__ import annotations

from contexts.keys.application.provider_router import ProviderCallResult, ProviderRequest
from contexts.keys.domain.providers import ApiKeyProvider, ProviderCapability
from contexts.keys.infrastructure.adapters import base

_URL = "https://api.voyageai.com/v1/embeddings"


class VoyageAdapter:
    provider = ApiKeyProvider.VOYAGE

    async def invoke(self, *, secret: str, request: ProviderRequest) -> ProviderCallResult:
        if request.capability is not ProviderCapability.EMBEDDING:
            raise ValueError(f"voyage does not serve {request.capability.value}")
        payload = request.payload
        body = {
            "model": base.resolve_model(payload, ApiKeyProvider.VOYAGE),
            "input": payload["input"],
        }
        headers = {"Authorization": f"Bearer {secret}", "content-type": "application/json"}
        async with base.new_client() as client:
            resp = await client.post(_URL, json=body, headers=headers)
        if resp.status_code != 200:
            return ProviderCallResult(http_status=resp.status_code, body=base.scrub_error(resp))
        data = resp.json()
        rows = sorted(data.get("data") or [], key=lambda d: d.get("index", 0))
        usage = data.get("usage") or {}
        return ProviderCallResult(
            http_status=200,
            body={"embeddings": [r["embedding"] for r in rows]},
            input_tokens=int(usage.get("total_tokens", 0)),
        )


__all__ = ["VoyageAdapter"]
