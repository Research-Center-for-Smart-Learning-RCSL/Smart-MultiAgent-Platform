"""F-14 (R11.25) — Knowledge Map builder-group change reconciles attached agents.

The config-side half of the builder-vs-consumer isolation invariant: changing a
map's builder key group to a group an attached agent consumes must auto-detach
that agent (agents context owns the write) in the same transaction, report the
ids, audit each detach, and serialise on the config-id advisory lock. The
agent-side guard (attach/re-key rejection) is covered in ``test_agent_service``.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any, ClassVar
from unittest.mock import AsyncMock, patch

import pytest

from contexts.knowledge.application.knowmap_config_service import KnowmapConfigService
from contexts.knowledge.domain.errors import KnowmapEmbeddingModelChangeBlocked

_PROJECT_ID = uuid.uuid4()
_BUILT = SimpleNamespace()  # sentinel truthy last_build_at


def _cfg(*, builder_group: uuid.UUID, provider: str = "openai", model: str = "m", built: bool = False):
    return SimpleNamespace(
        id=uuid.uuid4(),
        project_id=_PROJECT_ID,
        builder_key_group_id=builder_group,
        embed_provider=provider,
        embed_model=model,
        last_build_at=_BUILT if built else None,
    )


def _make_service(cfg, *, resolved=("openai", "m", 1536), pinned_dim=None):
    svc = KnowmapConfigService(AsyncMock())
    svc.get = AsyncMock(return_value=cfg)  # type: ignore[method-assign]
    svc._assert_builder_group_in_project = AsyncMock()  # type: ignore[method-assign]
    svc._resolve_group_pin = AsyncMock(return_value=resolved)  # type: ignore[method-assign]
    svc._configs = AsyncMock()
    svc._configs.project_pinned_dim.return_value = pinned_dim
    svc._configs.update.return_value = cfg
    svc._pins = AsyncMock()
    return svc


class _FakeAgentsFacade:
    """Captures the detach call and returns a preset id list."""

    calls: ClassVar[list[dict[str, Any]]] = []
    returns: ClassVar[list[uuid.UUID]] = []

    def __init__(self, _db: Any) -> None:
        pass

    async def detach_agents_colliding_with_knowmap_builder(self, **kw: Any) -> list[uuid.UUID]:
        _FakeAgentsFacade.calls.append(kw)
        return list(_FakeAgentsFacade.returns)


def _patch_facade(returns: list[uuid.UUID]):
    _FakeAgentsFacade.calls = []
    _FakeAgentsFacade.returns = returns
    return patch("contexts.agents.interfaces.facade.AgentsFacade", _FakeAgentsFacade)


@pytest.mark.asyncio
async def test_builder_change_detaches_colliding_agents_and_reports_ids() -> None:
    cfg = _cfg(builder_group=uuid.uuid4())
    svc = _make_service(cfg)
    new_group = uuid.uuid4()
    detached = uuid.uuid4()
    emit = AsyncMock()
    with (
        _patch_facade([detached]),
        patch("contexts.knowledge.application.knowmap_config_service.audit.emit", new=emit),
    ):
        updated, ids = await svc.update(
            config_id=cfg.id,
            patch={"builder_key_group_id": new_group},
            actor_user_id=uuid.uuid4(),
            actor_ip=None,
        )

    assert updated is cfg
    assert ids == [detached]
    # The detach ran against the NEW builder group, project-scoped.
    call = _FakeAgentsFacade.calls[0]
    assert call["knowmap_config_id"] == cfg.id
    assert call["new_builder_key_group_id"] == new_group
    assert call["project_id"] == _PROJECT_ID
    # Audit metadata names the detached agent, and carries only ids (no secret).
    meta = emit.await_args.args[1].metadata
    assert meta["detached_agent_ids"] == [str(detached)]


@pytest.mark.asyncio
async def test_no_collision_returns_empty_detach() -> None:
    cfg = _cfg(builder_group=uuid.uuid4())
    svc = _make_service(cfg)
    emit = AsyncMock()
    with (
        _patch_facade([]),
        patch("contexts.knowledge.application.knowmap_config_service.audit.emit", new=emit),
    ):
        _updated, ids = await svc.update(
            config_id=cfg.id,
            patch={"builder_key_group_id": uuid.uuid4()},
            actor_user_id=uuid.uuid4(),
            actor_ip=None,
        )
    assert ids == []
    assert len(_FakeAgentsFacade.calls) == 1  # reconciliation still ran


@pytest.mark.asyncio
async def test_name_only_patch_never_detaches() -> None:
    cfg = _cfg(builder_group=uuid.uuid4())
    svc = _make_service(cfg)
    emit = AsyncMock()
    with (
        _patch_facade([uuid.uuid4()]),  # would explode if wrongly consulted
        patch("contexts.knowledge.application.knowmap_config_service.audit.emit", new=emit),
    ):
        _updated, ids = await svc.update(
            config_id=cfg.id,
            patch={"name": "renamed"},
            actor_user_id=uuid.uuid4(),
            actor_ip=None,
        )
    assert ids == []
    assert _FakeAgentsFacade.calls == []  # no builder change → no reconciliation


@pytest.mark.asyncio
async def test_rejected_model_swap_does_not_detach() -> None:
    # F-13 precedence: a blocked embedding-model swap raises before any detach.
    cfg = _cfg(builder_group=uuid.uuid4(), provider="openai", model="small", built=True)
    svc = _make_service(cfg, resolved=("voyage", "voyage-x", 1536))
    with (
        _patch_facade([uuid.uuid4()]),
        patch("contexts.knowledge.application.knowmap_config_service.audit.emit", new=AsyncMock()),
        pytest.raises(KnowmapEmbeddingModelChangeBlocked),
    ):
        await svc.update(
            config_id=cfg.id,
            patch={"builder_key_group_id": uuid.uuid4()},
            actor_user_id=uuid.uuid4(),
            actor_ip=None,
        )
    assert _FakeAgentsFacade.calls == []  # rejection precedes detach


@pytest.mark.asyncio
async def test_builder_change_acquires_config_lock() -> None:
    cfg = _cfg(builder_group=uuid.uuid4())
    svc = _make_service(cfg)
    lock = AsyncMock()
    with (
        _patch_facade([]),
        patch("contexts.knowledge.application.knowmap_config_service.audit.emit", new=AsyncMock()),
        patch("contexts.knowledge.application.knowmap_config_service.advisory_xact_lock", new=lock),
    ):
        await svc.update(
            config_id=cfg.id,
            patch={"builder_key_group_id": uuid.uuid4()},
            actor_user_id=uuid.uuid4(),
            actor_ip=None,
        )
    # Serialised on the config-id key so a concurrent attach cannot interleave.
    lock.assert_awaited_once()
    assert str(cfg.id) in lock.await_args.args[1]
