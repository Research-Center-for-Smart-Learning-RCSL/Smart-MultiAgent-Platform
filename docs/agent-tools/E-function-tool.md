# Phase E — Function Tool (server-side outbound HTTP)

**Goal.** Add a `local_function` tool type: the designer declares a function
(name, description, JSON-schema parameters, and an HTTP target with optional
auth); when the agent calls it, **SMAP makes one outbound HTTP request through the
Egress Proxy** (allowlisted, IP-screened, header-stripped) and returns the
response to the model. Reuses the existing egress machinery wholesale.

**Size.** L (E.6 modifies the standalone egress-proxy service + the shared client).
**Depends on.** A (`local_function` rows + `_build_function_tool` dispatch).
**Refs.** `contexts/agents/infrastructure/egress_client.py`
(`HttpxEgressProxyClient.request`, `egress_proxy_client_from_settings`),
`services/egress_proxy/app.py` (allowlist + IP pinning + 16 MiB cap),
`contexts/agents/infrastructure/mcp_repositories.py::is_allowed`,
`mcp_service.py::seal_tool_auth/unseal_tool_auth` (A.6).

## E.0 Reused egress contract (no changes needed)

`HttpxEgressProxyClient.request(method, url, project_id, headers?, params?,
json_body?, timeout_s)` → `(status, headers, body)`. It signs
`x-smap-egress-hmac = HMAC_SHA256(secret, str(project_id))`, sets
`x-smap-egress-url`, strips `authorization/cookie`, and the proxy enforces the
per-project `mcp_egress_allowlist`, screens resolved IPs (blocks RFC1918/loopback/
link-local/metadata), pins the socket to a screened IP, and caps the response at
16 MiB. Built from `EGRESS_PROXY_SHARED_SECRET`; fails closed (`McpEgressDenied`)
if the secret is absent.

## E.1 Function `config` schema — **CODE** — S

`local_function.config` (validated server-side in `agent_service.add_tool` and
client-side via Zod `functionConfigSchema`, Phase B/E.5):

```jsonc
{
  "name": "lookup_order",                  // tool name the model sees ([a-z0-9_]{1,64})
  "description": "Look up an order by id", // model-facing description
  "parameters": { "type": "object", ... }, // JSON Schema for the model's arguments
  "http": {
    "method": "POST",                      // GET|POST|PUT|PATCH|DELETE
    "url": "https://api.example.com/orders/lookup",
    "headers": { "X-App": "smap" }         // STATIC non-secret headers only
  }
}
// auth (separate top-level field on create/patch) -> sealed into config.auth
```

**Argument → request mapping (v1 contract, documented for users):**

- The model supplies `arguments` matching `parameters`.
- For `GET`/`DELETE`: arguments become **query params**.
- For `POST`/`PUT`/`PATCH`: arguments become the **JSON body**.
- No URL templating in v1 (keeps SSRF surface minimal — the host is fixed and
  allowlisted). A later version may add `{placeholder}` path templating.

**Auth — bearer + custom header, via the signed upstream-auth channel (E.6).**
`auth` (plaintext in, sealed at rest) supports `{"type":"bearer","token":"…"}`
(→ `Authorization: Bearer …`) and `{"type":"header","name":"X-Api-Key","value":"…"}`.
The catch both kinds hit: the egress client
(`contexts/agents/infrastructure/egress_client.py:35,72` —
`_STRIP_REQUEST_HEADERS = {"authorization","proxy-authorization","cookie"}`) and the
proxy (`services/egress_proxy/app.py` `_STRIPPED_INBOUND_HEADERS`) strip
`Authorization`/`Cookie` from **every** caller (SEC-H5: stop sandboxes impersonating
platform keys), so auth cannot ride the normal `headers` dict. **E.6 adds a dedicated
HMAC-signed upstream-auth channel** that lets the trusted in-process Function tool
inject the header while sandboxes still cannot — verified safe because a sandbox holds
only a pre-signed project HMAC, not the raw secret (`docker_runsc.py:272`). The
Function tool routes its unsealed auth through that channel and never places auth in
`headers`. The Function form (E.5) offers "none", "bearer", or "custom header".

**Validation:** `name` regex; `parameters` must be a JSON object schema (parse
with `jsonschema`); `http.method` in the verb set; `http.url` absolute https with
a hostname; reject `http.url` whose host is an IP literal (force DNS + allowlist).

**Exit criteria.** Schema validation unit tests (valid; bad method; non-https;
IP-literal host; non-object parameters).

## E.2 `_build_function_tool` — **CODE** — M

In `builtin_tools.py` (the `LOCAL_FUNCTION` case from A.4):

```python
def _build_function_tool(db, *, agent, deps, tool) -> Tool:
    cfg = tool.config
    http = cfg["http"]
    async def _invoke(args) -> ToolResult:
        host = urlsplit(http["url"]).hostname or ""
        # Pre-check the allowlist for a fast, clear error (the proxy re-checks).
        if not await EgressAllowlistRepository(db).is_allowed(project_id=agent.project_id, hostname=host.lower()):
            return ToolResult(content=f"function blocked: host {host} is not on the project egress allowlist.", is_error=True)
        headers = dict(http.get("headers") or {})
        upstream_auth = _auth_pair(_unseal_tool_auth(tool))   # (header_name, value) | None -> signed channel (E.6)
        method = http["method"].upper()
        try:
            if method in ("GET","DELETE"):
                status, _h, body = await deps.proxy.request(method=method, url=http["url"], project_id=agent.project_id, headers=headers, params=dict(args), upstream_auth=upstream_auth, timeout_s=30.0)
            else:
                status, _h, body = await deps.proxy.request(method=method, url=http["url"], project_id=agent.project_id, headers=headers, json_body=dict(args), upstream_auth=upstream_auth, timeout_s=30.0)
        except McpEgressDenied:
            await _audit_tool_invoke(db, agent, tool, ok=False); return ToolResult(content="function blocked by egress policy.", is_error=True)
        except Exception as exc:
            await _audit_tool_invoke(db, agent, tool, ok=False); return ToolResult(content=f"function call failed: {exc}", is_error=True)
        await _audit_tool_invoke(db, agent, tool, ok=200 <= status < 400)
        text = body.decode("utf-8", "replace")
        return ToolResult(content=_clip(f"HTTP {status}\n{text}"), is_error=not (200 <= status < 400))
    return Tool(name=cfg["name"], description=cfg["description"], input_schema=cfg["parameters"], invoke=_invoke)
```

- `deps.proxy` is the existing `EgressProxyClient` already in `AgentToolDeps`
  (web search uses it) — no new dependency.
- **New helpers to add** in `builtin_tools.py` (none exist yet): `_auth_pair(auth)`
  maps the unsealed auth dict to `(header_name, value) | None` —
  `("Authorization", f"Bearer {token}")` for bearer, `(name, value)` for the custom
  header; `_unseal_tool_auth(tool)` is A.6's `unseal_tool_auth(tool.id,
  tool.config["auth"])` returning `None` when absent; `_audit_tool_invoke(db, agent,
  tool, *, ok)` is a copy of the existing `_audit_mcp_invoke` emitting
  `mcp.tool_invoked` with `tool="function"` and the function's `display_name`/`name`
  (truncate any echoed args). Auth is passed to the proxy via `upstream_auth`, never
  in `headers`.
- `McpEgressDenied` import path is `contexts.agents.domain.errors` (already used by
  `egress_client`); `EgressAllowlistRepository` is in
  `contexts.agents.infrastructure.mcp_repositories` (verified).
- Output capped by `_clip` (16 KB to the model) on top of the proxy's 16 MiB.

**Exit criteria.** Unit tests with a fake `EgressProxyClient`: GET maps args to
query, POST maps args to body; non-allowlisted host short-circuits;
`McpEgressDenied` and transport errors become soft `is_error` results (never
raise into the turn).

## E.3 Allowlist UX coupling — **CODE** — S

A function only works if its host is on the project egress allowlist (same table
MCP uses). To avoid silent failures:

- On `add_tool`/`patch_tool` for `local_function`, if the host is **not** on the
  allowlist, still create the row but return a non-fatal warning field
  (`config_warnings: ["host not on egress allowlist"]`) so the FE can prompt the
  owner to add it via the existing `/api/projects/{pid}/mcp/egress-allowlist` UI.
- Do **not** auto-add the host (only a Project Owner may edit the allowlist; the
  function author may be a lesser role).

**Exit criteria.** Creating a function with an un-allowlisted host returns the
warning; after the owner allowlists it, a call succeeds.

## E.4 Create / test API — **CODE** — M

Reuse the A.7 `/tools` surface:

- `POST /api/agents/{id}/tools` with `tool_type:"local_function"`, `display_name`,
  `config` (E.1), `auth`. Server validates + seals auth.
- `POST /api/agents/{id}/tools/{tool_id}/test` for a function performs a **dry
  reachability check**: an allowlist check + a single proxied `OPTIONS` (or `GET`
  with empty args) with a short timeout, returning `{ok, status, duration_ms,
  error?}`. Document that test does not execute business logic with real args.

**Exit criteria.** Create + test round-trip; sealed auth never returned (A.7
redaction covers `config.auth`).

## E.5 Frontend — Function form — **CODE** — M

In `AgentToolsView` Local group, the **Functions** card:

- List `local_function` rows (`display_name`, method + host, enabled toggle,
  Test, edit, delete).
- "Add function" modal with: `name`, `description`, **parameters** (JSON-schema
  code editor — reuse the MCP `config` JSON editor component), `method` select,
  `url`, `headers` (key/value rows), `auth` (none / bearer / custom header — E.6).
- `functionConfigSchema` (Zod) validates before submit; show the
  `config_warnings` (allowlist) inline with a link to the egress allowlist view.
- i18n `agents.tools.functions.*` (en + zh-TW). Escape any literal `@`.

**Exit criteria.** Create a function in the UI → Test shows reachable → agent chat
invokes it → response surfaces; editing parameters updates the tool schema.

## E.6 Trusted upstream-auth channel (egress client + proxy) — **CODE** — M

A small, additive, backward-compatible change to the egress client **and** the
egress-proxy microservice so a trusted in-process caller can have the proxy inject
an upstream auth header that is otherwise stripped — without weakening SEC-H5.

**Verified threat model.** The raw `EGRESS_PROXY_SHARED_SECRET` lives only in the
backend process (`egress_client.py`, `docker_runsc.py:992`). Sandbox containers get
**only** a pre-signed `SMAP_EGRESS_HMAC = HMAC(secret, project_id)`
(`docker_runsc.py:265-275`), never the secret. So a second HMAC computed over
*different content* is unforgeable inside a sandbox but trivial for the in-process
Function tool. That asymmetry is the whole mechanism.

**Egress client (`contexts/agents/infrastructure/egress_client.py`).** Add an
optional `upstream_auth: tuple[str, str] | None = None` to `request()` (default
`None` ⇒ existing callers like `web_search` unchanged). When set to
`(header_name, header_value)`:

```python
import hashlib
vh = hashlib.sha256(header_value.encode()).hexdigest()
sig = hmac.new(self.shared_secret,
               f"{project_id}:auth:{header_name.lower()}:{vh}".encode("ascii"),
               sha256).hexdigest()
out_headers["x-smap-egress-auth-name"]  = header_name
out_headers["x-smap-egress-auth-value"] = header_value
out_headers["x-smap-egress-auth-sig"]   = sig
```

The real `Authorization`/`Cookie` strip on caller `headers` stays — auth ONLY ever
travels via these three signed control headers (which are not in the strip set).

**Egress proxy (`services/egress_proxy/app.py`).** After base-HMAC verification and
the existing inbound strip, if `x-smap-egress-auth-name/value` are present:

1. Recompute `expected = HMAC(secret, f"{project_id}:auth:{name.lower()}:{sha256(value)}")`
   and `hmac.compare_digest` against `x-smap-egress-auth-sig`; mismatch →
   `403 mcp-egress-denied` ("invalid upstream-auth signature").
2. Enforce a header-name **allowlist** (`{"authorization"}` + at most one configured
   custom name) so the channel can't set `Host`, `x-smap-*`, etc.
3. Inject `{name: value}` into the upstream headers **after** the strip step.
4. **Consume** the three `x-smap-egress-auth-*` headers — never forward them upstream.
5. Add `x-smap-egress-auth-value` to the proxy's log-redaction set; never log it.

**Security properties (assert in tests):**

- A request carrying a sandbox's pre-signed base HMAC but a forged/absent
  `x-smap-egress-auth-sig` is **denied** — sandboxes cannot inject `Authorization`
  (SEC-H5 preserved).
- The signature binds `project_id + header_name + sha256(value)`, so a captured
  signature can't be replayed for a different value, header, or project.
- The injected value never appears in proxy logs.

**Backward compatibility.** Both changes are additive: `upstream_auth=None` is the
default; the proxy ignores requests without the new headers. N-1 safe (deploy proxy
first, then the client).

**Exit criteria.** Function with bearer auth reaches an upstream that echoes
`Authorization: Bearer …`; a unit test forging the channel with only the base HMAC
(the sandbox's credential) is denied; proxy logs contain no token.

## E.∞ Phase gate

- [ ] `functionConfigSchema` validated both sides; SSRF-relevant inputs rejected
      (IP-literal host, non-https).
- [ ] `_build_function_tool` routes through the Egress Proxy; allowlist enforced;
      all failures are soft `is_error` results.
- [ ] Auth sealed (A.6) + redacted on read; bearer + custom header both work via the
      E.6 signed channel; the sandbox-cannot-forge test is green and the proxy never
      logs the token.
- [ ] Create/test API live; FE form works end-to-end.
- [ ] `00-overview.md` §0.6: E = done.

## Cross-cutting checklist

1. **AuthZ.** Function CRUD = `RESOURCE_CREATE_EDIT`; allowlist edits stay
   Owner-only (unchanged).
2. **Audit.** `agent.tool_added/updated/removed`; calls → `mcp.tool_invoked`
   `tool="function"`.
3. **SSRF.** No new egress path — reuses proxy allowlist + IP pinning + metadata
   blocks. Host must be allowlisted; IP-literal hosts rejected at config time.
4. **Rate limit.** Add a `function-call` bucket (reuse the search rate-limiter
   pattern, default 60/min/project, admin-tunable) checked in `_invoke`.
   Pattern: `contexts/agents/infrastructure/search_rate_limiter.py:16-30` uses
   Redis key `search:rl:{project_id}:{minute_window}`; clone as
   `function:rl:{project_id}:{minute_window}`.
5. **RFC 7807.** Reuse `mcp-egress-denied`; add `function-config-invalid`.
6. **Secrets.** `config.auth` sealed via Vault Transit; never logged or returned.

## Appendix: Codebase coordinates for implementors

### Egress client (the trusted caller)

- `contexts/agents/infrastructure/egress_client.py:38-47` — `HttpxEgressProxyClient` dataclass with `proxy_base_url` and `shared_secret`
- `egress_client.py:49-50` — `_sign(project_id)` computes `HMAC(secret, str(project_id).encode("ascii"), sha256).hexdigest()`
- `egress_client.py:52-112` — `request(method, url, project_id, headers?, params?, json_body?, timeout_s)` → `(status, headers, body)`
- `egress_client.py:35` — `_STRIP_REQUEST_HEADERS = frozenset({"authorization", "proxy-authorization", "cookie"})` — client-side strip on caller headers
- `egress_client.py:72-73` — the strip: `{k:v for k,v in headers.items() if k.lower() not in _STRIP_REQUEST_HEADERS}`
- `egress_client.py:115-131` — `egress_proxy_client_from_settings()` factory reads `EGRESS_PROXY_SHARED_SECRET`

**For E.6:** add `upstream_auth: tuple[str, str] | None = None` param to `request()`. When set, compute the content-bound HMAC and add the three `x-smap-egress-auth-*` headers to `out_headers` (after the caller strip, alongside the existing project HMAC headers). **Do not put auth in `headers`** — it would be stripped at line 72.

### Egress proxy (the enforcer)

- `services/egress_proxy/app.py` — standalone FastAPI service
- Request flow verified (in order): HMAC verify (lines 196-210) → parse target URL (212-230) → IP resolve + block (232-252) → allowlist check (254-269) → **header strip + rebuild** (271-282) → socket pin (284-298) → body size check (300-317) → httpx upstream request (335-342) → response stream (343-415)
- **Header strip code (lines 271-282):**
  ```python
  upstream_headers: dict[str, str] = {}
  for name, value in request.headers.items():
      if name.lower() in _STRIPPED_INBOUND_HEADERS:
          continue
      if name.lower() == "host":
          continue
      upstream_headers[name] = value
  upstream_headers["host"] = _host_header(host, scheme, target_port)
  ```
- **Upstream request build (line 335-341):**
  ```python
  req = client.build_request(method=request.method, url=pinned_url, headers=upstream_headers, content=body, extensions={"sni_hostname": host})
  ```

**For E.6:** Insert the auth injection **between line 282 (after Host set) and line 291 (before IP pinning / URL build):**
1. Read `x-smap-egress-auth-name/value/sig` from original request headers
2. Verify sig = `HMAC(secret, f"{project_id}:auth:{name.lower()}:{sha256(value)}")` — same `secret` already available
3. Validate header name against allowlist `{"authorization"}` + admin-configured set
4. `upstream_headers[name] = value` — injected **after** the strip erased any original
5. **Consume** the three control headers (remove from `upstream_headers` if they leaked through — they're not in `_STRIPPED_INBOUND_HEADERS` currently, so add them)

**Logging:** lines 381-390 log body (truncated 2048 bytes) but **never log header values** — auth is safe. Add `x-smap-egress-auth-value` to a redaction note so future log expansions don't leak it.

### Sandbox HMAC (the asymmetry that makes E.6 safe)

- `docker_runsc.py:265-275` — `_egress_env(project_id)`:
  ```python
  signature = hmac.new(self.egress_shared_secret, str(project_id).encode("ascii"), sha256).hexdigest()
  return {"SMAP_EGRESS_PROXY_URL": self.egress_proxy_url, "SMAP_EGRESS_HMAC": signature}
  ```
  Sandbox gets **only** this pre-signed HMAC. It cannot compute `HMAC(secret, f"{project_id}:auth:...")` because it doesn't have `secret`.
- `docker_runsc.py:992` — `secret = bytes.fromhex(cfg.egress.shared_secret)` — raw secret loaded only in the backend process

### Search adapter pattern (how web_search uses the proxy today)

- `contexts/agents/application/tools/web_search.py:151` — adapter receives `proxy=self.proxy` (the `EgressProxyClient` instance)
- Search adapters call `proxy.request(method="POST", url=..., project_id=..., headers={"X-Api-Key": api_key})` — note the key goes in `headers` as a **non-Authorization** header, which is NOT stripped
- This confirms: custom-header auth already works through the proxy; E.6 adds the signed channel for bearer specifically
