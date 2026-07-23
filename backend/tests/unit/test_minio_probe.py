"""Unit tests for the MinIO readiness probe.

The probe must gate readiness on *all* buckets `smap.bootstrap minio-init`
provisions, not just chat-uploads: a MinIO that predates a newer feature's
bucket is live but every upload to the missing bucket 500s with NoSuchBucket.
"""

from __future__ import annotations

import types

import pytest

from shared_kernel.infra.probes import minio as probe_mod

_BUCKETS = {
    "bucket_chat_uploads": "chat-uploads",
    "bucket_rag_sources": "rag-sources",
    "bucket_knowmap_sources": "knowmap-sources",
    "bucket_exports": "exports",
    "bucket_agent_workspace": "agent-workspace",
    "bucket_prompt_assistant_files": "prompt-assistant-files",
    "bucket_skill_bundles": "skill-bundles",
}


def _settings() -> types.SimpleNamespace:
    return types.SimpleNamespace(minio=types.SimpleNamespace(**_BUCKETS))


class _FakeClient:
    def __init__(self, existing: set[str]) -> None:
        self._existing = existing

    def bucket_exists(self, name: str) -> bool:
        return name in self._existing


def _patch_client(monkeypatch: pytest.MonkeyPatch, existing: set[str]) -> None:
    monkeypatch.setattr(probe_mod, "_client", lambda _settings: _FakeClient(existing))


class TestProbeMinio:
    async def test_ready_when_all_buckets_present(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_client(monkeypatch, set(_BUCKETS.values()))
        result = await probe_mod.probe_minio(_settings())
        assert result.ok is True
        assert result.detail is None

    async def test_not_ready_and_names_the_missing_bucket(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # The exact regression: knowmap-sources absent (the newer feature's bucket).
        present = set(_BUCKETS.values()) - {"knowmap-sources"}
        _patch_client(monkeypatch, present)
        result = await probe_mod.probe_minio(_settings())
        assert result.ok is False
        assert result.detail is not None
        assert "knowmap-sources" in result.detail

    async def test_reports_every_missing_bucket(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_client(monkeypatch, {"chat-uploads"})
        result = await probe_mod.probe_minio(_settings())
        assert result.ok is False
        assert result.detail is not None
        for missing in ("rag-sources", "knowmap-sources", "skill-bundles"):
            assert missing in result.detail
