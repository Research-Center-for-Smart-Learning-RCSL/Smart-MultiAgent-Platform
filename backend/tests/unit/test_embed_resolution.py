"""SEC-H3 regression: the shared embed-key resolver must gate on active carry.

``resolve_embed_key`` selects keys via ``list_ordered_carried`` — the active-carry
join — never the unguarded ``list_ordered``. Before Phase 0 the worker build path
used ``list_ordered`` and could embed with a key whose project carry was revoked;
these tests pin the fix so a regression that reintroduces the unguarded listing fails.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from contexts.knowledge.application.embed_resolution import resolve_embed_key


@pytest.mark.asyncio
async def test_resolver_selects_via_carried_listing(monkeypatch: pytest.MonkeyPatch) -> None:
    key_id = uuid.uuid4()
    group_id = uuid.uuid4()
    called: list[str] = []

    class _FakeMemberRepo:
        def __init__(self, _db: object) -> None: ...

        async def list_ordered_carried(self, gid: uuid.UUID) -> list[object]:
            called.append("carried")
            return [SimpleNamespace(key_id=key_id)]

        async def list_ordered(self, gid: uuid.UUID) -> list[object]:
            called.append("unguarded")
            return [SimpleNamespace(key_id=key_id)]

    class _FakeApiKeyRepo:
        def __init__(self, _db: object) -> None: ...

        async def get_active(self, kid: uuid.UUID) -> object:
            return SimpleNamespace(id=kid, provider=SimpleNamespace(value="openai"))

    monkeypatch.setattr(
        "contexts.keys.infrastructure.group_repository.KeyGroupMemberRepository",
        _FakeMemberRepo,
    )
    monkeypatch.setattr(
        "contexts.keys.infrastructure.repositories.ApiKeyRepository",
        _FakeApiKeyRepo,
    )

    resolved = await resolve_embed_key(object(), group_id)  # type: ignore[arg-type]

    # The active-carry listing is used; the unguarded list_ordered never is (SEC-H3).
    assert called == ["carried"]
    assert resolved == ("openai", "text-embedding-3-small", key_id)


@pytest.mark.asyncio
async def test_resolver_skips_non_embedding_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    """A non-embedding provider is skipped; the first carried embedding-capable key wins."""
    good = uuid.uuid4()

    class _FakeMemberRepo:
        def __init__(self, _db: object) -> None: ...

        async def list_ordered_carried(self, gid: uuid.UUID) -> list[object]:
            return [SimpleNamespace(key_id=uuid.uuid4()), SimpleNamespace(key_id=good)]

    class _FakeApiKeyRepo:
        def __init__(self, _db: object) -> None: ...

        async def get_active(self, kid: uuid.UUID) -> object:
            provider = "voyage" if kid == good else "anthropic"
            return SimpleNamespace(id=kid, provider=SimpleNamespace(value=provider))

    monkeypatch.setattr(
        "contexts.keys.infrastructure.group_repository.KeyGroupMemberRepository",
        _FakeMemberRepo,
    )
    monkeypatch.setattr(
        "contexts.keys.infrastructure.repositories.ApiKeyRepository",
        _FakeApiKeyRepo,
    )

    resolved = await resolve_embed_key(object(), uuid.uuid4())  # type: ignore[arg-type]
    assert resolved == ("voyage", "voyage-3", good)
