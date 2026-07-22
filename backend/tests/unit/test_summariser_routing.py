from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from contexts.agents.application.runtime.summariser import RouterSummariser
from contexts.keys.application.provider_router import ProviderCallResult
from contexts.keys.domain.providers import ApiKeyProvider


class _FakeRouter:
    def __init__(self) -> None:
        self.request = None

    async def call(self, *, group_id, request):
        self.request = request
        return ProviderCallResult(200, {"text": "summary"})


@pytest.mark.asyncio
async def test_summariser_pins_provider_and_model() -> None:
    router = _FakeRouter()
    summariser = RouterSummariser(
        router=router,
        key_group_id=uuid.uuid4(),
        provider=ApiKeyProvider.CLAUDE,
        model="claude-opus-4-8",
    )

    result = await summariser.summarise([SimpleNamespace(role="user", content="hello")])

    assert result == "summary"
    assert router.request.provider is ApiKeyProvider.CLAUDE
    assert router.request.payload["model"] == "claude-opus-4-8"
    assert "models" not in router.request.payload
