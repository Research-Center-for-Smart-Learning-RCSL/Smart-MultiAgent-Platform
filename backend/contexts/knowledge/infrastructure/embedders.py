"""Embedder adapters for the R10.05 whitelist.

Four providers × four models; one HTTP call per batch per provider. Each
adapter receives the *plaintext* API key at construction — the caller
(ingest / retrieve services) obtains it via ``KeysFacade.unwrap_api_key_plaintext``
and scrubs it afterwards.

The vector sizes embedded below mirror the current provider dimensions
as of 2026-Q1 and are authoritative for Qdrant `ensure_collection` sizing.
Changing provider defaults means migrating collections — tracked in
``docs/implement/E-agents-knowledge.md`` Risks section.
"""

from __future__ import annotations

import httpx

from contexts.knowledge.application.ports import Embedder

__all__ = [
    "GeminiEmbedder",
    "OpenAIEmbedder",
    "VoyageEmbedder",
    "embedder_for",
]


_VECTOR_SIZES: dict[tuple[str, str], int] = {
    ("openai", "text-embedding-3-small"): 1536,
    ("openai", "text-embedding-3-large"): 3072,
    ("gemini", "text-embedding-004"): 768,
    ("voyage", "voyage-3"): 1024,
}


def _vector_size(provider: str, model: str) -> int:
    try:
        return _VECTOR_SIZES[(provider, model)]
    except KeyError as exc:
        raise ValueError(f"unknown embedder ({provider}, {model})") from exc


class _BaseEmbedder:
    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        vector_size: int,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self._model = model
        self._api_key = api_key
        self.vector_size = vector_size
        self._http = http or httpx.AsyncClient(timeout=30.0)


class OpenAIEmbedder(_BaseEmbedder, Embedder):
    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        r = await self._http.post(
            "https://api.openai.com/v1/embeddings",
            headers={"Authorization": f"Bearer {self._api_key}"},
            json={"model": self._model, "input": texts},
        )
        r.raise_for_status()
        data = r.json()["data"]
        # OpenAI preserves input order.
        return [d["embedding"] for d in data]


class VoyageEmbedder(_BaseEmbedder, Embedder):
    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        r = await self._http.post(
            "https://api.voyageai.com/v1/embeddings",
            headers={"Authorization": f"Bearer {self._api_key}"},
            json={"model": self._model, "input": texts},
        )
        r.raise_for_status()
        data = r.json()["data"]
        return [d["embedding"] for d in sorted(data, key=lambda x: x["index"])]


class GeminiEmbedder(_BaseEmbedder, Embedder):
    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        # Gemini's batch endpoint (`batchEmbedContents`) takes one request per
        # input; we fan out then await in order.
        if not texts:
            return []
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/"
            f"models/{self._model}:batchEmbedContents?key={self._api_key}"
        )
        body = {
            "requests": [
                {
                    "model": f"models/{self._model}",
                    "content": {"parts": [{"text": t}]},
                }
                for t in texts
            ],
        }
        r = await self._http.post(url, json=body)
        r.raise_for_status()
        return [e["values"] for e in r.json()["embeddings"]]


def embedder_for(
    *,
    provider: str,
    model: str,
    api_key: str,
    http: httpx.AsyncClient | None = None,
) -> Embedder:
    vs = _vector_size(provider, model)
    if provider == "openai":
        return OpenAIEmbedder(model=model, api_key=api_key, vector_size=vs, http=http)
    if provider == "voyage":
        return VoyageEmbedder(model=model, api_key=api_key, vector_size=vs, http=http)
    if provider == "gemini":
        return GeminiEmbedder(model=model, api_key=api_key, vector_size=vs, http=http)
    raise ValueError(f"no embedder for provider {provider!r}")
