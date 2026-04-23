"""`smap.bootstrap vault-init` — engines + Transit keys + KV seeds.

Pre-AppRole: this subcommand uses a root (prod) or dev token so it can create
the engines and policies. Everything after bootstrap uses AppRole exclusively.

Idempotency: every step checks before acting. Replaying the command on a
bootstrapped Vault produces only `already-present` Changes.
"""

from __future__ import annotations

import base64
import secrets
from typing import Any

import hvac

from app.config.settings import Settings
from shared_kernel.infra.vault import new_hmac_seed

from ._common import BootstrapReport


def _client(settings: Settings, root_token: str | None) -> hvac.Client:
    """Return an authenticated hvac client.

    Priority: explicit `root_token` > `settings.vault.dev_token`. AppRole is
    NOT used here because the role itself may not yet exist.
    """
    token = root_token or settings.vault.dev_token
    if not token:
        raise RuntimeError(
            "vault-init needs a root or dev token. Pass --root-token or set "
            "SMAP_VAULT_DEV_TOKEN."
        )
    client = hvac.Client(url=settings.vault.addr, token=token)
    if not client.is_authenticated():
        raise RuntimeError("Vault rejected the supplied token.")
    return client


def _ensure_mount(client: hvac.Client, path: str, backend: str, report: BootstrapReport, **opts: Any) -> None:
    mounts = client.sys.list_mounted_secrets_engines()
    # hvac returns either {"data": {...}} or the inner dict depending on version.
    table: dict[str, Any] = mounts.get("data") or mounts
    if f"{path}/" in table:
        report.already(f"mount:{path}", f"{backend}")
        return
    client.sys.enable_secrets_engine(backend_type=backend, path=path, options=opts or None)
    report.did(f"mount:{path}", f"{backend}")


def _ensure_auth(client: hvac.Client, method: str, report: BootstrapReport) -> None:
    methods = client.sys.list_auth_methods()
    table: dict[str, Any] = methods.get("data") or methods
    if f"{method}/" in table:
        report.already(f"auth:{method}")
        return
    client.sys.enable_auth_method(method_type=method, path=method)
    report.did(f"auth:{method}")


def _ensure_transit_key(
    client: hvac.Client,
    name: str,
    *,
    key_type: str,
    exportable: bool,
    deletion_allowed: bool,
    allow_plaintext_backup: bool = False,
    report: BootstrapReport,
) -> None:
    try:
        existing = client.secrets.transit.read_key(name=name)
        # Read succeeded — key exists. Reconcile config in case flags drifted.
        client.secrets.transit.update_key_configuration(
            name=name,
            deletion_allowed=deletion_allowed,
            exportable=exportable,
            allow_plaintext_backup=allow_plaintext_backup,
        )
        report.already(f"transit/keys/{name}", f"type={existing['data']['type']}")
        return
    except hvac.exceptions.InvalidPath:
        pass
    client.secrets.transit.create_key(
        name=name,
        key_type=key_type,
        exportable=exportable,
        allow_plaintext_backup=allow_plaintext_backup,
    )
    client.secrets.transit.update_key_configuration(
        name=name,
        deletion_allowed=deletion_allowed,
        exportable=exportable,
        allow_plaintext_backup=allow_plaintext_backup,
    )
    report.did(f"transit/keys/{name}", f"type={key_type}")


def _ensure_kv(
    client: hvac.Client,
    mount: str,
    path: str,
    seed: dict[str, Any],
    report: BootstrapReport,
) -> None:
    try:
        client.secrets.kv.v2.read_secret_version(
            mount_point=mount, path=path, raise_on_deleted_version=True
        )
        report.already(f"kv:{mount}/{path}")
        return
    except hvac.exceptions.InvalidPath:
        pass
    client.secrets.kv.v2.create_or_update_secret(
        mount_point=mount, path=path, secret=seed
    )
    report.did(f"kv:{mount}/{path}")


def run(settings: Settings, *, root_token: str | None = None) -> BootstrapReport:
    report = BootstrapReport(subcommand="vault-init")
    client = _client(settings, root_token)

    # --- secrets engines ---
    _ensure_mount(client, path="transit", backend="transit", report=report)
    _ensure_mount(
        client,
        path=settings.vault.kv_mount,
        backend="kv",
        report=report,
        version="2",
    )

    # --- auth methods ---
    _ensure_auth(client, method="approle", report=report)

    # --- transit keys ---
    _ensure_transit_key(
        client,
        settings.vault.transit_key_provider,
        key_type="aes256-gcm96",
        exportable=False,
        deletion_allowed=False,
        report=report,
    )
    _ensure_transit_key(
        client,
        settings.vault.transit_key_guest,
        key_type="ed25519",
        exportable=False,
        deletion_allowed=False,
        report=report,
    )
    _ensure_transit_key(
        client,
        settings.vault.transit_key_jwt,
        key_type="rsa-2048",
        exportable=False,
        deletion_allowed=False,
        report=report,
    )

    # --- policies (from the HCL files already in repo) ---
    from pathlib import Path

    policies_dir = Path(__file__).resolve().parents[3] / "deploy" / "vault" / "policies"
    for name in ("smap-backend", "smap-rotation"):
        hcl = (policies_dir / f"{name}.hcl").read_text()
        try:
            existing = client.sys.read_policy(name=name)
            existing_rules: str = (existing.get("data") or existing).get("rules", "")
            if existing_rules.strip() == hcl.strip():
                report.already(f"policy:{name}")
                continue
        except hvac.exceptions.InvalidPath:
            pass
        client.sys.create_or_update_policy(name=name, policy=hcl)
        report.did(f"policy:{name}")

    # --- KV seeds ---
    prefix = settings.vault.kv_prefix.rstrip("/")
    seeds: dict[str, dict[str, Any]] = {
        f"{prefix}/captcha": {"provider": "hcaptcha", "public_key": "", "secret_key": ""},
        f"{prefix}/smtp": {"host": "", "port": 587, "user": "", "password": ""},
        f"{prefix}/hmac-key": {"key": base64.b64encode(new_hmac_seed()).decode()},
        f"{prefix}/minio": {"access_key": "", "secret_key": ""},
    }
    for path, seed in seeds.items():
        _ensure_kv(client, settings.vault.kv_mount, path, seed, report)

    return report


def seed_minio_service_account(
    settings: Settings,
    *,
    access_key: str,
    secret_key: str,
    root_token: str | None = None,
) -> None:
    """Write the scoped MinIO service-account creds into KV after `minio-init`.

    Called by `smap.bootstrap minio-init` once the account exists.
    """
    client = _client(settings, root_token)
    client.secrets.kv.v2.create_or_update_secret(
        mount_point=settings.vault.kv_mount,
        path=f"{settings.vault.kv_prefix.rstrip('/')}/minio",
        secret={"access_key": access_key, "secret_key": secret_key},
    )


def random_secret_id() -> str:
    return secrets.token_urlsafe(32)
