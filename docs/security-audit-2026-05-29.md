# SMAP Pre-Release Security Audit — 2026-05-29

**Auditor:** Claude (Opus 4.8), driven by the SMAP architect
**Codebase state:** `main` @ `0132ee2` (clean working tree)
**Trigger:** Hardening pass before the AGPL public release. All phases A–J are marked *CODE complete*; this audit tests that claim against the actual code.

---

## 1. Methodology

This audit does **not** rely on the phase-gate documentation. Every finding below was traced to concrete source. The pass combined:

1. **Six parallel deep reviews**, one per risk surface: crypto/secrets, authorization/tenant-isolation, web-security/injection, MCP-sandbox/SSRF, SEL/workflow-engine, auth-flows/sessions.
2. **One false-negative sweep** for server-side request forgery and injection surfaces outside the MCP egress path.
3. **Independent re-verification** of every High/Medium finding by reading the exact code (and, where version-dependent, executing the relevant function).
4. **Dependency CVE scans**: `pip-audit` (backend) and `pnpm audit --prod` (frontend).

Each finding carries a **Verification** tag:

- **VERIFIED** — re-read the exact code (and/or executed it) for this report.
- **VERIFIED (relayed)** — substantiated by a sub-audit with a file:line citation; conclusion accepted but not independently line-stepped here.
- **CONDITIONAL** — real code defect; exploitability depends on a deployment fact noted inline.

---

## 2. Executive summary

The parts of the system that are *hard to get right* are, in fact, right: the envelope-encryption core (per-row DEK, AES-GCM with AAD, plaintext DEK never leaves Vault), the JWT verify path (RS256-pinned, no `alg=none`, kid-downgrade rejected), the SEL interpreter (hand-written tree-walker, **no** `eval`/`exec`, no attribute/dunder access — no sandbox escape found), the 24-capability authz matrix, Argon2id password handling, single-use hashed tokens, and refresh-family reuse detection.

The defects cluster at the **boundaries**: network isolation of the sandbox, cross-tenant object authorization, revocation on long-lived connections, and dependency freshness. One is release-blocking.

| ID | Severity | Title | Status |
|----|----------|-------|--------|
| **C1** | 🔴 Critical | MCP sandbox egress proxy is bypassable — `egress_net` is not `internal` and no proxy/firewall is enforced | VERIFIED |
| **H1** | 🟠 High | Cross-tenant RAG/GraphRAG attachment (IDOR → document exfiltration) | VERIFIED |
| **H2** | 🟠 High | WebSocket sessions never re-check token expiry or the revocation denylist | VERIFIED |
| **H3** | 🟠 High | Revoked/withdrawn provider keys remain usable (cache + no DB authorization gate) | VERIFIED |
| **H4** | 🟠 High | Egress policy does not block CGNAT `100.64.0.0/10` despite claiming to | VERIFIED |
| **H5** | 🟠 High | No concrete `EgressProxyClient` is wired — built-in `web_search` bypasses or cannot use the proxy | VERIFIED |
| **DEP** | 🟠 High | Known-CVE dependencies (backend & frontend), several reachable from user input | VERIFIED |
| **M1** | 🟡 Medium | `TRUSTED_PROXIES` default does not match nginx's peer → per-IP controls collapse to one bucket | VERIFIED |
| **M2** | 🟡 Medium | Uploaded `Content-Type` is attacker-controlled and served inline → stored XSS | CONDITIONAL |
| **M3** | 🟡 Medium | Login timing oracle enables account enumeration | VERIFIED |
| **M4** | 🟡 Medium | Registration returns a distinct `409 email-taken` → account enumeration | VERIFIED |
| **M5** | 🟡 Medium | Docker socket mounted into backend; gVisor runtime assumed, not enforced | VERIFIED |
| **L1** | ⚪ Low | JWT `nbf` is validated only if present (defense-in-depth; always minted) | VERIFIED |
| **L2** | ⚪ Low | Password-reset tokens are not invalidated when a newer one is issued | VERIFIED |
| **L3** | ⚪ Low | `is_chatroom_participant` stub returns `True` (fail-open; currently unreachable) | VERIFIED |
| **L4** | ⚪ Low | tus `rag_source` uploads have no ACL (latent; finalize path is a no-op) | VERIFIED (relayed) |
| **L5** | ⚪ Low | SEL not parsed at save-time; `re2`→stdlib `re` fallback is ReDoS-prone if re2 missing | VERIFIED (relayed) |
| **L6** | ⚪ Info | `export_key` does not sanitize the filename (others do) | VERIFIED |
| **F1** | ⚙ Functional | `create_workflow`/`patch_workflow` omit `valid_agent_ids` → workflows referencing agents are rejected | VERIFIED |

---

## 3. Critical

### C1 — MCP sandbox egress proxy is fully bypassable

**Severity:** Critical · **Status:** VERIFIED · **Release-blocking.**

**Locations**
- `deploy/compose/docker-compose.yml:20-22` — `egress_net` declared `internal: false`
- `deploy/compose/docker-compose.yml:99,128` — host Docker socket mounted into `backend-web` and `backend-worker`
- `backend/contexts/agents/infrastructure/sandbox/docker_runsc.py:61-73` — sandbox `_base_host_config`: `network_mode: <egress_net>`, **no** `HTTP_PROXY`/`HTTPS_PROXY` env injected
- No iptables/nftables OUTPUT rule anywhere under `deploy/` (grep: none)

**What's wrong.** The threat model is an untrusted/compromised MCP tool server. The egress proxy (`backend/services/egress_proxy/app.py`) is supposed to be the *only* network path out of the sandbox, enforcing HMAC auth + an IP allowlist + RFC-1918/metadata blocking. But the sandbox container is attached only to `egress_net`, and that network is **not** `internal`, so it has a default route to the Docker bridge gateway. Nothing forces the container's traffic through the proxy: no proxy env vars, no transparent-redirect firewall rule. A hostile process inside the sandbox simply opens a raw socket.

```yaml
# docker-compose.yml:20-22
egress_net:
  name: smap_egress_net
  internal: false      # <-- container has a gateway; proxy is optional
```
```python
# docker_runsc.py:61-73 — no HTTP_PROXY, network_mode is the non-internal egress_net
def _base_host_config(self) -> dict[str, Any]:
    return {"runtime": "runsc", "network_mode": self.egress_network, ...}
```

**Exploit.** A malicious MCP server runs, from inside the sandbox:
`curl http://169.254.169.254/latest/meta-data/iam/security-credentials/` (cloud metadata / IAM creds), or connects directly to `vault:8200`, `postgres:5432`, `redis:6379`, or any public host. The proxy's allowlist, HMAC, and IP policy never see this traffic. The README's claim ("the *only* network endpoint sandbox containers can reach") is false.

**Blast radius is amplified by the Docker socket.** `backend-web`/`backend-worker` mount `/var/run/docker.sock`. Any RCE in those processes (a much larger surface than the sandbox) is full host root via the Docker API — and those containers are not themselves gVisor-isolated.

**Fix.**
1. Make `egress_net` `internal: true` (no gateway). Put the egress proxy on a *second*, non-internal network; sandboxes stay only on the internal one.
2. Inject `HTTP_PROXY`/`HTTPS_PROXY`/`NO_PROXY` into sandbox containers **and** add a default-deny `OUTPUT` iptables rule in the sandbox image (a hostile process ignores proxy env vars — the L3 chokepoint is what actually enforces it).
3. Replace the raw Docker socket mount with a scoped docker-socket-proxy that only exposes the `containers.run` verbs the runner needs.

Until an L3 chokepoint exists, every other egress control (H4, H5, the HMAC/allowlist) is advisory.

---

## 4. High

### H1 — Cross-tenant RAG / GraphRAG attachment (IDOR)

**Severity:** High · **Status:** VERIFIED

**Locations**
- `backend/contexts/agents/application/agent_service.py:66-131` (`create`), `:142-222` (`patch`)
- Exploited at `backend/contexts/knowledge/application/retrieve.py:~93` (search keyed on `cfg.project_id`) — *VERIFIED (relayed)*
- Unused guard available: `backend/contexts/knowledge/interfaces/facade.py` `get_rag_config`

**What's wrong.** `AgentService.create`/`patch` validate that `key_group_id` belongs to the agent's project (`_assert_key_group_in_project`, `agent_service.py:61-64`) but pass `rag_config_id` and `graphrag_config_id` straight through with **no** project check:

```python
# agent_service.py:91-105 — key_group is validated; rag/graphrag are not
await self._assert_key_group_in_project(key_group_id=draft.key_group_id, project_id=project_id)
...
agent = await self._agents.create(
    ...,
    rag_config_id=draft.rag_config_id,          # <-- no project validation
    graphrag_config_id=draft.graphrag_config_id, # <-- no project validation
)
```

**Exploit.** A member of Project A with `RESOURCE_CREATE_EDIT` creates/patches an agent (`POST /api/projects/{A}/agents` or `PATCH /api/agents/{id}`) with `rag_config_id` set to **Project B's** config UUID. At retrieval time the Qdrant collection is selected from the *config's* `project_id`, so the agent pulls Project B's document chunks into its context — cross-tenant data exfiltration.

**Fix.** In `create`/`patch`, when `rag_config_id`/`graphrag_config_id` is set, load it via `KnowledgeFacade.get_rag_config` (and the GraphRAG facade) and reject with 404 if `config.project_id != project_id`, mirroring `_assert_key_group_in_project`.

---

### H2 — WebSocket sessions never expire or honor revocation

**Severity:** High · **Status:** VERIFIED

**Locations**
- `backend/shared_kernel/realtime/connection.py:206-250` (`_reader` loop)
- `backend/shared_kernel/realtime/ws_auth.py` (`authenticate_subprotocol` / `refresh_principal`)

**What's wrong.** The token is verified once at the handshake. After `accept`, the only re-validation happens when the **client voluntarily** sends a `{"type":"refresh"}` frame. The server never tracks the held token's `exp` and never re-checks the jti denylist on a live socket. A client that sends only `ping` frames (`connection.py:231-232`) keeps the connection — and its event stream — alive indefinitely on a 15-minute access token.

```python
# connection.py:231-248 — refresh/denylist only run on a client-initiated 'refresh'
if mtype == "ping":
    await conn.enqueue({"type": "pong"}); continue
if mtype == "refresh":
    new_principal = await refresh_principal(token)  # the ONLY re-auth path
    ...
```

**Exploit.** A user logs out, is banned, or has a session revoked. On HTTP this is enforced immediately (the middleware re-checks the denylist and DB status every request — `shared_kernel/auth/middleware/auth.py`). On an already-open WebSocket, nothing tears it down: the revoked principal keeps receiving real-time chat/workflow/admin data until they disconnect. This breaks the server-side-revocation guarantee the HTTP path upholds.

**Fix.** Store `exp` and `jti` on the connection; in `_reader` (fold into the existing `wait_for` idle timer) periodically (a) close with 4401 once `now >= exp`, and (b) re-check `tokens.is_denied(jti)` so logout/ban/revoke tears the socket down within the access-TTL window.

---

### H3 — Revoked/withdrawn provider keys remain usable

**Severity:** High · **Status:** VERIFIED

**Locations**
- `backend/contexts/keys/application/provider_router.py:269-281` (`_load_eligible`), `:351-363` (`_unwrap_secret`, `DEK_CACHE`)
- `backend/contexts/keys/application/carry_service.py:106-138` (`withdraw`)
- `backend/contexts/keys/infrastructure/revocation_listener.py`

**What's wrong.** Key eligibility is computed purely from group membership + `get_active`:

```python
# provider_router.py:272-281 — no carry/project authorization check
members = await self._members_repo.list_ordered(group_id)
for m in members:
    key = await self._keys_repo.get_active(m.key_id)   # only 'active', no carry check
    if key is None: continue
    if capability not in _CAPS[key.provider]: continue
    eligible.append(_EligibleMember(member=m, key=key))
```

`carry_service.withdraw` only flips the carry row and publishes `key.carry_revoked` (`carry_service.py:121,138`); it does **not** remove the `key_group_members` row. So a withdrawn key is still selected and decrypted. Worse, `_unwrap_secret` caches the **plaintext provider secret** keyed by `key_id` for 60s (`DEK_CACHE`), and the only invalidation is Redis pub/sub — which is at-most-once. A message dropped during the listener's re-subscribe gap (`revocation_listener.py`) is lost permanently, so a hard-deleted key stays usable from cache for the full TTL with no backstop.

**Exploit.** (a) A Project Owner withdraws a user's carried key from a project; the provider router keeps using it. (b) Delete a key during a Redis blip; its plaintext stays live in one or more workers' caches for up to 60s.

**Fix.** Push the authorization into the DB query: require an active `key_projects` carry row scoped to the request's project before a member is eligible. Treat `DEK_CACHE` as a pure optimization — revalidate `deleted_at`/carry from the DB before serving a cached secret older than a few seconds, or move to a versioned Redis revocation set checked per call. (Note: `ProviderRequest` at `provider_router.py:~68` carries `agent_id`/`chatroom_id` but no `project_id`; the request context likely needs the project id threaded through to enforce carry scope.)

---

### H4 — Egress policy does not block CGNAT `100.64.0.0/10`

**Severity:** High · **Status:** VERIFIED (executed)

**Location:** `backend/services/egress_proxy/ip_policy.py:1-77`

**What's wrong.** The module docstring (line 6) lists "Carrier-grade NAT (`100.64.0.0/10`)" as blocked, but neither `_EXPLICIT_BLOCKED_IPS`, `_EXPLICIT_BLOCKED_NETWORKS`, nor the stdlib flags catch it. Executed against the shipped code on Python 3.12.6:

```
is_blocked_ip('100.64.0.1')      -> False
is_blocked_ip('100.127.255.254') -> False
ipaddress.ip_address('100.64.0.1').is_private -> False
```

CGNAT space backs cloud-internal load balancers, Kubernetes node/pod ranges, and Tailscale — real SSRF targets. NAT64 (`64:ff9b::/96`) is only caught incidentally by `is_reserved` and is version-dependent.

**Exploit.** Once the proxy is actually enforced (C1 fixed), an allowlisted hostname whose DNS resolves into `100.64.0.0/10` reaches internal infrastructure.

**Fix.** Add `100.64.0.0/10` and `64:ff9b::/96` to `_EXPLICIT_BLOCKED_NETWORKS`. Better: invert the gate to *allow* only `addr.is_global is True` minus the explicit metadata set, instead of enumerating special ranges.

---

### H5 — No concrete `EgressProxyClient` is wired

**Severity:** High · **Status:** VERIFIED

**Locations**
- `backend/contexts/agents/application/mcp_ports.py:73` — `EgressProxyClient` is a `Protocol` only
- `backend/contexts/agents/application/tools/web_search.py:87`, `backend/contexts/agents/infrastructure/search_adapters/tavily.py:43` — consumers
- No class implementing the protocol / setting `x-smap-egress-hmac` exists outside a test fake (grep across `backend/`)

**What's wrong.** The built-in `web_search` tool depends on an `EgressProxyClient` that signs the HMAC and sets the forward-URL header, but there is no production implementation and no composition root that builds one. Either `web_search` cannot run in production, or it is wired to call Tavily directly — in which case the IP policy/allowlist never applies to that egress path.

**Fix.** Implement and wire the concrete client (in the API/DI layer that builds the search/MCP tools) so built-in tool egress traverses the proxy; add a test that asserts the HMAC header is present. (Largely subsumed by C1: with the L3 chokepoint in place, even a direct call is forced through the proxy.)

---

### DEP — Known-CVE dependencies

**Severity:** High (aggregate) · **Status:** VERIFIED (scanned)

> **Caveat:** `pip-audit` scanned the **active interpreter environment**, which may differ from the versions pinned in the container image / lockfile. Re-run inside the built image before acting. The frontend scan used the committed `pnpm-lock.yaml`.

**Backend (`pip-audit`)** — security-relevant, user-reachable first:
| Package | Installed | Advisory | Fixed in | Relevance |
|---|---|---|---|---|
| `pypdf` | 6.5.0 | 15× CVE (2026-…) | 6.10.2 | **RAG ingests user PDFs** — DoS/parse bugs directly reachable |
| `python-jose` | 3.3.0 | PYSEC-2024-232/233, PYSEC-2025-185 | 3.4.0+ | JWT library (verify path uses Vault, but the dep is present) |
| `starlette` | 0.49.1 | PYSEC-2026-161 | 1.0.1 | Core web framework |
| `python-multipart` | 0.0.26 | CVE-2026-42561 | 0.0.27 | Form/multipart parsing (DoS) |
| `requests` | 2.32.4 | CVE-2026-25645 | 2.33.0 | HTTP client |
| `urllib3` | 2.6.3 | PYSEC-2026-141/142 | 2.7.0 | HTTP transport |

(Also flagged, likely dev/transitive: `tornado`, `ujson`, `python-dotenv`, `wheel`, `virtualenv`.)

**Frontend (`pnpm audit --prod`)** — 17 vulnerabilities (4 high, 12 moderate, 1 low):
| Package | Installed | Issue | Fixed in |
|---|---|---|---|
| `axios` | 1.15.0 | **4× high**: NO_PROXY/loopback bypass, prototype-pollution gadgets (×2), header injection | 1.15.2 |
| `mermaid` | 11.10.0 | `classDef` HTML injection in state diagrams (moderate) — **renders user markdown**; DOMPurify is the compensating control | 11.15.0 |

**Fix.** Bump the pinned versions (notably `pypdf`, `starlette`, `python-multipart`, `axios`→1.15.2, `mermaid`→11.15.0), re-run both scanners inside the production image, and add a dependency-audit step to CI.

---

## 5. Medium

### M1 — `TRUSTED_PROXIES` default does not match the proxy peer

**Severity:** Medium · **Status:** VERIFIED

**Locations:** `backend/app/config/settings.py:144` (`["127.0.0.1/32"]`), `.env.example:76` (`["127.0.0.1/32","::1/128"]`), `backend/shared_kernel/auth/trusted_proxy.py:19-45`.

The XFF parser is correct and **fail-secure** against spoofing (an untrusted peer's header is discarded). The problem is the shipped default: in the compose topology nginx reaches `backend-web` over the Docker bridge, so the backend's peer is nginx's `172.x` address — never `127.0.0.1`. `resolve_actor_ip` therefore ignores `X-Forwarded-For` and returns nginx's container IP for **every** request.

**Impact.** All users collapse into a single source IP: IP bans become all-or-nothing, and per-IP rate-limit buckets (AUTH/AUTH_RECOVERY) become one shared global bucket — a single attacker can exhaust the auth/recovery limit for everyone, and "per-IP" brute-force isolation is lost. (Per-account lockout still works.) Docs (`deploy/compose/nginx/README.md:30`, `REQUIREMENTS.md:887`) claim the default already includes the bridge subnet — it does not.

**Fix.** Ship `SMAP_SEC_TRUSTED_PROXIES` including the Docker bridge CIDR (e.g. `172.16.0.0/12`) in `.env.example` and compose, and log a startup warning when the request peer is never in the trusted list.

### M2 — Uploaded `Content-Type` is attacker-controlled and served inline (stored XSS)

**Severity:** Medium · **Status:** CONDITIONAL (same-origin MinIO)

**Locations:** `backend/contexts/conversation/application/attachment_service.py:103,149` (client `mime`→MinIO `content_type`, no allowlist), `backend/shared_kernel/storage/minio_client.py:152-165` (`presigned_get` sets no `response-content-disposition`/`response-content-type`).

A user uploads a chat attachment declaring `mime: text/html` (or `image/svg+xml` with script). MinIO stores and later serves it with that type via a presigned GET that the browser fetches directly. If a victim opens the link, the browser renders attacker HTML/SVG. Whether this yields session theft depends on whether the MinIO host shares a registrable domain / cookie scope with the SPA. (Note: the *RAG* ingest path **does** enforce a MIME allowlist — `knowledge/application/ingest_service.py` — so this is specific to chat attachments.)

**Fix.** Validate `mime` against an allowlist at upload (tus create + single-shot); force `response-content-disposition: attachment` and a safe `response-content-type` on `presigned_get`; serve uploads from a distinct cookie-less origin.

### M3 — Login timing oracle → account enumeration

**Severity:** Medium · **Status:** VERIFIED

**Location:** `backend/contexts/identity/application/auth_service.py:205-216`.

When the email does not exist, the expensive Argon2id verify is skipped (`if user is None: fail = True`); an existing account pays the full ~64 MiB/t=3 cost. The latency difference is a reliable enumeration oracle independent of the response body.

**Fix.** When `user is None`, verify the password against a fixed module-level dummy Argon2 hash so both branches pay the same cost.

### M4 — Registration enumeration via distinct `409`

**Severity:** Medium · **Status:** VERIFIED

**Location:** `backend/contexts/identity/application/auth_service.py:133-135` → `409 auth/email-taken` (`interfaces/error_mapping.py`).

`register` returns a distinct 409 when the email exists vs 201 when new — an unauthenticated enumeration endpoint. (The password-reset flow already does this correctly: always `202`.) CAPTCHA raises cost but does not remove the oracle.

**Fix.** Return 201/202 ("verification email sent if the address is new") regardless; if taken, send an out-of-band "you already have an account" email instead of signalling via HTTP status.

### M5 — Docker socket exposure + unenforced gVisor runtime

**Severity:** Medium · **Status:** VERIFIED

**Locations:** `deploy/compose/docker-compose.yml:99,128` (socket into backend-web/worker), `backend/contexts/agents/infrastructure/sandbox/docker_runsc.py:63` (`runtime: "runsc"` requested, never asserted post-create).

The runner requests `runsc` but never verifies the spawned container's effective runtime; if `runsc` is misconfigured the isolation assumption silently weakens. Combined with the raw Docker socket in the backend (see C1), an RCE in the backend is host root.

**Fix.** Inspect `HostConfig.Runtime` after create and kill on mismatch; gate sandbox spawns on the supervisor `/healthz` runsc probe; replace the raw socket with a scoped docker-socket-proxy.

---

## 6. Low / Informational

- **L1 — JWT `nbf` optional** (`shared_kernel/auth/jwt.py:140`). `if "nbf" in claims:` — a token lacking `nbf` skips the not-yet-valid check. SMAP always mints `nbf` (`jwt.py:83`), so this is defense-in-depth only. *Downgraded from Medium.* Fix: require `nbf` for access tokens.
- **L2 — Reset tokens not invalidated on reissue** (`contexts/identity/infrastructure/repositories.py:279-290`). `issue()` only inserts; up to ~5 simultaneously-valid reset tokens can exist (each single-use, 30 min). Fix: mark prior unused tokens used before inserting.
- **L3 — `is_chatroom_participant` fail-open stub** (`contexts/tenancy/interfaces/role_resolver.py:74-78`) returns `True` for any user/room. Currently unreachable (the one `ROOM_ACL` matrix caller passes no `chatroom_id` and fails closed; message/WS/export paths use the correct `conversation/application/access.py` ACL). A landmine for any future `ROOM_ACL` route. Fix: implement the real lookup or raise `NotImplementedError`.
- **L4 — tus `rag_source` has no ACL** (`backend/app/api/v1/tus.py:~87`, `tus_service.py:~245`). Latent: client-declared `rag_config_id` is accepted without a membership check, but finalize is a no-op so nothing is persisted. Becomes a cross-tenant ingestion vector the moment the feature is wired. *VERIFIED (relayed).*
- **L5 — SEL hardening gaps** (`contexts/workflow/`). No `eval`/`exec` and no sandbox escape (good). But: the linter never `parse()`s SEL expressions, so invalid/whitelist-violating expressions save and only fail at run time; the `re2`→stdlib `re` fallback is ReDoS-prone if `re2` fails to import; a malformed numeric literal raises raw `ValueError`. *VERIFIED (relayed).* Fix: parse SEL at save time; fail closed if `re2` is unavailable; confirm `import re2` at startup.
- **L6 — `export_key` filename not sanitized** (`shared_kernel/storage/minio_client.py:209-210`) unlike `chat_upload_key`/`rag_source_key`. Object keys are UUID-namespaced so not a traversal; fix for consistency.

---

## 7. Functional (non-security) issue found en route

- **F1 — Workflow save rejects agent references.** `backend/app/api/v1/workflows.py:345,379` call `WorkflowService.create`/`patch` **without** `valid_agent_ids`/`valid_chatroom_ids`/`subagent_parent_ids`. Linter rules 6/8/9 reject any id not in the (then-empty) valid set, so any workflow that references an agent or chatroom fails to save. Fail-*closed* (not a vulnerability) but likely breaks legitimate use. Confirm the service signature defaults and thread the valid-id sets through.

---

## 8. Areas verified clean (false-negative checks)

These were actively checked and found sound — recording them so the next reviewer doesn't re-litigate:

- **SQL injection — none.** Every raw-SQL site uses bound parameters (`invite_service.py:140`, all of `retention.py`); the three f-string `text()` sites interpolate only server-derived identifiers (partition names from integers / `pg_class`, extension names from a hardcoded list). FTS binds the query as a `literal` into `plainto_tsquery`.
- **XSS via markdown — sound.** Only two `v-html` sites (`ChatroomView.vue:49,108`), both behind DOMPurify with a tight allowlist (`FORBID_TAGS: script,iframe,object,embed,form,meta`); Mermaid SVG is DOMPurified before insertion; Mermaid runs `securityLevel:'strict'`. (See DEP for the Mermaid library CVE — defense-in-depth.)
- **SSRF outside MCP — none.** No RAG-source-by-URL fetcher exists (ingest is multipart bytes only); no user-overridable provider `base_url` (all hostnames are literals, provider chosen by key prefix); no webhooks; CAPTCHA endpoint is operator config; email is a logging stub.
- **Mass assignment — none.** No `extra="allow"`; request models use `extra="forbid"`; the three `**`-spread sites spread server-built dicts/typed models, never raw client input into ORM columns.
- **Crypto core — sound.** Per-row 256-bit DEK from Vault, fresh 12-byte GCM nonce, AAD binds ciphertext to table+row UUID, HMAC with `compare_digest`, plaintext DEK never crosses the Python boundary on rewrap. JWT verify rejects non-RS256, pins `typ`, requires integer `kid`, rejects kid-downgrade.
- **Authz matrix — sound.** Every id-addressable resource checked (agents aside, per H1) resolves to its parent project/org and runs a capability check before acting; impersonation read-only, last-admin guard, OC-transfer guards, and guest room-isolation are correctly enforced.
- **Token/session core — sound.** Argon2id (64 MiB/t=3/p=2), single-use SHA-256-hashed verify/reset tokens with atomic consume, refresh rotation with O(1) family-reuse kill, access-jti denylist enforced on every HTTP request, WS handshake via single-use Redis ticket (`GETDEL`). (The gap is H2, the *live* WS socket.)

---

## 9. Recommended remediation order

1. **C1** (release-blocking) — make `egress_net` internal + force sandbox egress through the proxy + scope the Docker socket.
2. **DEP** — bump `pypdf`, `starlette`, `python-multipart`, `axios`→1.15.2, `mermaid`→11.15.0; re-scan in the production image; add audit to CI.
3. **H1** — validate `rag_config_id`/`graphrag_config_id` project on agent create/patch.
4. **H2** — enforce token expiry + denylist on live WebSockets.
5. **H3** — push key carry/revocation into the DB authorization gate.
6. **H4 / H5** — block CGNAT+NAT64; wire (or remove) the concrete egress client.
7. **M1–M5** — fix the trusted-proxy default, upload MIME/disposition, login/register enumeration, Docker-socket/runtime hardening.
8. **L1–L6, F1** — defense-in-depth cleanup.

---

## 10. Appendix — verification commands run

```
# Backend test suite (mock/fixture-based; no live services)
cd backend && python -m pytest -q          # 396 passed in 43s
python -c "from app.main import app"        # imports OK, 177 routes

# Frontend
cd frontend && pnpm run typecheck           # clean
pnpm run lint                               # clean (--max-warnings=0)
pnpm run test                               # 122 passed / 52 files

# CGNAT block check (H4)
python -c "from services.egress_proxy.ip_policy import is_blocked_ip; \
           print(is_blocked_ip('100.64.0.1'))"   # -> False

# Dependency CVEs (DEP)
python -m pip_audit
cd frontend && pnpm audit --prod
```

> **Scope note.** The backend "integration" tests (231 of the 396) pass without live Postgres/Redis/Qdrant/Neo4j/MinIO/Vault — they are mock/fixture-based. No part of this system has been exercised end-to-end against the real stack (the Playwright E2E specs have not been run against `compose.test.yml`). Several findings here (C1, M1, M5) are deployment/topology issues that a live E2E bring-up would also surface. The J.∞ phase gate remains genuinely open.
