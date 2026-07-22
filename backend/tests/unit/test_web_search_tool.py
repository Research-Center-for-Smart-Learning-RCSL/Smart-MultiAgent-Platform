"""Unit tests for :class:`WebSearchTool` — E.11 / §12.4."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from contexts.agents.application.tools.web_search import WebSearchTool, _cache_key
from contexts.agents.domain.errors import (
    SearchKeyNotConfigured,
    SearchQuotaExceeded,
)
from contexts.agents.domain.mcp import SearchResult
from contexts.keys.domain.search import SearchKey, SearchProvider
from contexts.keys.infrastructure.probes.base import ProbeStatus


class _FakeAdapter:
    name = "fake"

    def __init__(self, results: list[SearchResult]) -> None:
        self.results = results
        self.calls: list[dict[str, Any]] = []

    async def search(
        self,
        query: str,
        *,
        top_k: int,
        locale: str,
        freshness: str,
        api_key: bytes,
        proxy: Any,
        project_id: uuid.UUID,
        config: dict[str, Any] | None = None,
    ) -> list[SearchResult]:
        self.calls.append(
            {
                "query": query,
                "top_k": top_k,
                "locale": locale,
                "freshness": freshness,
                "api_key": api_key,
                "project_id": project_id,
            }
        )
        return list(self.results)


class _DictCache:
    def __init__(self) -> None:
        self._store: dict[str, list[SearchResult]] = {}

    async def get(self, cache_key: str) -> list[SearchResult] | None:
        return self._store.get(cache_key)

    async def set(self, cache_key: str, results: list[SearchResult], *, ttl_s: int) -> None:
        self._store[cache_key] = list(results)


class _TokenLimiter:
    def __init__(self, tokens: int) -> None:
        self.tokens = tokens
        self.limit_seen: int | None = None

    async def try_acquire(self, *, project_id: uuid.UUID, limit_per_minute: int) -> bool:
        self.limit_seen = limit_per_minute
        if self.tokens <= 0:
            return False
        self.tokens -= 1
        return True


class _FakeProxy:
    async def request(self, **_: Any) -> tuple[int, dict[str, str], bytes]:
        return (200, {}, b"{}")


class _FakeSession:
    async def execute(self, stmt: Any) -> Any:
        class _R:
            def first(self: Any) -> None:
                return None

            def all(self: Any) -> list[Any]:
                return []

            def one(self: Any) -> None:
                return None

        return _R()


def _sk(
    provider: SearchProvider,
    *,
    is_active: bool = True,
    key_id: uuid.UUID | None = None,
    project_id: uuid.UUID | None = None,
    config: dict[str, Any] | None = None,
) -> SearchKey:
    return SearchKey(
        id=key_id or uuid.uuid4(),
        project_id=project_id or uuid.uuid4(),
        provider=provider,
        masked_preview="****",
        test_status=ProbeStatus.OK,
        test_error=None,
        last_test_at=datetime.now(tz=UTC),
        is_active=is_active,
        config={} if config is None else config,
        transit_key_version=1,
        hmac_key_version=1,
        created_at=datetime.now(tz=UTC),
        deleted_at=None,
    )


class _StubWebSearchTool(WebSearchTool):
    """Patches the DB-dependent paths so tests can run without a live session."""

    def __init__(self, *args: Any, active_key: SearchKey | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._stub_active_key = active_key

    async def _active_key(self) -> SearchKey:  # type: ignore[override]
        if self._stub_active_key is None:
            raise SearchKeyNotConfigured("no key")
        return self._stub_active_key

    async def _unwrap_search_key(self, key_id: uuid.UUID) -> bytes:  # type: ignore[override]
        return b"TEST-SECRET"


def _result(title: str) -> SearchResult:
    return SearchResult(
        title=title,
        url=f"https://example.com/{title}",
        snippet="snippet " + title,
        published_at=None,
        score=0.5,
    )


def _tool(
    *,
    project_id: uuid.UUID,
    active_key: SearchKey,
    adapter: Any,
    cache: _DictCache,
) -> _StubWebSearchTool:
    return _StubWebSearchTool(
        agent_id=uuid.uuid4(),
        project_id=project_id,
        db=_FakeSession(),  # type: ignore[arg-type]
        adapters={active_key.provider: adapter},
        cache=cache,
        rate_limiter=_TokenLimiter(10),
        proxy=_FakeProxy(),
        active_key=active_key,
    )


@pytest.mark.asyncio
async def test_missing_active_key_raises() -> None:
    tool = _StubWebSearchTool(
        agent_id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        db=_FakeSession(),  # type: ignore[arg-type]
        adapters={SearchProvider.TAVILY: _FakeAdapter([])},
        cache=_DictCache(),
        rate_limiter=_TokenLimiter(10),
        proxy=_FakeProxy(),
        active_key=None,
    )
    with pytest.raises(SearchKeyNotConfigured):
        await tool.search("anything")


@pytest.mark.asyncio
async def test_rate_limit_denies() -> None:
    adapter = _FakeAdapter([_result("a")])
    sk = _sk(SearchProvider.TAVILY)
    tool = _StubWebSearchTool(
        agent_id=uuid.uuid4(),
        project_id=sk.project_id,
        db=_FakeSession(),  # type: ignore[arg-type]
        adapters={SearchProvider.TAVILY: adapter},
        cache=_DictCache(),
        rate_limiter=_TokenLimiter(tokens=0),
        proxy=_FakeProxy(),
        active_key=sk,
    )
    with pytest.raises(SearchQuotaExceeded):
        await tool.search("hello")


@pytest.mark.asyncio
async def test_live_call_then_cache_hit() -> None:
    adapter = _FakeAdapter([_result("a"), _result("b")])
    sk = _sk(SearchProvider.TAVILY)
    cache = _DictCache()
    tool = _StubWebSearchTool(
        agent_id=uuid.uuid4(),
        project_id=sk.project_id,
        db=_FakeSession(),  # type: ignore[arg-type]
        adapters={SearchProvider.TAVILY: adapter},
        cache=cache,
        rate_limiter=_TokenLimiter(10),
        proxy=_FakeProxy(),
        active_key=sk,
    )

    first = await tool.search("hi", top_k=5, freshness="any", locale="en-US")
    assert len(first) == 2
    assert len(adapter.calls) == 1  # live

    second = await tool.search("hi", top_k=5, freshness="any", locale="en-US")
    assert len(second) == 2
    # No additional adapter call — served from cache.
    assert len(adapter.calls) == 1


@pytest.mark.asyncio
async def test_live_and_failed_searches_audit_http_status(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[dict[str, object]] = []

    class _StatusAdapter:
        name = "status"

        async def search(self, _query: str, **kwargs: Any) -> list[SearchResult]:
            await kwargs["proxy"].request(
                method="GET", url="https://example.com", project_id=kwargs["project_id"]
            )
            return [_result("live")]

    async def _capture_audit(
        _self: WebSearchTool,
        _query: str,
        _provider: SearchProvider,
        source: str,
        result_count: int,
        *,
        http_status: int | None,
    ) -> None:
        events.append({"source": source, "result_count": result_count, "http_status": http_status})

    monkeypatch.setattr(WebSearchTool, "_audit", _capture_audit)
    sk = _sk(SearchProvider.TAVILY)
    tool = _tool(
        project_id=sk.project_id,
        active_key=sk,
        adapter=_StatusAdapter(),
        cache=_DictCache(),
    )

    await tool.search("hi")

    assert events == [{"source": "live", "result_count": 1, "http_status": 200}]

    class _FailingStatusAdapter(_StatusAdapter):
        async def search(self, _query: str, **kwargs: Any) -> list[SearchResult]:
            await super().search(_query, **kwargs)
            raise RuntimeError("provider failed after HTTP response")

    failed_tool = _tool(
        project_id=sk.project_id,
        active_key=sk,
        adapter=_FailingStatusAdapter(),
        cache=_DictCache(),
    )
    with pytest.raises(RuntimeError, match="provider failed"):
        await failed_tool.search("hi")

    assert events[-1] == {"source": "live", "result_count": 0, "http_status": 200}


@pytest.mark.asyncio
async def test_cache_is_not_shared_across_projects() -> None:
    project_a = uuid.uuid4()
    project_b = uuid.uuid4()
    cache = _DictCache()
    adapter_a = _FakeAdapter([_result("project-a")])
    adapter_b = _FakeAdapter([_result("project-b")])
    tool_a = _tool(
        project_id=project_a,
        active_key=_sk(SearchProvider.TAVILY, project_id=project_a),
        adapter=adapter_a,
        cache=cache,
    )
    tool_b = _tool(
        project_id=project_b,
        active_key=_sk(SearchProvider.TAVILY, project_id=project_b),
        adapter=adapter_b,
        cache=cache,
    )

    results_a = await tool_a.search("hi", top_k=5, locale="en-US", freshness="any")
    results_b = await tool_b.search("hi", top_k=5, locale="en-US", freshness="any")

    assert [result.title for result in results_a] == ["project-a"]
    assert [result.title for result in results_b] == ["project-b"]
    assert len(adapter_a.calls) == 1
    assert len(adapter_b.calls) == 1


@pytest.mark.asyncio
async def test_cache_is_not_shared_across_key_ids_in_one_project() -> None:
    project_id = uuid.uuid4()
    cache = _DictCache()
    adapter_a = _FakeAdapter([_result("key-a")])
    adapter_b = _FakeAdapter([_result("key-b")])
    tool_a = _tool(
        project_id=project_id,
        active_key=_sk(SearchProvider.TAVILY, project_id=project_id),
        adapter=adapter_a,
        cache=cache,
    )
    tool_b = _tool(
        project_id=project_id,
        active_key=_sk(SearchProvider.TAVILY, project_id=project_id),
        adapter=adapter_b,
        cache=cache,
    )

    await tool_a.search("hi", top_k=5, locale="en-US", freshness="any")
    results_b = await tool_b.search("hi", top_k=5, locale="en-US", freshness="any")

    assert [result.title for result in results_b] == ["key-b"]
    assert len(adapter_a.calls) == 1
    assert len(adapter_b.calls) == 1


@pytest.mark.asyncio
async def test_cache_is_not_shared_across_differing_key_config() -> None:
    project_id = uuid.uuid4()
    key_id = uuid.uuid4()
    cache = _DictCache()
    adapter_a = _FakeAdapter([_result("corpus-a")])
    adapter_b = _FakeAdapter([_result("corpus-b")])
    tool_a = _tool(
        project_id=project_id,
        active_key=_sk(
            SearchProvider.GOOGLE_CSE,
            key_id=key_id,
            project_id=project_id,
            config={"cx": "corpus-a"},
        ),
        adapter=adapter_a,
        cache=cache,
    )
    tool_b = _tool(
        project_id=project_id,
        active_key=_sk(
            SearchProvider.GOOGLE_CSE,
            key_id=key_id,
            project_id=project_id,
            config={"cx": "corpus-b"},
        ),
        adapter=adapter_b,
        cache=cache,
    )

    await tool_a.search("hi", top_k=5, locale="en-US", freshness="any")
    results_b = await tool_b.search("hi", top_k=5, locale="en-US", freshness="any")

    assert [result.title for result in results_b] == ["corpus-b"]
    assert len(adapter_a.calls) == 1
    assert len(adapter_b.calls) == 1


@pytest.mark.asyncio
async def test_cache_key_is_stable_under_config_dict_ordering() -> None:
    project_id = uuid.uuid4()
    key_id = uuid.uuid4()
    cache = _DictCache()
    adapter_a = _FakeAdapter([_result("canonical")])
    adapter_b = _FakeAdapter([_result("should-not-be-called")])
    tool_a = _tool(
        project_id=project_id,
        active_key=_sk(
            SearchProvider.GOOGLE_CSE,
            key_id=key_id,
            project_id=project_id,
            config={"cx": "corpus", "safe": "active"},
        ),
        adapter=adapter_a,
        cache=cache,
    )
    tool_b = _tool(
        project_id=project_id,
        active_key=_sk(
            SearchProvider.GOOGLE_CSE,
            key_id=key_id,
            project_id=project_id,
            config={"safe": "active", "cx": "corpus"},
        ),
        adapter=adapter_b,
        cache=cache,
    )

    await tool_a.search("hi", top_k=5, locale="en-US", freshness="any")
    results_b = await tool_b.search("hi", top_k=5, locale="en-US", freshness="any")

    assert [result.title for result in results_b] == ["canonical"]
    assert len(adapter_a.calls) == 1
    assert len(adapter_b.calls) == 0


def test_cache_key_is_project_prefixed() -> None:
    project_id = uuid.uuid4()
    key = _sk(SearchProvider.TAVILY, project_id=project_id)

    cache_key = _cache_key(project_id, key, "hi", 5, "en-US", "any")

    assert cache_key.startswith(f"search:{project_id}:")
    assert not cache_key.startswith("search:rl:")


@pytest.mark.asyncio
async def test_four_kb_truncation() -> None:
    # Each result is ~1 KB, so >4 should be dropped after the 4 KB budget.
    big_snip = "x" * 900
    many = [
        SearchResult(
            title=f"t{i}",
            url=f"https://example.com/{i}",
            snippet=big_snip,
            published_at=None,
            score=0.1,
        )
        for i in range(20)
    ]
    adapter = _FakeAdapter(many)
    sk = _sk(SearchProvider.TAVILY)
    tool = _StubWebSearchTool(
        agent_id=uuid.uuid4(),
        project_id=sk.project_id,
        db=_FakeSession(),  # type: ignore[arg-type]
        adapters={SearchProvider.TAVILY: adapter},
        cache=_DictCache(),
        rate_limiter=_TokenLimiter(10),
        proxy=_FakeProxy(),
        active_key=sk,
    )
    out = await tool.search("hi", top_k=20, freshness="any", locale="en-US")
    # Each entry json size ~950+; 4096 / 950 ≈ 4 entries.
    assert len(out) <= 5
    serialised = json.dumps(
        [
            {
                "title": r.title,
                "url": r.url,
                "snippet": r.snippet,
                "published_at": None,
                "score": r.score,
            }
            for r in out
        ],
        separators=(",", ":"),
    ).encode("utf-8")
    assert len(serialised) <= 4096


@pytest.mark.asyncio
async def test_unregistered_provider_treated_as_not_configured() -> None:
    sk = _sk(SearchProvider.BRAVE)  # Only Tavily is registered in this test.
    tool = _StubWebSearchTool(
        agent_id=uuid.uuid4(),
        project_id=sk.project_id,
        db=_FakeSession(),  # type: ignore[arg-type]
        adapters={SearchProvider.TAVILY: _FakeAdapter([])},
        cache=_DictCache(),
        rate_limiter=_TokenLimiter(10),
        proxy=_FakeProxy(),
        active_key=sk,
    )
    with pytest.raises(SearchKeyNotConfigured):
        await tool.search("hi")
