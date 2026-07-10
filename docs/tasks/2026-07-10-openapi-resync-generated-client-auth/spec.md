---
type: refactor
status: approved
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

- [ ] AC-1: `backend/openapi.json` is byte-identical to a fresh
      `python -m scripts.export_openapi` run; `check:openapi-drift` passes.
- [ ] AC-2: `frontend/src/shared/api-client` is identical to a fresh `pnpm run gen:api`
      run against the regenerated spec.
- [ ] AC-3: the new `axios.spec.ts` characterization tests pass against the
      pre-refactor code, then continue passing unmodified after steps 5-7 land (proves
      no observable change to `http`'s existing callers) plus the new bare-singleton
      assertion passes once step 6 lands.
- [ ] AC-4: `NotificationsView.test.ts` and the new `unreadCount()` test pass
      unmodified before and after step 8's conversion.
- [ ] AC-5: a generated-client call (via the converted `notifications` slice), made
      with an expired access token, silently refreshes and retries exactly like an
      `http`-based call does — verified per step 9's behavioral check.
- [ ] AC-6: `pnpm typecheck && pnpm lint && pnpm test && pnpm build` all pass.
- [ ] AC-7: `check-security` run specifically against the `axios.ts` diff (session/auth
      surface) reports no unresolved CRITICAL/HIGH findings.

## 10. SRS Delta

None — this restores/extends `[R24.13]`, an existing requirement; it does not define
new behavior.

## 11. Deviation Log

Appended by /build.

## 12. Follow-ups

- FU-1: convert the remaining eight slices to wrap the generated client now that the
  pattern and auth wiring are proven. Largest: `tenancy` (30 endpoints across
  `orgs.ts`/`projects.ts`/`invites.ts`); smallest first is the natural order.
- FU-2: if the deployment model ever moves to cookie-based auth read by JS (currently
  N/A — Bearer-token model, confirmed in §3), revisit whether `withXSRFToken`
  hardening is needed on both `http` and the newly-instrumented bare `axios` singleton.
