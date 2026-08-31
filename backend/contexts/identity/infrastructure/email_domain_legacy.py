"""The three pre-R19a.13 Redis keys, read and written atomically.

`config:email_domain:{allow,deny,mode}` were the whole policy before this change:
unversioned, TTL-less, with no writer and no way to read all three consistently.
They survive as a rollout/rollback bridge, because a replica running the previous
image knows only these keys, and are removed once every supported deployment is
past the compatibility version (the dossier's FU-4).

Two things the predecessor could not do, and both are why this module exists:

* **Atomicity.** The old reader used a non-transactional pipeline, so it could
  combine `mode` from one update with `allow` from another. Both operations here
  are single Lua scripts, so Redis executes them without interleaving.
* **Classification.** Redis cannot distinguish a key that was never set from a
  set that was emptied, so the import matrix (Q-10) rejects every corrupt shape
  that *is* distinguishable and refuses to guess at the rest. All three keys
  absent is the one absence with an unambiguous reading — explicit `off`.
"""

from __future__ import annotations

from typing import Any, Final

from contexts.identity.domain.email_domain_policy import (
    EmailDomainPolicy,
    EmailDomainPolicyMode,
    EmailDomainPolicyRolloutState,
    normalise_domain,
)
from contexts.identity.domain.errors import (
    EmailDomainPolicyUnavailable,
    InvalidEmailDomain,
    InvalidLegacyEmailDomainPolicy,
)
from shared_kernel.auth.clients import get_redis

KEY_ALLOW: Final = "config:email_domain:allow"
KEY_DENY: Final = "config:email_domain:deny"
KEY_MODE: Final = "config:email_domain:mode"

# TYPE is consulted first and SMEMBERS/GET only run on a matching type: calling
# SMEMBERS on a string aborts the script, which would report "Redis is broken"
# for what is actually a specific, reportable corruption.
_READ_SCRIPT: Final = """
local function kind(key) return redis.call('TYPE', key)['ok'] end
local allow_type, deny_type, mode_type = kind(KEYS[1]), kind(KEYS[2]), kind(KEYS[3])
local allow, deny, mode = {}, {}, false
if allow_type == 'set' then allow = redis.call('SMEMBERS', KEYS[1]) end
if deny_type == 'set' then deny = redis.call('SMEMBERS', KEYS[2]) end
if mode_type == 'string' then mode = redis.call('GET', KEYS[3]) end
return {allow_type, deny_type, mode_type, allow, deny, mode}
"""

# DEL before SADD, so a replacement removes members the new policy dropped
# instead of unioning with them. One script, so no reader can observe the gap.
# ARGV is [mode, allow_count, deny_count, <allow...>, <deny...>]: the counts are
# passed rather than inferred, so the two variable-length runs can share one
# flat argument list without a delimiter a domain could impersonate.
_REPLACE_SCRIPT: Final = """
redis.call('DEL', KEYS[1], KEYS[2])
local allow_count = tonumber(ARGV[2])
local deny_count = tonumber(ARGV[3])
for i = 1, allow_count do redis.call('SADD', KEYS[1], ARGV[3 + i]) end
for i = 1, deny_count do redis.call('SADD', KEYS[2], ARGV[3 + allow_count + i]) end
redis.call('SET', KEYS[3], ARGV[1])
return 1
"""


class RedisLegacyEmailDomainPolicyStore:
    async def read_snapshot(self) -> EmailDomainPolicy:
        try:
            raw = await get_redis().eval(_READ_SCRIPT, 3, KEY_ALLOW, KEY_DENY, KEY_MODE)
        except Exception as exc:
            # Unreadable is not empty. With no database row this blocks boot, and
            # in compatibility it fails the request closed — never `off`.
            raise EmailDomainPolicyUnavailable("the legacy email-domain keys are unreadable") from exc
        return classify_legacy_snapshot(raw)

    async def replace(self, policy: EmailDomainPolicy) -> None:
        allow = sorted(policy.allow)
        deny = sorted(policy.deny)
        args: list[Any] = [policy.mode.value, len(allow), len(deny), *allow, *deny]
        try:
            await get_redis().eval(_REPLACE_SCRIPT, 3, KEY_ALLOW, KEY_DENY, KEY_MODE, *args)
        except Exception as exc:
            raise EmailDomainPolicyUnavailable("the legacy email-domain keys are unwritable") from exc


def classify_legacy_snapshot(raw: Any) -> EmailDomainPolicy:
    """Turn one Lua reply into a policy, or reject it (Q-10).

    Separate from the store so the whole matrix is testable without Redis, and
    because it is the part that decides whether a deployment boots.
    """
    allow_type, deny_type, mode_type, allow_members, deny_members, mode_value = raw

    for key, kind, expected in (
        (KEY_ALLOW, allow_type, "set"),
        (KEY_DENY, deny_type, "set"),
        (KEY_MODE, mode_type, "string"),
    ):
        if kind not in (expected, "none"):
            raise InvalidLegacyEmailDomainPolicy(f"{key} holds a {kind}, expected {expected} or nothing")

    allow = _normalise_members(KEY_ALLOW, allow_members)
    deny = _normalise_members(KEY_DENY, deny_members)

    if mode_value is None:
        # All three absent is the only unambiguous absence: nobody ever
        # configured this deployment, so `off` is a fact rather than a guess.
        # A list with members but no mode is not — the operator set one half of
        # a policy, and either reading of the missing half could be wrong.
        if allow or deny:
            raise InvalidLegacyEmailDomainPolicy(f"{KEY_MODE} is absent while a domain list holds members")
        return _policy(EmailDomainPolicyMode.OFF, frozenset(), frozenset())

    try:
        mode = EmailDomainPolicyMode(str(mode_value).strip().lower())
    except ValueError as exc:
        raise InvalidLegacyEmailDomainPolicy(f"{KEY_MODE} holds {mode_value!r}") from exc
    # An empty allow list under `allow` is a legal deny-all and an empty deny
    # list under `deny` is a legal allow-all; neither is evidence of loss.
    return _policy(mode, allow, deny)


def _normalise_members(key: str, members: object) -> frozenset[str]:
    if not isinstance(members, list):  # pragma: no cover - the script returns a table
        raise InvalidLegacyEmailDomainPolicy(f"{key} did not return a list")
    try:
        return frozenset(normalise_domain(str(member)) for member in members)
    except InvalidEmailDomain as exc:
        raise InvalidLegacyEmailDomainPolicy(f"{key} holds an invalid domain: {exc}") from exc


def _policy(mode: EmailDomainPolicyMode, allow: frozenset[str], deny: frozenset[str]) -> EmailDomainPolicy:
    # Version 0 and `compatibility`: this snapshot is not a stored version and
    # must never be mistaken for one. `create_from_legacy` assigns version 1.
    return EmailDomainPolicy(
        mode=mode,
        allow=allow,
        deny=deny,
        version=0,
        rollout_state=EmailDomainPolicyRolloutState.COMPATIBILITY,
    )


__all__ = [
    "KEY_ALLOW",
    "KEY_DENY",
    "KEY_MODE",
    "RedisLegacyEmailDomainPolicyStore",
    "classify_legacy_snapshot",
]
