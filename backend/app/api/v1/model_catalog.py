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


class ChatModelSpecOut(BaseModel):
    """Per-model request-shaping capabilities (R9.03a) — what the agent-config
    form needs to disable a control the selected model refuses, and to bound
    the context-token-cap input by the model's own window rather than the
    provider's."""

    model_id: str
    context_limit: int
    accepts_effort: bool
    effort_values: list[str]
    accepts_sampling: bool
    accepts_vision: bool
    uses_completion_token_field: bool
    effort_conflicts_with_tools: bool
    source_url: str
    verified_on: str


class ChatModelProviderOut(BaseModel):
    provider: str
    models: list[ChatModelSpecOut]
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
            ChatModelProviderOut(
                provider=c.provider,
                models=[
                    ChatModelSpecOut(
                        model_id=m.model_id,
                        context_limit=m.context_limit,
                        accepts_effort=m.accepts_effort,
                        effort_values=list(m.effort_values),
                        accepts_sampling=m.accepts_sampling,
                        accepts_vision=m.accepts_vision,
                        uses_completion_token_field=m.uses_completion_token_field,
                        effort_conflicts_with_tools=m.effort_conflicts_with_tools,
                        source_url=m.source_url,
                        verified_on=m.verified_on,
                    )
                    for m in c.models
                ],
                default=c.default,
            )
            for c in chat
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
