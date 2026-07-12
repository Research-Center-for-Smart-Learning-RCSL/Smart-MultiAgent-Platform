---
type: refactor
status: implemented
created: 2026-07-12
requirements: [R24.13]
---

# Wrap the `identity` slice's api layer over the generated client

## 1. Summary

Next increment of the [R24.13] slice-wrap program: convert the `identity` slice's api
(one module — `auth.ts`/`authApi`, **16 methods**) to call the generated
`@shared/api-client` `AuthService` instead of the bare `@shared/transport` `http`
singleton. Like `keys`/`agents`/`tenancy`, `authApi` returns the **raw `AxiosResponse`**,
so this is the **agent-groups pattern**: return the bare body and drop `.data` at every
consumer. The sweep is unusually small — **6 `.data` sites, all inside the slice**
(session store ×3, `RegisterView`, `ProfileView`, `SessionsView`), no cross-slice
consumers, no test-mock envelopes. All 16 methods map 1:1 to `AuthService` (no dead code).
The slice keeps its hand-rolled types (Q-2); two generated `*Out` are **not** directly
assignable, so this needs **two small boundary bridges** — `toCaptchaConfig` (union
widening) and `toMe` (optional-vs-required `display_name`).

This is the security-critical auth surface (login, password change/reset, email change,
account deletion, session management). The conversion is wiring-only and — verified — does
**not** touch the token-handling path: the axios 401-refresh interceptor uses a separate,
deliberately uninstrumented `refreshHttp` instance that never calls `authApi`, and the
generated client's bare `axios` singleton carries the **same interceptor function
references** as `http`, so bearer injection, silent 401-refresh-and-replay, and
problem+json→typed-error mapping are all preserved unchanged.

## 2. Motivation

- **[R24.13] convergence.** `auth.ts` (`auth.ts:1` imports `http`) hand-encodes 16
  request/response shapes against the axios singleton, duplicating URL/verb/body knowledge
  `pnpm run gen:api` already owns and `check:openapi-drift` guards. `identity` is one of the
  last `http`-based api layers (only `admin`, `prompt-studio` remain — FU-2).
- **Truthful contract on the auth surface.** `deleteAccount` currently uses the axios
  `{ data: {...} }` trick to attach a body to a `DELETE` (`auth.ts:82`); the generated
  `deleteAccountApiAuthMeDelete` models the DELETE body as a first-class `requestBody`
  (`AuthService.ts:134-143`), removing a hand-rolled idiom. Deriving the shapes from the
  OpenAPI removes drift risk on the most sensitive endpoints in the app.

## 3. Non-goals

- **No behavior change on the wire, and none on the token path.** Same endpoints/verbs/
  bodies, same silent-refresh behavior. Critically, `shared/transport/axios.ts` —
  `attemptRefresh`/`refreshHttp` (`axios.ts:244-263`) and `fetchWsTicket`
  (`axios.ts:285-290`) — is **out of scope and untouched**; those are transport-internal and
  keep their `res.data`.
- **No slice-type rebase.** The hand-rolled `LoginRequest`/`TokenPair`/`Me`/`Session`/
  `CaptchaConfig` types stay (Q-2); divergences are bridged at the api boundary.
- **No `gen:api` rerun.** Frontend-only edit; the contract is unchanged.
- **No session-store / interceptor re-architecture.** `applyTokens`, `hydrate`, the 401
  interceptor, and the idle-logout composable keep their shape; only the `.data` unwrap at
  the six store/view sites is removed.
- **No barrel cleanup.** The `api/index.ts` `authApi` re-export is currently unused (every
  consumer imports from `../api/auth` directly) but stays — removing it is orthogonal (FU-3).
- **No `session-policy` / `ws-ticket` wrapping.** `AuthService.sessionPolicy…` and
  `issueWsTicket…` have no `authApi` wrapper — they are consumed directly by
  `useIdleLogout.ts` and `axios.ts` respectively and stay that way.

## 4. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | (settled, carried) enum widening? | Backend enums already narrowed where they matter; see Q-4 for the one exception. | `UserStatus` is emitted as the exact string-literal union `'active'\|'pending'\|'banned'\|'deleted'`, matching `Me.status`. |
| Q-2 | (settled, carried) Keep hand-rolled types or alias generated? | Keep hand-rolled; bridge divergences. | Same program-wide strategy; here it needs two bridges. |
| Q-3 | (settled, carried) How to convert safely at this scale? | Rewrite over `AuthService`; `pnpm typecheck` enumerates the `.data` sites; update the six; characterization spec pins the wire contract. | Proven across six prior slices. |
| Q-4 | `CaptchaConfigOut` types `mode`/`provider` as `string`, widening the hand-rolled `'on'\|'off'` / `'hcaptcha'\|'turnstile'\|'off'` unions — `RegisterView` narrows on these. Bridge or relax the type? | Add a `toCaptchaConfig` bridge that casts the two fields back to the unions. | Keeps the meaningful unions the widget switches on (`RegisterView.vue:22,63`); it relocates the *same* unchecked assertion the old `http.get<CaptchaConfig>` already made (the backend enum guarantees the values), matching the workflow-slice cast pattern. |
| Q-5 | `UserOut.display_name` is optional (`string\|null\|undefined`); `Me.display_name` is required (`string\|null`) — not assignable. Bridge or relax `Me`? | Add a `toMe` bridge defaulting `display_name: u.display_name ?? null`. | Keeps `Me` intact so no consumer churns on a newly-`undefined` field (mirrors the agents-slice `?? []`/`?? null` boundary defaults); the server always sends the field, so `?? null` is inert at runtime and truthful. |

## 5. Current vs Target Structure

Frontend layer direction unchanged (`slices/identity/api` → `shared/api-client`). Each
method body changes from `http.<verb><T>(url, …)` (returning `AxiosResponse<T>`) to
`AuthService.<method>({ …options })` (resolving the bare body); method names/signatures
stay. Full 16-row mapping is in the Explore artifact; highlights:

### 5A. Mapping highlights (all 16 matched, no NO-MATCH)

| Group | AuthService method | Notes |
|---|---|---|
| `login`/`refresh` | `login…Post`/`refresh…Post` | resolve `TokenPairOut` → assignable to `TokenPair` (required `refresh_token` satisfies the optional field) — no bridge |
| `me`/`updateProfile` | `meApiAuthMeGet`/`updateMeApiAuthMePatch` | resolve `UserOut` → **`toMe` bridge** (Q-5) |
| `captchaConfig` | `captchaConfig…Get` | resolves `CaptchaConfigOut` → **`toCaptchaConfig` bridge** (Q-4) |
| `listSessions` | `listSessions…Get` | resolves `SessionOut[]` → assignable to `Session[]` — no bridge; param object required, pass `{}` (or `PaginationParams`) |
| `register`/`verifyEmail`/`requestPasswordReset` | `register…`/`verifyEmail…`/`requestPasswordReset…` | loosely-typed generated returns (`Record<string,string>`/`any`); all **await-only** — annotate wrapper `Promise<void>`, no typing needed |
| `deleteAccount` | `deleteAccount…Delete` | body via `requestBody: { password }` (drops the axios `{ data }` DELETE-body trick) |
| `logout`/`resetPassword`/`changePassword`/`changeEmail`/`revokeSession` | `…` | resolve `void`; await-only |

### 5B. Response bridges (two)

- `toMe(u: UserOut): Me` — `{ id, email, email_verified, is_admin, status, display_name: u.display_name ?? null }`. `status` is already the right union; only `display_name` needs the default.
- `toCaptchaConfig(c: CaptchaConfigOut): CaptchaConfig` — `{ mode: c.mode as CaptchaConfig['mode'], provider: c.provider as CaptchaConfig['provider'], sitekey: c.sitekey }`.

`TokenPairOut`→`TokenPair` and `SessionOut`→`Session` are directly assignable — no bridge.

### 5C. Consumer sweep (drop `.data`) — 6 sites, all in-slice

`pnpm typecheck` lists every `.data`-on-bare-body site. From the sweep, all are the
`const { data } = await …` form:
- `stores/session.ts:27` (`login` → `applyTokens`), `:33` (`me` → `me.value`), `:66`
  (`refresh` → `applyTokens`).
- `views/RegisterView.vue:49` (`captchaConfig`), `views/ProfileView.vue:37`
  (`updateProfile` → `setMe`), `views/SessionsView.vue:77` (`listSessions`).
- The remaining 9 call sites (register, verifyEmail, resetPassword, requestPasswordReset,
  changePassword, changeEmail, logout, deleteAccount, revokeSession) are await-only — no
  change.
- **No cross-slice consumers** (the `@slices/identity` barrel does not re-export `authApi`).

### 5D. `listSessions` param object

`listSessionsApiAuthSessionsGet` destructures a **required** options object, so the wrapper
must call it with `({})`. Its `limit` defaults to 100 and is sent as an explicit query
param (D-1, same as tenancy — server default is identical, results unchanged).

### 5E. Test updates

**None required.** Grep confirms no test mocks `authApi` or `../api/auth` (the store/views
are tested via the `http`/transport seam, not by mocking `authApi` envelopes). Add
`identity/api/__tests__/auth.spec.ts` — request-level MSW characterization across the 16
methods: verb/path/body for a representative read/write, the `deleteAccount` DELETE-body,
the `login`/`refresh` `TokenPair` shape, the two bridges (`toMe` defaulting `display_name`,
`toCaptchaConfig` preserving the unions), and the await-only mutations.

## 6. Security Considerations

This is the auth surface — the highest-stakes dimension of `check-security` (auth,
credentials, session/token handling):

- **Token-handling path untouched (verified).** The 401 interceptor's refresh uses a
  separate bare `refreshHttp` instance (`axios.ts:244-263`) that never calls `authApi` and
  is never intercepted — a refresh 401 cannot recurse. Converting `authApi.refresh`/`login`
  to `AuthService` does not touch this path. `attemptRefresh`/`fetchWsTicket` stay untouched
  and keep their `res.data`.
- **Interceptor behavior preserved.** The generated client resolves through the bare `axios`
  singleton, which registers the **same function references** (`injectAuthHeader`,
  `injectIdempotencyKey`, `injectAcceptLanguage`, `handleResponseSuccess`,
  `handleResponseError`) as `http` (`axios.ts:223-228`) — bearer injection, silent
  refresh-and-replay, and problem+json→typed-error mapping are identical. Login/refresh
  bearer semantics are unchanged (not a new stale-token exposure).
- **No circular import.** `shared/transport` imports only the `OpenAPI` config object from
  `@shared/api-client`; the generated `AuthService` never imports `transport` or
  `@slices/identity`. The new `auth.ts → AuthService` edge introduces no cycle.
- **Credentials never logged, bodies byte-identical.** login/register/changePassword/
  changeEmail/resetPassword/deleteAccount send the same `{ …password… }` bodies to the same
  endpoints; no `console.*` added; the `deleteAccount` re-auth password now rides the
  generated `requestBody` (still the DELETE body) instead of the axios `{ data }` field —
  same wire, no logging.
- **Session management AuthZ unchanged.** `listSessions`/`revokeSession` hit the same
  self-scoped endpoints; the server owns the "only your own sessions" check.
- **Smoke-test the error path.** `LoginView`/`SessionsView` read typed errors
  (`RateLimitError`/`isProblemWithType`/`e.response.status`) from the interceptor's thrown
  errors; since the bare singleton runs the same `handleResponseError`, these are unchanged
  — but verify a failed login still surfaces the rate-limit/invalid-credentials UI.

## 7. Migration Steps

1. Rewrite `auth.ts` over `AuthService`; drop the `http` import; keep the type exports and
   method names; add `toMe`/`toCaptchaConfig`; apply Q-5/Q-4 and §5D.
2. `pnpm typecheck` → drop `.data` at the six sites (§5C) until green.
3. `pnpm test` → all pass unmodified (no authApi mocks); add
   `identity/api/__tests__/auth.spec.ts` (§5E).
4. `pnpm lint` (changed files) + `pnpm build`. No `gen:api`.
5. Behavioral smoke via the `verify` skill: log in, refresh, view/rename profile, list &
   revoke a session, and confirm a bad login still shows the typed error (§6).

## 8. Risks and Rollback

- **Auth surface — highest blast radius.** A mistake could break login/refresh for every
  user. Mitigated: the token path is provably untouched (§6), `pnpm typecheck` is exhaustive
  on the six `.data` sites, the characterization spec pins the wire contract, and a
  behavioral smoke-test covers the live login/refresh/session flows before close-out.
- **Bridge correctness.** `toMe`/`toCaptchaConfig` must not drop or mistype a field —
  pinned by spec cases (Q-4/Q-5).
- **`listSessions` query delta (§5D/D-1).** Benign — server default matches the injected
  `limit=100`.
- Rollback is `git revert` of the implementation commit; `auth.ts` is self-contained and the
  six sweep edits are mechanical.

## 9. Acceptance Criteria

- [x] AC-1: every `authApi` method calls an `AuthService` method; no `@shared/transport`
      `http` import remains in `identity/api/*`; each method resolves the bare body typed as
      its slice type (via `toMe`/`toCaptchaConfig` where Q-4/Q-5 apply; the await-only
      mutations resolve the generated body, unread by consumers).
- [x] AC-2: the six `.data` sites are converted; `pnpm typecheck` green and the changed
      source files lint clean, with no consumer edit beyond the six. (Repo-wide `pnpm lint`
      stays red on the 296 pre-existing warnings in untouched files — FU-4 of the tenancy
      dossier.)
- [x] AC-3: the two bridges behave — `auth.spec.ts` asserts `toMe` defaults an absent
      `display_name` to `null` and preserves `status`, and `toCaptchaConfig` yields the
      `mode`/`provider` union values. Security audit confirmed `is_admin`/`email_verified`/
      `status` pass through faithfully.
- [x] AC-4: request bodies/params/verbs unchanged — `auth.spec.ts` (16 cases) asserts
      verb/path/body per method, including the `deleteAccount` DELETE-body `{ password }`, the
      `login` `TokenPair` shape, and `refresh` posting an empty body to `/auth/refresh`.
- [x] AC-5: `pnpm test` green (574 passed — all prior tests unmodified + the 16-case new
      spec); `pnpm build` green.
- [~] AC-6: token/interceptor path unchanged — `axios.ts` confirmed **untouched** (empty
      diff-stat); security audit: **no findings** (token path, credential non-logging, and the
      `deleteAccount` DELETE-body all verified statically). The live behavioral smoke-test
      (login/refresh/profile/session-revoke/failed-login UI) is **deferred** — it needs the
      running dev stack, unavailable in this build environment (D-1). Recommended before
      treating the auth surface as fully validated.

## 10. SRS Delta

None — behavior-preserving refactor of the api-client layer.

## 11. Deviation Log

- D-1: the live behavioral smoke-test in AC-6 was not run — the dev stack (backend +
  Postgres + Redis + Vault) is not available in this build environment. Compensating
  evidence: `axios.ts` is provably untouched (empty diff-stat), the token/interceptor path
  is unchanged by construction (the 401-refresh interceptor uses its own uninstrumented
  instance and never calls `authApi`), the 16-case characterization spec pins every method's
  wire contract and both bridges, and the security audit found nothing. The smoke-test is
  carried as a recommended manual check for the user's running environment.
- D-2: `listSessions` now emits the generated client's default `limit=100` query param where
  the old `http.get('/auth/sessions')` sent none (same as the tenancy slice). The backend
  default is identical, so the result set is unchanged; only the wire query gains an explicit
  `limit`. The spec asserts method/path, not the query, so a future default change would not
  break it — accepted as behavior-equivalent per the program-wide convention.
- D-3: the shared test helper `tests/helpers/requestCapture.ts` was widened to capture
  request bodies on every non-GET verb (was: non-GET-and-non-DELETE), so the `deleteAccount`
  DELETE re-auth body can be asserted. Verified harmless to the sibling specs
  (workflow/tenancy/agents): their bodyless DELETEs still resolve `body: undefined`, and none
  asserts a DELETE body — all three specs re-run green.

## 12. Follow-ups

- FU-1: (carried) merge the two `useProjectRole` composables (tenancy/workflow).
- FU-2: remaining slice wraps (`admin`, `prompt-studio`) — the last `http`-based api layers.
- FU-3: delete the unused `authApi` re-export in `identity/api/index.ts` (dead — every
  consumer imports from `../api/auth`), or wire consumers through the barrel for consistency.
