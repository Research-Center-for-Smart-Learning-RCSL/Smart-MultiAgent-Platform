"""F-13 — reject a builder-group embedding model swap on a config with vectors.

A config stores a full (provider, model, dim) embedding pin, but the pre-fix
guard compared only the dimension, so a swap to a different embedding
provider/model at the same dimension was silently accepted and the next query
embedded with the new model against old-model vectors (recall collapse). These
tests exercise the update-path guard directly: the resolved (provider, model)
comparison and the fail-closed `last_build_at IS NOT NULL` rule, for both Concept
Maps and Knowledge Maps. The pin *resolution* seam is mocked (its dimension logic
is covered by test_graphrag_embed_pin.py); what is under test here is the new
model-change decision and its precedence relative to the dimension conflict.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from contexts.knowledge.application.graphrag_config_service import GraphRagConfigService
from contexts.knowledge.application.knowmap_config_service import KnowmapConfigService
from contexts.knowledge.domain.errors import (
    GraphRagEmbedDimensionConflict,
    GraphRagEmbeddingModelChangeBlocked,
    KnowmapEmbedDimensionConflict,
    KnowmapEmbeddingModelChangeBlocked,
)

_BUILT = datetime(2026, 7, 14, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Concept Map (graphrag) update path
# ---------------------------------------------------------------------------


class _FirstRowDb:
    """AsyncSession double: every query's ``.first()`` resolves to ``project_id``
    so the builder-group project check inside update() passes."""

    def __init__(self, project_id: Any) -> None:
        self._project_id = project_id

    async def execute(self, *_a: Any, **_k: Any) -> Any:
        pid = self._project_id

        class _R:
            def first(_self) -> Any:  # noqa: N805
                return SimpleNamespace(project_id=pid)

        return _R()


def _graphrag_cfg(project_id: uuid.UUID, *, provider: str | None, model: str | None, built: bool) -> Any:
    return SimpleNamespace(
        id=uuid.uuid4(),
        project_id=project_id,
        embed_provider=provider,
        embed_model=model,
        builder_key_group_id=uuid.uuid4(),
        last_build_at=_BUILT if built else None,
    )


async def _run_graphrag_update(
    cfg: Any,
    *,
    new_pin: tuple[str, str, int] | None = None,
    pin_error: Exception | None = None,
) -> tuple[Any, Any]:
    svc = GraphRagConfigService(_FirstRowDb(cfg.project_id))  # type: ignore[arg-type]
    svc.get = AsyncMock(return_value=cfg)  # type: ignore[method-assign]
    svc._config_owner = AsyncMock(return_value=("agent_group", uuid.uuid4()))  # type: ignore[method-assign]
    svc._assert_owner_in_project = AsyncMock()  # type: ignore[method-assign]
    if pin_error is not None:
        svc._enforce_and_resolve_pin = AsyncMock(side_effect=pin_error)  # type: ignore[method-assign]
    else:
        svc._enforce_and_resolve_pin = AsyncMock(return_value=new_pin)  # type: ignore[method-assign]
    svc._configs = AsyncMock()
    svc._configs.get.return_value = cfg
    svc._pins = AsyncMock()
    with patch("contexts.knowledge.application.graphrag_config_service.audit.emit", new=AsyncMock()):
        result = await svc.update(
            config_id=cfg.id,
            builder_key_group_id=uuid.uuid4(),  # differs -> group_changed
            trigger_config=None,
            actor_user_id=uuid.uuid4(),
            actor_ip=None,
        )
    return result, svc


@pytest.mark.asyncio
async def test_graphrag_rejects_same_dimension_model_swap_on_built_config() -> None:
    # Latent silent-corruption case: same dim, different model, built config.
    cfg = _graphrag_cfg(uuid.uuid4(), provider="openai", model="text-embedding-3-small", built=True)
    with pytest.raises(GraphRagEmbeddingModelChangeBlocked):
        await _run_graphrag_update(cfg, new_pin=("voyage", "voyage-x", 1536))
    # Persists nothing — the guard raises before set_embed_pin / pin upsert.


@pytest.mark.asyncio
async def test_graphrag_rejects_different_dimension_model_swap_on_single_built_config() -> None:
    # Observable-today case: single-config project, built, swap to a different-dim
    # model. Rejected cleanly at update (previously surfaced only at the D7 build
    # guard).
    cfg = _graphrag_cfg(uuid.uuid4(), provider="openai", model="text-embedding-3-small", built=True)
    with pytest.raises(GraphRagEmbeddingModelChangeBlocked):
        await _run_graphrag_update(cfg, new_pin=("openai", "text-embedding-3-large", 3072))


@pytest.mark.asyncio
async def test_graphrag_allows_model_change_on_never_built_config() -> None:
    cfg = _graphrag_cfg(uuid.uuid4(), provider="openai", model="text-embedding-3-small", built=False)
    _result, svc = await _run_graphrag_update(cfg, new_pin=("openai", "text-embedding-3-large", 3072))
    svc._configs.set_embed_pin.assert_awaited_once()
    svc._pins.upsert.assert_awaited_once()  # F-11 pin refreshed on the allowed change


@pytest.mark.asyncio
async def test_graphrag_allows_same_model_group_change_on_built_config() -> None:
    # Same resolved embedding, different group (e.g. extraction LLM only) — allowed
    # even on a built config; the guard keys on (provider, model), not group id.
    cfg = _graphrag_cfg(uuid.uuid4(), provider="openai", model="text-embedding-3-small", built=True)
    _result, svc = await _run_graphrag_update(cfg, new_pin=("openai", "text-embedding-3-small", 1536))
    svc._configs.set_embed_pin.assert_awaited_once()


@pytest.mark.asyncio
async def test_graphrag_dimension_conflict_keeps_precedence() -> None:
    # A different-dimension swap on a multi-config project still raises the
    # existing dimension conflict, not the new model-change error.
    cfg = _graphrag_cfg(uuid.uuid4(), provider="openai", model="text-embedding-3-small", built=True)
    with pytest.raises(GraphRagEmbedDimensionConflict):
        await _run_graphrag_update(cfg, pin_error=GraphRagEmbedDimensionConflict("sibling pins 768"))


# ---------------------------------------------------------------------------
# Knowledge Map (knowmap) update path
# ---------------------------------------------------------------------------


def _knowmap_cfg(project_id: uuid.UUID, *, provider: str, model: str, built: bool) -> Any:
    return SimpleNamespace(
        id=uuid.uuid4(),
        project_id=project_id,
        embed_provider=provider,
        embed_model=model,
        builder_key_group_id=uuid.uuid4(),
        last_build_at=_BUILT if built else None,
    )


async def _run_knowmap_update(
    cfg: Any,
    *,
    resolved: tuple[str, str, int],
    pinned_dim: int | None = None,
) -> tuple[Any, Any]:
    svc = KnowmapConfigService(AsyncMock())
    svc.get = AsyncMock(return_value=cfg)  # type: ignore[method-assign]
    svc._assert_builder_group_in_project = AsyncMock()  # type: ignore[method-assign]
    svc._resolve_group_pin = AsyncMock(return_value=resolved)  # type: ignore[method-assign]
    svc._configs = AsyncMock()
    svc._configs.project_pinned_dim.return_value = pinned_dim
    svc._configs.update.return_value = cfg
    svc._pins = AsyncMock()

    class _FakeAgentsFacade:
        def __init__(self, _db: Any) -> None:
            pass

        async def detach_agents_colliding_with_knowmap_builder(self, **_kw: Any) -> list[Any]:
            # F-14 detach seam — no collisions in the swap-guard scenarios (F-13).
            return []

    with (
        patch("contexts.knowledge.application.knowmap_config_service.audit.emit", new=AsyncMock()),
        patch("contexts.agents.interfaces.facade.AgentsFacade", _FakeAgentsFacade),
    ):
        result, _detached = await svc.update(
            config_id=cfg.id,
            patch={"builder_key_group_id": uuid.uuid4()},  # differs -> swap branch
            actor_user_id=uuid.uuid4(),
            actor_ip=None,
        )
    return result, svc


@pytest.mark.asyncio
async def test_knowmap_rejects_model_swap_on_built_config() -> None:
    cfg = _knowmap_cfg(uuid.uuid4(), provider="openai", model="text-embedding-3-small", built=True)
    with pytest.raises(KnowmapEmbeddingModelChangeBlocked):
        await _run_knowmap_update(cfg, resolved=("voyage", "voyage-x", 1536))


@pytest.mark.asyncio
async def test_knowmap_allows_model_change_on_never_built_config() -> None:
    cfg = _knowmap_cfg(uuid.uuid4(), provider="openai", model="text-embedding-3-small", built=False)
    _result, svc = await _run_knowmap_update(cfg, resolved=("openai", "text-embedding-3-large", 3072))
    svc._configs.update.assert_awaited_once()
    svc._pins.upsert.assert_awaited_once()


@pytest.mark.asyncio
async def test_knowmap_allows_same_model_group_change_on_built_config() -> None:
    cfg = _knowmap_cfg(uuid.uuid4(), provider="openai", model="text-embedding-3-small", built=True)
    _result, svc = await _run_knowmap_update(cfg, resolved=("openai", "text-embedding-3-small", 1536))
    svc._configs.update.assert_awaited_once()


@pytest.mark.asyncio
async def test_knowmap_dimension_conflict_keeps_precedence() -> None:
    # Sibling pins 1536; a swap resolving to 3072 raises the dimension conflict,
    # not the model-change error.
    cfg = _knowmap_cfg(uuid.uuid4(), provider="openai", model="text-embedding-3-small", built=True)
    with pytest.raises(KnowmapEmbedDimensionConflict):
        await _run_knowmap_update(cfg, resolved=("openai", "text-embedding-3-large", 3072), pinned_dim=1536)
