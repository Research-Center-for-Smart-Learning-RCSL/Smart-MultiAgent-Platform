# Nginx TLS terminator

Implements C.11 / §19a.01–§19a.04: TLS 1.2+/1.3 AEAD-only, HSTS, and the
`§19a.2` response-header set on the HTML shell. Backend (`security_headers`
middleware) echoes the same headers for `/api/*` JSON responses.

## Certificates

The container mounts a named volume `nginx_certs` at `/etc/nginx/certs`.

- **Dev / CI.** On first boot the entrypoint generates a self-signed cert
  (`smap.crt` / `smap.key`, CN=`smap.local`, 365 d). Browsers will warn — this
  is expected in dev.
- **Prod.** Replace the volume contents with operator-provisioned certs
  before `docker compose up`:

  ```sh
  docker run --rm -v smap_nginx_certs:/certs -v $PWD:/host alpine \
      sh -c "cp /host/fullchain.pem /certs/smap.crt && \
             cp /host/privkey.pem   /certs/smap.key && \
             chmod 600 /certs/smap.key"
  ```

  Rotate by overwriting the two files and `docker compose exec nginx nginx -s reload`.

## Upstream resolution

`backend-web` and `frontend` are reached through `set` variables plus a
`resolver 127.0.0.11` (Docker's embedded DNS), **not** through `upstream {}`
blocks. An `upstream {}` block resolves its hostnames once, at startup, so every
`compose up -d` that recreated a container left this edge proxying to a dead IP
until nginx was restarted — and restarting the edge is what lets a caching front
proxy capture 502s for hashed asset URLs. Per-request resolution is what makes
`docker compose up -d --no-deps <service>` a safe deploy (see `deploy/README.md`
§6a).

Two consequences worth knowing before changing this back:

- **No upstream keepalive.** Only `upstream {}` can pool connections, so each
  proxied request opens a new socket to the app container. On a shared Docker
  bridge that is a sub-millisecond cost; the long-lived paths (WebSocket, SSE)
  hold one connection anyway.
- **Every `proxy_pass` appends `$request_uri` explicitly.** With a variable in
  the target, the "no URI part" form is easy to get subtly wrong. None of these
  locations rewrite the URI, so the raw client URI is the correct value — this
  matches what `proxy_pass http://upstream;` passed before.

`nginx -t` runs against these files in CI (`compose-validate`), because the
compose boot test skips the nginx service.

## Trust boundary

Nginx forwards `X-Forwarded-For` / `X-Forwarded-Proto`; the backend
`TrustedProxyMiddleware` walks the list right-to-left, trusting only peers in
`SMAP_SEC_TRUSTED_PROXIES` (default: `127.0.0.1/32`, `::1/128`,
`172.16.0.0/12` — the Docker bridge range). A front proxy such as Nginx Proxy
Manager reaches this container over the Docker bridge, which the default
already covers — no extra CIDR is needed for that topology. Widen the list
only when a CDN / ALB attaches from a network outside `172.16.0.0/12`, and
verify the result with `deploy/scripts/verify-actor-ip.py` before relying on
it (over-trusting a range lets that range spoof `X-Forwarded-For`).

## Port 80

`:80` serves a 308 redirect to `:443` for everything except `/healthz` and
`/readyz`, which stay plaintext so the Docker healthcheck does not need to
trust the dev self-signed cert.
