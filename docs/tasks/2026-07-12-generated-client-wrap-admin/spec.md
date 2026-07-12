---
type: refactor
status: draft
created: 2026-07-12
requirements: [R24.13]
---

# Wrap the `admin` slice's api layer over the generated client

## 1. Summary

Next increment of the [R24.13] slice-wrap program: convert the `admin` slice's api
(one module — `admin.ts`/`adminApi`, **25 methods**) to call the generated
`AdminService` (plus `GraphragAdminService` for one method) instead of the bare
`@shared/transport` `http` singleton. Unlike the recent slices, `adminApi` **already
unwraps `.data`** (every method ends `.then(r => r.data)`), so this is the
**conversation/workflow pattern**: signature-preserving, **zero consumer changes** — the
25 call sites across `useAdminActions`/`useImpersonation`/the 11 `Admin*View`s stay
untouched, and the view/store tests (which mock at the MSW HTTP layer) pass unmodified.
All 25 methods have a generated match (no dead code). The slice keeps its hand-rolled
types (Q-2); the work is concentrated in the boundary details: a snake→camel translation
for the audit query params, three small casts, one truthful type widening, and the
second-service import.

This is the **admin-only privileged surface** (ban / impersonate / hard-delete /
force-transfer / IP-bans / rate-limits / audit-with-PII). The conversion is wiring-only;
authorization stays server-side behind the admin guard, and the impersonation access-token
flow is preserved.

## 2. Motivation

- **[R24.13] convergence.** `admin.ts` (`admin.ts:1` imports `http`) hand-encodes 25
  request/response shapes against the axios singleton, duplicating URL/verb/body/query
  knowledge `pnpm run gen:api` already owns. `admin` is one of the last two `http`-based api
  layers (only `prompt-studio` remains after this — FU-2).
- **Contract truthfulness on a privileged surface.** Deriving the audit query params, the
  `restore` path, and the IP-ban shape from the OpenAPI removes hand-maintained drift on the
  endpoints that ban users, delete orgs, and mint impersonation tokens.

## 3. Non-goals

- **No consumer changes, no wire change.** The methods already return bare bodies; keeping
  the hand-rolled return types means the 25 call sites, the composables, and the views are
  untouched. Same endpoints/verbs/bodies/query params.
- **No slice-type rebase** beyond one truthful widening (Q-4: `IpBan.created_by_user_id` →
  `string | null`, matching the backend contract). All other hand-rolled types stay (Q-2).
- **No `gen:api` rerun** and **no backend change.** In particular the `restore` endpoint's
  OpenAPI `resource_type` enum is narrower (`'user'|'org'|'project'`) than the six values the
  UI sends; widening that enum is a backend follow-up (FU-4), not this task — the wrapper
  casts to preserve today's behavior.
- **No composable/view/store re-architecture** and **no barrel change** (`adminApi` is not
  re-exported from the slice barrel and stays that way).

## 4. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | (settled, carried) enum widening? | Matching where it matters. | `UserStatus` is the exact union `'active'\|'pending'\|'banned'\|'deleted'`; `RateLimitPolicyOut.scope` narrows to a union but flows into the wider hand-rolled `string` (return path — fine). |
| Q-2 | (settled, carried) Keep hand-rolled types or alias generated? | Keep hand-rolled; annotate-and-return, bridge/widen only where a generated `*Out` is not assignable. | Consumers depend on the hand-rolled field names/unions (e.g. `AdminUserDetailView` reads 13 `UserDetail` fields). |
| Q-3 | (settled, carried) How to convert safely? | Swap `http.X().then(r=>r.data)` for the generated call, keep return annotations; `pnpm typecheck` + the view tests + a new api characterization spec guard it. | Signature-preserving, so typecheck alone proves the consumer surface is unchanged. |
| Q-4 | `IpBanOut.created_by_user_id` is `string \| null`; `IpBan.created_by_user_id` is required `string` — not assignable. Bridge (`?? ''`) or widen the type? | Widen `IpBan.created_by_user_id` to `string \| null`. | The field is genuinely nullable in the backend contract (system-created bans); a `?? ''` bridge would fabricate an empty creator. Views cast IP-ban rows through `as unknown as Row[]`, so widening breaks no consumer. |
| Q-5 | The generated `restore` `resourceType` is `'user'\|'org'\|'project'`, but `AdminOpsView` sends six types (`+agent/workflow/chatroom`). Narrow the wrapper or cast? | Keep the wrapper signature `restoreResource(type: string, id: string)`; cast `resourceType: type as 'user'\|'org'\|'project'` at the boundary. | Behavior-preserving (the raw string flows into the path exactly as the old `http.post('/admin/restore/${type}/${id}')`); narrowing the wrapper would reject three valid UI values. The narrow OpenAPI enum is a backend defect → FU-4. |

## 5. Current vs Target Structure

Frontend layer direction unchanged (`slices/admin/api` → `shared/api-client`). Each method
body changes from `http.<verb><T>(url, …).then(r => r.data)` to `AdminService.<method>({…})`
(already-bare body), keeping the wrapper name/signature/return type. Full 25-row mapping is
in the Explore artifact; highlights:

### 5A. Mapping highlights (all 25 matched)

- **Annotate-and-return, no bridge (directly assignable):** `listUsers`, `getUser`,
  `listAdmins`, `promoteAdmin`, `queryAudit`, `listOrgs`, `listProjects`, `getMetrics`,
  `listRateLimits`, `patchRateLimit`, `impersonate`, `restoreResource`, `demote`/`ban`/
  `unban`/`softDelete`/`hardDelete`/`endImpersonate`/`forceDeleteOrg`/`deleteIpBan` (void).
- **`resetGraphrag` → `GraphragAdminService.adminResetApiAdminGraphragConfigIdResetPost`**
  (`{ configId }`) — same `POST /api/admin/graphrag/{config_id}/reset` endpoint, return
  ignored by the sole consumer.
- **Second-service import:** `GraphragAdminService` alongside `AdminService`.

### 5B. Bridges, casts, and the widening

- **Widen (Q-4):** `IpBan.created_by_user_id: string | null` in `types/index.ts`; then
  `listIpBans`/`createIpBan` are annotate-and-return (no bridge).
- **Cast `restoreResource` arg (Q-5):** `resourceType: type as 'user' | 'org' | 'project'`.
- **Cast `exportAudit` return:** generated `Record<string, any>` → `{ url: string; job_id: string }`
  (the consumer reads `.url`).
- **`forceTransferOC`:** generated `Record<string, any>`; consumer ignores the body —
  annotate `Promise<void>`.
- **Request bodies map cleanly** (assignable, no cast): `ban {reason}`→`BanIn`,
  `promote {user_id}`→`AdminPromoteIn`, `forceTransfer {target_user_id}`→`ForceTransferIn`,
  `createIpBan {cidr,reason}`→`IpBanIn`, `patchRateLimit {window_sec?,max_count?,scope?}`→
  `RateLimitPatchIn` (optional-`number` assignable to optional-`number|null`).

### 5C. Query-param translation

`listUsers`/`listOrgs`/`listProjects` forward their param object directly (names match, type
assignable); `listRateLimits` must pass `{}` (the generated options object is required).
`queryAudit`/`exportAudit` need a **snake→camel** translation — `AuditFilter`'s snake_case
keys (`actor_user_id`, `resource_type`, `ip_prefix`, `session_id`, `request_id`, …) map to
the generated camelCase options (`actorUserId`, `resourceType`, `ipPrefix`, …). Build the
options with **conditional spreads (omit-when-undefined)** so absent filters are not sent —
preserving today's "only ship set keys" behavior and satisfying `exactOptionalPropertyTypes`
(the generated options are `(string|null)`, which does not admit an explicit `undefined`). A
private `auditFilterToOptions` helper covers the nine shared fields; `queryAudit` adds
`cursor`/`limit`, `exportAudit` omits them (its generated options have neither).

### 5D. Consumer sweep

**None.** Signature-preserving: `pnpm typecheck` is the proof — if any return type or param
drifted, a consumer would fail to compile. The 25 call sites stay byte-for-byte.

### 5E. Test updates

No existing test mocks `adminApi` (the `Admin*View.test.ts` suite mocks MSW HTTP handlers
returning bare bodies — transparent to this change, provided paths are unchanged, which they
are). There is no `admin/api/__tests__`. Add `admin/api/__tests__/admin.spec.ts` — request-
level MSW characterization across the 25 methods: verb/path/body for a representative
read/write per shape, the audit snake→camel query translation (a filter with several fields
set → asserts the emitted `actor_user_id`/`ip_prefix`/… query keys), the `exportAudit` query
+ `.url` return, the `restore` six-type path, the `promoteAdmin`/`forceTransferOC`/`ban`
bodies, the `resetGraphrag` graphrag-admin route, and the IP-ban create/list.

## 6. Security Considerations

Admin-only privileged surface (`check-security`: privilege, session/token, PII/audit):

- **AuthZ unchanged.** Every endpoint is admin-gated server-side; the client calls identical
  paths with identical params. No authorization decision moves client-side.
- **Impersonation token flow preserved.** `impersonate` still resolves `{ session_id,
  access_token }`; `useImpersonation` reads `.access_token` and hands it to
  `setAccessToken` (in-memory), saving the admin token in module scope for `endImpersonate`
  to restore — the bare-body return is unchanged, so this flow is byte-identical.
- **Destructive actions byte-identical.** `ban {reason}`, `forceTransfer {target_user_id}`,
  `hard-delete`, `force-delete`, `createIpBan {cidr,reason}` send the same bodies to the same
  endpoints. No field added/dropped; no `console.*` added; no secret logged.
- **Audit PII unchanged.** The audit query ships exactly the filters set today (conditional
  spreads, no extra keys, no leaked defaults); `metadata` (`Record<string,unknown>`) passes
  through untouched. The audit CSV export hits the same route with the same filters.
- **`restore` cast is inert.** `type as 'user'|'org'|'project'` erases at runtime; the raw
  string flows into the path exactly as before — no new reachable action.

## 7. Migration Steps

1. Widen `IpBan.created_by_user_id` to `string | null` in `types/index.ts` (Q-4).
2. Rewrite `admin.ts` over `AdminService` (+ `GraphragAdminService`); drop the `http` import;
   keep every wrapper name/signature and hand-rolled return annotation; add the
   `auditFilterToOptions` helper and the three casts (§5B/§5C, Q-5).
3. `pnpm typecheck` → green with **no consumer edit** (the proof of signature-preservation).
4. `pnpm test` → the `Admin*View` suite passes unmodified; add
   `admin/api/__tests__/admin.spec.ts` (§5E).
5. `pnpm lint` (changed files) + `pnpm build`. No `gen:api`, no backend change.

## 8. Risks and Rollback

- **Privileged surface.** A mistake could misfire a ban/delete/impersonation. Mitigated:
  signature-preserving (typecheck proves the consumer contract is unchanged), the
  characterization spec pins every verb/path/body incl. the audit query translation, and the
  security review covers the impersonation/destructive paths.
- **Audit query translation (§5C).** The snake→camel rename is the one place a typo would
  silently drop a filter. Mitigated by an explicit multi-field spec case asserting the emitted
  query keys.
- **`restore` cast (Q-5).** Lies at the type level (claims 3 of 6 types) but is
  behavior-preserving at runtime; the six-type path is pinned by a spec case, and the backend
  enum fix is tracked as FU-4.
- **`listRateLimits` `limit=100` query delta** — same benign D-n as the prior slices.
- Rollback is `git revert` of the implementation commit; `admin.ts` is self-contained.

## 9. Acceptance Criteria

- [ ] AC-1: every `adminApi` method calls a generated `AdminService`/`GraphragAdminService`
      method; no `@shared/transport` `http` import remains in `admin/api/*`; each wrapper keeps
      its name/signature and resolves the bare body typed as its hand-rolled type.
- [ ] AC-2: `pnpm typecheck` green with **zero consumer edits** outside `admin/api/admin.ts`
      and the `IpBan` widening in `types/index.ts`; changed files lint clean.
- [ ] AC-3: the audit query translation works — the spec asserts `queryAudit` with several
      `AuditFilter` fields set emits the correct snake_case query keys, and omits unset ones;
      `exportAudit` ships its filters and its `.url` return is typed.
- [ ] AC-4: request bodies/verbs/paths unchanged — the spec asserts a representative read/write
      per shape, including `ban {reason}`, `promoteAdmin {user_id}`, `forceTransferOC
      {target_user_id}`, `createIpBan {cidr,reason}`, the `restore/{type}/{id}` six-type path,
      and the `resetGraphrag` graphrag-admin route.
- [ ] AC-5: `pnpm test` green — the `Admin*View` suite unmodified + the new characterization
      spec; `pnpm build` green.
- [ ] AC-6: security holds — impersonation token flow, destructive-action bodies, and audit
      filters are byte-identical; AuthZ unchanged (§6). Security audit: no findings.

## 10. SRS Delta

None — behavior-preserving refactor of the api-client layer.

## 11. Deviation Log

Appended by /build.

## 12. Follow-ups

- FU-1: (carried) merge the two `useProjectRole` composables (tenancy/workflow).
- FU-2: `prompt-studio` slice wrap — the last `http`-based api layer.
- FU-3: (carried) the pre-existing 296-warning lint debt blocking `--max-warnings=0` (from the
  tenancy dossier).
- FU-4: widen the backend `restore` endpoint's `resource_type` enum (or the OpenAPI schema) to
  the six values the admin UI actually offers (`user/org/project/agent/workflow/chatroom`), so
  the generated `resourceType` type stops needing a boundary cast. Also review whether
  `useAdminActions` should invalidate caches for the other three types on restore.
