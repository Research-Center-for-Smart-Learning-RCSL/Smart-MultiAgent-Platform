"""Unit tests for the GraphRAG embedding pin (Phase 2a D2, R11.18).

The anchor defect: the embedding provider/model — and therefore the vector
dimension — was derived at build time from whichever key sorted first in the
builder key group, while every config in a project shares one fixed-dimension
Qdrant collection. Swapping the group's first embedding provider silently
changed the dimension. The fix pins one ``(provider, model, dim)`` per project
and selects the build/retrieval key by that pinned provider.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest

import contexts.knowledge.application.embed_resolution as er
from contexts.knowledge.application.embed_resolution import select_embed_candidate
from contexts.knowledge.application.graphrag_config_service import GraphRagConfigService
from contexts.knowledge.domain.errors import GraphRagEmbedDimensionConflict

# ---------------------------------------------------------------------------
# AC-1 — the anchor: selection follows the pinned provider, not group order
# ---------------------------------------------------------------------------


def test_select_embed_candidate_honors_pinned_provider_over_group_order() -> None:
    key_gemini, key_openai = uuid.uuid4(), uuid.uuid4()
    # Group order puts gemini first, openai second.
    candidates = [
        ("gemini", "text-embedding-004", key_gemini),
        ("openai", "text-embedding-3-small", key_openai),
    ]
    pinned = ("openai", "text-embedding-3-small", key_openai)

    # Pinned to openai → openai is chosen even though gemini sorts first.
    assert select_embed_candidate(candidates, provider="openai") == pinned
    # Flipping which provider sorts first must NOT change the pinned resolution
    # (the pre-fix "first key wins" behaviour is exactly what this defeats).
    assert select_embed_candidate(list(reversed(candidates)), provider="openai") == pinned


def test_select_embed_candidate_unpinned_returns_first() -> None:
    key_a, key_b = uuid.uuid4(), uuid.uuid4()
    candidates = [("gemini", "text-embedding-004", key_a), ("openai", "text-embedding-3-small", key_b)]
    assert select_embed_candidate(candidates, provider=None) == candidates[0]


def test_select_embed_candidate_no_match_returns_none() -> None:
    # AC-4 fail-loud precondition: a pinned provider with no carried key in the
    # group resolves to nothing, so the factory raises rather than silently
    # switching providers/dimensions.
    candidates = [("gemini", "text-embedding-004", uuid.uuid4())]
    assert select_embed_candidate(candidates, provider="openai") is None


# The single shared resolver the build / retrieval / reconciler embedders all
# use — the D2 invariant lives here so no path can drift.


@pytest.mark.asyncio
async def test_resolve_pinned_embed_key_uses_pin_and_overrides_model(monkeypatch: Any) -> None:
    key = uuid.uuid4()
    seen: dict[str, Any] = {}

    async def fake_resolve(_db: Any, _group: Any, *, provider: Any = None) -> tuple[str, str, uuid.UUID]:
        seen["provider"] = provider
        return ("openai", "text-embedding-3-small", key)

    monkeypatch.setattr(er, "resolve_embed_key", fake_resolve)
    cfg = SimpleNamespace(
        builder_key_group_id=uuid.uuid4(),
        embed_provider="openai",
        embed_model="text-embedding-3-large",
    )

    provider, model, key_id = await er.resolve_pinned_embed_key(object(), cfg)

    assert seen["provider"] == "openai"  # the pinned provider narrows selection
    assert (provider, model, key_id) == ("openai", "text-embedding-3-large", key)  # pinned model wins


@pytest.mark.asyncio
async def test_resolve_pinned_embed_key_unpinned_passes_none(monkeypatch: Any) -> None:
    async def fake_resolve(_db: Any, _group: Any, *, provider: Any = None) -> tuple[str, str, uuid.UUID]:
        assert provider is None
        return ("gemini", "text-embedding-004", uuid.uuid4())

    monkeypatch.setattr(er, "resolve_embed_key", fake_resolve)
    cfg = SimpleNamespace(builder_key_group_id=uuid.uuid4(), embed_provider=None, embed_model=None)

    provider, model, _ = await er.resolve_pinned_embed_key(object(), cfg)

    assert (provider, model) == ("gemini", "text-embedding-004")


@pytest.mark.asyncio
async def test_resolve_pinned_embed_key_raises_when_no_key_for_pinned_provider(monkeypatch: Any) -> None:
    async def fake_resolve(_db: Any, _group: Any, *, provider: Any = None) -> None:
        return None

    monkeypatch.setattr(er, "resolve_embed_key", fake_resolve)
    cfg = SimpleNamespace(
        builder_key_group_id=uuid.uuid4(),
        embed_provider="openai",
        embed_model="text-embedding-3-small",
    )

    with pytest.raises(RuntimeError, match="openai"):
        await er.resolve_pinned_embed_key(object(), cfg)


# ---------------------------------------------------------------------------
# AC-3 — create/update rejects a group whose dimension differs from the pin
# ---------------------------------------------------------------------------


class _NullDb:
    """AsyncSession stand-in; the pin-enforcement seam is monkeypatched so no
    query runs."""

    async def execute(self, *_a: Any, **_kw: Any) -> Any:  # pragma: no cover - unused
        raise AssertionError("no query expected in this test")


def _service() -> GraphRagConfigService:
    return GraphRagConfigService(_NullDb())  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_enforce_pin_raises_on_project_dimension_conflict(monkeypatch: Any) -> None:
    svc = _service()

    async def fake_resolve(_gid: Any) -> tuple[str, str, int]:
        return ("gemini", "text-embedding-004", 768)

    async def fake_pin(_pid: Any, *, exclude_config_id: Any = None) -> int:
        return 1536

    monkeypatch.setattr(svc, "_resolve_group_pin", fake_resolve)
    monkeypatch.setattr(svc, "_project_pinned_dim", fake_pin)

    with pytest.raises(GraphRagEmbedDimensionConflict):
        await svc._enforce_and_resolve_pin(uuid.uuid4(), uuid.uuid4())


@pytest.mark.asyncio
async def test_enforce_pin_allows_matching_dimension(monkeypatch: Any) -> None:
    svc = _service()
    resolved = ("openai", "text-embedding-3-small", 1536)

    async def fake_resolve(_gid: Any) -> tuple[str, str, int]:
        return resolved

    async def fake_pin(_pid: Any, *, exclude_config_id: Any = None) -> int:
        return 1536

    monkeypatch.setattr(svc, "_resolve_group_pin", fake_resolve)
    monkeypatch.setattr(svc, "_project_pinned_dim", fake_pin)

    assert await svc._enforce_and_resolve_pin(uuid.uuid4(), uuid.uuid4()) == resolved


@pytest.mark.asyncio
async def test_enforce_pin_null_when_group_has_no_embedding_key(monkeypatch: Any) -> None:
    # A builder group with no embedding key resolves to None: the config is
    # created with a null pin and self-pins on its first successful build.
    svc = _service()

    async def fake_resolve(_gid: Any) -> None:
        return None

    called = False

    async def fake_pin(_pid: Any, *, exclude_config_id: Any = None) -> int | None:
        nonlocal called
        called = True
        return 1536

    monkeypatch.setattr(svc, "_resolve_group_pin", fake_resolve)
    monkeypatch.setattr(svc, "_project_pinned_dim", fake_pin)

    assert await svc._enforce_and_resolve_pin(uuid.uuid4(), uuid.uuid4()) is None
    # No project pin is consulted when the group yields no embedding key.
    assert called is False


@pytest.mark.asyncio
async def test_enforce_pin_first_config_in_project_is_allowed(monkeypatch: Any) -> None:
    # No sibling pin yet → the resolved triple becomes the project pin.
    svc = _service()
    resolved = ("voyage", "voyage-3", 1024)

    async def fake_resolve(_gid: Any) -> tuple[str, str, int]:
        return resolved

    async def fake_pin(_pid: Any, *, exclude_config_id: Any = None) -> int | None:
        return None

    monkeypatch.setattr(svc, "_resolve_group_pin", fake_resolve)
    monkeypatch.setattr(svc, "_project_pinned_dim", fake_pin)

    assert await svc._enforce_and_resolve_pin(uuid.uuid4(), uuid.uuid4()) == resolved
