# syntax=docker/dockerfile:1.7

# Stage 1: build static assets
FROM node:20-alpine AS build

RUN corepack enable
WORKDIR /app

COPY pnpm-lock.yaml pnpm-workspace.yaml package.json ./
COPY frontend/package.json frontend/
RUN --mount=type=cache,target=/root/.local/share/pnpm/store \
    cd frontend && pnpm install --frozen-lockfile

COPY frontend/ frontend/
RUN cd frontend && pnpm run build

# Stage 2: serve via nginx (lightweight, no node runtime in prod)
FROM nginx:1.27-alpine

COPY --from=build /app/frontend/dist /usr/share/nginx/html

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
        try_files $uri $uri/ /index.html;\n\
    }\n\
}\n' > /etc/nginx/conf.d/default.conf

EXPOSE 5173

CMD ["nginx", "-g", "daemon off;"]
