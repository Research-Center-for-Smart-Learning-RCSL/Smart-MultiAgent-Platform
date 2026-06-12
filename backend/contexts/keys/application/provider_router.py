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
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.keys.application.router_policy import (
    ErrorOutcome,
    RotationReason,
    backoff_delay_ms,
    classify_http,
)
from contexts.keys.domain.errors import CapabilityMismatch, KeyGroupExhausted, KeyNotFound
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
# Streaming seam (K.1). Chat adapters yield token deltas as they arrive and
# close with exactly one `StreamComplete` carrying final usage. Embedding /
# rerank adapters do not implement `stream`.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TokenDelta:
    """One incremental chunk of assistant text."""

    text: str


@dataclass(frozen=True, slots=True)
class StreamComplete:
    """Terminal stream event — carries the same shape as a non-stream call.

    `result.body` holds the accumulated `{text, tool_calls, finish_reason}`
    so a consumer that missed deltas can still reconstruct the full turn.
    `result.http_status` is the provider's status; a non-2xx with no preceding
    `TokenDelta` lets the router rotate to the next key.
    """

    result: ProviderCallResult


StreamEvent = TokenDelta | StreamComplete


@runtime_checkable
class StreamingAdapter(Protocol):
    """Optional capability — chat adapters add streaming on top of `invoke`."""

    provider: ApiKeyProvider

    def stream(
        self, *, secret: str, request: ProviderRequest
    ) -> AsyncGenerator[StreamEvent, None]: ...


class ProviderStreamError(RuntimeError):
    """A streamed turn failed *after* the first token — not retryable.

    Per the K.1 adapter contract (and §K Risks): once any text has been
    emitted to the client we cannot transparently rotate keys, because the
    partial output is already committed. The turn fails visibly (R9.09).
    """

    def __init__(self, http_status: int) -> None:
        super().__init__(f"provider stream failed after first token (HTTP {http_status})")
        self.http_status = http_status


class _RotateStream(RuntimeError):
    """Internal — a stream failed *before* the first token; rotate to next key."""


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

    async def call_stream(
        self, *, group_id: uuid.UUID, request: ProviderRequest
    ) -> AsyncGenerator[StreamEvent, None]:
        """Streaming variant of :meth:`call` with rotate-before-first-token.

        Rotation semantics are deliberately weaker than the unary path
        (§K Risks): a member that fails *before* emitting any `TokenDelta`
        is abandoned and the next priority is tried; once the first token
        has been yielded the turn is committed and a later failure raises
        `ProviderStreamError` rather than silently rotating (partial output
        cannot be replayed across keys). There is no same-member retry and
        no quota queue-wait — a human is waiting on the socket.

        Yields `TokenDelta` events followed by one terminal `StreamComplete`.
        Raises `KeyGroupExhausted` if no member can serve the request.
        """
        members = await self._load_eligible(group_id, request.capability)
        if not members:
            raise KeyGroupExhausted(group_id=group_id, reason="no_members")

        quota_blocked = False
        for em in members:
            if await self._quota_exceeded(em):
                quota_blocked = True
                continue
            # Widen to `object` so the StreamingAdapter Protocol check narrows
            # correctly — `ProviderAdapter` lacks `stream`, which would otherwise
            # make mypy treat the positive branch as unreachable.
            adapter_obj: object = self._adapters.get(em.key.provider)
            if not isinstance(adapter_obj, StreamingAdapter):
                # No streaming adapter for this provider — treat as a fatal
                # member (mis-provisioned group) and rotate.
                continue
            # Own the inner generator's lifecycle: closing it in `finally`
            # guarantees its usage accounting runs even when *our* consumer
            # abandons the stream — `async for` does not aclose its sub-iterator,
            # so the abort signal must be propagated explicitly down the chain.
            inner = self._stream_member(em, request, adapter_obj)
            try:
                async for ev in inner:
                    yield ev
                return  # streamed to completion
            except (_RotateStream, _KeyVanished):
                continue
            finally:
                await inner.aclose()
        # Nothing produced a stream.
        if quota_blocked:
            KEY_GROUP_EXHAUSTED_TOTAL.labels(reason="quota").inc()
            raise KeyGroupExhausted(group_id=group_id, reason="quota")
        KEY_GROUP_EXHAUSTED_TOTAL.labels(reason="errors").inc()
        raise KeyGroupExhausted(group_id=group_id, reason="errors")

    async def _stream_member(
        self,
        em: _EligibleMember,
        request: ProviderRequest,
        adapter: StreamingAdapter,
    ) -> AsyncGenerator[StreamEvent, None]:
        """Drive one member's stream; account usage exactly once; classify status.

        Accounting lives in the `finally` so it fires on every exit path —
        normal completion, provider transport error, **and** consumer
        abandonment (client disconnect → `GeneratorExit`, or an exception thrown
        into our `yield`). A consumer-side abort records ``client_abort`` rather
        than being mislabelled a provider ``transport_error``; a provider
        transport error is caught around the adapter's `__anext__` only, so it is
        never confused with an exception originating in the consumer.

        Raises `_RotateStream` when nothing was emitted (safe to rotate),
        `ProviderStreamError` when a non-OK terminal status follows ≥1 token,
        and re-raises provider transport errors that occur after the first token.
        """
        secret_bytes = await self._unwrap_secret(em.key.id)
        t0 = time.monotonic()
        first_token = False
        complete: StreamComplete | None = None
        transport_exc: Exception | None = None

        async def _account() -> None:
            # Best-effort and self-contained: we run inside a `finally`, so a
            # DB/Redis hiccup must never mask the real control-flow outcome.
            try:
                elapsed_ms = int((time.monotonic() - t0) * 1000)
                if complete is not None:
                    res = complete.result
                    error_code = classify_http(res.http_status, em.member.rotation).error_code
                    await redis_buckets.record(
                        em.key.id,
                        input_tokens=res.input_tokens,
                        output_tokens=res.output_tokens,
                        requests=1,
                    )
                    await record_usage_event(
                        self._db,
                        key_id=em.key.id,
                        input_tokens=res.input_tokens,
                        output_tokens=res.output_tokens,
                        request_ms=res.request_ms or elapsed_ms,
                        http_status=res.http_status,
                        error_code=error_code,
                        agent_id=request.agent_id,
                        parent_agent_id=request.parent_agent_id,
                        chatroom_id=request.chatroom_id,
                    )
                    PROVIDER_CALL_TOTAL.labels(
                        provider=em.key.provider.value, status=str(res.http_status)
                    ).inc()
                else:
                    # No terminal event: a provider transport error, or the
                    # consumer walked away mid-stream (token counts unknown).
                    code = "transport_error" if transport_exc is not None else "client_abort"
                    await record_usage_event(
                        self._db,
                        key_id=em.key.id,
                        input_tokens=0,
                        output_tokens=0,
                        request_ms=elapsed_ms,
                        http_status=None,
                        error_code=code,
                        agent_id=request.agent_id,
                        parent_agent_id=request.parent_agent_id,
                        chatroom_id=request.chatroom_id,
                    )
            except Exception:
                _log.exception("stream usage accounting failed key=%s", em.key.id)

        agen = adapter.stream(secret=secret_bytes.decode("utf-8"), request=request)
        try:
            while True:
                try:
                    ev = await agen.__anext__()
                except StopAsyncIteration:
                    break
                except Exception as exc:  # provider transport error
                    transport_exc = exc
                    _log.warning("stream transport error key=%s err=%s", em.key.id, exc)
                    break
                if isinstance(ev, TokenDelta):
                    first_token = True
                    # A consumer exception / GeneratorExit lands HERE, outside the
                    # inner try — so it is never miscounted as a provider failure.
                    yield ev
                else:  # StreamComplete
                    complete = ev

            if transport_exc is not None:
                if first_token:
                    raise transport_exc
                raise _RotateStream from transport_exc
            if complete is None:
                # Adapter closed without a terminal event — dry failure.
                if first_token:
                    raise ProviderStreamError(0)
                raise _RotateStream
            outcome = classify_http(complete.result.http_status, em.member.rotation)
            if outcome.reason is RotationReason.OK:
                yield complete
                return
            # Non-OK terminal status.
            if first_token:
                raise ProviderStreamError(complete.result.http_status)
            raise _RotateStream
        finally:
            await _account()
            await agen.aclose()
            secret_bytes = b"\x00" * len(secret_bytes)  # scrub rebind

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

    async def call_single_key(
        self, *, key_id: uuid.UUID, request: ProviderRequest
    ) -> ProviderCallResult:
        """Pinned-key call — no rotation — for embedding / rerank traffic.

        Rotation is wrong for these: RAG pins one ``embed_key_id`` and a
        GraphRAG collection's vector *dimensions* depend on the embedding
        model, so silently rotating to another provider's key would corrupt
        the index. This still routes through the concrete adapter (no
        key-prefix sniffing) and writes a `key_usage_events` row (R7.12), so
        no caller needs ``unwrap_api_key_plaintext`` + raw httpx anymore.
        """
        key = await self._keys_repo.get_active(key_id)
        if key is None:
            raise KeyNotFound(str(key_id))
        if request.capability not in _CAPS[key.provider]:
            raise CapabilityMismatch(provider=key.provider, required=request.capability)
        adapter = self._adapters.get(key.provider)
        if adapter is None:
            raise ValueError(f"no adapter for provider {key.provider.value}")

        secret = await self._unwrap_secret(key_id)
        t0 = time.monotonic()
        try:
            result = await adapter.invoke(secret=secret.decode("utf-8"), request=request)
        except Exception:
            elapsed_ms = int((time.monotonic() - t0) * 1000)
            await record_usage_event(
                self._db,
                key_id=key_id,
                input_tokens=0,
                output_tokens=0,
                request_ms=elapsed_ms,
                http_status=None,
                error_code="transport_error",
                agent_id=request.agent_id,
                parent_agent_id=request.parent_agent_id,
                chatroom_id=request.chatroom_id,
            )
            raise
        finally:
            secret = b"\x00" * len(secret)  # scrub rebind
        elapsed_ms = int((time.monotonic() - t0) * 1000)

        status = result.http_status
        if 200 <= status < 300:
            error_code: str | None = None
        elif status in (401, 403):
            error_code = "provider_unauthorized"
        else:
            error_code = f"http_{status}"
        await redis_buckets.record(
            key_id,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            requests=1,
        )
        await record_usage_event(
            self._db,
            key_id=key_id,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            request_ms=result.request_ms or elapsed_ms,
            http_status=status,
            error_code=error_code,
            agent_id=request.agent_id,
            parent_agent_id=request.parent_agent_id,
            chatroom_id=request.chatroom_id,
        )
        PROVIDER_CALL_TOTAL.labels(provider=key.provider.value, status=str(status)).inc()
        return result

    # -----------------------------------------------------------------

    async def _load_eligible(
        self, group_id: uuid.UUID, capability: ProviderCapability
    ) -> list[_EligibleMember]:
        # SEC-H3: `list_ordered_carried` (not `list_ordered`) requires an
        # active `key_projects` carry into the group's project, so a withdrawn
        # or never-carried key is never selected. `get_active` then filters
        # soft-deleted keys. Both are fresh DB reads per call, so the
        # authorization gate does not rely on the DEK cache being invalidated
        # in time — a revoked key is excluded here, before `_unwrap_secret`.
        members = await self._members_repo.list_ordered_carried(group_id)
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
    "ProviderStreamError",
    "RouterConfig",
    "StreamComplete",
    "StreamEvent",
    "StreamingAdapter",
    "TokenDelta",
]
