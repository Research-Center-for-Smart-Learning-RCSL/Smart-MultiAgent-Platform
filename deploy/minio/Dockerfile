# syntax=docker/dockerfile:1.7
# Build MinIO from source — pre-built images removed from all registries
# after the project was archived (2026-04-25).
FROM golang:1.24-alpine AS build

RUN apk add --no-cache git

ARG MINIO_VERSION=RELEASE.2025-09-07T16-13-09Z
RUN git clone --depth 1 --branch "${MINIO_VERSION}" \
        https://github.com/minio/minio.git /src/minio

WORKDIR /src/minio
RUN --mount=type=cache,target=/root/.cache/go-build \
    --mount=type=cache,target=/go/pkg/mod \
    CGO_ENABLED=0 go build -trimpath \
        -ldflags "-s -w -X github.com/minio/minio/cmd.Version=${MINIO_VERSION}" \
        -o /usr/bin/minio .

# backup.sh / restore.sh exec `mc` inside this container
ARG MC_VERSION=RELEASE.2025-08-13T08-35-41Z
RUN git clone --depth 1 --branch "${MC_VERSION}" \
        https://github.com/minio/mc.git /src/mc
WORKDIR /src/mc
RUN --mount=type=cache,target=/root/.cache/go-build \
    --mount=type=cache,target=/go/pkg/mod \
    CGO_ENABLED=0 go build -trimpath \
        -ldflags "-s -w" \
        -o /usr/bin/mc .

FROM alpine:3.20
RUN apk add --no-cache ca-certificates curl
COPY --from=build /usr/bin/minio /usr/bin/minio
COPY --from=build /usr/bin/mc    /usr/bin/mc

EXPOSE 9000 9001
VOLUME ["/data"]
ENTRYPOINT ["minio"]
