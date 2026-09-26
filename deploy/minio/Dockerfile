# syntax=docker/dockerfile:1.7
# Build MinIO from source — pre-built images removed from all registries
# after the project was archived (2026-04-25).
FROM golang:1.24-alpine AS build

RUN apk add --no-cache git

ARG MINIO_VERSION=RELEASE.2025-09-07T16-13-09Z
RUN git clone --depth 1 --branch "${MINIO_VERSION}" \
        https://github.com/minio/minio.git /src

WORKDIR /src
RUN --mount=type=cache,target=/root/.cache/go-build \
    --mount=type=cache,target=/go/pkg/mod \
    CGO_ENABLED=0 go build -trimpath -tags kqueue \
        -ldflags "-s -w -X github.com/minio/minio/cmd.Version=${MINIO_VERSION}" \
        -o /usr/bin/minio .

FROM alpine:3.20
RUN apk add --no-cache ca-certificates curl
COPY --from=build /usr/bin/minio /usr/bin/minio

RUN adduser -D -u 1000 minio && mkdir -p /data && chown minio:minio /data
USER minio

EXPOSE 9000 9001
VOLUME ["/data"]
ENTRYPOINT ["minio"]
