---
type: refactor
status: approved
created: 2026-07-12
requirements: [R24.13]
---

# Wrap the `tenancy` slice's api layer over the generated client

## 1. Summary

Next increment of the [R24.13] slice-wrap program: convert the `tenancy` slice's api
(three modules — `orgs.ts`/`orgsApi`, `projects.ts`/`projectsApi`, `invites.ts`/`invitesApi`,
**30 methods total**) to call the generated `@shared/api-client` services
(`OrgsService`, `ProjectsService`, `InvitesService`) instead of the bare
`@shared/transport` `http` singleton. Like `keys`/`agents`, these methods return the **raw
`AxiosResponse`**, so this is the **agent-groups pattern**: return the bare body and drop
`.data` at every consumer — **28 `.data` sites across ~13 files, cross-slice**. All 30
methods have a URL+verb match (no dead code). The slice keeps its hand-rolled types (Q-2),
and — verified — every generated `*Out` is **directly assignable** to them, so **zero
response bridges** are needed (simpler than keys/agents). This is the multi-tenant AuthZ
boundary (orgs, projects, members, roles, invites, original-creator transfer); the
conversion is wiring-only, request bodies and `If-Match` byte-identical.

## 2. Motivation

- **[R24.13] convergence.** One instrumented axios singleton owns bearer auth, 401-refresh,
  and problem+json→typed-error mapping; the generated `request` core calls into it. The
  three tenancy modules hand-encode ~30 request/response shapes against `http`
  (`orgs.ts:1`, `projects.ts:1`, `invites.ts:1` import `http`), duplicating URL/verb/param
  knowledge `pnpm run gen:api` already owns and `check:openapi-drift` guards.
  `agent-groups`, `conversation`, `keys`, `agents`, `workflow` are done; `tenancy` is the
  next `http` holdout.
- **Truthful contract on a security surface.** The hand-rolled `restore` methods are typed
  `http.post<Org>` / `http.post<Project>` (`orgs.ts:50`, `projects.ts:33`) but the backend
  returns an empty body (generated `void`) — an optimistic type the wrapper corrects. On a
  tenant-boundary surface, deriving the shapes from the OpenAPI (not by hand) removes a
  class of silent drift.

## 3. Non-goals

- **No behavior change on the wire.** Same endpoints/verbs/bodies/query params, same
  `If-Match` preconditions. No invite email/role, member role, org/project name, or
  OC-transfer target is reshaped, logged, or dropped. AuthZ stays entirely server-side.
- **No slice-type rebase.** The hand-rolled types stay (Q-2): `Org`, `OrgMember`,
  `OrgQuotas`, `OriginalCreatorTransfer`, `Project`, `ProjectMember`, `Invite`. They are
  consumed cross-slice; keeping them as the api's public types means consumers that read
  `Project.owner_name` / `ProjectMember.is_inherited` (optional fields the generated `*Out`
  omit) still typecheck and receive whatever the backend sends at runtime (the generated
  client does not strip response fields).
- **No `gen:api` rerun.** Frontend-only edit; the contract is unchanged (contrast the
  orchestration task, which changed the backend).
- **No composable/store re-architecture.** `useMemberActions`, `useEntityLifecycle`,
  `useProjectRole` (both copies), and the tenancy stores keep their shape; only the `.data`
  unwrap at each site is removed.
- **No consolidation of the duplicate `useProjectRole`.** There are two
  (`tenancy/composables/useProjectRole.ts` and `workflow/composables/useProjectRole.ts`) —
  both are swept, neither is merged here (FU-1).

## 4. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | (settled, carried) enum widening? | Backend enums already narrowed. | Verified: `OrgMemberRole`/`ProjectMemberRole`/`ProjectOwnerType`/`OCTransferState`/`InviteScope`/`InviteState` are emitted as string-literal unions (`--useUnionTypes`) that match the hand-rolled unions exactly — no drift, no backend change. |
| Q-2 | (settled, carried) Keep hand-rolled types or alias generated? | Keep hand-rolled; bridge divergences. | Consumed cross-slice; and keeping `Project`/`ProjectMember` (with their optional `owner_name`/`is_inherited`) as the return type preserves consumer typechecking for fields the generated `*Out` omits. |
| Q-3 | (settled, carried) How to convert safely at this scale? | Rewrite over the generated services; `pnpm typecheck` enumerates every `.data` site; sweep mechanically; update the one module-mock test. | Proven across five prior slices. |
| Q-4 | The invite endpoints emit **three different** disambiguated models — how to map each? | Map by endpoint: `orgsApi.invite` → `app__api__v1__orgs__InviteOut`; `projectsApi.invite` → `Record<string,string>` (no invite body); `invitesApi.list`/`acceptByToken`/`accept`/`reject` → `app__api__v1__invites__InviteOut` (the only one with `created_at`+`scope_name`, assignable to the hand-rolled `Invite`). | FastAPI emitted scope-qualified schema names because the `InviteOut`/`InviteCreateIn` schemas collide. Only the `invites`-scoped model backs the `Invite` type; the org/project invite *mutations* are await-only (no consumer reads their return), so their asymmetric returns are harmless. |
| Q-5 | `orgsApi.restore`/`projectsApi.restore` are typed `<Org>`/`<Project>` but the backend returns `void`. | Type them `Promise<void>` (the generated/backend reality). | Both are consumed only via the `restoreApi` await-only callback into `useEntityLifecycle` (`OrgDetailView.vue:89`, `ProjectDetailView.vue:82`) — no `.data` is read, so narrowing to `void` is truthful and breaks nothing. |

## 5. Current vs Target Structure

Frontend layer direction unchanged (`slices/tenancy/api` → `shared/api-client`). Each
method body changes from `http.<verb><T>(url, …)` (returning `AxiosResponse<T>`) to
`<Service>.<method>({ …options })` (returning the bare body); the module keeps its method
names/signatures. Full method→generated-method table lives in the mapping artifact (Explore
output attached to this task); highlights:

### 5A. Mapping highlights (all 30 matched, no NO-MATCH)

| Group | Service | Notes |
|---|---|---|
| Org CRUD + members + quotas | `OrgsService` | `rename` → `ifMatch: String(version)`; `setRole` → `Record<string,string>` (untyped, await-only); `restore` → `void` (Q-5) |
| OC transfers (initiate/list/accept/cancel/reject) | `OrgsService` | `initiateTransfer` body `{ target_user_id }` → `TransferInitIn`; list/accept return `TransferOut` (assignable to `OriginalCreatorTransfer`) |
| Project CRUD + members | `ProjectsService` | `rename` → `ifMatch`; `restore` → `void` (Q-5); `list` scope/id → flat `{ scope, id, limit, offset }` options, preserve the both-or-neither guard (§5D) |
| Invites (list/accept/acceptByToken/reject) | `InvitesService` | `list` default `state='pending'` (matches); returns `app__api__v1__invites__InviteOut` (Q-4) |
| Org/Project invite create | `OrgsService`/`ProjectsService` | asymmetric returns (Q-4), await-only |

### 5B. Response bridges

**None expected.** Verified field-by-field: `OrgOut`→`Org`, `OrgMemberOut`→`OrgMember`,
`OrgQuotasOut`→`OrgQuotas`, `TransferOut`→`OriginalCreatorTransfer`, `ProjectOut`→`Project`,
`ProjectMemberOut`→`ProjectMember`, and `app__api__v1__invites__InviteOut`→`Invite` are all
directly assignable (enums match; `ProjectOut`/`ProjectMemberOut` omit the *optional*
`owner_name`/`is_inherited`, which keeps assignability). If `pnpm typecheck` surfaces any
gap, add a minimal `to<Type>` bridge at that site and record it as a deviation — but the
analysis predicts zero.

### 5C. Consumer sweep (drop `.data`) — 28 sites, ~13 files

`pnpm typecheck` lists every `.data`-on-bare-body site. Known set from the sweep:
- **Cross-slice** (via `@slices/tenancy`): `app/components/OrgProjectSwitcher.vue` (orgs.list, projects.list), `identity/views/DeleteAccountView.vue` (orgs.list destructure), `agents/composables/useProjectBreadcrumbs.ts` (`(await projectsApi.get).data`), `agents/views/McpEgressAllowlistView.vue` (projects.listMembers), `conversation/views/ChatroomSettingsView.vue` + `conversation/composables/useObservations.ts` (projects.listMembers), `workflow/composables/useProjectRole.ts` (projects.listMembers).
- **In-slice**: `OrgListView`, `OrgDetailView` (incl. `const { data } = await orgsApi.rename` → `setQueryData`), `OrgMembersView`, `OrgTransferView`, `ProjectListView`, `ProjectDetailView`, `ProjectMembersView`, `ProjectMembersView`, `tenancy/composables/useProjectRole.ts`, `InboxInvitesView`.
- Patterns: `.then(r => r.data)` → drop `.then`; `(await x).data` → `await x`; `const { data } = await x` → `const data = await x` (rename local as needed for `setQueryData`).
- **Await-only (no change):** all mutations — create/remove/restore/setRole/removeMember/invite/initiateTransfer/accept/cancel/reject transfers, invites accept/reject/acceptByToken — and the callbacks handed to `useMemberActions`/`useEntityLifecycle` (typed `Promise<unknown>`).

### 5D. `projectsApi.list` scope/id guard

The hand-rolled `list` merges `{ scope, id }` into the query **only when both are set**
(`projects.ts:28`); the generated method takes `scope?`/`id?` as independent options and
would send `scope` without `id`. Preserve the invariant in the wrapper:
`ProjectsService.listProjectsApiProjectsGet({ ...(scope && id ? { scope, id } : {}), limit: params?.limit, offset: params?.offset })` — under `exactOptionalPropertyTypes`, spread the conditional object rather than passing explicit `undefined`.

### 5E. Test updates

One module-mock test returns the `{ data }` `AxiosResponse` envelope and must return the
bare body: `conversation/__tests__/useObservations.test.ts:44`
(`projectsApi: { listMembers: vi.fn(async () => ({ data: [] })) }` → `async () => []`). All
other consumer tests use `renderView` + MSW HTTP-layer stubs (not api-object mocks), so
they are unaffected. There is no `tenancy/api/__tests__`; add
`tenancy/api/__tests__/index.spec.ts` — request-level MSW characterization across the three
modules (verb/path/query/body), the two `If-Match` renames, the `projectsApi.list`
scope/id guard, the invite bodies, and the OC-transfer flow.

## 6. Security Considerations

This is the multi-tenant AuthZ boundary (`check-security` tenant-isolation + user-input
dimensions):
- **AuthZ unchanged.** All org/project membership and role checks are server-side; the
  client calls identical endpoints with identical path/query params. No authorization
  decision moves client-side.
- **Invite / role / transfer bodies byte-identical.** `invite` sends `{ email, role }`,
  `setRole` sends `{ role }`, `initiateTransfer` sends `{ target_user_id }` — unchanged. The
  org invite `role` widens to `string` in the generated `InviteCreateIn` but the wrapper
  still passes the `'owner'|'member'` value (union ⊆ string) — no privilege widening.
- **`If-Match` preserved** on `rename` (orgs + projects) — optimistic-concurrency guard
  against lost updates on the org/project row.
- **No secret logging, no new attack surface.** No `console.*` added; path params
  (orgId/projectId/userId/transferId/inviteId) flow through the generated client's encoder;
  no dynamic URL construction remains.

## 7. Migration Steps

1. Rewrite the three api modules over `OrgsService`/`ProjectsService`/`InvitesService`; drop
   the `http` import; keep the hand-rolled type exports and method names. Apply Q-5 (`restore`
   → `void`) and §5D (scope/id guard).
2. `pnpm typecheck` → sweep every reported `.data` site (§5C), in-slice and cross-slice,
   until green. Add a `to<Type>` bridge only if typecheck demands one (§5B; none expected).
3. `pnpm test` → update `useObservations.test.ts:44` to a bare body; all other tests pass
   unmodified.
4. Add `tenancy/api/__tests__/index.spec.ts` (§5E).
5. `pnpm lint` (changed files) + `pnpm build`. No `gen:api`.

## 8. Risks and Rollback

- **Volume / cross-slice sweep.** 28 `.data` sites in ~13 files, 6 of them outside the
  tenancy slice. Mitigated: `pnpm typecheck` is exhaustive — a missed `.data` or a bad
  method name cannot ship. The keys increment validated this driver at similar scale.
- **Invite-model asymmetry (Q-4).** The three invite endpoints resolve to three different
  shapes; a shared bridge would be wrong. Mitigated: the org/project invite mutations are
  await-only (no consumer reads their return), and only the `invites`-scoped model backs the
  `Invite` type — pinned by the characterization spec.
- **`restore` void narrowing (Q-5).** If a future caller expects the restored entity back,
  `void` would surface it at typecheck (safer than the old optimistic `<Org>`). No current
  caller reads it.
- **`projectsApi.list` scope/id guard (§5D).** Sending `scope` without `id` would change the
  query; the spread guard preserves the both-or-neither behavior — pinned by a spec case.
- Rollback is `git revert` of the implementation commit; the modules are self-contained.

## 9. Acceptance Criteria

- [ ] AC-1: every method in the three tenancy api modules calls an `OrgsService`/
      `ProjectsService`/`InvitesService` method; no `@shared/transport` `http` import remains
      in `tenancy/api/*`; each method resolves the bare body typed as its slice type (or
      `void` for `restore`, Q-5).
- [ ] AC-2: every `.data` site (in-slice and the 6 cross-slice files) is converted; `pnpm
      typecheck` and `pnpm lint` (changed files) are green with no other consumer edits.
- [ ] AC-3: the invite endpoints map per Q-4 — the characterization spec asserts
      `orgsApi.invite`/`projectsApi.invite` post `{ email, role }` to the org/project routes
      and `invitesApi.list`/`acceptByToken` resolve the `invites`-scoped invite body.
- [ ] AC-4: request bodies/params/preconditions unchanged — the spec asserts verb/path/body
      for a representative read/write per module, including both `If-Match` renames, the
      `initiateTransfer` `{ target_user_id }` body, and the `projectsApi.list` scope/id guard
      (both-set sends the params, either-unset omits both).
- [ ] AC-5: `pnpm test` green — `useObservations.test.ts` updated to a bare body, all other
      tests pass unmodified, the new characterization spec passes; `pnpm build` green.
- [ ] AC-6: security holds — no response carries a new field or secret; invite/role/transfer
      bodies and `If-Match` are byte-identical; AuthZ path unchanged (§6). `check-security`
      lens: no findings.

## 10. SRS Delta

None — behavior-preserving refactor of the api-client layer.

## 11. Deviation Log

Appended by /build.

## 12. Follow-ups

- FU-1: merge the two `useProjectRole` composables (`tenancy/` and `workflow/`) — near-
  identical, both call `projectsApi.listMembers`; a shared one belongs in the tenancy slice's
  public surface.
- FU-2: remaining slice wraps (`identity`, `admin`, `prompt-studio`) — the last `http`-based
  api layers after this increment.
- FU-3: consider adding `owner_name` to the backend `ProjectOut` (and `is_inherited` to
  `ProjectMemberOut`) if any consumer relies on them — today they are optional in the
  hand-rolled type and simply pass through when the backend sends them.
