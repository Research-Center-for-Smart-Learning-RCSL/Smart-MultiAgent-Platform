"""SEC-H3 regression — provider-call eligibility must require an active carry.

The bug: ``ProviderRouter._load_eligible`` selected keys from bare group
membership (``list_ordered``), so a withdrawn carry (Project Owner pulling a
user's key, or the membership-removal fanout) left the key selectable and
decryptable. The fix routes eligibility through ``list_ordered_carried``,
which joins ``key_projects`` and drops any key not actively carried into the
group's project — re-evaluated from the DB on every call, so it does not
depend on the in-process DEK cache being invalidated in time.

This test proves the router consults the carry-filtered membership and does
*not* fall back to the unfiltered list.
"""

from __future__ import annotations

import uuid

from contexts.keys.application.provider_router import ProviderRouter
from contexts.keys.domain.providers import ApiKeyProvider, ProviderCapability


class _Member:
    def __init__(self, key_id: uuid.UUID) -> None:
        self.key_id = key_id


class _Key:
    def __init__(self, key_id: uuid.UUID, provider: ApiKeyProvider) -> None:
        self.id = key_id
        self.provider = provider


class _FakeMembersRepo:
    """`list_ordered_carried` returns only carried members; `list_ordered`
    returns everything. The router must call the former."""

    def __init__(self, carried: list[_Member], everything: list[_Member]) -> None:
        self._carried = carried
        self._all = everything
        self.calls: list[str] = []

    async def list_ordered(self, _group_id: uuid.UUID) -> list[_Member]:
        self.calls.append("list_ordered")
        return self._all

    async def list_ordered_carried(self, _group_id: uuid.UUID) -> list[_Member]:
        self.calls.append("list_ordered_carried")
        return self._carried


class _FakeKeysRepo:
    def __init__(self, active: dict[uuid.UUID, _Key]) -> None:
        self._active = active

    async def get_active(self, key_id: uuid.UUID) -> _Key | None:
        return self._active.get(key_id)


def _router(members_repo: _FakeMembersRepo, keys_repo: _FakeKeysRepo) -> ProviderRouter:
    router = ProviderRouter.__new__(ProviderRouter)
    router._members_repo = members_repo  # type: ignore[attr-defined]
    router._keys_repo = keys_repo  # type: ignore[attr-defined]
    return router


async def test_load_eligible_excludes_withdrawn_key() -> None:
    carried_kid = uuid.uuid4()
    withdrawn_kid = uuid.uuid4()
    members_repo = _FakeMembersRepo(
        carried=[_Member(carried_kid)],
        everything=[_Member(carried_kid), _Member(withdrawn_kid)],
    )
    keys_repo = _FakeKeysRepo(
        {
            carried_kid: _Key(carried_kid, ApiKeyProvider.OPENAI),
            withdrawn_kid: _Key(withdrawn_kid, ApiKeyProvider.OPENAI),
        }
    )
    router = _router(members_repo, keys_repo)

    eligible = await router._load_eligible(uuid.uuid4(), ProviderCapability.LLM_CHAT)

    assert [em.key.id for em in eligible] == [carried_kid]
    # Must use the carry-filtered query, and must NOT fall back to the
    # unfiltered membership list.
    assert "list_ordered_carried" in members_repo.calls
    assert "list_ordered" not in members_repo.calls


async def test_load_eligible_empty_when_nothing_carried() -> None:
    only_kid = uuid.uuid4()
    members_repo = _FakeMembersRepo(carried=[], everything=[_Member(only_kid)])
    keys_repo = _FakeKeysRepo({only_kid: _Key(only_kid, ApiKeyProvider.OPENAI)})
    router = _router(members_repo, keys_repo)

    eligible = await router._load_eligible(uuid.uuid4(), ProviderCapability.LLM_CHAT)

    assert eligible == []
