---
type: feature
status: implemented
created: 2026-08-30
approved: 2026-08-31
implemented: 2026-08-31
requirements: [R6.18, R19a.13]
depends_on: []
---

# Identity onboarding policy hardening

## 1. Summary

Close FU-6, FU-9, FU-10 and FU-11 of `2026-08-20-onboarding-without-smtp` without
turning a Redis compatibility bridge into a second authority. Make the email-domain policy
durable and admin-manageable, refuse activation-link minting for banned accounts, and bound
per-admin provisioning. A three-state rollout protocol protects mixed-version deployment and
rollback because the current application reads three unversioned Redis keys that cannot be
atomically dual-written with PostgreSQL.

Freshness was re-verified against `main` at `73125821` (2026-08-28). The policy is still a
module-global 30-second cache over three Redis keys and no API writes them
(`backend/contexts/identity/infrastructure/email_domain_policy.py:1-60`). The activation and
provisioning hardening gaps recorded in the source dossier also remain.

## 2. Goals and Non-goals

**Goals**

- Persist one versioned, audited email-domain policy in PostgreSQL and expose an Admin UI/API.
- Preserve a restrictive legacy policy through first deployment, mixed-version operation and a
  prepared rollback without pretending PostgreSQL and Redis can commit atomically.
- Ensure active-version readers converge on a committed policy within 30 seconds even when a
  Redis mirror write fails, is evicted or is malformed.
- Fail closed when the policy authority is unavailable; missing cache state never means `off`.
- Refuse activation links for banned accounts before any rate-limit, token or audit side effect.
- Limit one Admin to 60 account creations in a rolling 10-minute window.

**Non-goals**

- Bulk/CSV onboarding, invitation batching or batch-specific rate limits.
- Wildcard, suffix or public-suffix-aware domain matching; exact normalized domains only.
- Removing the legacy Redis reader in this change. It remains a rollout/rollback bridge.
- General identity layer cleanup beyond the minimum facade, port and factory seam required here.
- Automatically detecting that every old replica has drained. Activation is an explicit operator
  assertion and is documented as such.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Which onboarding follow-ups belong in one PR? | FU-6, FU-9, FU-10 and FU-11 from `2026-08-20-onboarding-without-smtp`. | They share the Admin onboarding boundary, identity service, security tests and operator story. FU-8/FU-13 are member-picker pagination; FU-12 is frontend clipboard cleanup. |
| Q-2 | Persist the policy, or keep Redis-only and add a startup assertion? | Persist one versioned singleton in PostgreSQL. | An assertion detects loss but supplies neither an Admin control nor durable recovery. PostgreSQL supplies authority and optimistic concurrency. |
| Q-3 | What does first startup do with the legacy Redis policy? | Atomically classify and import one legacy snapshot into a PostgreSQL row in `compatibility` state. It does not switch authority. | Current-version replicas still know only the legacy triple. Separating import from activation avoids unsafe mixed-version writes. |
| Q-4 | How is the policy edited? | Admin-only GET plus full-replacement PUT with an integer version/`If-Match` guard. PUT is permitted only in `active`. | Full replacement makes the singleton and audit outcome legible. A rollout write fence prevents a new Admin write from racing old readers or rollback preparation. |
| Q-5 | What provisioning cap applies? | 60 account creations per acting Admin per 10 minutes, enforced in the application service with a user-keyed Redis sliding window. | It bounds request-speed abuse while allowing a typical class through the existing single-item flow. Bulk import must define a separate batch rule. |
| Q-6 | Does this depend on another active dossier? | No; `depends_on: []`, and no file overlap either. | Checked against all four other non-implemented dossiers: `2026-08-30-chatroom-approval-and-overlay-discoverability` and `2026-08-30-runtime-contract-integrity` are confined to the conversation and agents/keys surfaces, and `2026-07-07-graphrag-two-axis-redesign` and `2026-07-19-large-artifacts-silently-dropped` to `AgentDetailView.vue`/`turn_engine.py`. None touches identity onboarding, this policy table, Admin users or the planned maintenance commands. |
| Q-7 | How do old and new replicas coexist? | A durable `rollout_state` is `compatibility`, `active` or `rollback_frozen`. In compatibility, new readers enforce the atomic legacy snapshot and PUT returns a typed 409. An explicit maintenance command activates only after the operator has drained old replicas. | The three legacy keys and PostgreSQL cannot be one transaction. A write fence is the only provable way to keep unaware old replicas from enforcing a stale policy. |
| Q-8 | How does an active cache recover from a failed mirror refresh? | New readers use one versioned JSON key with a 30-second TTL and cap any local cache lifetime to its remaining PTTL. Missing, expired, malformed or unreadable Redis falls back to PostgreSQL; Redis-derived data never extends Redis TTL. | A valid but stale value otherwise lives forever. A non-renewable TTL creates a hard freshness boundary without an outbox or background worker. |
| Q-8a | How does a reader learn which rollout state it is in without a per-request DB read? | `rollout_state` is a field of the same v2 JSON value and is cached with it, in every phase, so one read answers both "which authority" and "what policy". A state transition deletes the v2 key inside the same maintenance command that commits it, so the next reader misses, falls back to PostgreSQL and repopulates. | State is not separable from policy: a reader that cached the policy but re-read the state would defeat the cache, and one that cached the state but not the policy would enforce a stale authority. Deleting on transition makes an operator-visible change take effect at the next request rather than after a TTL, and the 30-second TTL remains the upper bound if that delete fails. |
| Q-9 | How is rollback made race-free? | `prepare-email-domain-policy-rollback` first changes the row to `rollback_frozen`, then atomically replaces and verifies the legacy triple before recording `legacy_mirrored_version`. PUT remains blocked until rollback is cancelled after old replicas drain. | Freezing and Admin updates serialize on the same row, so the verified legacy snapshot cannot immediately become stale. Alembic remains DB-only. |
| Q-10 | Which legacy shapes are importable? | One Lua read returns key types, mode and both sets. All absent means explicit `off`; mode absent with any member, invalid mode, wrong key type or invalid domain blocks startup. `allow` with an empty set is legal deny-all; `deny` with an empty set is legal allow-all; `off` may retain dormant lists. | Redis cannot distinguish a missing empty set from an intentionally empty set. This matrix rejects distinguishable corruption without inventing information Redis does not hold. |
| Q-11 | How does AC-7's 429 carry `Retry-After`? *(decided at approval, 2026-08-31)* | As the `retry_after_seconds` RFC 7807 member, not an HTTP header. | The shared identity handler (`shared_kernel/errors/context_handler.py:64-75`) renders extras into the Problem body and sets no headers; `ActivationLinkRateLimited` — the sibling cap this one is modelled on — is already reported that way. Adding a header would mean a route-level catch that identity has nowhere else, giving one context two 429 contracts. |
| Q-12 | What does "rewrite `docs/operations.md`" cover? *(decided at approval, 2026-08-31)* | §7a.5 is rewritten and a rollout/rollback procedure section is added beside it. The other thirteen chapters are untouched. | The manual is 492 lines spanning logging, Vault bootstrap, backups and alerting; none of that is in this dossier's blast radius, and rewriting it would put unreviewable churn in a security-sensitive diff. |

## 4. Current State

- `email_domain_policy.py` reads `allow`, `deny` and `mode` with a non-transactional pipeline,
  treats absent mode as `off`, and caches the result for 30 seconds (`:19-60`). A mixed read can
  combine values from different updates, and Redis loss silently admits every domain.
- No endpoint writes those keys; operators use `redis-cli` (`email_domain_policy.py:6-9`).
- Ordered startup initializers propagate uncaught failures (`backend/app/main.py:60-66`), while the
  rate-limit primer deliberately swallows failures (`backend/app/bootstrap/startup.py:59-70`). The
  policy initializer must use the former fail-boot behavior, not copy the latter.
- Transaction-scoped advisory locks already exist at
  `backend/shared_kernel/db/advisory_lock.py:1-30`.
- Activity policy persistence supplies the guarded singleton update pattern
  (`backend/contexts/activities/infrastructure/repositories/policy_repo.py:65-136`).
- The maintenance CLI has stable subcommands and non-zero failure exits
  (`backend/smap/maintenance/__main__.py:16-18,34-69`).

## 5. Design

### Options considered

**Option A — phased PostgreSQL authority with compatibility and rollback fences (selected).**
Import legacy state, activate explicitly after old replicas drain, resolve phase and policy through
one short-lived v2 cache, and freeze/verify before rollback. This is more operationally explicit but
every transition is serializable and testable.

**Option B — commit PostgreSQL, then best-effort dual-write legacy Redis.** Rejected. A failed
refresh leaves a valid stale mirror indefinitely, and the three-key reader can observe a mixed
snapshot. No ordering of two independent systems makes that atomic.

**Option C — PostgreSQL-only with no rollout compatibility.** Semantically smallest, but an old
replica during rollout or rollback would continue enforcing unrelated Redis state. It is safe only
with downtime, which is not the deployment contract this change targets.

### Decision and state machine

The singleton stores `rollout_state` and nullable `legacy_mirrored_version` in addition to policy
data and version.

| State | New reader authority | Admin PUT | Old replica contract |
|---|---|---|---|
| `compatibility` | Atomic legacy Redis snapshot | Typed 409 | May continue reading legacy keys |
| `active` | PostgreSQL with disposable v2 Redis cache | Allowed | Must already be drained |
| `rollback_frozen` | Frozen PostgreSQL snapshot | Typed 409 | May start only after verified legacy mirror |

The state itself is read through the same disposable v2 cache in all three phases (Q-8a), so the
column above describes where the *policy* comes from once the reader knows the phase, not a second
lookup. Only `active` answers both questions from that one value. In the other two phases the v2
key is a routing hint and nothing more: `compatibility` still resolves the policy from the legacy
triple, and `rollback_frozen` still reads PostgreSQL directly, so no cached value can serve a frozen
policy while rollback rewrites Redis beside it.

State transitions are explicit maintenance operations, not incidental startup side effects. Because
a transition deletes the v2 key, the observable bound on a transition is the next request, and 30
seconds only in the degraded case where the delete failed.

## 6. Detailed Changes

### Domain and application

- Add `EmailDomainPolicyMode`, `EmailDomainPolicyRolloutState`, an immutable policy model,
  exact-domain normalization and domain errors for stale version, inactive rollout, invalid legacy
  state and unavailable authority.
- Define repository/cache ports in the identity application layer. `AuthService` and
  `AdminService` receive a reader rather than importing the infrastructure module.
- The reader resolves the rollout state first, through process cache, v2 Redis and PostgreSQL in
  that order and subject to Q-8's freshness rules, then applies the phase's authority: active reads
  the policy from the same resolved value, compatibility from the atomic legacy reader, frozen from
  PostgreSQL only. One resolution per request; the state is never looked up separately.
- The policy service full-replaces an active row with a version guard and emits an audit event with
  actor, state, mode, old/new version and list counts, never domain values.
- `issue_activation_links` rejects `UserStatus.BANNED` before identity lookup, rate limit, token
  creation or audit emission.
- `create_user` checks `rl:admin-provision:u:{admin_user_id}` at 60/600 seconds before policy lookup
  and persistence and maps refusal to the shared 429 problem with `Retry-After`.

### Infrastructure and migration

- Add an expand-compatible migration for a singleton `email_domain_policies` row with checked
  mode/state, normalized `text[]` lists, integer version, nullable `legacy_mirrored_version`,
  timestamps and updating-Admin FK. The migration creates no default. Singleton is a schema
  constraint, not a convention: a fixed primary key plus a `CHECK` pinning it makes a second row
  impossible, so the bootstrap advisory lock only has to serialize concurrent first starts rather
  than be the sole guard. `0083` is the head as of this dossier; take the actual revision number
  from `alembic heads` at build time, because any dossier that lands first moves it.
- A fatal ordered startup initializer takes
  `advisory_xact_lock("identity:email-domain-policy-bootstrap")`, re-reads the row under the lock,
  and, only when absent, imports one Lua-captured legacy snapshot as version 1 compatibility state.
  Concurrent processes therefore select one winner; a pre-commit failure leaves no row and is
  safely retried.
- The Lua reader returns Redis types plus all values in one server-side operation and applies
  Q-10's classification. Unreadable Redis with no DB row blocks boot.
- Cache key `config:email_domain:policy:v2` contains schema, `rollout_state`, version, mode and both
  lists in one JSON value written with `EX 30`. Reads obtain value and PTTL together. Only a
  successful DB read may repair/extend it; a Redis-derived snapshot never does. The key is written
  in every phase, because the state is what a reader needs before it knows which authority to use,
  but only `active` serves the effective policy from it. In `compatibility` the mode/list fields
  describe the imported row while the legacy triple governs, and in `rollback_frozen` they are
  ignored in favour of a direct read, so that a frozen policy cannot be served from a cache while
  the rollback is rewriting Redis beside it.
- Add idempotent `activate-email-domain-policy` and
  `prepare-email-domain-policy-rollback` maintenance commands. Both use advisory/row locking,
  readback verification and non-zero failure exits. The activation command captures any final
  compatibility-era legacy edit before switching state. The rollback command freezes first, then
  atomically replaces all legacy keys and records the verified version. Both delete the v2 key after
  committing the new state (Q-8a); a failed delete is logged and not fatal, because the TTL still
  bounds it.
- Alembic downgrade touches PostgreSQL only. Operations documentation makes a successful rollback
  preparation marker a precondition before starting an old image or dropping the table.

### API and frontend

- Add Admin-only `GET /api/admin/email-domain-policy` and
  `PUT /api/admin/email-domain-policy`. Responses expose rollout state and version. PUT accepts a
  typed full snapshot, requires `If-Match`, and maps stale writes and write-fenced states to distinct
  RFC 7807 409 types.
- Route through `IdentityFacade` and an identity application factory; no route imports a service,
  repository or table directly. Regenerate OpenAPI and the frontend client.
- Add the policy section to the Admin users/onboarding surface. Compatibility and frozen states are
  visibly read-only with operator guidance; active state supports mode and newline-separated exact
  lists, load/error/retry, validation and conflict recovery.
- Rewrite `docs/operations.md` with deploy, activate, rollback-prepare, rollback-cancel and
  break-glass diagnosis procedures. Never instruct an operator to run Alembic downgrade before the
  verified rollback marker exists.

## 7. NFR Checklist

- [ ] i18n — every new label, problem message, rollout state and recovery instruction exists in
  `en` and `zh-TW` through `$t()`.
- [ ] Audit — updates and state transitions record actor, versions, state and list counts only.
- [ ] Tenant isolation — policy endpoints and maintenance mutations are platform-Admin/operator
  surfaces and expose no org/project data.
- [ ] Error UX — load, retry, 409 stale, 409 rollout fence, 429 provisioning and 503 authority
  unavailable are distinct.
- [ ] Performance — in `active` and `compatibility`, state resolution and policy together cost at
  most one singleton DB read per 30-second expiry window; no cache entry outlives its Redis PTTL.
  `rollback_frozen` reads PostgreSQL per request by design and is a bounded operator window, not a
  steady state.

## 8. Security Considerations

- Both API endpoints require `Depends(require_admin)` and explicit bounded Pydantic models.
- Domain entries are trimmed, IDNA-normalized, lower-cased, de-duplicated and bounded by count and
  length; schemes, paths, `@`, ports, wildcards, empty labels and edge dots are rejected.
- Missing/malformed/expired cache state falls back to DB; DB failure raises typed 503 and never
  changes effective mode to `off`.
- Compatibility import is atomic and fail-boot. An invalid mode, wrong Redis type, invalid member or
  distinguishable partial state never becomes an authoritative row.
- The Admin provisioning bucket is keyed by authenticated actor id, not client input. Global CSRF
  handling remains unchanged.
- Domain lists can identify institutions and are excluded from audit/log metadata.

## 9. Quality Notes

- **Existing debt** — identity application services import infrastructure directly and the Admin
  route imports `AdminService`. This change introduces only the minimum port/factory/facade seam and
  does not expand those upward dependencies.
- **Patterns** — follow `advisory_xact_lock`, `ActivityPolicyRepository`'s guarded update and the
  maintenance CLI's explicit non-zero failure behavior. Borrow the rate-limit mirror's commit
  ordering only; do not copy its compile-time-default fallback.
- **Reuse** — `require_admin`, RFC 7807 `Problem`, `audit.emit`, `ratelimit.check_raw`,
  `IdentityFacade`, `useQuery`/`useMutation`, `SQueryError`, `SSelect`, `STextarea`, `SButton`,
  `useToast`, Admin query keys and Redis script loading.

## 10. Risks and Rollback

- **Operator activates too early.** The command requires an explicit acknowledgement that old
  replicas are drained and prints the resulting version/state. This remains an operational
  assertion; automatic replica discovery is a non-goal.
- **Cache refresh failure.** An old v2 value expires within 30 seconds and cannot be extended from a
  cache hit. The next read reaches PostgreSQL and repairs Redis best-effort.
- **Import ambiguity.** Q-10 distinguishes every observable corrupt shape. Empty Redis sets are not
  evidence of absence and are interpreted according to mode.
- **Concurrent rollback/update.** Both serialize on the row; freeze-first rejects the update, while
  update-first causes rollback to mirror the newer committed version.
- **Rollback.** Prepare and verify the frozen legacy snapshot, then start old replicas, then run the
  documented DB downgrade. A failed preparation remains frozen and is safe to retry; it must never
  proceed to old binaries.

## 11. Acceptance Criteria

A ticked box means the mapped test was **executed and passed on this machine**. The
db/integration tier needs a real PostgreSQL and Redis, which this host does not run
(see §17); those criteria carry their test file and stay unticked until CI is green.
Nothing below is closed on the strength of the code having been written.

- [ ] AC-1: N concurrent first-start initializers create exactly one version-1 compatibility row
  whose policy equals one atomic legacy Lua snapshot; pre-commit failure is safely retryable.
  *Lock/re-read ordering passes in `test_email_domain_policy_admin_api.py`; the concurrency and
  single-row halves are `test_email_domain_policy_db.py`, pending CI.*
- [ ] AC-2: unreadable Redis, missing mode with a non-empty list, invalid mode, wrong key type and an
  invalid domain all block first boot without creating a row; all-absent alone imports explicit
  `off`, and the legal empty-list cases in Q-10 are covered.
  *The whole matrix passes in `test_email_domain_policy_resolution.py`; "without creating a row"
  and the real wrong-key-type error are db-tier, pending CI.*
- [ ] AC-3: an Admin can GET policy/state in every phase; PUT succeeds only in active, while
  compatibility/frozen returns typed 409 without DB, Redis or audit mutation. Non-Admins receive
  403, invalid domains 422 and stale versions 409.
  *Route and service halves pass in `test_email_domain_policy_admin_api.py`; the guarded UPDATE
  against the real constraint is db-tier, pending CI.*
- [x] AC-4: after a committed active update whose v2 Redis SET fails, the old cache is not renewed,
  every new replica reads the DB/new value within 30 seconds, and the committed response is not
  falsely rolled back.
- [ ] AC-5: registration, change-email and Admin provisioning enforce the same normalized active
  policy; exact-domain and non-implied-subdomain behavior is unchanged.
  *Matching semantics pass in `test_email_domain_policy_domain.py`; the three-call-site proof is
  db-tier, pending CI.*
- [x] AC-6: a banned credential-less account cannot receive activation links; no reset/verify token,
  audit event or rate-limit entry is created by the refused call.
- [x] AC-7: one Admin may create at most 60 accounts in a rolling 10-minute window; the 61st returns
  429 with `Retry-After`, while another Admin has an independent bucket.
  *`Retry-After` is the `retry_after_seconds` Problem member — Q-11, D-1.*
- [ ] AC-8: a forward/rollback mixed-version test proves compatibility old/new readers enforce one
  legacy snapshot, activation is required before PUT, and frozen new readers plus old readers
  enforce the same verified rollback snapshot. Each transition is observed by an already-warm reader
  on its next request, and within 30 seconds when the command's v2 delete is made to fail.
  **Its second sentence is not achievable and has been restated — see D-3.** Transition ordering
  passes in `test_email_domain_policy_rollout.py`; the rest is db-tier, pending CI.
- [x] AC-9: OpenAPI and generated frontend types are regenerated and the drift gate passes.
  *`backend/openapi.json` and the client regenerated; a re-run produces no diff. The `bash`
  gate script itself could not run on this host (no `python` on its PATH), so the equivalence
  was established by running its two steps directly.*
- [ ] AC-10: targeted unit, DB/Redis integration, frontend component, lint, typecheck and build gates
  pass. *Backend unit/ruff/mypy and frontend test/lint/typecheck/build all pass; DB/Redis
  integration pending CI.*
- [x] AC-11: v2 missing, malformed, expired, evicted or unreadable always falls back to DB; a value
  whose `rollout_state` is absent or unrecognized is treated as malformed rather than defaulted to a
  phase; when DB is also unavailable the request returns typed 503 and never uses `off`.
- [x] AC-12: a local cache sourced from Redis never outlives the mirror PTTL, and a Redis-derived
  snapshot never refreshes the Redis TTL. *The unit tier proves both; the db tier additionally
  pins that the mirror's PTTL is real, pending CI.*
- [ ] AC-13: activation failure during Redis prepare/readback or DB commit leaves the row in
  compatibility and remains idempotently retryable. *Passes at the unit tier; the rollback-to-no-row
  half is db-tier, pending CI.*
- [ ] AC-14: rollback preparation serializes against PUT, records
  `legacy_mirrored_version == version` only after atomic write/readback equality, and exits non-zero
  while safely frozen on failure. *Ordering and the non-zero exit pass at the unit tier and the
  unarmed refusal was executed by hand; the serialisation proof is db-tier, pending CI.*
- [ ] AC-15: operations tests/documentation prove Alembic never accesses Redis and prohibit old-image
  rollout or downgrade without a verified rollback marker. *`docs/operations.md` §7a.6, §4.4 and
  §13 are written; `test_migration_0084_schema.py` proves the downgrade is PostgreSQL-only but
  needs `SMAP_SCRATCH_DATABASE_URL`, pending CI.*

## 12. Test Plan

- **Domain/unit** — normalization; state transitions; legacy classification matrix; banned refusal
  ordering; per-Admin rate-limit isolation; typed problem mapping.
- **Redis integration** — atomic legacy read/replace, key-type errors, v2 GET+PTTL, schema/version/
  `rollout_state` validation, an unrecognized state falling back rather than defaulting, TTL
  non-renewal, transition-time key deletion and DB repair.
- **PostgreSQL integration** — advisory-lock bootstrap race, guarded updates, phase fences,
  activation failures and update-vs-freeze ordering.
- **Failure injection** — DB commit/v2 SET failure, Redis read failure with DB available, both stores
  unavailable, and a valid mirror whose PTTL is shorter than the local-cache default.
- **Frontend** — active edit, compatibility/frozen read-only guidance, load/error/retry, validation,
  409 refresh and translated copy.
- **Contract/tooling** — OpenAPI/client regeneration, targeted pytest, `ruff`, `mypy`, `pnpm test`,
  lint, typecheck, build and drift gates.
- **Compose/manual** — restrictive legacy policy -> compatibility deployment -> old replica drain ->
  activation; Redis flush in active; prepare rollback -> old image; malformed legacy boot refusal.

## 13. SRS Delta

Amend **[R19a.13]** to:

> **[R19a.13]** An Admin may set an exact email-domain allowlist or denylist to block
> disposable-mail signups. The policy is stored durably, is versioned and audited, and is applied
> consistently to self-registration, email changes, and Admin-provisioned accounts. Loss,
> eviction, corruption or temporary unavailability of an acceleration cache must not change the
> effective policy or silently disable the control. Mixed-version activation and rollback use an
> explicit write-fenced transition so an old replica cannot unknowingly enforce a stale policy.
> Domains are matched exactly after normalization; a listed parent domain does not imply its
> subdomains.

The delta remains draft and is not applied to `REQUIREMENTS.md` until explicit user approval.

## 14. Open Questions

None blocking. The operator acknowledgement on activation is deliberately explicit; automatic
replica-version discovery is outside this dossier.

## 15. Deviation Log

- **D-1 — `Retry-After` is a Problem member, not an HTTP header** (Q-11, agreed at approval).
  The shared identity RFC 7807 handler renders extras into the body and sets no headers
  (`shared_kernel/errors/context_handler.py:64-75`); `ActivationLinkRateLimited`, the sibling cap
  this one is modelled on, already reports itself that way. A header would have needed a
  route-level catch that identity has nowhere else, giving one context two 429 contracts.

- **D-2 — a third maintenance command.** §6 named two. The documented rollback procedure needs a
  third, `cancel-email-domain-policy-rollback`, because a prepared rollback that is not taken has
  to be lifted somehow. The alternative was to let `activate-email-domain-policy` also lift a
  freeze, which was rejected: the two have different preconditions ("old replicas have drained"
  versus "the old images are gone again"), and on an operator surface the wrong command is the
  expensive mistake.

- **D-3 — AC-8's "on its next request" was not achievable and has been restated.** The criterion
  claimed a rollout transition is observed by an *already-warm* reader on its next request. It
  cannot be: a process-local cache hit performs no I/O by construction, so a maintenance command
  running in its own process cannot reach into a serving process's memory. Deleting the shared
  mirror does make the new phase visible to the next reader that *consults* it — a cold process,
  or one whose snapshot has expired — and that is what the design delivers. A reader still holding
  a live snapshot converges when it expires, which the mirror's TTL bounds at 30 seconds. **Both
  paths are bounded by the same 30 seconds; only "immediately" differs**, so the criterion's
  operational intent stands and its wording did not.

  This was found in the build's own self-audit, and it had already produced a bad test: the first
  version of `test_a_warm_reader_sees_a_transition_on_its_next_request` called
  `reset_process_cache()` inside its own transition helper and so asserted a property the
  production path does not have. It is now two tests — one for the reader that consults the mirror,
  one pinning that a warm reader is bounded by its own TTL — and the reader's module docstring
  states the limit. Narrowing the local lifetime would shrink the window at the cost of a Redis
  round trip per request per window, which is the trade the process cache exists to avoid; that is
  a design change, not a defect fix, and is not made here.

- **D-4 — `idna` is now a declared direct dependency.** §8 requires IDNA normalisation and the
  stdlib `"idna"` codec implements the older IDNA2003 rules with per-label quirks, so the same
  address would normalise differently here than in the validators around it. `idna` was already
  resolved transitively at 3.18; it is declared because the identity domain now imports it
  directly, and an undeclared direct import breaks the day a transitive consumer drops it. The
  floor is `>=3.7` deliberately: that is the fix version for CVE-2024-3651, an `idna.encode` DoS,
  and this code path is reachable from unauthenticated registration. It is the domain layer's one
  third-party import, which the module docstring justifies.

- **D-5 — the response model does not reuse the request's list-size cap.** Found in self-audit.
  Both models originally shared one `Annotated` alias carrying `max_length=1000`. The boot-time
  legacy import applies no count cap, so a deployment that had more than 1000 domains in Redis
  holds a row that bound cannot describe, and validating the *response* against it turned the one
  screen an operator needs in order to shrink the list into a 500. Confirmed by construction
  before the fix, and pinned by a regression test. The asymmetry is recorded as FU-8.

- **D-6 — `docs/operations.md` scope** (Q-12, agreed at approval). §7a.5 rewritten, §7a.6 added,
  plus the rollout note in §4.4, five problem types in §6.1 and the rollback line in §13. The
  other chapters are untouched.

- **D-8 — the fatal startup step needed a unit-tier stub, and that stub needed its own guard.**
  Adding an initializer that reads Redis and writes PostgreSQL broke the eight `healthz`/`readyz`/
  `metrics` tests, which build the real app through `tests/conftest.py`'s `client` fixture and so
  run the whole lifespan. Those tests have no infrastructure and had passed before this change.
  Softening the step was the wrong fix — being fatal is the point — so the fixture now drops it,
  beside the `ip_bans.reload` stub already there for the same reason. The first attempt patched the
  step by name in `app.bootstrap.startup` and silently did nothing: `app.main` binds `INITIALIZERS`
  by value at import and the list holds function objects, so the lifespan still iterated the real
  one. It replaces `app.main.INITIALIZERS` now.

  **A stub that hides a step also hides the step going missing**, so two tests pin what the fixture
  can no longer observe: that `import_email_domain_policy_step` is in `INITIALIZERS` and ordered
  before the best-effort rate-limit primer, and that it propagates rather than swallowing a failure.
  Without them, deleting the step would leave the suite green while every registration 503'd in
  production.

- **D-7 — two smaller corrections made during the quality and self-audit gates.**
  `infrastructure/email_domain_policy.py` was deleted: it imported an application-layer concrete
  class in order to construct it, which is the dependency direction inverted, and it duplicated a
  role `application/factory.py` already played for the sibling service. And
  `EmailDomainPolicyService.publish` reset the process cache *before* writing the mirror, leaving a
  window in which another request in the same process could read the old mirror value and re-cache
  it; the reset now runs in a `finally` after the write.

## 16. Follow-ups

- FU-1: bulk onboarding must define batch validation, partial failure and rate limits rather than
  bypassing AC-7.
- FU-2: wildcard/subdomain matching requires explicit precedence and public-suffix semantics.
- FU-3: broader identity route/application/infrastructure dependency cleanup belongs in a dedicated
  refactor dossier.
- FU-4: after every supported deployment is beyond the compatibility version, remove the legacy
  reader and rollout states in a separately reversible cleanup.
- FU-5: in `compatibility` the effective policy is read from the legacy triple on **every**
  request, with no snapshot caching — the process cache holds the rollout state, and the phase's
  authority is consulted behind it. The predecessor cached for 30 seconds. It is one extra Redis
  `EVAL` on registration, change-email and Admin provisioning, which are low-frequency endpoints,
  and the phase is a bounded deployment window that FU-4 removes entirely; caching it would need
  its own freshness bound for a phase that is meant to be short. Measure before adding one.
- FU-6: `admin.users.created` is a **duplicate key** in both admin locale files — the Users table's
  "Created" column header and the provisioning toast collide, and the last wins, so the column
  header currently reads "Account created." Pre-existing and found only because this change edits
  those files; deliberately not fixed here, since any JSON-aware edit silently collapses the
  duplicate and the repair is a user-visible copy change outside this dossier's scope.
- FU-7: `EmailDomainPolicyMirror.delete()` is declared on the port but has no port-typed consumer —
  the maintenance commands hold the concrete adapter. Harmless, but either the commands should take
  the port or the method should leave it.
- FU-8: the boot-time legacy import applies no count cap while the API caps each list at 1000
  (D-5). A deployment arriving with more can be read and shrunk but not saved unchanged. Deciding
  the right behaviour — cap the import and fail the boot, or raise the API cap — needs a real
  deployment's numbers rather than a guess.

## 17. What was not verified

Recorded plainly, because the value of the criteria above depends on knowing which of them
were executed. Per the user's direction at approval, the tiers needing real infrastructure
were written in full and left to CI rather than run on this Windows host.

**No PostgreSQL and no Redis were available.** `tests/integration/test_email_domain_policy_db.py`
(20 tests) and `tests/integration/test_migration_0084_schema.py` (8 tests) were written, import
cleanly and collect, and have never been executed. Everything they assert — the advisory-lock
race, the singleton `CHECK`, the guarded `UPDATE` against the real constraint, the mirror's real
PTTL, the freeze-versus-update serialisation, the three enforcement points sharing one reader —
rests on the code being right, not on having been observed. The migration schema test additionally
needs `SMAP_SCRATCH_DATABASE_URL` and skips without it, so a CI run that does not set it reports
green while proving nothing; check that it ran rather than that it passed.

**`alembic upgrade head` has never been run against a database.** Migration 0084 is unexecuted
DDL. Its `CHECK` constraints are the kind of PostgreSQL-specific fact `backend/CLAUDE.md` records
as invisible to the unit tier, which is exactly why the schema test exists — and why it not having
run matters.

**No browser pass.** `EmailDomainPolicyForm.vue` is covered by 13 component tests against mocked
HTTP, and the harness renders `$t` as the key, so **no rendered copy in either locale has been
seen by anyone**. The locale files are checked only for key parity between `en` and `zh-TW` and
for the escaped literal `@`. The read-only states in particular are asserted as key names and
`disabled` attributes, not as a screen an operator could act on.

**The compose/manual sequence in §12 was not run**: restrictive legacy policy, compatibility
deployment, drain, activation, Redis flush while active, prepared rollback into an old image, and
a malformed legacy boot refusal. That sequence is the only thing that exercises two releases at
once, which is the risk the whole rollout design exists to manage. The three maintenance commands
have never been run against a database; only `activate-email-domain-policy`'s unarmed refusal was
executed by hand (exit 1 with the intended message).
