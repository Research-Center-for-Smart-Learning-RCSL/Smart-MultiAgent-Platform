"""Application settings.

Resolution order (per A.5): **Vault → env → .env → defaults**.

Phase A scope: env + .env + defaults. The Vault source is registered as a
stub that currently yields no values; Phase B replaces it with a real
`hvac`-backed `SettingsSource` that pulls `secret/smap/config/*` from KV.

Missing-required-var failures are *aggregated* — we catch a single
`ValidationError` and re-raise as `ConfigError` with every missing key
listed, so the operator sees one message instead of playing whack-a-mole.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import AliasChoices, Field, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict


class ConfigError(RuntimeError):
    """Raised at startup when one or more required settings are missing."""


# ---------- Section models ----------


class AppSection(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SMAP_APP_", extra="ignore")

    env: Literal["dev", "test", "prod"] = "dev"
    version: str = "0.0.0"
    docs_enabled: bool = True  # disabled in prod via env
    problem_url_base: str = "https://smap.local/problems"


class DatabaseSection(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SMAP_DB_", extra="ignore")

    dsn: str = "postgresql+asyncpg://smap:smap@postgres:5432/smap"
    pool_size: int = 20
    max_overflow: int = 10
    statement_timeout_ms: int = 30_000


class RedisSection(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SMAP_REDIS_", extra="ignore")

    dsn: str = "redis://redis:6379/0"


class QdrantSection(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SMAP_QDRANT_", extra="ignore")

    url: str = "http://qdrant:6333"
    api_key: str | None = None


class Neo4jSection(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SMAP_NEO4J_", extra="ignore")

    url: str = "bolt://neo4j:7687"
    user: str = "neo4j"
    password: str = "neo4j"  # dev default; prod pulls from Vault KV
    database: str = "smap"


class MinioSection(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SMAP_MINIO_", extra="ignore")

    endpoint: str = "minio:9000"
    # Root creds are used **only** by the bootstrap CLI to provision a scoped
    # service account on first run; the runtime backend talks to MinIO via the
    # service-account creds read from Vault KV (`secret/smap/config/minio`).
    root_access_key: str = "minioadmin"
    root_secret_key: str = "minioadmin"
    use_tls: bool = False
    region: str = "us-east-1"
    bucket_chat_uploads: str = "chat-uploads"
    bucket_rag_sources: str = "rag-sources"
    bucket_exports: str = "exports"
    # TTLs encoded here so both the bootstrap CLI and tests reference a single
    # source of truth; changing them is a config + Alembic change, never a
    # one-off mutation of the bucket lifecycle via the console.
    chat_uploads_expiry_days: int = 3       # R13.10
    exports_expiry_hours: int = 24          # §21.5
    service_account_name: str = "smap-backend"


class VaultSection(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SMAP_VAULT_", extra="ignore")

    addr: str = "http://vault:8200"
    role_id: str | None = None  # required in prod; dev uses root token below
    secret_id: str | None = None
    dev_token: str | None = None  # dev-only fallback for `vault -dev`; must be unset in prod
    transit_key_provider: str = "smap-provider-secret"
    transit_key_guest: str = "smap-guest-link"
    transit_key_jwt: str = "smap-jwt-sign"
    kv_mount: str = "secret"
    kv_prefix: str = "smap/config"


class JwtSection(BaseSettings):
    """Phase B exposes only what the Vault Transit-backed JWT signer needs.

    The actual `kid` value is a Vault key version string (e.g. `"3"`) resolved
    at sign time; this section carries the rotation window per R6.03.
    """

    model_config = SettingsConfigDict(env_prefix="SMAP_JWT_", extra="ignore")

    access_ttl_seconds: int = 15 * 60       # R6.03
    refresh_ttl_seconds: int = 30 * 24 * 3600
    issuer: str = "smap.local"
    audience: str = "smap.api"
    # Operator-tunable, but the default MUST match R6.03: 7-day overlap.
    verify_overlap_days: int = 7
    rotation_days: int = 90


class ObservabilitySection(BaseSettings):
    """Opt-in /metrics + OTEL SDK wiring per B.10.

    Nginx restricts `/metrics` to localhost and the Nginx upstream subnet — see
    `deploy/compose/nginx/conf.d/smap.conf`. This section only controls whether
    the endpoint is mounted at all.
    """

    model_config = SettingsConfigDict(env_prefix="SMAP_OBS_", extra="ignore")

    metrics_enabled: bool = True
    metrics_path: str = "/metrics"
    # OTEL SDK is only activated if this endpoint is non-empty.
    otel_exporter_otlp_endpoint: str | None = None
    otel_service_name: str = "smap-backend"


class SecuritySection(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SMAP_SEC_", extra="ignore")

    trusted_proxies: list[str] = Field(default_factory=lambda: ["127.0.0.1/32"])
    # Accept both the spec-literal env name `SMAP_CSP_REPORT_ONLY` (R19a.06)
    # and the section-prefixed `SMAP_SEC_CSP_REPORT_ONLY` form.
    csp_report_only: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "SMAP_CSP_REPORT_ONLY", "SMAP_SEC_CSP_REPORT_ONLY",
        ),
    )
    cors_origins: list[str] = Field(default_factory=list)
    session_cookie_name: str = "smap_session"
    session_cookie_secure: bool = True
    session_cookie_samesite: Literal["lax", "strict", "none"] = "lax"


class LoggingSection(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SMAP_LOG_", extra="ignore")

    level: Literal["debug", "info", "warning", "error"] = "info"
    service_name: str = "smap-backend"
    json: bool = True


class LimitsSection(BaseSettings):
    """Numeric budgets keyed by §19.02 bucket; real enforcement lands in C.12."""

    model_config = SettingsConfigDict(env_prefix="SMAP_LIMIT_", extra="ignore")

    auth_per_min_ip: int = 10
    chat_per_min_user: int = 60
    upload_per_min_user: int = 10
    other_per_min_user: int = 300
    ws_concurrent_per_user: int = 5


# ---------- Root ----------


class Settings(BaseSettings):
    """Top-level settings. Each section is loaded independently so one
    missing var in (say) Neo4j does not mask a missing var in Database.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        extra="ignore",
    )

    app: AppSection = Field(default_factory=AppSection)
    database: DatabaseSection = Field(default_factory=DatabaseSection)
    redis: RedisSection = Field(default_factory=RedisSection)
    qdrant: QdrantSection = Field(default_factory=QdrantSection)
    neo4j: Neo4jSection = Field(default_factory=Neo4jSection)
    minio: MinioSection = Field(default_factory=MinioSection)
    vault: VaultSection = Field(default_factory=VaultSection)
    jwt: JwtSection = Field(default_factory=JwtSection)
    observability: ObservabilitySection = Field(default_factory=ObservabilitySection)
    security: SecuritySection = Field(default_factory=SecuritySection)
    logging: LoggingSection = Field(default_factory=LoggingSection)
    limits: LimitsSection = Field(default_factory=LimitsSection)


def _check_prod_secrets(s: Settings) -> None:
    """Raise ConfigError if insecure dev defaults are active in production."""
    if s.app.env != "prod":
        return
    problems: list[str] = []
    if s.neo4j.password == "neo4j":
        problems.append("SMAP_NEO4J_PASSWORD is the insecure default 'neo4j'")
    if s.minio.root_access_key == "minioadmin":
        problems.append("SMAP_MINIO_ROOT_ACCESS_KEY is the insecure default 'minioadmin'")
    if s.vault.dev_token is not None:
        problems.append("SMAP_VAULT_DEV_TOKEN must not be set in production")
    if problems:
        raise ConfigError(
            "Insecure defaults active in production:\n  - " + "\n  - ".join(problems)
        )


def _load() -> Settings:
    try:
        settings = Settings()
    except ValidationError as exc:
        missing = [".".join(str(p) for p in e["loc"]) for e in exc.errors()]
        msg = "Configuration errors:\n  - " + "\n  - ".join(missing)
        raise ConfigError(msg) from exc
    _check_prod_secrets(settings)
    return settings


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return _load()
