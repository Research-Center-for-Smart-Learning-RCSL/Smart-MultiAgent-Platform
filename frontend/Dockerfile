# syntax=docker/dockerfile:1.7

# Stage 1: build static assets
FROM node:20.18.1-alpine AS build

RUN corepack enable
WORKDIR /app

COPY pnpm-lock.yaml pnpm-workspace.yaml package.json ./
COPY frontend/package.json frontend/
RUN --mount=type=cache,target=/root/.local/share/pnpm/store \
    cd frontend && pnpm install --frozen-lockfile

COPY frontend/ frontend/
RUN cd frontend && pnpm run build \
 && find dist -name '*.map' -delete

# Stage 2: serve via nginx (lightweight, no node runtime in prod)
FROM nginx:1.27.3-alpine

COPY --from=build /app/frontend/dist /usr/share/nginx/html

# Non-root nginx: override main config — remove the 'user' directive (only root
# may use it) and redirect pid/temp paths to /tmp which is world-writable.
RUN printf 'pid /tmp/nginx.pid;\n\
worker_processes auto;\n\
error_log /var/log/nginx/error.log notice;\n\
\n\
events {\n\
    worker_connections 1024;\n\
}\n\
\n\
http {\n\
    include       /etc/nginx/mime.types;\n\
    default_type  application/octet-stream;\n\
    client_body_temp_path /tmp;\n\
    proxy_temp_path       /tmp;\n\
    fastcgi_temp_path     /tmp;\n\
    uwsgi_temp_path       /tmp;\n\
    scgi_temp_path        /tmp;\n\
    sendfile on;\n\
    keepalive_timeout 65;\n\
    include /etc/nginx/conf.d/*.conf;\n\
}\n' > /etc/nginx/nginx.conf

# SPA fallback: all non-file requests → index.html (Vue Router handles routing)
RUN printf 'server {\n\
    listen 5173;\n\
    root /usr/share/nginx/html;\n\
    index index.html;\n\
\n\
    location /assets/ {\n\
        expires 1y;\n\
        add_header Cache-Control "public, immutable";\n\
    }\n\
\n\
    location / {\n\
        add_header Cache-Control "no-cache, must-revalidate" always;\n\
        try_files $uri $uri/ /index.html;\n\
    }\n\
}\n' > /etc/nginx/conf.d/default.conf

RUN chown -R nginx:nginx /usr/share/nginx/html /var/cache/nginx /var/log/nginx \
 && touch /var/run/nginx.pid \
 && chown nginx:nginx /var/run/nginx.pid

USER nginx
EXPOSE 5173

CMD ["nginx", "-g", "daemon off;"]
