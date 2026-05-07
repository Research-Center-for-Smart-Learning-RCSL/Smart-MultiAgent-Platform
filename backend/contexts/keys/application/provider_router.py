"""Provider call router + rotation + exhaustion (D.7 / R7.06–R7.08).

Responsibilities
----------------

1. Pick the next eligible `key_group_members` row by ascending priority.
2. Check hourly caps via `redis_buckets.usage`; a row past any cap is
   temporarily unavailable (``token_quota``).
3. Unwrap the DEK (with AAD bound to the row's id), hand the plaintext
   secret to a provider adapter, HTTPS call, record usage, zeroise.
4. Classify the response. Retry with exp-backoff / rotate / surface error
   according to the member's `RotationPolicy`.
5. Exhaustion (R7.08):
   - All members quota-exhausted → queue up to `queue_wait_seconds` and
     re-poll; still unavailable → `KeyGroupExhausted(reason="quota")`.
   - All members errored through their retry budgets → `KeyGroupExhausted(
     reason="errors")`.

The adapter layer (Phase E) shapes provider-specific requests. The router
is provider-agnostic: it hands the adapter a secret + the caller's abstract
request and takes back a `ProviderCallResult` that encapsulates the HTTP
status plus usage counters.

SoC: no FastAPI. Callers are Phase E agent runners and the admin test
endpoint. The router opens its own `httpx`-free façade by delegating to
`ProviderAdapter.invoke(secret, request)`.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.keys.application.router_policy import (
    ErrorOutcome,
    RotationReason,
    backoff_delay_ms,
    classify_http,
)
from contexts.keys.domain.errors import KeyGroupExhausted
from contexts.keys.domain.groups import HourlyLimits, KeyGroupMember
from contexts.keys.domain.models import ApiKey
from contexts.keys.domain.providers import ApiKeyProvider, ProviderCapability
from contexts.keys.infrastructure.group_repository import KeyGroupMemberRepository
from contexts.keys.infrastructure.repositories import ApiKeyRepository
from contexts.keys.infrastructure.usage_events import record_usage_event
from shared_kernel.infra import redis_buckets
from shared_kernel.observability.metrics import KEY_GROUP_EXHAUSTED_TOTAL, PROVIDER_CALL_TOTAL
from shared_kernel.security import envelope as env

_log = logging.getLogger(__name__)

DEFAULT_QUEUE_WAIT_SECONDS = 60
_QUEUE_POLL_INTERVAL_S = 5.0


# ---------------------------------------------------------------------------
# Adapter seam — concrete classes land in Phase E.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ProviderRequest:
    """Provider-agnostic call request."""

    capability: ProviderCapability
    payload: dict[str, Any]
    agent_id: uuid.UUID | None = None
    parent_agent_id: uuid.UUID | None = None
    chatroom_id: uuid.UUID | None = None


@dataclass(frozen=True, slots=True)
class ProviderCallResult:
    http_status: int
    body: dict[str, Any]
    input_tokens: int = 0
    output_tokens: int = 0
    request_ms: int = 0


class ProviderAdapter(Protocol):
    provider: ApiKeyProvider

    async def invoke(self, *, secret: str, request: ProviderRequest) -> ProviderCallResult: ...


# ---------------------------------------------------------------------------
# DEK cache — per-process, short TTL, invalidated by pub/sub.
# ---------------------------------------------------------------------------


@dataclass
class _DekCacheEntry:
    plaintext: bytes
    loaded_at: float


class _DekCache:
    TTL_SECONDS = 60.0

    def __init__(self) -> None:
        self._entries: dict[uuid.UUID, _DekCacheEntry] = {}

    def get(self, key_id: uuid.UUID) -> bytes | None:
        entry = self._entries.get(key_id)
        if entry is None:
            return None
        if time.monotonic() - entry.loaded_at > self.TTL_SECONDS:
            self._entries.pop(key_id, None)
            return None
        return entry.plaintext

    def put(self, key_id: uuid.UUID, plaintext: bytes) -> None:
        self._entries[key_id] = _DekCacheEntry(plaintext, time.monotonic())

    def drop(self, key_id: uuid.UUID) -> None:
        self._entries.pop(key_id, None)

    def clear(self) -> None:
        self._entries.clear()


# Module singleton — the revocation listener (`revocation_listener.py`)
# punches entries out in response to Redis pub/sub.
DEK_CACHE = _DekCache()


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _EligibleMember:
    member: KeyGroupMember
    key: ApiKey


@dataclass
class _MemberState:
    """Per-call tracking for one member — attempts burned, last error."""

    attempts: int = 0
    last_outcome: ErrorOutcome | None = None
    quota_blocked: bool = False
    exhausted: bool = False  # terminal non-OK — don't re-try on subsequent passes


@dataclass
class RouterConfig:
    queue_wait_seconds: int = DEFAULT_QUEUE_WAIT_SECONDS


class ProviderRouter:
    def __init__(
        self,
        db: AsyncSession,
        adapters: dict[ApiKeyProvider, ProviderAdapter],
        *,
        config: RouterConfig | None = None,
    ) -> None:
        self._db = db
        self._adapters = adapters
        self._config = config or RouterConfig()
        self._members_repo = KeyGroupMemberRepository(db)
        self._keys_repo = ApiKeyRepository(db)

    async def call(self, *, group_id: uuid.UUID, request: ProviderRequest) -> ProviderCallResult:
        """Execute the request against `group_id` with full rotation policy.

        Per-member: exhaust the retry budget (`retry_max` retries after the
        initial attempt, total ≤ `retry_max + 1`) before rotating.
        Per-group: if every member has either failed fatally or exhausted
        retries AND none is quota-blocked → `KeyGroupExhausted("errors")`.
        If any member is still quota-blocked → sleep & re-poll until
        `queue_wait_seconds` → `KeyGroupExhausted("quota")`.
        """
        members = await self._load_eligible(group_id, request.capability)
        if not members:
            raise KeyGroupExhausted(group_id=group_id, reason="no_members")

        state: dict[uuid.UUID, _MemberState] = {em.key.id: _MemberState() for em in members}
        quota_deadline = time.monotonic() + self._config.queue_wait_seconds

        while True:
            for em in members:
                st = state[em.key.id]
                if st.exhausted:
                    continue
                if await self._quota_exceeded(em):
                    st.quota_blocked = True
                    continue
                st.quota_blocked = False

                adapter = self._adapters.get(em.key.provider)
                if adapter is None:
                    st.last_outcome = ErrorOutcome(
                        RotationReason.FATAL,
                        None,
                        "no_adapter",
                    )
                    st.exhausted = True
                    continue

                result = await self._try_member(em, request, adapter, st)
                if result is not None:
                    return result
                # _try_member flipped `st.exhausted` for non-OK terminal outcomes.

            # End of pass. Decide whether to exit or queue-wait.
            any_quota = any(s.quota_blocked and not s.exhausted for s in state.values())
            if not any_quota:
                KEY_GROUP_EXHAUSTED_TOTAL.labels(reason="errors").inc()
                raise KeyGroupExhausted(group_id=group_id, reason="errors")
            if time.monotonic() >= quota_deadline:
                KEY_GROUP_EXHAUSTED_TOTAL.labels(reason="quota").inc()
                raise KeyGroupExhausted(group_id=group_id, reason="quota")

            sleep_for = min(
                _QUEUE_POLL_INTERVAL_S,
                max(0.5, quota_deadline - time.monotonic()),
            )
            await asyncio.sleep(sleep_for)

    async def _try_member(
        self,
        em: _EligibleMember,
        request: ProviderRequest,
        adapter: ProviderAdapter,
        st: _MemberState,
    ) -> ProviderCallResult | None:
        """Exhaust one member's retry budget. Returns a successful result or
        None. Flips `st.exhausted` on any terminal non-OK outcome.
        """
        retry_max = em.member.rotation.retry_max
        while True:
            try:
                outcome, result = await self._call_member(em, request, adapter)
            except _KeyVanished:
                st.last_outcome = ErrorOutcome(RotationReason.FATAL, None, "key_vanished")
                st.exhausted = True
                return None
            st.attempts += 1
            st.last_outcome = outcome

            if outcome.reason is RotationReason.OK:
                assert result is not None
                return result
            if outcome.reason is RotationReason.FATAL:
                st.exhausted = True
                return None
            if outcome.reason is RotationReason.RETRY and st.attempts <= retry_max:
                delay_ms = backoff_delay_ms(st.attempts, em.member.rotation)
                await asyncio.sleep(delay_ms / 1000)
                continue
            # ROTATE, or RETRY exhausted → terminal for this member, advance.
            st.exhausted = True
            return None

    # -----------------------------------------------------------------

    async def _load_eligible(
        self, group_id: uuid.UUID, capability: ProviderCapability
    ) -> list[_EligibleMember]:
        members = await self._members_repo.list_ordered(group_id)
        eligible: list[_EligibleMember] = []
        for m in members:
            key = await self._keys_repo.get_active(m.key_id)
            if key is None:
                continue
            if capability not in _CAPS[key.provider]:
                continue
            eligible.append(_EligibleMember(member=m, key=key))
        return eligible

    async def _quota_exceeded(self, em: _EligibleMember) -> bool:
        lim = em.member.limits
        if _limits_unbounded(lim):
            return False
        used = await redis_buckets.usage(em.key.id)
        if lim.max_input_tokens_per_hour and used.input_tokens >= lim.max_input_tokens_per_hour:
            return True
        if lim.max_output_tokens_per_hour and used.output_tokens >= lim.max_output_tokens_per_hour:
            return True
        if lim.max_requests_per_hour and used.requests >= lim.max_requests_per_hour:
            return True
        return False

    async def _call_member(
        self,
        em: _EligibleMember,
        request: ProviderRequest,
        adapter: ProviderAdapter,
    ) -> tuple[ErrorOutcome, ProviderCallResult | None]:
        secret = await self._unwrap_secret(em.key.id)
        t0 = time.monotonic()
        try:
            result = await adapter.invoke(secret=secret.decode("utf-8"), request=request)
        except Exception as exc:
            elapsed_ms = int((time.monotonic() - t0) * 1000)
            _log.warning("adapter transport error key=%s err=%s", em.key.id, exc)
            await record_usage_event(
                self._db,
                key_id=em.key.id,
                input_tokens=0,
                output_tokens=0,
                request_ms=elapsed_ms,
                http_status=None,
                error_code="transport_error",
                agent_id=request.agent_id,
                parent_agent_id=request.parent_agent_id,
                chatroom_id=request.chatroom_id,
            )
            return ErrorOutcome(RotationReason.RETRY, None, "transport_error"), None
        finally:
            secret = b"\x00" * len(secret)  # scrub rebind
        elapsed_ms = int((time.monotonic() - t0) * 1000)

        outcome = classify_http(result.http_status, em.member.rotation)
        await redis_buckets.record(
            em.key.id,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            requests=1,
        )
        await record_usage_event(
            self._db,
            key_id=em.key.id,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            request_ms=elapsed_ms,
            http_status=result.http_status,
            error_code=outcome.error_code,
            agent_id=request.agent_id,
            parent_agent_id=request.parent_agent_id,
            chatroom_id=request.chatroom_id,
        )
        PROVIDER_CALL_TOTAL.labels(
            provider=em.key.provider.value,
            status=str(result.http_status),
        ).inc()
        return outcome, (result if outcome.reason is RotationReason.OK else None)

    async def _unwrap_secret(self, key_id: uuid.UUID) -> bytes:
        cached = DEK_CACHE.get(key_id)
        if cached is not None:
            return cached
        loaded = await self._keys_repo.get_active_with_envelope(key_id)
        if loaded is None:
            # Key deleted mid-call. Surface as fatal for this member — caller
            # rotates to the next priority.
            raise _KeyVanished(key_id)
        _, record = loaded
        plaintext = env.decrypt_envelope(record, env.api_key_aad(key_id))
        DEK_CACHE.put(key_id, plaintext)
        return plaintext


class _KeyVanished(RuntimeError):
    def __init__(self, key_id: uuid.UUID) -> None:
        super().__init__(f"key {key_id} was removed mid-call")
        self.key_id = key_id


# R7.01 lookup — kept inline to avoid importing `providers.capabilities_of`
# every call; the table is tiny and immutable.
_CAPS: dict[ApiKeyProvider, frozenset[ProviderCapability]] = {
    ApiKeyProvider.CLAUDE: frozenset({ProviderCapability.LLM_CHAT}),
    ApiKeyProvider.OPENAI: frozenset({ProviderCapability.LLM_CHAT, ProviderCapability.EMBEDDING}),
    ApiKeyProvider.GEMINI: frozenset({ProviderCapability.LLM_CHAT, ProviderCapability.EMBEDDING}),
    ApiKeyProvider.VOYAGE: frozenset({ProviderCapability.EMBEDDING}),
    ApiKeyProvider.COHERE: frozenset({ProviderCapability.RERANK}),
}


def _limits_unbounded(lim: HourlyLimits) -> bool:
    return (
        lim.max_input_tokens_per_hour is None
        and lim.max_output_tokens_per_hour is None
        and lim.max_requests_per_hour is None
    )


__all__ = [
    "DEK_CACHE",
    "DEFAULT_QUEUE_WAIT_SECONDS",
    "ProviderAdapter",
    "ProviderCallResult",
    "ProviderRequest",
    "ProviderRouter",
    "RouterConfig",
]
