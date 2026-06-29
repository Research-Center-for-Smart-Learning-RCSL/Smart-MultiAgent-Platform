#!/usr/bin/env python3
"""Verify that audit_logs.actor_ip records the real client IP, not a proxy hop.

Behind a reverse proxy (nginx, and on staging an additional Nginx Proxy Manager
layer) the backend only ever sees a proxy's peer address. It reconstructs the
real client IP from X-Forwarded-For, trusting only the CIDRs in
SMAP_SEC_TRUSTED_PROXIES. If that trust list is misconfigured the header is
discarded and every client collapses to a single internal IP (SEC-M1) — which
silently defeats per-IP bans, rate limits, and the audit trail.

This script proves what actually gets stored. Run it FROM A REAL CLIENT MACHINE
(e.g. your workstation), NOT from the server host, so the source IP is a genuine
external address:

    python verify-actor-ip.py \
        --base-url https://smap.rcsl.online \
        --email admin@example.com

It logs in (emitting an `auth.login.success` audit row), reads that row back via
the admin audit API, and compares the stored actor_ip against this machine's
public IP. Stdlib only — no pip install required.

Exit code 0 = real client IP recorded; 1 = collapsed/mismatch; 2 = error.
"""

from __future__ import annotations

import argparse
import getpass
import ipaddress
import json
import os
import ssl
import sys
import urllib.error
import urllib.request

_PUBLIC_IP_SERVICES = (
    "https://api.ipify.org",
    "https://checkip.amazonaws.com",
    "https://ifconfig.me/ip",
)


def _http_json(
    url: str,
    *,
    method: str = "GET",
    body: dict | None = None,
    token: str | None = None,
    ctx: ssl.SSLContext | None = None,
    timeout: float = 15.0,
) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        raw = resp.read().decode()
    return json.loads(raw) if raw else {}


def _public_ip(ctx: ssl.SSLContext | None) -> str | None:
    for url in _PUBLIC_IP_SERVICES:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "smap-verify/1"})
            with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
                ip = resp.read().decode().strip()
            ipaddress.ip_address(ip)  # validate
            return ip
        except Exception:  # noqa: BLE001 — best-effort across fallbacks
            continue
    return None


def _classify(actor_ip: str | None, public_ip: str | None) -> tuple[int, str]:
    """Return (exit_code, verdict_message)."""
    if not actor_ip:
        return 2, "Audit row has NO actor_ip (null). Cannot evaluate."
    try:
        addr = ipaddress.ip_address(actor_ip)
    except ValueError:
        return 2, f"Stored actor_ip {actor_ip!r} is not a valid IP."

    if public_ip and actor_ip == public_ip:
        return 0, f"PASS — actor_ip == your public IP ({actor_ip}). Real client IP is recorded."

    if addr.is_private or addr.is_loopback:
        return 1, (
            f"FAIL — actor_ip is an INTERNAL address ({actor_ip}). X-Forwarded-For is "
            f"being discarded: clients are collapsing to a proxy/bridge IP. Add the "
            f"upstream-proxy subnet to SMAP_SEC_TRUSTED_PROXIES."
        )

    if not public_ip:
        # actor_ip is a public address but we could not learn this machine's
        # public IP to compare against — cannot decide, don't cry wolf.
        return 2, (
            f"INCONCLUSIVE — actor_ip is a public address ({actor_ip}) but this "
            f"machine's public IP could not be determined (no outbound?). Compare "
            f"manually against your real client IP."
        )

    # Public but not equal to our detected public IP — likely the front proxy's
    # own public egress IP (collapse one hop too early), or NAT/CGNAT disagreeing.
    return 1, (
        f"MISMATCH — actor_ip is a PUBLIC address ({actor_ip}) but not your detected "
        f"public IP ({public_ip}). Most likely the front proxy's IP — the chain stops "
        f"one hop short. Add that proxy's subnet to SMAP_SEC_TRUSTED_PROXIES."
    )


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--base-url", required=True, help="e.g. https://smap.rcsl.online")
    p.add_argument("--email", required=True, help="Admin account email")
    p.add_argument("--password", default=None, help="Admin password (prompted if omitted)")
    p.add_argument(
        "--insecure",
        action="store_true",
        help="Skip TLS verification (only for internal self-signed endpoints)",
    )
    args = p.parse_args()

    base = args.base_url.rstrip("/")
    password = args.password or os.environ.get("SMAP_ADMIN_PASSWORD") or getpass.getpass("Admin password: ")
    ctx = ssl._create_unverified_context() if args.insecure else None

    # 1. Log in — this emits an auth.login.success audit row stamped with the
    #    actor_ip the backend resolved for THIS request.
    try:
        tokens = _http_json(
            f"{base}/api/auth/login",
            method="POST",
            body={"email": args.email, "password": password},
            ctx=ctx,
        )
    except urllib.error.HTTPError as e:
        print(f"Login failed: HTTP {e.code} {e.reason}", file=sys.stderr)
        return 2
    except Exception as e:  # noqa: BLE001
        print(f"Login request error: {e}", file=sys.stderr)
        return 2

    token = tokens.get("access_token")
    if not token:
        print("Login response had no access_token.", file=sys.stderr)
        return 2

    # 2. Identify our own user id so we read back OUR login, not another admin's.
    actor_user_id = None
    try:
        me = _http_json(f"{base}/api/auth/me", token=token, ctx=ctx)
        actor_user_id = me.get("id")
    except Exception:  # noqa: BLE001 — fall back to action-only filter
        pass

    # 3. Read the most recent auth.login.success row (ours).
    query = f"{base}/api/admin/audit?action=auth.login.success&limit=5"
    if actor_user_id:
        query += f"&actor_user_id={actor_user_id}"
    try:
        page = _http_json(query, token=token, ctx=ctx)
    except urllib.error.HTTPError as e:
        hint = " (account is not an admin?)" if e.code == 403 else ""
        print(f"Audit query failed: HTTP {e.code} {e.reason}{hint}", file=sys.stderr)
        return 2
    except Exception as e:  # noqa: BLE001
        print(f"Audit query error: {e}", file=sys.stderr)
        return 2

    items = page.get("items") or []
    if not items:
        print("No auth.login.success rows found — cannot verify.", file=sys.stderr)
        return 2

    stored_ip = items[0].get("actor_ip")
    public_ip = _public_ip(ctx)

    print(f"  this machine's public IP : {public_ip or '<could not determine>'}")
    print(f"  stored audit actor_ip    : {stored_ip}")
    code, verdict = _classify(stored_ip, public_ip)
    print()
    print(verdict)
    return code


if __name__ == "__main__":
    sys.exit(main())
