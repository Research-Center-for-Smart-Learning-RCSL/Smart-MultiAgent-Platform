"""`smap.bootstrap minio-init` — buckets + lifecycle + scoped service account.

Exactly three buckets per §21.5:
  * `chat-uploads` — 3-day expiration (R13.10)
  * `rag-sources` — kept as long as the `rag_documents` row lives
  * `exports` — 24-hour expiration (MinIO minimum is one day; equivalent)

A dedicated MinIO service account is created with a canned IAM-style policy
scoped to just these three buckets. Its access_key/secret_key are written to
Vault KV `secret/smap/config/minio`; the runtime backend reads them via the
Vault client at startup and never touches root credentials.
"""

from __future__ import annotations

import json
import secrets
from typing import Any

from minio import Minio
from minio.commonconfig import ENABLED, Filter
from minio.error import S3Error
from minio.lifecycleconfig import Expiration, LifecycleConfig, Rule
from minio.credentials import StaticProvider
from minio.minioadmin import MinioAdmin

from app.config.settings import Settings

from ._common import BootstrapReport
from .vault_init import seed_minio_service_account


def _policy_document(settings: Settings) -> dict[str, Any]:
    buckets = (
        settings.minio.bucket_chat_uploads,
        settings.minio.bucket_rag_sources,
        settings.minio.bucket_exports,
    )
    obj_arns = [f"arn:aws:s3:::{b}/*" for b in buckets]
    bucket_arns = [f"arn:aws:s3:::{b}" for b in buckets]
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": ["s3:PutObject", "s3:GetObject", "s3:DeleteObject"],
                "Resource": obj_arns,
            },
            {
                "Effect": "Allow",
                "Action": ["s3:ListBucket"],
                "Resource": bucket_arns,
            },
        ],
    }


def _lifecycle(name: str, *, days: int) -> LifecycleConfig:
    return LifecycleConfig(
        [
            Rule(
                rule_id=f"{name}-expire",
                status=ENABLED,
                rule_filter=Filter(prefix=""),
                expiration=Expiration(days=days),
            )
        ]
    )


def _ensure_bucket(
    client: Minio,
    name: str,
    *,
    lifecycle: LifecycleConfig | None,
    report: BootstrapReport,
) -> None:
    if client.bucket_exists(name):
        report.already(f"bucket:{name}")
    else:
        client.make_bucket(name)
        report.did(f"bucket:{name}")
    if lifecycle is None:
        return
    try:
        existing = client.get_bucket_lifecycle(name)
    except S3Error as exc:
        if exc.code != "NoSuchLifecycleConfiguration":
            raise
        existing = None
    if existing is None:
        client.set_bucket_lifecycle(name, lifecycle)
        report.did(f"lifecycle:{name}")
    else:
        client.set_bucket_lifecycle(name, lifecycle)  # reconcile, idempotent
        report.already(f"lifecycle:{name}")


def _data_client(settings: Settings) -> Minio:
    return Minio(
        settings.minio.endpoint,
        access_key=settings.minio.root_access_key,
        secret_key=settings.minio.root_secret_key,
        secure=settings.minio.use_tls,
        region=settings.minio.region,
    )


def _admin_client(settings: Settings) -> MinioAdmin:
    creds = StaticProvider(
        access_key=settings.minio.root_access_key,
        secret_key=settings.minio.root_secret_key,
    )
    return MinioAdmin(
        settings.minio.endpoint, credentials=creds, secure=settings.minio.use_tls
    )


def run(
    settings: Settings,
    *,
    root_token: str | None = None,
) -> BootstrapReport:
    report = BootstrapReport(subcommand="minio-init")

    data = _data_client(settings)
    _ensure_bucket(
        data,
        settings.minio.bucket_chat_uploads,
        lifecycle=_lifecycle(
            settings.minio.bucket_chat_uploads,
            days=settings.minio.chat_uploads_expiry_days,
        ),
        report=report,
    )
    _ensure_bucket(data, settings.minio.bucket_rag_sources, lifecycle=None, report=report)
    _ensure_bucket(
        data,
        settings.minio.bucket_exports,
        # MinIO lifecycle is day-granular; 1 day == 24 hours (§21.5).
        lifecycle=_lifecycle(settings.minio.bucket_exports, days=1),
        report=report,
    )

    admin = _admin_client(settings)

    # Service account with **inline** policy — we intentionally do NOT publish
    # a canned server-side policy (MinIO's `policy_add` takes a file path, not
    # a dict, and splitting the policy off the account buys nothing here).
    # The inline policy is the exact same IAM-style document as would be
    # installed server-side; it scopes the account to the three buckets only.
    policy_str = json.dumps(_policy_document(settings))
    new_secret = secrets.token_urlsafe(32)
    try:
        admin.add_service_account(
            access_key=settings.minio.service_account_name,
            secret_key=new_secret,
            name=settings.minio.service_account_name,
            description="SMAP backend scoped account (chat-uploads/rag-sources/exports).",
            policy=policy_str,
        )
    except Exception as exc:  # noqa: BLE001 — MinIO error taxonomy is open-ended
        msg = str(exc).lower()
        if "exists" in msg or "already" in msg or "duplicate" in msg:
            report.already(f"service-account:{settings.minio.service_account_name}")
            return report
        raise

    report.did(f"service-account:{settings.minio.service_account_name}")
    seed_minio_service_account(
        settings,
        access_key=settings.minio.service_account_name,
        secret_key=new_secret,
        root_token=root_token,
    )
    report.did("vault:kv:smap/config/minio")
    return report
