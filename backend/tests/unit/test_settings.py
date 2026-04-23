from __future__ import annotations

from pathlib import Path

import pytest

from app.config.settings import Settings


def test_defaults_boot(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # Run from a temp cwd so a stray .env in the repo root does not bleed into the test.
    monkeypatch.chdir(tmp_path)
    s = Settings()
    assert s.minio.bucket_chat_uploads == "chat-uploads"
    assert s.minio.bucket_rag_sources == "rag-sources"
    assert s.minio.bucket_exports == "exports"
    assert s.vault.transit_key_provider == "smap-provider-secret"
    assert s.vault.transit_key_guest == "smap-guest-link"
    assert s.vault.transit_key_jwt == "smap-jwt-sign"
    assert s.app.problem_url_base == "https://smap.local/problems"
