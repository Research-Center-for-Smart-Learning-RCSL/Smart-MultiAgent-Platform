"""Bundled local reranker service — bge-reranker-v2-m3 on CPU (R10.08 / F-19).

Serves the exact contract the backend's ``LocalBgeReranker`` adapter calls
(``contexts/knowledge/infrastructure/rerankers.py``):

    POST /rerank  {"query": str, "candidates": [str, ...], "top_k": int}
               -> {"results": [{"index": int, "score": float}, ...]}   # score-desc

    GET  /health -> {"status": "ok"}   (200 once the model is loaded)

Keyless by design: SMAP never sends a provider API key here. The service is
internal-only (data_net, no public port) and the model weights are baked into
the image at build so the container needs no network at runtime.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel, Field

_MODEL_NAME = "BAAI/bge-reranker-v2-m3"
_state: dict[str, Any] = {}


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    # Load once at startup; CPU inference is acceptable at the documented scale.
    from FlagEmbedding import FlagReranker

    _state["reranker"] = FlagReranker(_MODEL_NAME, use_fp16=False)
    yield
    _state.clear()


app = FastAPI(title="smap-bge-reranker", lifespan=_lifespan)


class RerankRequest(BaseModel):
    query: str
    candidates: list[str]
    top_k: int = Field(default=8, gt=0)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok" if _state.get("reranker") is not None else "loading"}


@app.post("/rerank")
def rerank(body: RerankRequest) -> dict[str, list[dict[str, float]]]:
    if not body.candidates:
        return {"results": []}
    reranker = _state["reranker"]
    # FlagReranker scores [query, passage] pairs; higher = more relevant.
    scores = reranker.compute_score([[body.query, c] for c in body.candidates], normalize=True)
    if not isinstance(scores, list):
        scores = [scores]
    ranked = sorted(
        ({"index": i, "score": float(s)} for i, s in enumerate(scores)),
        key=lambda r: r["score"],
        reverse=True,
    )
    return {"results": ranked[: body.top_k]}
