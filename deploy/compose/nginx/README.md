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

## Trust boundary

Nginx forwards `X-Forwarded-For` / `X-Forwarded-Proto`; the backend
`TrustedProxyMiddleware` walks the list right-to-left, trusting only peers in
`SMAP_TRUSTED_PROXIES` (default: `127.0.0.0/8`, Docker bridge subnet). Add the
front-proxy's subnet there when Nginx sits behind a CDN / ALB.

## Port 80

`:80` serves a 308 redirect to `:443` for everything except `/healthz` and
`/readyz`, which stay plaintext so the Docker healthcheck does not need to
trust the dev self-signed cert.
