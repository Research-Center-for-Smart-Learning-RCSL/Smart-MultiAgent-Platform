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

    env: Literal["dev", "test", "staging", "prod"] = "dev"
    version: str = "0.0.0"
    docs_enabled: bool = True  # disabled in prod via env
    problem_url_base: str = "https://smap.local/problems"


class DatabaseSection(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SMAP_DB_", extra="ignore")

    dsn: str = "postgresql+asyncpg://smap:smap@postgres:5432/smap"
    pool_size: int = 20
    max_overflow: int = 10
    pool_timeout: int = 30
    pool_recycle: int = 300
    connect_timeout: int = 10
    statement_timeout_ms: int = 30_000


class RedisSection(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SMAP_REDIS_", extra="ignore")

    dsn: str = "redis://redis:6379/0"
    socket_connect_timeout: int = 5
    socket_timeout: int = 5
    health_check_interval: int = 30


class QdrantSection(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SMAP_QDRANT_", extra="ignore")

    url: str = "http://qdrant:6333"
    api_key: str | None = None
    # F-3: the configless-collection teardown runs inside an open Postgres
    # transaction holding the (project, kind) advisory lock, so an unbounded call
    # would pin a pooled connection and block config creation for that project for
    # as long as Qdrant hangs. On timeout the teardown reports FAILED, retains the
    # pin, and the retry paths reclaim the collection later. Bounded above 0 because
    # a 0/negative value would expire every teardown instantly, stranding pins that
    # no retry could ever release.
    teardown_timeout_s: float = Field(default=10.0, gt=0, le=60)


class Neo4jSection(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SMAP_NEO4J_", extra="ignore")

    url: str = "bolt://neo4j:7687"
    user: str = "neo4j"
    password: str = "neo4jneo4j"  # noqa: S105 — dev default matches compose; prod pulls from Vault KV
    database: str = "smap"


class GraphRagSection(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SMAP_GRAPHRAG_", extra="ignore")

    # D8 (R11.16): max concurrent graphrag builds per worker. Builds are heavy
    # (LLM extraction + Neo4j + Qdrant), so a burst could otherwise monopolise
    # the shared worker resources; the semaphore bounds concurrent heavy work.
    build_concurrency: int = 4

    # Phase 2b WS5 (R11.21): platform default Concept Map recency half-life, in
    # days, used when a config leaves ``recency_half_life_days`` NULL. Retrieval
    # weights an edge by ``exp(-Δt / halflife)`` over its ``last_seen_at``.
    recency_half_life_days_default: float = 30.0


class MinioSection(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SMAP_MINIO_", extra="ignore")

    endpoint: str = "minio:9000"
    # Root creds are used **only** by the bootstrap CLI to provision a scoped
    # service account on first run; the runtime backend talks to MinIO via the
    # service-account creds read from Vault KV (`secret/smap/config/minio`).
    root_access_key: str = "minioadmin"
    root_secret_key: str = "minioadmin"  # noqa: S105 — dev default; prod reads Vault KV
    use_tls: bool = False
    region: str = "us-east-1"
    bucket_chat_uploads: str = "chat-uploads"
    bucket_rag_sources: str = "rag-sources"
    bucket_knowmap_sources: str = "knowmap-sources"
    bucket_exports: str = "exports"
    bucket_agent_workspace: str = "agent-workspace"
    bucket_prompt_assistant_files: str = "prompt-assistant-files"
    bucket_skill_bundles: str = "skill-bundles"
    # TTLs encoded here so both the bootstrap CLI and tests reference a single
    # source of truth; changing them is a config + Alembic change, never a
    # one-off mutation of the bucket lifecycle via the console.
    #
    # `exports` is deliberately absent: its 24-hour TTL (§21.5) is enforced twice, and
    # neither enforcer can take an hours-valued setting. The bucket lifecycle is
    # day-granular, so `minio_init` states `days=1`; the retention worker
    # (`_purge_exports_bucket`) states `timedelta(hours=24)` alongside every other TTL in
    # that module, which are all literals. A setting read by nobody is worse than no
    # setting — it reads as configurable and is not.
    chat_uploads_expiry_days: int = 3  # R13.10
    service_account_name: str = "smap-backend"


class VaultSection(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SMAP_VAULT_", extra="ignore")

    addr: str = "http://vault:8200"
    role_id: str | None = None  # required in prod; dev uses root token below
    secret_id: str | None = None
    dev_token: str | None = None  # dev-only fallback for `vault -dev`; must be unset in prod
    ca_cert: str | None = None  # path to internal CA PEM for TLS verification (B2-2)
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

    access_ttl_seconds: int = 15 * 60  # R6.03
    refresh_ttl_seconds: int = 30 * 24 * 3600
    # Sliding inactivity window. A session that is not rotated (refreshed) for
    # longer than this is treated as expired on its next refresh — a server-
    # authoritative backstop to the SPA's input-driven idle logout. The SPA
    # fetches both values from `/api/auth/session-policy` so the warning UI and
    # the enforced timeout stay in lockstep. Set <= 0 to disable the backstop.
    idle_timeout_seconds: int = 45 * 60
    # How long before the idle timeout the SPA shows its "are you still there?"
    # countdown. Purely a client-side UX lead time; carried here so the timeout
    # policy has a single source of truth.
    idle_warning_seconds: int = 60
    issuer: str = "smap.local"
    audience: str = "smap.api"
    # NOT YET IMPLEMENTED — reserved for future JWT rotation (R6.03).
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

    # SEC-M1: the default MUST cover the reverse-proxy peer or X-Forwarded-For
    # is discarded and every client collapses to one IP (per-IP bans and
    # rate-limit buckets become a single global bucket). In the compose
    # topology nginx reaches backend-web over the Docker bridge (172.16.0.0/12),
    # so it is trusted by default; ::1 covers IPv6 loopback. Trusting a private
    # range that no proxy uses is low-risk (private IPs are not internet-
    # routable to a public peer). Operators tighten this to their actual bridge
    # subnet via SMAP_SEC_TRUSTED_PROXIES.
    trusted_proxies: list[str] = Field(default_factory=lambda: ["127.0.0.1/32", "::1/128", "172.16.0.0/12"])
    # Accept both the spec-literal env name `SMAP_CSP_REPORT_ONLY` (R19a.06)
    # and the section-prefixed `SMAP_SEC_CSP_REPORT_ONLY` form.
    csp_report_only: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "SMAP_CSP_REPORT_ONLY",
            "SMAP_SEC_CSP_REPORT_ONLY",
        ),
    )
    cors_origins: list[str] = Field(default_factory=list)
    session_cookie_secure: bool = True
    # Intentionally restricted to "lax" / "strict": the v1 deploy is
    # same-origin (SPA + API behind nginx) and we ship no CSRF middleware,
    # so allowing "none" (cross-site cookies with `Secure`) would weaken
    # the CSRF posture without compensating mitigations.
    session_cookie_samesite: Literal["lax", "strict"] = "lax"
    # AV / file-scan integration (R22.15.07). When False, the
    # ``file_scan_requested`` worker marks every attachment CLEAN (no-op
    # pass). Set to True once a ClamAV or VirusTotal adapter is wired in.
    file_scan_enabled: bool = False
    clamav_host: str | None = None
    clamav_port: int = 3310
    clamav_max_scan_bytes: int = 100 * 1024 * 1024


class LoggingSection(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SMAP_LOG_", extra="ignore")

    level: Literal["debug", "info", "warning", "error"] = "info"
    service_name: str = "smap-backend"
    json: bool = True  # type: ignore[assignment]


class EgressSection(BaseSettings):
    """Caller-side config for reaching the Egress Proxy (R12.04 / SEC-H5).

    The built-in tool runtime builds :class:`HttpxEgressProxyClient` from this
    so ``web_search`` egress traverses the proxy's allowlist + IP policy.
    """

    model_config = SettingsConfigDict(env_prefix="SMAP_EGRESS_", extra="ignore")

    proxy_url: str = "http://egress-proxy:8080"
    upstream_timeout_s: float = 20.0
    # Same HMAC secret the egress-proxy verifies. Read from the un-prefixed
    # EGRESS_PROXY_SHARED_SECRET (matches the proxy service + .env.example),
    # falling back to the section-prefixed form.
    shared_secret: str = Field(
        default="",
        validation_alias=AliasChoices(
            "EGRESS_PROXY_SHARED_SECRET",
            "SMAP_EGRESS_SHARED_SECRET",
        ),
    )


class SandboxSection(BaseSettings):
    """gVisor sandbox image references (R12.03 / R12.05 / K.5).

    Production pins both images by digest (``smap/mcp-runtime@sha256:…``) via
    ``SANDBOX_MCP_IMAGE`` / ``SANDBOX_CODE_EXEC_IMAGE``; the CI build job records
    the digests. The defaults are the dev tags so a self-hosting operator who
    ran ``docker compose --profile sandbox-build build`` gets a working stack
    without extra config. Read from un-prefixed names to match ``.env.example``.
    """

    model_config = SettingsConfigDict(env_prefix="SMAP_SANDBOX_", extra="ignore")

    mcp_image: str = Field(
        default="smap/mcp-runtime:pinned",
        validation_alias=AliasChoices("SANDBOX_MCP_IMAGE", "SMAP_SANDBOX_MCP_IMAGE"),
    )
    code_exec_image: str = Field(
        default="smap/code-exec:pinned",
        validation_alias=AliasChoices("SANDBOX_CODE_EXEC_IMAGE", "SMAP_SANDBOX_CODE_EXEC_IMAGE"),
    )
    # Readiness gate (R12.05): base URL of the mcp-sandbox-supervisor health
    # probe, e.g. ``http://mcp-sandbox-supervisor:9090``. Empty disables the
    # gate, in which case the per-container ``_assert_runsc`` check remains the
    # sole (and security-sufficient) gVisor guarantee. The gate only adds a
    # fail-fast, doc-pointing error when the runtime is missing host-wide.
    supervisor_url: str = Field(
        default="",
        validation_alias=AliasChoices("SANDBOX_SUPERVISOR_URL", "SMAP_SANDBOX_SUPERVISOR_URL"),
    )


class KnowledgeSection(BaseSettings):
    """Knowledge-retrieval runtime config (R10.08 / F-19).

    ``bge_reranker_url`` is the base URL of the bundled local reranker service
    (``bge-reranker-v2-m3``, CPU inference) that backs a RAG config's keyless
    ``rerank_provider="bge"`` option. The default is the in-cluster Compose
    service DNS so a self-hosting operator gets a working local reranker out of
    the box; an operator who does not deploy the service sets ``RERANK_BGE_URL``
    empty, which makes selecting ``bge`` on a config fail validation with a
    doc-pointing error (mirrors the sandbox ``supervisor_url`` empty-disables
    gate). Read from the un-prefixed ``RERANK_BGE_URL`` (matches ``.env.example``)
    with a section-prefixed fallback.
    """

    model_config = SettingsConfigDict(env_prefix="SMAP_KNOWLEDGE_", extra="ignore")

    bge_reranker_url: str = Field(
        default="http://bge-reranker:80",
        validation_alias=AliasChoices("RERANK_BGE_URL", "SMAP_KNOWLEDGE_BGE_RERANKER_URL"),
    )


class EmailSection(BaseSettings):
    """Outbound SMTP transport config (R6.01 / K.6).

    Only non-secret connection parameters live here; the SMTP *credentials*
    (username / password) are read from Vault KV ``secret/smap/config/smtp`` by
    :class:`SmtpEmailSender`, never from the environment. ``smtp_host`` being
    empty is the signal that no real mail transport is configured — the factory
    then keeps the dev ``LoggingEmailSender`` (and, in prod, logs a warning).
    Read from un-prefixed names to match ``.env.example``.
    """

    model_config = SettingsConfigDict(env_prefix="SMAP_SMTP_", extra="ignore")

    smtp_host: str = Field(
        default="",
        validation_alias=AliasChoices("SMTP_HOST", "SMAP_SMTP_HOST"),
    )
    smtp_port: int = Field(
        default=587,
        validation_alias=AliasChoices("SMTP_PORT", "SMAP_SMTP_PORT"),
    )
    smtp_from: str = Field(
        default="SMAP <no-reply@localhost>",
        validation_alias=AliasChoices("SMTP_FROM", "SMAP_SMTP_FROM"),
    )
    # "starttls" (587, upgrade), "implicit" (465, TLS-on-connect), or "none"
    # (plaintext — only for an in-cluster relay / MailHog).
    smtp_tls_mode: Literal["starttls", "implicit", "none"] = Field(
        default="starttls",
        validation_alias=AliasChoices("SMTP_TLS_MODE", "SMAP_SMTP_TLS_MODE"),
    )
    smtp_timeout_s: float = Field(
        default=15.0,
        validation_alias=AliasChoices("SMTP_TIMEOUT_S", "SMAP_SMTP_TIMEOUT_S"),
    )


class OAuthSection(BaseSettings):
    """Google OIDC login config (§6.1a / R6.14).

    Only the non-secret client id + connection params live here; the Google
    OAuth *client secret* is read from Vault KV ``secret/smap/config/google_oauth``
    by the adapter, never from the environment. ``google_client_id`` being empty is
    the signal that Google login is not configured (the button/endpoints fail
    closed, mirroring the SMTP posture).
    """

    model_config = SettingsConfigDict(env_prefix="SMAP_OAUTH_", extra="ignore")

    google_client_id: str = Field(
        default="",
        validation_alias=AliasChoices("GOOGLE_OAUTH_CLIENT_ID", "SMAP_OAUTH_GOOGLE_CLIENT_ID"),
    )
    # Path appended to the public origin to form the Google-registered redirect URI.
    google_redirect_path: str = Field(
        default="/api/auth/google/callback",
        validation_alias=AliasChoices("GOOGLE_OAUTH_REDIRECT_PATH", "SMAP_OAUTH_GOOGLE_REDIRECT_PATH"),
    )
    # TTL for the server-side (Redis) OAuth transaction state — an in-flight
    # round-trip is seconds long; 10 min is generous headroom.
    state_ttl_seconds: int = Field(
        default=600,
        validation_alias=AliasChoices("OAUTH_STATE_TTL_S", "SMAP_OAUTH_STATE_TTL_SECONDS"),
    )
    # Bounded timeout on the outbound token-exchange / JWKS calls so an
    # unauthenticated callback can never hang on a slow Google endpoint.
    http_timeout_s: float = Field(
        default=10.0,
        validation_alias=AliasChoices("OAUTH_HTTP_TIMEOUT_S", "SMAP_OAUTH_HTTP_TIMEOUT_S"),
    )


class ProviderProbeSection(BaseSettings):
    """Base URLs the key-upload live probes dial (R7.02).

    This configures the probe's *destination only*. The probe itself is
    unconditional and byte-identical in every environment: every upload still
    performs a real HTTP round-trip and still records the real outcome. There
    is deliberately no switch that skips, short-circuits, or force-passes it.

    Defaults are the real provider endpoints, so an unconfigured deployment
    probes the real providers. Overriding is for deployments that must dial an
    in-cluster egress gateway instead of the public internet, and for the E2E
    stack, which answers probes from a fake provider so CI can seed a working
    key without a real secret.

    A redirected probe validates nothing about the real provider, so
    ``_check_prod_secrets`` rejects any override when env is prod/staging.
    """

    model_config = SettingsConfigDict(env_prefix="SMAP_PROBE_", extra="ignore")

    anthropic_base_url: str = "https://api.anthropic.com"
    openai_base_url: str = "https://api.openai.com"
    gemini_base_url: str = "https://generativelanguage.googleapis.com"
    voyage_base_url: str = "https://api.voyageai.com"
    cohere_base_url: str = "https://api.cohere.com"


class LimitsSection(BaseSettings):
    """Numeric budgets keyed by §19.02 bucket; real enforcement lands in C.12."""

    model_config = SettingsConfigDict(env_prefix="SMAP_LIMIT_", extra="ignore")

    auth_per_min_ip: int = 10
    chat_per_min_user: int = 60
    upload_per_min_user: int = 10
    other_per_min_user: int = 300
    ws_concurrent_per_user: int = 5
    # F-4 (R14.07a): per-workflow ceiling on event-triggered runs within a
    # rolling window. Above any human- or agent-paced event rate, two orders of
    # magnitude below a runaway. Set the count to 0 to disable the breaker
    # (emergency lever without a code deploy).
    workflow_trigger_per_window: int = 20
    workflow_trigger_window_seconds: int = 60


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
    graphrag: GraphRagSection = Field(default_factory=GraphRagSection)
    minio: MinioSection = Field(default_factory=MinioSection)
    vault: VaultSection = Field(default_factory=VaultSection)
    jwt: JwtSection = Field(default_factory=JwtSection)
    observability: ObservabilitySection = Field(default_factory=ObservabilitySection)
    security: SecuritySection = Field(default_factory=SecuritySection)
    logging: LoggingSection = Field(default_factory=LoggingSection)
    limits: LimitsSection = Field(default_factory=LimitsSection)
    egress: EgressSection = Field(default_factory=EgressSection)
    sandbox: SandboxSection = Field(default_factory=SandboxSection)
    knowledge: KnowledgeSection = Field(default_factory=KnowledgeSection)
    email: EmailSection = Field(default_factory=EmailSection)
    oauth: OAuthSection = Field(default_factory=OAuthSection)
    provider_probe: ProviderProbeSection = Field(default_factory=ProviderProbeSection)


_EGRESS_DEV_DEFAULT = "0" * 63 + "1"  # known dev sentinel


def _check_prod_secrets(s: Settings) -> None:
    """Raise ConfigError if insecure dev defaults are active in prod/staging."""
    if s.app.env not in ("prod", "staging"):
        return
    is_prod = s.app.env == "prod"
    problems: list[str] = []
    if s.neo4j.password in ("neo4j", "neo4jneo4j"):
        problems.append("SMAP_NEO4J_PASSWORD is the insecure default")
    if s.minio.root_access_key == "minioadmin":
        problems.append("SMAP_MINIO_ROOT_ACCESS_KEY is the insecure default 'minioadmin'")
    if s.minio.root_secret_key == "minioadmin":  # noqa: S105 — comparing against the dev default
        problems.append("SMAP_MINIO_ROOT_SECRET_KEY is the insecure default 'minioadmin'")
    if s.vault.dev_token is not None and s.vault.dev_token != "":
        problems.append("SMAP_VAULT_DEV_TOKEN must not be set in production")
    if not (s.vault.role_id and s.vault.secret_id):
        problems.append("SMAP_VAULT_ROLE_ID and SMAP_VAULT_SECRET_ID must be set in production")
    # A probe pointed anywhere but the real provider proves nothing about the
    # uploaded key, so a redirected probe is a silently-accepted invalid key.
    # The override exists for the E2E stack; it must never survive into a
    # live-facing env, hence a hard fail rather than a warning.
    probe_defaults = ProviderProbeSection.model_fields
    for name, field in probe_defaults.items():
        if getattr(s.provider_probe, name) != field.default:
            problems.append(
                f"SMAP_PROBE_{name.upper()} must not be overridden in production"
                " — a redirected key probe validates nothing"
            )
    if not s.egress.shared_secret:
        problems.append("EGRESS_PROXY_SHARED_SECRET must not be empty in production")
    if s.egress.shared_secret == _EGRESS_DEV_DEFAULT:
        problems.append("EGRESS_PROXY_SHARED_SECRET is the insecure dev default — generate a real key")
    # S6: docs allowed in staging for debugging, but not in prod.
    if is_prod and s.app.docs_enabled:
        problems.append("SMAP_APP_DOCS_ENABLED must be false in production")
    if "*" in s.security.cors_origins:
        problems.append("SMAP_SEC_CORS_ORIGINS must not contain '*' in production (credentials leak)")
    if not s.security.cors_origins:
        problems.append("SMAP_SEC_CORS_ORIGINS must contain at least one origin in production")
    if not s.security.session_cookie_secure:
        problems.append("SMAP_SEC_SESSION_COOKIE_SECURE must be true in production (HTTPS required)")
    # Parse the DSN properly: a password-bearing Redis URL has a non-empty
    # password component between the scheme separator and the @ sign.
    try:
        from urllib.parse import urlparse

        parsed = urlparse(s.redis.dsn)
        if not parsed.password:
            problems.append(
                "SMAP_REDIS_DSN must include a password (redis://:PASSWORD@host:port/0)"
                " — prod Redis requires --requirepass"
            )
    except Exception:
        problems.append("SMAP_REDIS_DSN is not a valid URL")
    if problems:
        raise ConfigError("Insecure defaults active in production:\n  - " + "\n  - ".join(problems))


def _load(*, skip_prod_checks: bool = False) -> Settings:
    try:
        settings = Settings()
    except ValidationError as exc:
        missing = [".".join(str(p) for p in e["loc"]) for e in exc.errors()]
        msg = "Configuration errors:\n  - " + "\n  - ".join(missing)
        raise ConfigError(msg) from exc
    if not skip_prod_checks:
        _check_prod_secrets(settings)
    return settings


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return _load()


def get_settings_for_bootstrap() -> Settings:
    """Return settings without prod-secret checks (bootstrap needs dev tokens)."""
    return _load(skip_prod_checks=True)
