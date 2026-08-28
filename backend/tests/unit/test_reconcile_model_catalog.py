"""`smap.maintenance.reconcile_model_catalog` diff logic (Q-4, AC-11).

The live HTTP half needs a real provider key and is out of scope for the unit
tier; only the pure diff, the pagination-cursor parser, and the
provider-mismatch guard (mocked session/facade, no real key) are covered here.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import pytest

from smap.maintenance import reconcile_model_catalog as rmc
from smap.maintenance.reconcile_model_catalog import _parse_page, diff_against_upstream


def test_diff_reports_no_disagreement_when_upstream_matches_the_table() -> None:
    upstream = frozenset({"claude-opus-4-8", "claude-sonnet-4-6", "claude-haiku-4-5"})
    report = diff_against_upstream("claude", upstream)
    assert report.stale == frozenset()
    assert report.unseen == frozenset()


def test_diff_reports_a_catalogued_model_no_longer_served_as_stale() -> None:
    upstream = frozenset({"gpt-5.5", "gpt-5.4-mini", "o3", "o3-mini"})  # gpt-5.4 missing upstream
    report = diff_against_upstream("openai", upstream)
    assert report.stale == frozenset({"gpt-5.4"})
    assert report.unseen == frozenset()


def test_diff_reports_a_served_model_not_yet_catalogued_as_unseen() -> None:
    upstream = frozenset({"gemini-3.5-flash", "gemini-2.5-pro", "gemini-2.5-flash", "gemini-3.7-flash"})
    report = diff_against_upstream("gemini", upstream)
    assert report.stale == frozenset()
    assert report.unseen == frozenset({"gemini-3.7-flash"})


# --------------------------------------------------------------------------- #
# Pagination cursor parsing (both Anthropic and Gemini paginate; OpenAI does not) #
# --------------------------------------------------------------------------- #
def test_openai_page_never_carries_a_cursor() -> None:
    ids, cursor = _parse_page("openai", {"data": [{"id": "gpt-5.4"}]})
    assert ids == frozenset({"gpt-5.4"})
    assert cursor is None


def test_claude_page_carries_last_id_only_when_has_more() -> None:
    ids, cursor = _parse_page(
        "claude", {"data": [{"id": "claude-opus-4-8"}], "has_more": True, "last_id": "claude-opus-4-8"}
    )
    assert ids == frozenset({"claude-opus-4-8"})
    assert cursor == "claude-opus-4-8"


def test_claude_last_page_carries_no_cursor() -> None:
    ids, cursor = _parse_page(
        "claude", {"data": [{"id": "claude-haiku-4-5"}], "has_more": False, "last_id": "claude-haiku-4-5"}
    )
    assert ids == frozenset({"claude-haiku-4-5"})
    assert cursor is None


def test_gemini_page_carries_next_page_token() -> None:
    ids, cursor = _parse_page(
        "gemini", {"models": [{"name": "models/gemini-3.5-flash"}], "nextPageToken": "tok"}
    )
    assert ids == frozenset({"gemini-3.5-flash"})
    assert cursor == "tok"


def test_gemini_last_page_carries_no_cursor() -> None:
    ids, cursor = _parse_page("gemini", {"models": [{"name": "models/gemini-2.5-pro"}]})
    assert ids == frozenset({"gemini-2.5-pro"})
    assert cursor is None


# --------------------------------------------------------------------------- #
# run() -- provider-mismatch guard (mocked session + facade, no real key)     #
# --------------------------------------------------------------------------- #
def _patch_session(monkeypatch: pytest.MonkeyPatch, *, key_provider: str | None) -> None:
    """Route run() at a fake session + KeysFacade whose get_key() returns a key
    for `key_provider` (or None, simulating no active key for that id)."""

    class _FakeFacade:
        def __init__(self, _db: object) -> None:
            pass

        async def get_key(self, _key_id: uuid.UUID) -> Any:
            if key_provider is None:
                return None
            return SimpleNamespace(provider=SimpleNamespace(value=key_provider))

        async def unwrap_api_key_plaintext(self, _key_id: uuid.UUID) -> bytearray:
            raise AssertionError("must not reach the key unwrap when the provider check should refuse first")

    @asynccontextmanager
    async def _sessionmaker_call() -> Any:
        yield object()

    monkeypatch.setattr(rmc, "get_sessionmaker", lambda: _sessionmaker_call)
    monkeypatch.setattr(rmc, "KeysFacade", _FakeFacade)


@pytest.mark.asyncio
async def test_run_refuses_a_key_belonging_to_a_different_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    # The exact mistake this guard exists to catch: an operator pastes the
    # wrong key id and the command would otherwise send that real secret, as
    # an Authorization header, to a provider it was never issued for.
    _patch_session(monkeypatch, key_provider="claude")
    with pytest.raises(ValueError, match="belongs to provider 'claude', not 'openai'"):
        await rmc.run(provider="openai", key_id=uuid.uuid4())


@pytest.mark.asyncio
async def test_run_refuses_a_key_id_with_no_active_key(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_session(monkeypatch, key_provider=None)
    with pytest.raises(ValueError, match="no active api_keys row"):
        await rmc.run(provider="openai", key_id=uuid.uuid4())
