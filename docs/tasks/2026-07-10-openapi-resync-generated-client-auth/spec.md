---
type: refactor
status: implemented
created: 2026-07-10
requirements: [R24.13]
---

# OpenAPI contract resync + generated api-client auth wiring (pilot: notifications slice)

## 1. Summary

`backend/openapi.json` and the generated `frontend/src/shared/api-client` are stale
(last regenerated 2026-06-29, predate the entire Knowledge Map backend), which fails
the required `check:openapi-drift` CI gate on every push. Separately, `[R24.13]`
requires slice `api/` folders to wrap the generated client, but zero of the nine
slices do — the generated client is currently unreachable safely at runtime because
its request pipeline bypasses every auth/session behavior `shared/transport/axios.ts`
provides. This task regenerates the contract, wires the generated client's request
pipeline into the existing auth/session machinery without duplicating it, and converts
the `notifications` slice (the smallest surface) as the first real consumer, proving
the pattern for future slices.

## 2. Motivation

- **Stale contract, failing CI gate.** `backend/openapi.json` was last committed at
  `54b7eb6` (Mon Jun 29 21:39:06 2026) and contains zero references to `knowmap`. The
  Knowledge Map backend (`backend/app/api/v1/knowmap.py`) was built and extended across
  `ed72df5`, `1d9e1ec`, and `1a35a1e` (Fri Jul 10 2026, the same day as this dossier) —
  the committed spec predates all of it. `check:openapi-drift` (defined in
  `frontend/scripts/check-openapi-drift.sh:1-39`, wired via
  `frontend/package.json:15`) regenerates both artifacts into place and fails if `git
  status` shows any diff (script lines 21-33). It is a required job in
  `.github/workflows/ci.yml:337` (gate `frontend-gate-openapi-drift`, aggregated at
  lines 764/786/806) — every PR is currently red on this gate regardless of what it
  touches.
- **`[R24.13]` violation.** `REQUIREMENTS.md:1788`: "Slice `api/` folders wrap
  [the generated client] into use-case-shaped calls." A repo-wide check found no
  runtime import of `@shared/api-client` outside the generated tree itself: the only
  three references are `frontend/src/slices/prompt-studio/types/index.ts:1-3` (a
  comment noting the client is "drift-check-only and not imported at runtime"),
  `frontend/src/slices/README.md:21` (aspirational documentation), and
  `frontend/src/shared/api-client/queryKeys.ts` (internal to the generated dir). Every
  slice, including `notifications` (`frontend/src/slices/notifications/api/index.ts`),
  calls `@shared/transport`'s `http` directly with hand-rolled interfaces
  (`Notification`, `UnreadCount`, `MarkReadResult` at `index.ts:6-22`) that duplicate
  the generated `NotificationOut`/`UnreadCountOut`/`MarkReadOut`/`MarkReadIn` models —
  two sources of truth for the same schema, silently divergible.
- **Root blocker: the generated client cannot authenticate.** The `OpenAPI` singleton
  (`frontend/src/shared/api-client/core/OpenAPI.ts:22-32`) defaults `TOKEN`,
  `WITH_CREDENTIALS`, and `HEADERS` to unset/false, and nothing in the app initializes
  it (repo-wide grep for `OpenAPI\.(BASE|TOKEN|WITH_CREDENTIALS|HEADERS)` outside the
  generated tree: zero matches). Every generated service method calls
  `__request(OpenAPI, {...})` with no third argument
  (e.g. `frontend/src/shared/api-client/services/NotificationsService.ts:25,47,63`),
  so `core/request.ts:294`'s `axiosClient: AxiosInstance = axios` default applies —
  the bare global `axios` singleton, not the `http` instance
  (`frontend/src/shared/transport/axios.ts:77-84`) that carries every interceptor:
  bearer-token injection, idempotency keys, `Accept-Language`, silent 401 refresh, and
  `problem+json` → typed-error parsing (`axios.ts:1-8` enumerates all six). A slice
  calling a generated service today would silently send unauthenticated,
  credential-less requests.

## 3. Non-goals

- **No externally observable behavior change.** `notificationsApi.list` /
  `.markRead` / `.unreadCount` must return identical shapes to identical callers
  (`NotificationBell.vue:40`, `useNotificationsList.ts:25,62,84`); `http`'s behavior
  for every other slice is untouched.
- Converting only the `notifications` slice in this task. The other eight slices
  (largest: `tenancy` at 30 endpoints across `orgs.ts`/`projects.ts`/`invites.ts`) are
  out of scope — see Follow-ups.
- No CSRF/XSRF token handling. `http` sets no `withXSRFToken` config
  (`axios.ts:77-84`); this app authenticates via Bearer token, not a cookie read by
  JS, so XSRF-cookie handling does not apply. Confirmed out of scope, not silently
  dropped.
- No change to the Idempotency-Key interceptor's behavior. `notifications` has no
  endpoint that opts in today (`api/index.ts:36-37`'s `markRead` sets no
  `X-Idempotent` header) — the pilot doesn't exercise it, but the shared
  instrumentation still carries the interceptor for slices that do.
- Not modifying `check:openapi-drift`'s detection mechanism — only fixing the drift
  it correctly detects.

## 4. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Scope this dossier to just the `openapi.json`/`gen:api` regen (closes the CI gate today, low risk), or also close the `[R24.13]` gap in the same task? | Also close `[R24.13]` in this task. | User: "這次一併修，擴大範圍" — do it together rather than defer. |
| Q-2 | Closing `[R24.13]` requires editing `shared/transport/axios.ts` (relocating the `attemptRefresh` call off the bare `axios` singleton and registering its interceptors on that singleton too) — the file has zero existing unit tests and is the most security-sensitive file in the frontend (401 silent-refresh, `problem+json` typed-error parsing). Add characterization tests first, or split into a separate follow-up dossier for the risky edit? | Add characterization tests for `axios.ts` first, in this same dossier, then edit it. | User: "先補 axios.ts 特徵測試，再改它" — the refactor discipline's safety net, not skipped for convenience. |

## 5. Current vs Target Structure

**Before:**
- `backend/openapi.json` stale; `frontend/src/shared/api-client/` generated from it,
  imported nowhere at runtime.
- `OpenAPI` singleton never initialized.
- `shared/transport/axios.ts:194`'s `attemptRefresh()` calls bare
  `axios.post('/api/auth/refresh', {})` — the same global `axios` singleton the
  generated client's `request()` defaults to (deliberate: this bypasses `http`'s own
  401-interceptor to avoid the refresh call recursively triggering itself).
- `slices/notifications/api/index.ts` hand-rolls types, calls `http.get`/`http.post`
  directly.

**After:**
- `backend/openapi.json` and `frontend/src/shared/api-client/` regenerated against the
  current route graph (includes `NotificationsService` with correctly-typed
  `NotificationOut`/`MarkReadIn`/`MarkReadOut`/`UnreadCountOut` — already verified
  field-identical to the hand-rolled types they replace).
- `shared/transport/axios.ts`: `attemptRefresh()` moves to a new dedicated,
  uninstrumented instance (`const refreshHttp = axios.create({ withCredentials: true
  })`) — behaviorally identical to today's bare call (no interceptors either way),
  freeing the bare global `axios` singleton from that special-case use.
- `shared/transport/axios.ts`: the same three request-interceptor functions and the
  response interceptor (currently inline arrows passed to `http.interceptors.*.use`)
  become named consts, registered on **both** `http` and the bare global `axios`
  singleton. No logic is duplicated — the same function references are registered
  twice. Any generated-client call (which resolves to that singleton per
  `core/request.ts:294`'s default) now gets identical auth-header injection, silent
  401-refresh-and-replay, and `problem+json` typed-error behavior, with zero new
  code paths to diverge from `http`'s.
- `OpenAPI.WITH_CREDENTIALS = true` set once at app init (exact file TBD by
  implementer — see Migration Steps §7 step 7 — most likely alongside `axios.ts`'s
  own module-level setup, since that module already runs a side-effecting `http`
  export at import time). `OpenAPI.TOKEN`/`HEADERS` stay unset deliberately: the
  shared interceptors already inject `Authorization` and `Accept-Language` after
  `core/request.ts`'s own header-building step runs, so setting them again on
  `OpenAPI` would be redundant.
- `slices/notifications/api/index.ts` calls
  `NotificationsService.listNotificationsApiNotificationsGet(...)` /
  `.markReadApiNotificationsReadPost(...)` / `.unreadCountApiNotificationsUnreadCountGet()`
  directly, re-exporting the generated model types in place of the hand-rolled ones.
  External function names/signatures (`notificationsApi.list`/`.markRead`/`.unreadCount`)
  stay identical — zero call-site changes required.

**Dependency direction:** `shared/transport` (axios.ts) gains an import from
`shared/api-client` (the `OpenAPI` config object) — a new shared→shared edge, which
CLAUDE.md's frontend layer rule permits (`shared/` → `shared/` only). No reverse edge:
the generated tree imports nothing outside itself, so no cycle is introduced.

## 6. Characterization Test Plan

**Existing partial coverage (reuse, don't duplicate):**
`frontend/src/slices/notifications/__tests__/NotificationsView.test.ts` already
exercises `notificationsApi.list` and `.markRead` end-to-end through the view, against
MSW handlers matching `/api/notifications` and `/api/notifications/read`
(`NotificationsView.test.ts:40,116-121`), including a request-body assertion
(`expect(markRead).toHaveBeenCalledWith({ ids: ['n_1'] })`, line 131) and a 500-error
path (lines 77-88). This file must continue to pass **unmodified** after the
conversion — that is itself part of AC-4's evidence, not a file to edit.

**New — `frontend/src/shared/transport/__tests__/axios.spec.ts`** (this file has zero
test coverage today; write before touching `axios.ts`). Use `msw`'s existing
`frontend/tests/mocks/server.ts`/`handlers.ts` (`server.use()` for per-test overrides,
same pattern as `NotificationsView.test.ts`), pinning current behavior:

- Request interceptor injects `Authorization: Bearer <token>` after `setAccessToken`;
  omits it when the token is `null`.
- Request interceptor injects `Idempotency-Key` and deletes the `X-Idempotent`
  sentinel, only on `POST` with `X-Idempotent` present (`axios.ts:98-107`).
- Request interceptor sets `Accept-Language` from `i18n.global.locale.value`
  (`axios.ts:110-115`).
- Response interceptor on 401, problem type not `token-revoked`, original request
  carried `Authorization`: calls refresh, replays once (`_retry` guard prevents a
  second retry), resolves with the replayed response (`axios.ts:146-173`).
- Response interceptor on 401 with `token-revoked` problem type: skips refresh,
  invokes `onUnauthorized`, throws `AuthError` (`axios.ts:156,165-172`).
- Response interceptor on 401 with no `Authorization` on the original request (e.g.
  login): skips refresh, throws directly (`axios.ts:157-159`).
- Response interceptor on network error (no `error.response`): calls
  `markConnectionLost()`, throws `NetworkError`; a canceled request
  (`axios.isCancel`) rethrows without marking the connection lost (`axios.ts:128-136`).
- Response interceptor on non-401 `problem+json`: `parseProblem()` result is thrown
  (`axios.ts:175-181`).
- `refreshAccessToken()` coalesces concurrent callers onto one in-flight refresh
  (`refreshInFlight` dedup, `axios.ts:188-205`).
- **New assertion pinning the target behavior**, added once the migration step lands:
  the bare global `axios` singleton (imported directly, not `http`) also carries the
  auth-header interceptor — i.e. a request issued via plain `axios.request(...)` (not
  `http`) picks up the same `Authorization` header. This is the test that proves
  generated-client calls get auth for free.
- One characterization case for the `attemptRefresh` relocation risk (§8): the refresh
  call itself must never carry a stale `Authorization` header regardless of
  `accessTokenRef`'s value, both before and after the `refreshHttp` extraction.

**New — coverage for `unreadCount()`**, which has no existing test anywhere
(`NotificationBell.vue` consumes it via `useNotificationsList.ts` but no test exercises
the count fetch). Add either a small `NotificationBell.vue` test or a direct
`notifications/api/index.spec.ts` case using the same MSW pattern, before conversion.

All new/existing tests above must pass against the **current** (pre-refactor) code
before any implementation edit — this is the safety net the refactor discipline
requires.

## 7. Migration Steps

1. Write the new `axios.spec.ts` characterization tests (§6); confirm green against
   current `axios.ts`.
2. Write the `unreadCount()` characterization test; confirm green against current
   `notifications/api/index.ts`. (`NotificationsView.test.ts` already covers
   `list`/`markRead` — no new file needed for those.)
3. Regenerate `backend/openapi.json` (`python -m scripts.export_openapi`), commit.
4. Regenerate `frontend/src/shared/api-client` (`pnpm run gen:api`), commit —
   `check:openapi-drift` passes from this point on regardless of the rest of this
   dossier.
5. In `axios.ts`: extract the inline interceptor arrows to named consts. No behavior
   change — step-1 tests still pass unmodified.
6. In `axios.ts`: introduce `refreshHttp` for `attemptRefresh()`; register the same
   named interceptors on the bare `axios` singleton. Step-1 tests still pass; add the
   new "bare `axios` also gets the auth interceptor" assertion (§6) and confirm it now
   passes.
7. Set `OpenAPI.WITH_CREDENTIALS = true` at the init point the implementer confirms is
   correct against `main.ts`'s actual import graph (document the chosen location in
   the Deviation Log if it differs from the plan in §5).
8. Convert `notifications/api/index.ts` to call `NotificationsService.*`; re-export
   generated model types. Confirm `NotificationsView.test.ts` and the step-2
   `unreadCount()` test both pass **unmodified**.
9. Behavioral check (Definition of Done step 4): in a running dev stack, let an access
   token expire (or force a 401), confirm a notifications-slice call silently
   refreshes and succeeds — the integration proof that generated-client calls now
   share `http`'s session behavior.

Each step leaves `pnpm test`/`pnpm typecheck`/`pnpm lint` green before the next starts.

## 8. Risks and Rollback

- **Highest risk:** relocating `attemptRefresh`'s axios instance changes which
  singleton issues that call. If anything implicitly relied on the bare `axios`
  object's un-instrumented state specifically for the refresh call, moving it to
  `refreshHttp` (also uninstrumented) should be behaviorally identical — but the new
  characterization test in §6 ("refresh call never carries a stale `Authorization`
  header") exists specifically to catch a regression here before step 6 lands.
- Attaching interceptors to the global default `axios` singleton affects **any**
  future bare `import axios from 'axios'` call anywhere in the app, not only the
  generated client. Confirmed today only two files import `axios` directly
  (`shared/transport/axios.ts`, generated `shared/api-client/core/request.ts`) — grep
  `frontend/src` for `from 'axios'`. Leave a comment at the new interceptor
  registration warning future editors this is now a shared, instrumented singleton.
- Rollback is `git revert` per step; steps 6 and 7 must both land before step 8 is
  attempted (step 8 depends on the global singleton actually carrying auth) — do not
  reorder.

## 9. Acceptance Criteria

- [x] AC-1: `backend/openapi.json` is byte-identical to a fresh
      `python -m scripts.export_openapi` run (verified via a non-destructive diff to a
      temp file — see D-5); `check:openapi-drift`'s own detection logic confirmed
      manually equivalent (see D-5 for why the script itself couldn't be run as-is).
      Commit `fb35ef0`.
- [x] AC-2: `frontend/src/shared/api-client` is identical to a fresh `pnpm run gen:api`
      run against the regenerated spec (verified via a temp-directory regen + file-list
      diff — only pre-existing, non-generated `queryKeys.ts` differs, as expected).
      Commit `2dd1e7f`.
- [x] AC-3: `frontend/src/shared/transport/__tests__/axios.spec.ts` (13 tests) passes
      against the pre-refactor code (commit `fea9e15`), continues passing unmodified
      after steps 5-7 land (commits `3a81f4d`, `5226509`, `2bed0b8`), plus the
      bare-singleton assertions added in step 6 pass.
- [x] AC-4: `NotificationsView.test.ts` and
      `notifications/api/__tests__/index.spec.ts` (`unreadCount`, plus the `list`
      cursor-edge-case coverage added in D-3) pass unmodified before (commit `fea9e15`)
      and after (commits `273085f`, `b3770c5`) step 8's conversion.
- [x] AC-5: proven at the transport level — the two "bare axios singleton" tests in
      `axios.spec.ts` (added in commit `5226509`) exercise exactly the request path a
      generated-client call takes (bare `axios`, no `http`), including a 401 →
      silent-refresh → replay → 200 round trip. The `notifications` slice's conversion
      (commit `273085f`) routes through that exact path. Live docker-stack walkthrough
      skipped per user decision — see D-6 (same call as Phase 4β's D-4).
- [x] AC-6: `pnpm typecheck`, `pnpm lint` (scoped to touched files — see D-7),
      `pnpm test` (113 files / 405-407 tests, one pre-existing unrelated flake — see
      D-8), `pnpm build`, `check:bundle-size`, and `check:type-coverage` (98.25%) all
      pass.
- [x] AC-7: `check-security` run against the full diff (auth/session surface
      specifically) — 0 Critical/High/Medium findings, 1 hardening suggestion (see
      Follow-ups).

## 10. SRS Delta

None — this restores/extends `[R24.13]`, an existing requirement; it does not define
new behavior.

## 11. Deviation Log

- **D-1 — `refreshHttp` sets `withCredentials: true` explicitly.** The bare
  `axios.post('/api/auth/refresh', {})` call it replaces had no explicit credentials
  setting (default `false`). Added for parity with `http`'s own `withCredentials: true`
  (`axios.ts:78-84`'s comment on why: cross-origin deployments need it for the cookie
  to ride along). In the current same-origin deployment this has no observable effect
  (the browser already sends same-origin cookies regardless of the flag), but it is a
  literal, if inert, departure from §5's "behaviorally identical" framing — called out
  rather than silently accepted.
- **D-2 — characterization test caught a real bug before commit.** Step 6's
  `handleResponseError` was shared across two axios instances with different
  `baseURL`s. The retry path (`if (ok) return http(original)`) was hardcoded to replay
  via `http` regardless of which instance the original request came through — for a
  request issued via the bare `axios` singleton (no `baseURL`, so its URLs already
  include the full `/api/...` prefix), replaying through `http` (`baseURL: '/api'`)
  produced a double `/api/api/...` prefix. The new "bare axios singleton" test in
  `axios.spec.ts` (§6/AC-5) failed against this before it was ever committed.
  `handleResponseError` now takes the originating `instance` and replays through it.
  Not anticipated in §7's Migration Steps — discovered by following the
  characterization-test-first discipline exactly as specified.
- **D-3 — self-audit caught a falsy-cursor divergence.** The first pass at
  `notifications/api/index.ts`'s conversion used `cursor ?? null`, which only
  coalesces `null`/`undefined` — an empty-string cursor would have been sent as a
  literal `cursor=` query param, unlike the original `http.get(...)` call's
  `cursor ? {cursor} : {}` check, which omitted it for any falsy value including `''`.
  Fixed to `cursor || null` (commit `b3770c5`) with new regression coverage for both
  cases. No real call site reaches this today — `useNotificationsList.ts`'s
  `pageParam` is always `undefined` or a real notification id, never `''` — but exact
  behavioral parity was the refactor's stated non-goal boundary, so it was fixed rather
  than left as a documented gap.
- **D-4 — removed `vi.useFakeTimers()` from the network-error characterization test.**
  The original test wrapped its assertion in `vi.useFakeTimers()`/`vi.useRealTimers()`
  defensively, to avoid `markConnectionLost()`'s real `setTimeout` probe outliving the
  test. This was unnecessary (the existing `afterEach`'s `markConnectionRestored()`
  call already cancels that timer via `clearProbeTimer()`) and turned out to leak
  faked-timer state into other test files run in the same `vitest` pass — reproduced by
  `src/app/__tests__/Landing.test.ts`'s deep-link-forward test failing only when run
  alongside `axios.spec.ts`, and passing reliably in isolation. Removed; confirmed the
  Landing test passes consistently when paired with `axios.spec.ts` afterward.
- **D-5 — `pnpm run check:openapi-drift` could not be run as originally planned, and
  destroyed `backend/openapi.json` mid-session.** The script's `bash` invocation on
  this machine resolves to WSL bash, where `python` is not on `PATH` (unlike the
  Windows Python used for every manual `export_openapi` run in this task). Worse: the
  script's `python -m scripts.export_openapi > openapi.json` line truncates the target
  file via shell redirection *before* discovering `python` doesn't resolve — and since
  WSL mounts the Windows filesystem at `/mnt/c`, that truncation hit the real,
  just-committed `backend/openapi.json` (confirmed 0 bytes afterward), not a WSL-local
  copy. Recovered via `git checkout -- openapi.json`; no data was lost beyond the
  already-committed state. AC-1/AC-2 were instead verified via a non-destructive
  manual regen-and-diff (export to a temp file / temp directory, diff, delete) — see
  their evidence above. This is a pre-existing local-environment issue, not introduced
  by this task, and out of the spec's stated non-goals ("not modifying
  check:openapi-drift's detection mechanism") — see FU-3.
- **D-6 — live docker-stack behavioral verification skipped, per user decision.**
  Same call as Phase 4β's D-4: the transport-level characterization tests already
  exercise the exact 401 → silent-refresh → replay → 200 path a generated-client call
  takes (both via `http` and via the bare `axios` singleton), so the user chose to
  accept that automated evidence rather than re-run the docker stack for a live check
  of an auth-timing-dependent flow that's awkward to trigger manually (waiting for
  real token expiry, or forcing one).
- **D-7 — `pnpm lint` run scoped to touched files, not the full repo.** A full-repo
  `pnpm lint` currently fails the `max-warnings: 0` gate with 296 pre-existing
  warnings (`vue/html-indent`, unused-var patterns) in files this task never touched —
  same category of pre-existing debt as Phase 4β's D-5. Verified via `npx eslint
  <touched files>` after every commit in this task: 0 warnings, 0 errors on every file
  this task created or modified.
- **D-8 — one pre-existing, unrelated test flake.** `src/app/__tests__/Landing.test.ts`'s
  "forwards a logged-out deep-link visitor on to login" test fails intermittently when
  the full 113-file suite runs, but passes reliably in isolation or in small groups.
  Confirmed pre-existing and unrelated to this task via `git stash` isolation: it
  reproduces identically with this entire dossier's diff reverted (stashed back to the
  last pre-task commit). See FU-4.

## 12. Follow-ups

- FU-1: convert the remaining eight slices to wrap the generated client now that the
  pattern and auth wiring are proven. Largest: `tenancy` (30 endpoints across
  `orgs.ts`/`projects.ts`/`invites.ts`); smallest first is the natural order.
- FU-2: if the deployment model ever moves to cookie-based auth read by JS (currently
  N/A — Bearer-token model, confirmed in §3), revisit whether `withXSRFToken`
  hardening is needed on both `http` and the newly-instrumented bare `axios` singleton.
- FU-3: `frontend/scripts/check-openapi-drift.sh:14` truncates `backend/openapi.json`
  via shell redirection before it discovers `python` doesn't resolve in the invoking
  shell (D-5) — on a machine where that shell is WSL bash without `python` on `PATH`,
  this destroys the real committed file (via the `/mnt/c` passthrough), not a
  disposable copy. Fix: write to a temp file and `mv` into place only on success (same
  pattern the export step already half-does for `openapi.json.new` per Phase 4β's
  investigation, just not followed through), so a failed export never touches the
  committed artifact.
- FU-4: `src/app/__tests__/Landing.test.ts`'s "forwards a logged-out deep-link
  visitor" test is flaky under full-suite load (D-8) — fails ~consistently when all
  113 files run together, passes reliably alone. Not investigated beyond confirming
  it's pre-existing and unrelated to this task; likely the bounded `flushPromises` +
  real-`setTimeout` retry loop not settling in time under CPU contention. Worth a
  dedicated stabilization pass (e.g. drive it with fake timers or a deterministic
  event instead of wall-clock retries).
- FU-5: harden `injectAuthHeader` (and the idempotency-key / Accept-Language
  interceptors) on the bare `axios` singleton to skip injection for requests to a
  different origin, as defense-in-depth. Not exploitable today — `pnpm why axios`
  confirms `axios` has exactly one consumer in the dependency tree, and the only
  current user of the bare singleton (the generated client) only ever constructs
  same-origin `/api/...` URLs — but nothing currently stops a future dependency that
  internally uses the bare `axios` default export from silently inheriting the
  bearer-token interceptor and leaking it to a third-party host. From the check-security
  audit's Hardening section.
