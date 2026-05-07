"""`smap.bootstrap vault-approle` — create both AppRoles, emit role/secret ids.

Replaying: role settings are reconciled; a new secret_id is minted only if the
role was just created OR `--rotate-secret-id` is passed. We intentionally do
not auto-rotate on every run, because that would break any backend currently
logged in via the old secret_id.
"""

from __future__ import annotations

from dataclasses import dataclass

import hvac

from app.config.settings import Settings

from ._common import BootstrapReport
from .vault_init import _client


@dataclass(frozen=True, slots=True)
class AppRoleCredentials:
    name: str
    role_id: str
    secret_id: str | None  # None iff we chose not to rotate on a replay


_ROLE_SETTINGS: dict[str, dict[str, object]] = {
    "smap-backend": {
        "token_policies": ["smap-backend"],
        "token_ttl": 3600,
        "token_max_ttl": 86400,
        "token_num_uses": 0,
        "secret_id_num_uses": 0,
        "secret_id_ttl": 0,
        "bind_secret_id": True,
        "local_secret_ids": False,
    },
    "smap-rotation": {
        "token_policies": ["smap-rotation"],
        "token_ttl": 1800,
        "token_max_ttl": 7200,
        "bind_secret_id": True,
    },
}


def _role_exists(client: hvac.Client, name: str) -> bool:
    try:
        client.auth.approle.read_role(role_name=name)
        return True
    except hvac.exceptions.InvalidPath:
        return False


def run(
    settings: Settings,
    *,
    root_token: str | None = None,
    rotate_secret_id: bool = False,
) -> tuple[BootstrapReport, list[AppRoleCredentials]]:
    report = BootstrapReport(subcommand="vault-approle")
    client = _client(settings, root_token)

    creds: list[AppRoleCredentials] = []
    for name, params in _ROLE_SETTINGS.items():
        existed = _role_exists(client, name)
        client.auth.approle.create_or_update_approle(role_name=name, **params)
        if existed:
            report.already(f"approle:{name}")
        else:
            report.did(f"approle:{name}")

        role_id = client.auth.approle.read_role_id(role_name=name)["data"]["role_id"]
        secret_id: str | None = None
        if not existed or rotate_secret_id:
            secret_id = client.auth.approle.generate_secret_id(role_name=name)["data"]["secret_id"]
            report.did(f"approle:{name}:secret_id")
        else:
            report.already(f"approle:{name}:secret_id")
        creds.append(AppRoleCredentials(name=name, role_id=role_id, secret_id=secret_id))

    return report, creds
