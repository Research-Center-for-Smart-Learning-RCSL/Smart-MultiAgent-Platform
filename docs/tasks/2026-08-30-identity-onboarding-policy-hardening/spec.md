---
type: feature
status: draft
created: 2026-08-30
requirements: [R6.18, R19a.13]
depends_on: []
---

# Identity onboarding policy hardening

## 1. Summary

Consolidate four still-open follow-ups from
`2026-08-20-onboarding-without-smtp`: make the email-domain admission policy durable and
admin-manageable (FU-6/FU-11), refuse activation-link minting for banned accounts (FU-9),
and add a per-admin provisioning rate cap (FU-10). The result keeps closed, mail-less
deployments closed across Redis eviction or replacement and removes two avoidable account
provisioning hazards.

Freshness was checked against `main` at `73125821` (2026-08-28). All four follow-ups still
reproduce in the current code; §4 cites the live paths.

## 2. Goals and Non-goals

**Goals**

- Make PostgreSQL the durable authority for the singleton email-domain policy while retaining
  a Redis hot-path mirror and the current maximum 30-second propagation window.
- Preserve a restrictive legacy Redis policy automatically during the first startup after the
  migration; never interpret an unreadable legacy policy as `off`.
- Give platform admins a typed, audited API and UI for reading and replacing the policy.
- Refuse activation-link issuance for banned accounts before any token or rate-limit state is
  written.
- Bound `POST /api/admin/users` per acting admin and return the established 429 contract when
  the cap is exhausted.

**Non-goals**

- No invite-only registration mode, wildcard/subdomain matching, bulk onboarding, or invite
  domain-policy change.
- No change to the existing exact-domain semantics: entries are lower-cased bare domains,
  and `example.edu` does not imply `dept.example.edu`.
- No migration of the general rate-limit subsystem or unrelated Redis configuration.
- No retroactive ban, deletion, or repair of accounts already provisioned under an earlier
  policy.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Which onboarding follow-ups belong in one PR? | FU-6, FU-9, FU-10 and FU-11 from `2026-08-20-onboarding-without-smtp`. | They share the admin onboarding boundary, identity service, security tests and operator story. FU-8/FU-13 are member-picker pagination work and FU-12 is a cross-slice clipboard cleanup, so including either would weaken the PR boundary. |
| Q-2 | Persist the policy, or keep Redis-only and add a startup assertion? | Persist one versioned policy row in PostgreSQL and mirror it to Redis. | A startup assertion detects loss but does not let an admin manage the control or recover it without shell access. PostgreSQL authority closes both FU-6 and FU-11 and follows the existing rate-limit policy authority/mirror pattern. |
| Q-3 | What happens to an existing deployment's three Redis keys? | On the first new-version startup only, import the complete legacy value into PostgreSQL, then mirror the durable row. If no DB row exists and Redis cannot be read, fail startup instead of creating `off`. | A migration cannot read Redis. First-start import preserves a restrictive deployment during a rolling upgrade, while fail-closed handling prevents an infrastructure error from silently reopening registration. |
| Q-4 | How is the policy edited? | Admin-only GET plus full-replacement PUT with an integer version/`If-Match` concurrency guard; the admin UI edits mode and newline-separated exact domains. | Full replacement makes the singleton state and audit outcome legible. Optimistic concurrency prevents two admins from silently overwriting each other and matches the activity-policy form's established pattern. |
| Q-5 | What provisioning cap applies? | 60 account creations per acting admin per 10 minutes, enforced in the application service with a user-keyed Redis sliding window. | The cap blocks request-speed abuse while allowing a typical class to be provisioned through the existing single-item flow. Bulk import remains FU-1 of the source dossier and must revisit this fixed cap deliberately. |
| Q-6 | Does this depend on another active dossier? | No; `depends_on: []`. | The only non-final dossiers are `graphrag-two-axis-redesign`, the invalid-status historical `activities-activation-ux`, and `large-artifacts-silently-dropped`; none touches identity onboarding, admin users, the planned policy table, or the relevant frontend admin surface. |

## 4. Current State

- The policy module documents that no API writes the three Redis keys and directs operators to
  `redis-cli` (`backend/contexts/identity/infrastructure/email_domain_policy.py:1-9`). Its
  module cache defaults to `mode = "off"`, and a missing Redis mode is converted to `off`
  (`backend/contexts/identity/infrastructure/email_domain_policy.py:24-33,48-60`).
- Production Redis uses `--maxmemory-policy allkeys-lru`
  (`deploy/compose/docker-compose.yml:245-255`). The operator guide therefore warns that an
  eviction, flush or replacement silently reopens registration
  (`docs/operations.md:405-425`).
- Self-registration and admin provisioning both call the infrastructure module directly
  (`backend/contexts/identity/application/auth_service.py:159`,
  `backend/contexts/identity/application/admin_service.py:146-151`), so the application layer
  has no injectable policy port and tests must patch a module global.
- `issue_activation_links` rejects a missing/deleted user and an already usable account, but it
  does not reject `UserStatus.BANNED` before minting tokens
  (`backend/contexts/identity/application/admin_service.py:185-238`).
- `POST /api/admin/users` calls `AdminService.create_user` with no route- or actor-specific cap
  (`backend/app/api/v1/admin_users.py:136-157`). The sibling re-issue operation already has a
  per-target Redis cap (`backend/contexts/identity/application/admin_service.py:223-232`).
- The existing runtime-policy exemplar stores rate-limit rows in PostgreSQL, mirrors them to
  Redis after commit, and re-primes them at startup
  (`backend/shared_kernel/auth/ratelimit.py:100-146`,
  `backend/app/api/v1/admin_rate_limits.py:122-135`). The activity-policy UI supplies the
  frontend concurrency/error-state exemplar
  (`frontend/src/slices/admin/components/ActivityPolicyForm.vue:199-217,298-315`).

## 5. Design

### Options considered

**Option A — PostgreSQL authority plus Redis mirror (selected).** Add a singleton, versioned
policy row. Reads use the in-process/Redis cache while it is healthy and fall back to the
authoritative row on an absent or malformed mirror. Admin writes commit PostgreSQL first, then
refresh the mirror. This adds a migration and a repository, but survives Redis loss and supplies
the API's concurrency authority.

**Option B — Redis-only plus fatal startup assertion.** Refuse to boot when the three keys are
missing. This is smaller, but leaves FU-6 open, makes intentional `off` hard to distinguish from
lost state without another sentinel, and still requires shell access for every change.

**Option C — PostgreSQL-only on every registration.** Remove Redis and query the singleton row
for each address. It is simplest semantically, but puts an avoidable database read on every
registration/change-email/provisioning check and abandons the existing 30-second cache contract.

### Decision

Use Option A. PostgreSQL is the sole durable authority; Redis is a disposable acceleration
layer. Cache absence is never interpreted as `off`: it triggers an authoritative read and mirror
repair. An unavailable authority raises an infrastructure error, which fails the admission
request rather than admitting an address. The first-start legacy import is the only code path
that reads the old set/string shape after migration; once a DB row exists, legacy keys are only
mirror output and rollback compatibility.

## 6. Detailed Changes

- **Domain/application**
  - Add `EmailDomainPolicyMode` (`off`, `allow`, `deny`), an immutable policy model, exact-domain
    normalization/validation, and domain errors for stale versions and unavailable activation.
  - Define an application-layer policy repository/cache port. Inject the reader into
    `AuthService` and `AdminService`; stop importing `identity.infrastructure.email_domain_policy`
    from those services.
  - Add a policy service that gets/replaces the singleton, increments its version, emits an audit
    event with mode and list counts (not the domain values), commits, then refreshes the mirror.
  - In `issue_activation_links`, reject `UserStatus.BANNED` before identity lookup, rate-limit
    mutation, or token creation.
  - In `create_user`, enforce `rl:admin-provision:u:{admin_user_id}` at 60/600 seconds before
    policy lookup and persistence; raise the shared rate-limit domain error with retry-after.
- **Infrastructure/migration**
  - Add an expand-compatible migration after `0083` for `email_domain_policies`: singleton key,
    checked mode, normalized `text[]` allow/deny lists, integer version, update timestamp and
    nullable updating-admin FK. The migration creates no default row.
  - Replace the module-global policy implementation with repository + Redis mirror adapters.
    Mirror data carries an explicit mode/version sentinel so missing state cannot resemble `off`.
  - Add an ordered startup initializer. When the table is empty, atomically import all three
    legacy Redis values; if Redis is readable and no keys exist, persist explicit `off`; if Redis
    is unreadable, abort startup. When a row exists, mirror it best-effort and keep DB fallback
    available to request reads.
  - On downgrade, mirror the durable row to the legacy keys before dropping the table; document
    that rollback must run while the new application image is still available.
- **API contract**
  - Add admin-only `GET /api/admin/email-domain-policy` and
    `PUT /api/admin/email-domain-policy`. The PUT body has explicit mode and bounded domain lists;
    each entry is length-bounded and validated before storage. Require `If-Match` once version is
    non-zero and return the established RFC 7807 409 shape on a stale write.
  - Route through `IdentityFacade`/an identity application factory rather than introducing a new
    route-to-infrastructure edge. Regenerate `backend/openapi.json` and the frontend client.
- **Frontend**
  - Add an email-domain policy section to the admin users/onboarding surface, using TanStack Query,
    an explicit loading/error/retry state, a mode select, newline-separated allow/deny editors,
    save conflict handling and translated validation/help text.
  - Disable the inactive list without deleting it, so switching modes does not erase an admin's
    staged data. Submit both lists as a full snapshot.
- **Operations**
  - Rewrite `docs/operations.md` §7a.5 to use the admin UI/API and state that PostgreSQL is
    authoritative. Keep a break-glass read/repair command, but remove the instruction to treat
    three unversioned Redis keys as durable configuration.

## 7. NFR Checklist

- [ ] i18n — every new label, validation message, toast and empty/error state exists in `en` and
  `zh-TW` through `$t()`.
- [ ] Audit log — policy changes record actor, mode, old/new version and list counts; no domain
  list or activation token is logged.
- [ ] Tenant isolation — policy endpoints are platform-admin-only and expose no org/project data.
- [ ] Error handling UX — initial load, retry, invalid domains, 409 conflict, 429 provisioning cap
  and save failure all have explicit states.
- [ ] Performance — policy reads remain cache-backed; DB fallback is one singleton indexed read;
  request lists are bounded and de-duplicated before write.

## 8. Security Considerations

- Both policy endpoints require `Depends(require_admin)` and typed Pydantic request models. No
  generic `dict` or mass-assignment path reaches the row.
- Domain inputs are trimmed, IDNA-normalized, lower-cased, de-duplicated and bounded by both item
  count and item length. Values containing a scheme, path, `@`, port, wildcard, empty label or
  leading/trailing dot are rejected.
- An absent/malformed mirror triggers a DB read; a DB failure fails the admission operation.
  Neither path silently changes the effective mode to `off`.
- The first-start importer distinguishes an intentionally absent legacy configuration from an
  unreadable Redis service. Concurrent replicas use a singleton constraint/compare-and-set so
  only one imported row wins and every replica then mirrors the winner.
- The provisioning limit is keyed by authenticated admin user id, not client-supplied input.
  CSRF protection remains the global cookie/API contract.
- Audit metadata contains counts and versions only. Domain lists can identify an institution and
  do not belong in broadly searchable audit metadata.

## 9. Quality Notes

- **Existing debt** — identity application services import infrastructure repositories/modules
  directly (`admin_service.py:37-49`), and `admin_users.py` imports `AdminService` rather than a
  facade (`admin_users.py:12-18,149`). This task must not add another such edge; it may introduce
  the minimum factory/facade wiring required for the policy and record any broader cleanup as FU.
- **Patterns to follow** — use the PostgreSQL-authority/Redis-mirror ordering in
  `shared_kernel/auth/ratelimit.py:100-146` and the optimistic policy UX in
  `ActivityPolicyForm.vue:199-217,298-315`. Keep API handlers limited to validation, facade call
  and response mapping.
- **Reuse inventory** — `require_admin`, `current_context`, RFC 7807 `Problem`, `audit.emit`,
  `ratelimit.check_raw`, `IdentityFacade`, `useQuery`/`useMutation`, `SQueryError`, `SSelect`,
  `STextarea`, `SButton`, `useToast`, and the admin query-key factory.

## 10. Risks and Rollback

- **Legacy import race or ambiguity.** Mitigate with a singleton insert/CAS and fail startup when
  no durable row exists and Redis cannot be read. Tests cover empty, restrictive, malformed,
  unavailable and concurrent import cases.
- **Rolling deployment.** New code writes the legacy mirror after each DB commit, so old replicas
  continue enforcing the latest policy. Do not remove legacy-key output in this PR.
- **Stale mirror.** Keep the current 30-second maximum local cache life and repair missing/malformed
  Redis state from PostgreSQL. A versioned mirror prevents mixed list/mode reads.
- **Rollback.** Revert application/frontend commits, confirm the legacy mirror contains the DB
  policy, then downgrade the migration. Account and token writes made while the feature was live
  require no data repair.

## 11. Acceptance Criteria

- [ ] AC-1: a pre-existing restrictive legacy Redis policy is imported exactly once and remains
  effective after Redis flush, eviction simulation and application restart.
- [ ] AC-2: when no DB row exists and legacy Redis is unreadable, startup fails with a diagnostic
  that contains no domain list; it never seeds `off`.
- [ ] AC-3: an admin can read and replace the policy through the UI/API; a non-admin receives 403,
  invalid domains receive 422, and a stale version receives 409 without changing state.
- [ ] AC-4: policy update audit events contain actor, mode, versions and list counts but no domain
  values; the committed DB row is authoritative if mirror refresh fails.
- [ ] AC-5: registration, change-email and admin provisioning all enforce the same normalized
  policy; exact-domain and non-implied-subdomain behavior remains unchanged.
- [ ] AC-6: a banned account with no credential cannot receive activation links; no reset/verify
  token, audit event or rate-limit entry is created by the refused call.
- [ ] AC-7: one admin may create at most 60 accounts in a rolling 10-minute window; the 61st call
  returns 429 with `Retry-After`, while another admin has an independent bucket.
- [ ] AC-8: upgrade and downgrade tests prove an old application replica can continue reading the
  legacy mirror during rollout/rollback.
- [ ] AC-9: OpenAPI and generated frontend types are regenerated and the drift gate passes.
- [ ] AC-10: targeted unit, DB/integration, frontend component, lint, typecheck and build gates pass.

## 12. Test Plan

- **Backend unit** — domain normalization; cache hit/miss/malformed paths; policy version conflict;
  banned activation refusal ordering; per-admin rate-limit isolation and 429 mapping.
- **Backend DB/integration** — migration upgrade/downgrade, singleton concurrency, legacy import,
  DB-authoritative update with Redis failure, all three policy consumers, admin AuthZ and audit
  metadata. Execute PostgreSQL-specific array/CHECK behavior in the `db` tier.
- **Frontend component** — load/error/retry, mode switching without data loss, validation, save,
  stale-write refresh and translated copy in both locales.
- **Contract/tooling** — regenerate OpenAPI/client; run targeted pytest, `ruff`, `mypy`, `pnpm test`,
  `pnpm lint`, `pnpm typecheck`, `pnpm build`, and OpenAPI drift.
- **Manual** — on a compose stack, set allow mode, confirm an outside domain is refused, flush
  Redis, restart API, and confirm the same domain remains refused and the UI retains the policy.

## 13. SRS Delta

Amend **[R19a.13]** to:

> **[R19a.13]** An Admin may set an exact email-domain allowlist or denylist to block
> disposable-mail signups. The policy is stored durably, is versioned and audited, and is applied
> consistently to self-registration, email changes, and Admin-provisioned accounts. Loss or
> eviction of an acceleration cache must not change the effective policy or silently disable the
> control. Domains are matched exactly after normalization; a listed parent domain does not imply
> its subdomains.

The delta remains draft and is not applied to `REQUIREMENTS.md` until explicit user approval.

## 14. Open Questions

None blocking. The implementation may measure whether the 30-second local cache remains useful
once the DB fallback and versioned Redis mirror exist; changing that contract is out of scope.

## 15. Deviation Log

None — implementation has not started.

## 16. Follow-ups

- FU-1: bulk onboarding remains the source dossier's FU-1. It must define its own batch-level
  validation, partial-failure and rate-limit semantics rather than bypass AC-7.
- FU-2: wildcard/subdomain rules remain intentionally unsupported; add them only with explicit
  precedence and public-suffix semantics.
- FU-3: the broader identity route/application/infrastructure dependency cleanup extends beyond
  the policy seam created here and belongs in a dedicated refactor dossier.
