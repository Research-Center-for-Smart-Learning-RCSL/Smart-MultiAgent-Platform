"""CAPTCHA verification — pluggable provider (R19a.12).

Supported providers:
  * `hcaptcha`  — POSTs to https://api.hcaptcha.com/siteverify
  * `turnstile` — POSTs to https://challenges.cloudflare.com/turnstile/v0/siteverify
  * `off`       — bypass (dev/test only); never set in prod.

Keys live in Vault KV `secret/smap/config/captcha`:
  { provider: "hcaptcha", secret: "…", sitekey: "…", mode: "on" }

SoC: this module has no knowledge of FastAPI. Handlers call `verify(token,
remote_ip)` and handle the raised `CaptchaError`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

import httpx
from loguru import logger

from app.config.settings import get_settings
from shared_kernel.auth.clients import get_vault_client

_KV_PATH: Final = "smap/config/captcha"
_HCAPTCHA_URL: Final = "https://api.hcaptcha.com/siteverify"
_TURNSTILE_URL: Final = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
# Strict provider → siteverify-URL allowlist (verify() fails closed on a miss).
_SITEVERIFY_URLS: Final[dict[str, str]] = {
    "hcaptcha": _HCAPTCHA_URL,
    "turnstile": _TURNSTILE_URL,
}


class CaptchaError(ValueError):
    """Raised when the CAPTCHA token is absent, malformed, or rejected."""


@dataclass(frozen=True, slots=True)
class _Config:
    provider: str
    secret: str
    mode: str  # "on" | "off"


@dataclass(frozen=True, slots=True)
class PublicCaptchaConfig:
    """The non-secret subset the frontend needs to render the widget.

    Crucially excludes the verify ``secret`` — only ``provider`` + ``sitekey``
    (both public-by-design) and the on/off ``mode`` leave the backend.
    """

    mode: str  # "on" | "off"
    provider: str  # "hcaptcha" | "turnstile" | "off"
    sitekey: str


def _load_config() -> _Config:
    raw = get_vault_client().kv_get(_KV_PATH)
    return _Config(
        provider=str(raw.get("provider", "off")).lower(),
        secret=str(raw.get("secret", "")),
        mode=str(raw.get("mode", "on")).lower(),
    )


def public_config() -> PublicCaptchaConfig:
    """Provider + sitekey + mode for ``GET /api/auth/captcha-config`` (R19a.12).

    Mirrors :func:`verify`'s test-mode bypass (the E2E stack has no Vault), and
    fails *open to off* if Vault is unreachable so the registration page can
    always render — a transient Vault outage shows no widget rather than a blank
    page. When ``mode=off`` the provider is forced to ``off`` so the SPA renders
    nothing regardless of the configured provider.
    """
    if get_settings().app.env == "test":
        return PublicCaptchaConfig(mode="off", provider="off", sitekey="")
    try:
        raw = get_vault_client().kv_get(_KV_PATH)
    except Exception:
        logger.bind(event="captcha_config_unreadable", path=_KV_PATH).warning(
            "captcha config unreadable from Vault; serving mode=off to the frontend"
        )
        return PublicCaptchaConfig(mode="off", provider="off", sitekey="")
    mode = str(raw.get("mode", "on")).lower()
    provider = str(raw.get("provider", "off")).lower()
    sitekey = str(raw.get("sitekey", ""))
    if mode == "off":
        provider = "off"
    return PublicCaptchaConfig(mode=mode, provider=provider, sitekey=sitekey)


async def verify(token: str | None, *, remote_ip: str | None) -> None:
    """Raise `CaptchaError` unless the token is valid.

    In `mode=off` this returns immediately — intended for the Playwright test
    stack and dev mode only. When the app is running with `SMAP_APP_ENV=test`,
    the bypass is automatic: Vault is not assumed to be provisioned in the
    E2E compose stack, so reading `_KV_PATH` would fail before we ever get to
    `mode`. The env check covers that without leaking into prod.
    """
    if get_settings().app.env == "test":
        return
    # Fail CLOSED: any unexpected error reading the Vault-backed config (down,
    # missing path, malformed) surfaces as a CaptchaError rather than a 500 or a
    # silent bypass. public_config() stays fail-OPEN; verify() never does.
    try:
        cfg = _load_config()
    except Exception as exc:
        raise CaptchaError(f"CAPTCHA config unavailable: {exc}") from exc
    if cfg.mode == "off":
        return
    if not token:
        raise CaptchaError("missing CAPTCHA token")

    # Strict provider allowlist: an unknown/typo provider must NOT fall through to
    # the Turnstile host (which would POST the secret to the wrong endpoint).
    url = _SITEVERIFY_URLS.get(cfg.provider)
    if url is None:
        raise CaptchaError(f"unsupported CAPTCHA provider: {cfg.provider!r}")
    payload = {"secret": cfg.secret, "response": token}
    if remote_ip:
        payload["remoteip"] = remote_ip

    async with httpx.AsyncClient(timeout=5.0) as client:
        try:
            resp = await client.post(url, data=payload)
        except httpx.HTTPError as exc:
            raise CaptchaError(f"CAPTCHA provider unreachable: {exc}") from exc
    if resp.status_code != 200:
        raise CaptchaError(f"CAPTCHA HTTP {resp.status_code}")
    body = resp.json()
    if not body.get("success"):
        raise CaptchaError(f"CAPTCHA rejected: {body.get('error-codes') or 'unknown'}")


__all__ = ["CaptchaError", "PublicCaptchaConfig", "public_config", "verify"]
