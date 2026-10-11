"""Signed download tickets for `GET /api/files/{ticket}` ([R19a.14]).

A ticket is a short-lived bearer capability for one stored object, with the
same semantics the MinIO presigned URLs it replaces had, but it points at the
backend instead of the storage plane, which no browser can reach. Design and
the trade-offs behind it: docs/tasks/2026-10-11-browser-file-delivery/spec.md.

Format: ``b64url(payload_json) "." b64url(mac)``, where the MAC is the Vault
KV HMAC seed over ``_DOMAIN + payload_json``. The payload names the seed
version, so tickets minted just before a rotation still verify.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import json
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final, Literal, Protocol
from urllib.parse import quote

from app.config.settings import get_settings
from shared_kernel.auth.clients import get_vault_client, now

TICKET_TTL: Final = timedelta(minutes=15)
PATH_PREFIX: Final = "/api/files/"
# Domain separation from the provider-secret envelopes that share the seed.
_DOMAIN: Final = b"smap-file-ticket:v1\x00"
_FORMAT_VERSION: Final = 1


class TicketInvalid(Exception):
    """Malformed, forged, signed by an unknown seed, or naming a foreign bucket."""


class TicketExpired(Exception):
    """Authentic but past its expiry."""


class _Signer(Protocol):
    def hmac_sign(self, data: bytes) -> tuple[int, bytes]: ...
    def hmac_verify(self, version: int, data: bytes, mac: bytes) -> bool: ...


def _signer() -> _Signer:
    return get_vault_client()


@dataclass(frozen=True, slots=True)
class FileTicket:
    bucket: str
    key: str
    content_type: str
    disposition: str
    expires_at: datetime
    # Identifies the ticket for rate limiting without putting the bearer
    # string itself into a Redis key.
    rate_key: str


def content_disposition(kind: Literal["inline", "attachment"], filename: str) -> str:
    """RFC 6266 value with an ASCII ``filename`` fallback and a UTF-8 ``filename*``.

    The value is sent verbatim as a response header, which Starlette encodes as
    latin-1, so a raw CJK filename would fail the response. Quotes, backslashes,
    separators and control characters are stripped so the value cannot break
    out of the header (SEC-M2).
    """
    cleaned = "".join(c for c in filename if c.isprintable() and c not in '"\\/\r\n')[:200].strip()
    cleaned = cleaned or "download"
    fallback = cleaned.encode("ascii", "ignore").decode("ascii").strip()
    if not fallback or fallback[0] in ".-_":
        fallback = "download" + fallback
    return f"{kind}; filename=\"{fallback}\"; filename*=UTF-8''{quote(cleaned, safe='')}"


def deliverable_buckets() -> frozenset[str]:
    m = get_settings().minio
    return frozenset({m.bucket_chat_uploads, m.bucket_exports, m.bucket_skill_bundles})


async def issue(
    *,
    bucket: str,
    key: str,
    content_type: str,
    disposition: str,
    ttl: timedelta = TICKET_TTL,
) -> str:
    """Return the same-origin path ``/api/files/{ticket}`` for one object."""
    if bucket not in deliverable_buckets():
        raise ValueError(f"bucket {bucket!r} is not browser-deliverable")
    expires = int((now() + ttl).timestamp())
    payload = json.dumps(
        {
            "v": _FORMAT_VERSION,
            "b": bucket,
            "k": key,
            "t": content_type,
            "d": disposition,
            "e": expires,
            # Distinct per issue, so the per-ticket rate limit is per ticket and
            # not shared by every link to one object minted in the same second.
            "n": secrets.token_urlsafe(6),
        },
        separators=(",", ":"),
        ensure_ascii=False,
        sort_keys=True,
    ).encode("utf-8")
    version, mac = await asyncio.to_thread(_signer().hmac_sign, _DOMAIN + payload)
    return f"{PATH_PREFIX}{_b64(payload)}.{_b64(version.to_bytes(2, 'big') + mac)}"


async def verify(ticket: str) -> FileTicket:
    try:
        payload_b64, sig_b64 = ticket.split(".")
        payload = _unb64(payload_b64)
        sig = _unb64(sig_b64)
    except (ValueError, binascii.Error) as exc:
        raise TicketInvalid("malformed") from exc
    if len(sig) != 2 + hashlib.sha256().digest_size:
        raise TicketInvalid("malformed")
    version, mac = int.from_bytes(sig[:2], "big"), sig[2:]
    # Authenticate before parsing anything beyond base64.
    ok = await asyncio.to_thread(_signer().hmac_verify, version, _DOMAIN + payload, mac)
    if not ok:
        raise TicketInvalid("bad signature")
    try:
        data = json.loads(payload)
        if data["v"] != _FORMAT_VERSION:
            raise TicketInvalid("unknown format")
        bucket, key, ctype, disp, exp = data["b"], data["k"], data["t"], data["d"], int(data["e"])
    except (ValueError, KeyError, TypeError) as exc:
        raise TicketInvalid("malformed payload") from exc
    if bucket not in deliverable_buckets():
        raise TicketInvalid("bucket not deliverable")
    expires_at = datetime.fromtimestamp(exp, tz=UTC)
    if now() >= expires_at:
        raise TicketExpired()
    return FileTicket(
        bucket=bucket,
        key=key,
        content_type=ctype,
        disposition=disp,
        expires_at=expires_at,
        rate_key=hashlib.sha256(mac).hexdigest()[:32],
    )


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(data: str) -> bytes:
    if not data:
        raise ValueError("empty segment")
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


__all__ = [
    "PATH_PREFIX",
    "TICKET_TTL",
    "FileTicket",
    "TicketExpired",
    "TicketInvalid",
    "content_disposition",
    "deliverable_buckets",
    "issue",
    "verify",
]
