"""`/api/model-catalog` — read-only provider/model catalog for the config UI.

Surfaces the preset choices the frontend renders as dropdowns: per-provider
chat models (with the runtime default) and the whitelisted embedding models
(with their vector dimensions). The lists are static configuration owned by the
agents and knowledge domains; this endpoint just composes the two facades so the
frontend never hardcodes a second copy that could drift from the backend.

AuthZ: any authenticated user. The catalog is global, non-tenant data, so no
project scope applies — `current_principal` (logged in) is the only gate.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from contexts.agents.interfaces.facade import AgentsFacade
from contexts.knowledge.interfaces.facade import KnowledgeFacade
from shared_kernel.auth.dependencies import current_principal
from shared_kernel.auth.permissions import Principal
from shared_kernel.db.session import db_session

router = APIRouter(prefix="/api/model-catalog", tags=["model-catalog"])


class ChatModelProviderOut(BaseModel):
    provider: str
    models: list[str]
    default: str


class EmbedModelOut(BaseModel):
    model: str
    dimension: int


class EmbedModelProviderOut(BaseModel):
    provider: str
    models: list[EmbedModelOut]
    default: str


class ModelCatalogOut(BaseModel):
    chat: list[ChatModelProviderOut]
    embedding: list[EmbedModelProviderOut]


@router.get("", response_model=ModelCatalogOut)
async def get_model_catalog(
    _: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> ModelCatalogOut:
    chat = AgentsFacade(db).chat_model_catalog()
    embedding = KnowledgeFacade(db).embedding_catalog()
    return ModelCatalogOut(
        chat=[
            ChatModelProviderOut(provider=c.provider, models=list(c.models), default=c.default) for c in chat
        ],
        embedding=[
            EmbedModelProviderOut(
                provider=e.provider,
                models=[EmbedModelOut(model=m.model, dimension=m.dimension) for m in e.models],
                default=e.default,
            )
            for e in embedding
        ],
    )


__all__ = ["router"]
