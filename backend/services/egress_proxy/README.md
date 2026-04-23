# SMAP Egress Proxy

Standalone FastAPI forward-proxy that every sandboxed MCP container and URL-
MCP call must traverse. Lives on `smap_egress_net` — it is the *only* network
endpoint sandbox containers can reach.

## Responsibilities

1. **Authenticate inbound sandbox traffic.**
   - `x-smap-project-id` + `x-smap-egress-hmac` (HMAC-SHA256 of the project id
     under `EGRESS_PROXY_SHARED_SECRET`).
   - Missing / mismatched → `401 mcp-egress-denied`.
2. **IP-policy block.**
   - Rejects RFC 1918, CGNAT, loopback, link-local, IPv6 ULA, multicast,
     reserved — plus cloud-metadata IPs (169.254.169.254, 100.100.100.200,
     192.0.0.192, 169.254.170.2, 169.254.170.23, IBM 161.26.0.0/16).
3. **Allowlist.**
   - Consults `mcp_egress_allowlist` by project id (`AllowlistChecker`
     protocol; prod wires a short-lived SQLA session against the main DB).
4. **Strip `Authorization` / `Proxy-Authorization` / `Cookie`** from the
   inbound request before forwarding. Sandboxes cannot impersonate platform
   keys.
5. **Audit.** Every request + truncated (2 KB) body is logged as structured
   text on `smap.egress_proxy`.

## Wire protocol

- Sandbox sets `x-smap-egress-url` to the absolute upstream URL.
- The proxy's own host/path is ignored — only the header URL is forwarded.
- Responses are relayed 1:1, minus hop-by-hop headers (RFC 7230).

## Running

Production launches with `uvicorn services.egress_proxy.app:create_app(...)`
under the same supervisor as the API workers. See `operations.md §2.1` for
deployment topology.

## Failure modes

| Condition                          | Response                         |
| ---------------------------------- | -------------------------------- |
| Missing / invalid HMAC             | 401 mcp-egress-denied            |
| DNS failure                        | 502 mcp-egress-denied            |
| IP resolves to blocked range       | 403 mcp-egress-denied            |
| Host not on project allowlist      | 403 mcp-egress-denied            |
| Upstream HTTP error                | 502 mcp-egress-denied            |
