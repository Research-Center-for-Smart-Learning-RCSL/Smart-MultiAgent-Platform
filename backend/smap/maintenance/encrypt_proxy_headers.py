"""Encrypt legacy plaintext proxy_headers left by migration 0088.

Migration 0088 copies proxy_headers from the config JSONB into
encrypted_proxy_headers, but cannot Vault-encrypt them (Vault may be
sealed at migration time). This command re-encrypts any rows that
still hold unencrypted (legacy) proxy_headers in encrypted_proxy_headers.

A row is considered unencrypted if its JSONB lacks the "ct" key that
the envelope serializer always produces.

Idempotent: already-encrypted rows are skipped. Dry-run by default.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy import create_engine

from contexts.keys.infrastructure import tables as t
from shared_kernel.security import envelope as env

_ARMED_ENV = "SMAP_ENCRYPT_PROXY_HEADERS_ARMED"


@dataclass(frozen=True, slots=True)
class EncryptReport:
    dry_run: bool
    scanned: int
    already_encrypted: int
    encrypted: int
    failed: int


def armed() -> bool:
    return os.environ.get(_ARMED_ENV, "").strip().lower() in ("1", "true", "yes")


def run() -> EncryptReport:
    from app.config.settings import get_settings

    settings = get_settings()
    engine = create_engine(settings.database.dsn.replace("+asyncpg", "+psycopg2"))
    dry_run = not armed()
    scanned = 0
    already_encrypted = 0
    encrypted = 0
    failed = 0

    with engine.begin() as conn:
        rows = conn.execute(
            sa.select(t.api_keys.c.id, t.api_keys.c.encrypted_proxy_headers).where(
                t.api_keys.c.encrypted_proxy_headers.is_not(None)
            )
        ).all()

        for row in rows:
            scanned += 1
            ph_jsonb = row.encrypted_proxy_headers
            if not isinstance(ph_jsonb, dict) or not ph_jsonb:
                continue
            if "ct" in ph_jsonb:
                already_encrypted += 1
                continue
            if dry_run:
                encrypted += 1
                continue
            try:
                key_id: uuid.UUID = row.id
                encrypted_envelope = env.encrypt_proxy_headers(ph_jsonb, key_id)
                conn.execute(
                    t.api_keys.update()
                    .where(t.api_keys.c.id == key_id)
                    .values(encrypted_proxy_headers=encrypted_envelope)
                )
                encrypted += 1
            except Exception:
                failed += 1

    engine.dispose()
    return EncryptReport(
        dry_run=dry_run,
        scanned=scanned,
        already_encrypted=already_encrypted,
        encrypted=encrypted,
        failed=failed,
    )
