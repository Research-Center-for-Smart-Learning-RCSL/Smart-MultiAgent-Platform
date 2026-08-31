"""The disposable v2 acceleration mirror for the email-domain policy (R19a.13).

One JSON value under one key, written with a 30-second expiry and never renewed
from itself. That is the whole freshness contract: a value that cannot be
extended from a cache hit has a hard upper bound on staleness without needing an
outbox, a background worker, or a successful invalidation.

`v2` is in the key name because the three `config:email_domain:*` keys are still
live for old replicas. A shared name would have made one image's write the other
image's corrupt read.

Every failure mode here is a cache miss: missing, expired, evicted, truncated,
wrong schema, unparsable JSON, or a `rollout_state` this build does not know. The
last one is not a nuance — defaulting an unrecognised phase would let a reader
enforce one version's policy under another version's authority.
"""

from __future__ import annotations

from typing import Any, Final

import orjson
from loguru import logger

from contexts.identity.application.ports import MirroredPolicy
from contexts.identity.domain.email_domain_policy import (
    EmailDomainPolicy,
    EmailDomainPolicyMode,
    EmailDomainPolicyRolloutState,
)
from shared_kernel.auth.clients import get_redis

KEY: Final = "config:email_domain:policy:v2"
#: Bumped whenever the value's shape changes. A reader that does not recognise
#: the schema treats the value as absent rather than as partially readable.
SCHEMA: Final = 2
TTL_SECONDS: Final = 30


class RedisEmailDomainPolicyMirror:
    async def read(self) -> MirroredPolicy | None:
        # Value and PTTL in one round trip, so the remaining life belongs to the
        # value that was returned. Read separately, the value could expire
        # between the two calls and the caller would cache it for a life it no
        # longer had.
        redis = get_redis()
        async with redis.pipeline(transaction=True) as pipe:
            pipe.get(KEY)
            pipe.pttl(KEY)
            raw, pttl_ms = await pipe.execute()
        if raw is None:
            return None
        # -1 is "no expiry" and -2 is "no key". Neither may be served: this key
        # is only ever written with a TTL, so a TTL-less one is not a value this
        # code wrote, and treating it as fresh would make it immortal.
        if not isinstance(pttl_ms, int) or pttl_ms <= 0:
            return None
        policy = _decode(raw)
        if policy is None:
            return None
        return MirroredPolicy(policy=policy, ttl_seconds=pttl_ms / 1000.0)

    async def write(self, policy: EmailDomainPolicy) -> None:
        value = orjson.dumps(
            {
                "schema": SCHEMA,
                "rollout_state": policy.rollout_state.value,
                "version": policy.version,
                "mode": policy.mode.value,
                "allow": sorted(policy.allow),
                "deny": sorted(policy.deny),
            }
        )
        await get_redis().set(KEY, value, ex=TTL_SECONDS)

    async def delete(self) -> None:
        await get_redis().delete(KEY)


def _decode(raw: str | bytes) -> EmailDomainPolicy | None:
    try:
        payload: Any = orjson.loads(raw)
    except (orjson.JSONDecodeError, ValueError, TypeError):
        _miss("unparsable")
        return None
    if not isinstance(payload, dict) or payload.get("schema") != SCHEMA:
        _miss("wrong schema")
        return None
    try:
        state = EmailDomainPolicyRolloutState(payload["rollout_state"])
        mode = EmailDomainPolicyMode(payload["mode"])
        version = int(payload["version"])
        allow = frozenset(str(d) for d in payload["allow"])
        deny = frozenset(str(d) for d in payload["deny"])
    except (KeyError, TypeError, ValueError):
        # An unrecognised rollout_state lands here with everything else that is
        # structurally wrong, which is the intent: it is missing information, not
        # a phase to pick a default for.
        _miss("unreadable fields")
        return None
    return EmailDomainPolicy(mode=mode, allow=allow, deny=deny, version=version, rollout_state=state)


def _miss(reason: str) -> None:
    logger.bind(event="email_domain_policy_mirror_malformed", reason=reason).warning(
        "discarding the email-domain policy mirror value; falling back to the database"
    )


__all__ = ["KEY", "SCHEMA", "TTL_SECONDS", "RedisEmailDomainPolicyMirror"]
