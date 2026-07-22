from __future__ import annotations

import pytest

from contexts.agents.infrastructure.search_cache import RedisSearchCache


class _FakeRedis:
    def __init__(self, value: bytes | str) -> None:
        self._value = value

    async def get(self, _cache_key: str) -> bytes | str:
        return self._value


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [b"\xff", '{"bad": 1}'])
async def test_corrupt_cache_values_degrade_to_miss(
    monkeypatch: pytest.MonkeyPatch, value: bytes | str
) -> None:
    import shared_kernel.auth.clients as clients

    monkeypatch.setattr(clients, "get_redis", lambda: _FakeRedis(value))

    assert await RedisSearchCache().get("search:test") is None
