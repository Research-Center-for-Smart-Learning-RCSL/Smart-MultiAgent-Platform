"""Egress proxy smoke test (R12.04).

Verifies two properties of the running egress proxy:

  1. Unauthenticated request → 401 with correct JSON error type.
  2. Authenticated request with RFC 1918 destination → 403 (IP policy block).

Exit 0 on pass, non-zero on fail.  Intended to run as a one-shot container
service in compose.test.yml (profile: smoke).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import urllib.error
import urllib.request
import uuid

PROXY_URL = os.environ.get("EGRESS_PROXY_URL", "http://egress-proxy:8080")
SECRET = bytes.fromhex(os.environ["EGRESS_PROXY_SHARED_SECRET"])

PROJECT_ID = str(uuid.uuid4())
SIG = hmac.new(SECRET, PROJECT_ID.encode("ascii"), hashlib.sha256).hexdigest()


def _fail(label: str, msg: str) -> None:
    print(f"FAIL {label}: {msg}", file=sys.stderr)
    sys.exit(1)


# ------ Test 1: missing HMAC headers → 401 ------
try:
    urllib.request.urlopen(PROXY_URL + "/")
    _fail("test1", "expected 401, got 200")
except urllib.error.HTTPError as exc:
    if exc.code != 401:
        _fail("test1", f"expected 401, got {exc.code}")
    body = json.loads(exc.read())
    if body.get("type") != "urn:smap:error:mcp-egress-denied":
        _fail("test1", f"wrong error type: {body}")

print("PASS test1: unauthenticated request rejected with 401")

# ------ Test 2: valid HMAC + RFC 1918 target → 403 ------
req = urllib.request.Request(
    PROXY_URL + "/",
    headers={
        "x-smap-project-id": PROJECT_ID,
        "x-smap-egress-hmac": SIG,
        "x-smap-egress-url": "http://192.168.1.1/",
    },
)
try:
    urllib.request.urlopen(req)
    _fail("test2", "RFC 1918 target was NOT blocked")
except urllib.error.HTTPError as exc:
    if exc.code != 403:
        _fail("test2", f"expected 403, got {exc.code}")
    body = json.loads(exc.read())
    if body.get("type") != "urn:smap:error:mcp-egress-denied":
        _fail("test2", f"wrong error type: {body}")

print("PASS test2: RFC 1918 destination correctly blocked with 403")
print("All egress proxy smoke tests passed.")
