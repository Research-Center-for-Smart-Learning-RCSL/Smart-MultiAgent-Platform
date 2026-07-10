---
type: refactor
status: implemented
created: 2026-07-10
requirements: [R24.13, R7.01, R7.03, R7.05]
supersedes:
---

# Wrap the `keys` slice's api layer over the generated client

## 1. Summary

Next increment of the [R24.13] slice-wrap program (after `agent-groups` and `conversation`):
convert the `keys` slice's four api modules —
`frontend/src/slices/keys/api/{keys,key-groups,search-keys,project-keys}.ts` — to call the
generated `@shared/api-client` services instead of the bare `@shared/transport` `http`
singleton. Unlike `conversation`, these modules return the **raw `AxiosResponse`** (e.g.
`list: () => http.get<ApiKey[]>('/keys')`), so this is the **agent-groups pattern**: the
return shape changes to the bare body and the nine `.data`-unwrap call sites must drop
`.data`. The backend response-enum sweep already narrowed the generated provider/status
unions to match this slice's hand-rolled ones exactly, so no per-slice backend work remains.

The slice keeps its **hand-rolled domain types** (`ApiKey`, `KeyGroup`, `KeyGroupDetail`,
`Rotation`, `Limits`, `SearchKey`, `KeyUsage`, and the `CAPABILITIES` table) as the api's
public types; a single response bridge (`toKeyGroup`) supplies the defaults for two fields
the contract marks optional. This is a provider-keys surface, so it is wiring-only by
construction: request bodies (including the secret on upload) and responses (masked, never a
secret) are byte-identical — only the transport call path changes.

## 2. Motivation

- **[R24.13] convergence.** One instrumented axios singleton (`shared/transport/axios.ts`)
  should own auth (bearer + silent 401 refresh) and problem+json error typing for every
  slice; the keys api should wrap the generated services rather than re-encode request/
  response shapes by hand. `agent-groups` and `conversation` are done.
- **Drift risk on a security-critical surface.** Hand-typed key/provider shapes rot silently
  when the contract moves; wrapping the generated services makes `pnpm run gen:api` the
  single source of truth, guarded by `check:openapi-drift`.

## 3. Non-goals

- **No behavior change on the wire.** Same endpoints, same verbs, same request bodies (the
  upload `secret`, the carry `key_id`, the member patch), same masked responses. No secret is
  newly exposed, logged, or reshaped.
- **No slice-type rebase.** The hand-rolled types stay (see Q-2). The `CAPABILITIES` record
  keyed by `ApiKeyProvider`, the `MemberPatch = Partial<Rotation & Limits & {priority}>`
  derivation, and the `KeyGroupDetail` nesting are consumed slice-wide and by tests.
- **No composable/query re-architecture.** `useMyKeys`, `useKeyGroups`, `useProjectKeys`,
  `useSearchKeys`, `useKeyProjects`, and `keysKeys` keep their shape; only the `.data`
  unwrap at each query/mutation site is removed.
- **No `gen:api` rerun.** This is a frontend-only edit; the contract is unchanged.

## 4. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | (settled by the program) enum widening? | Backend enum sweep first, then wrap. | Done — `ApiKeyProvider`/`ProbeStatus`/`SearchProvider` already match the slice unions literal-for-literal. |
| Q-2 | Keep the hand-rolled slice types, or re-export the generated models as aliases? | Keep hand-rolled; bridge the one divergence (`toKeyGroup`). | The types back the `CAPABILITIES` table and `MemberPatch`, and `GroupOut` is not directly assignable anyway (optional `member_count`/`providers` — §5B), so a mapping is required regardless. Minimal ripple. |
| Q-3 | The nine consumers read `.data`; how to convert? | Return bare bodies from the api, drop `.data` at each site (composables + two views), and update the two module-mock tests to return bare bodies. | The agent-groups precedent (settled playbook Q-3: unwrap + edit call sites). |

## 5. Current vs Target Structure

### 5A. Method → generated-service map (all four modules keep their object shape)

**`keys.ts` — `keysApi`** (`KeysService`):
`list` → `listMyKeysApiKeysGet({})` `KeyListOut[]`→`ApiKey[]` (direct) ·
`get` → `getMyKeyApiKeysKeyIdGet({keyId})` `KeyOut`→`ApiKey` ·
`upload` → `uploadKeyApiKeysPost({requestBody:{provider,name,secret}})` `KeyOut`→`ApiKey` ·
`retest` → `retestKeyApiKeysKeyIdRetestPost({keyId})` `KeyOut`→`ApiKey` ·
`remove` → `deleteKeyApiKeysKeyIdDelete({keyId})` void ·
`projects` → `listKeyProjectsApiKeysKeyIdProjectsGet({keyId})` `KeyProjectOut[]`→`KeyProject[]`.

**`key-groups.ts` — `keyGroupsApi`** (`KeyGroupsService`):
`listForProject` → `listGroupsApiProjectsProjectIdKeyGroupsGet({projectId})` `GroupOut[]` → map `toKeyGroup` ·
`create` → `createGroupApiProjectsProjectIdKeyGroupsPost({projectId,requestBody:{name}})` `GroupOut` → `toKeyGroup` ·
`get` → `readGroupApiKeyGroupsGroupIdGet({groupId})` `GroupDetailOut` → `{group: toKeyGroup(d.group), members: d.members}` ·
`rename` → `renameGroupApiKeyGroupsGroupIdPatch({groupId,requestBody:{name}})` void ·
`remove` → `deleteGroupApiKeyGroupsGroupIdDelete({groupId})` void ·
`addMember` → `addMemberApiKeyGroupsGroupIdKeysPost({groupId,requestBody:{key_id}})` `MemberOut`→`KeyGroupMember` (direct) ·
`patchMember` → `patchMemberApiKeyGroupsGroupIdKeysKeyIdPatch({groupId,keyId,requestBody:patch})` void ·
`removeMember` → `removeMemberApiKeyGroupsGroupIdKeysKeyIdDelete({groupId,keyId})` void ·
`reorder` → `reorderMembersApiKeyGroupsGroupIdReorderPost({groupId,requestBody:{priorities}})` void.

**`search-keys.ts` — `searchKeysApi`** (`SearchKeysService`):
`list` → `listSearchKeysApiProjectsProjectIdSearchKeysGet({projectId})` `SearchKeyOut[]`→`SearchKey[]` (direct) ·
`upload` → `uploadSearchKeyApiProjectsProjectIdSearchKeysPost({projectId,requestBody:{provider,secret,config}})` `SearchKeyOut`→`SearchKey` ·
`retest` → `retestSearchKeyApiProjectsProjectIdSearchKeysKeyIdRetestPost({projectId,keyId:id})` `SearchKeyOut`→`SearchKey` ·
`activate` → `activateSearchKeyApiProjectsProjectIdSearchKeysKeyIdActivatePost({projectId,keyId:id})` void ·
`remove` → `deleteSearchKeyApiProjectsProjectIdSearchKeysKeyIdDelete({projectId,keyId:id})` void.

**`project-keys.ts` — `projectKeysApi`** (`KeysService`):
`listCarried` → `listCarriedKeysApiProjectsProjectIdKeysGet({projectId})` `KeyOut[]`→`ApiKey[]` (direct) ·
`carry` → `carryKeyApiProjectsProjectIdKeysPost({projectId,requestBody:{key_id}})` void ·
`withdraw` → `withdrawKeyApiProjectsProjectIdKeysKeyIdDelete({projectId,keyId})` void ·
`usage` → `readUsageApiProjectsProjectIdKeysKeyIdUsageGet({projectId,keyId,window})` `UsageOut`→`KeyUsage` (direct).

### 5B. The one response bridge — `toKeyGroup`

`GroupOut.member_count` and `GroupOut.providers` are **optional** in the contract (the pydantic
model gives them defaults) but the slice's `KeyGroup` requires them non-optional (consumers
render `group.member_count` and flag agents off `group.providers`). Under
`exactOptionalPropertyTypes`, `GroupOut` is therefore not assignable to `KeyGroup`. Bridge in
`key-groups.ts`:

```ts
function toKeyGroup(g: GroupOut): KeyGroup {
  return { id: g.id, project_id: g.project_id, name: g.name, created_at: g.created_at,
           member_count: g.member_count ?? 0, providers: g.providers ?? [] }
}
```

`g.providers` is `ApiKeyProvider[] | undefined`; `?? []` yields `ApiKeyProvider[]`, assignable
to the slice's `providers: string[]`. The backend always populates both, so the defaults are
never actually hit — they satisfy the type, not a real null case.

### 5C. Verified type-compatibility matrix (generated → slice)

Enums match literal-for-literal: `ApiKeyProvider` (`claude|openai|gemini|voyage|cohere`),
`ProbeStatus` (`ok|failed|untested` = slice `TestStatus`), `SearchProvider`
(`brave|serper|tavily|google_cse`). Directly assignable: `KeyOut`/`KeyListOut`→`ApiKey`
(slice `project_count?` optional, so both the with- and without-count models fit),
`KeyProjectOut`→`KeyProject`, `UsageOut`→`KeyUsage`, `SearchKeyOut`→`SearchKey`
(`config: Record<string,any>`→`Record<string,unknown>` ok), `MemberOut`→`KeyGroupMember`
(`RotationOut`/`LimitsOut` match `Rotation`/`Limits` field-for-field). Bridged: `GroupOut`
and `GroupDetailOut.group` via `toKeyGroup`. Request bodies all assignable:
`KeyUploadIn`, `SearchKeyIn`, `GroupIn`, `GroupPatchIn`, `AddMemberIn`, `CarryIn`,
`ReorderIn`, and `MemberPatchIn` (slice `MemberPatch`'s optional `T` fits the generated
optional `T | null`).

### 5D. Call-site edits (drop `.data`)

Composables: `useMyKeys` (list `.then(r=>r.data)`; `const {data: created} = await upload`),
`useKeyGroups` (list; get), `useProjectKeys` (listCarried), `useSearchKeys` (list),
`useKeyProjects` (projects). Views: `KeyDetailView.vue:46` (`(await keysApi.get(id)).data`),
`components/UsageDashboard.vue:39` (`const {data} = await projectKeysApi.usage(...)`). Each
becomes the bare call / bare `await`. No signature of the composables' returned refs changes.

### 5E. Test updates

Two tests module-mock the api and return `{ data: ... }` (the old `AxiosResponse` shape) —
they must return bare bodies: `KeyGroupDetailView.test.ts` (`mockGet` group/members, the
`addMember`/`removeMember`/`patchMember`/`reorder` stubs, `mockListCarried`) and
`SearchKeyView.test.ts` (`list` array, the mutation stubs). The MSW-based `KeyDetailView.test.ts`
and the smoke `KeyListView.test.ts` / `ProjectKeysView.test.ts` pass unmodified.

## 6. Security Considerations

This touches the provider-keys surface, so per the `check-security` lens:
- **No secret in any response.** Every `*Out` model exposes `masked_preview` only; the wrap
  changes the transport, not the payload. Verified against `KeyOut`/`SearchKeyOut` (no secret
  field).
- **Upload request body byte-identical.** `uploadKeyApiKeysPost` sends `{provider,name,secret}`
  exactly as the current `http.post('/keys', {provider,name,secret})` does; the generated
  client posts `application/json` through the same instrumented axios, so no new logging or
  interception path handles the secret.
- **AuthZ unchanged.** All authorization is server-side; the client calls the identical
  endpoints. No tenant-scoping logic lives in the api layer.
- **No new attack surface.** No `eval`, no dynamic URL construction beyond path params the
  generated client encodes.

## 7. Migration Steps

1. Rewrite `keys.ts` and `project-keys.ts` over `KeysService`; drop the `http` import; keep
   all type exports.
2. Rewrite `key-groups.ts` over `KeyGroupsService`; add `toKeyGroup`; map list/create/get.
3. Rewrite `search-keys.ts` over `SearchKeysService`.
4. Drop `.data` at the nine call sites (§5D).
5. Update the two module-mock tests to bare bodies (§5E).
6. Add `keys/api/__tests__/index.spec.ts` — request-level MSW characterization across all four
   api objects (verb/path/body, and the `toKeyGroup` defaulting on a `GroupOut` missing
   `member_count`/`providers`).
7. `pnpm typecheck` + `pnpm lint` (changed files) + `pnpm test` + `pnpm build`. No `gen:api`.

## 8. Risks and Rollback

- **`toKeyGroup` masks a real absence.** If the backend ever legitimately omits `providers`,
  the UI sees `[]` (no serviceable providers) rather than crashing — the same graceful state
  it shows for a group with no keys, so the default is safe. Covered by a characterization
  test feeding a `GroupOut` without the fields.
- **A missed `.data` site.** `pnpm typecheck` catches any remaining `.data` on a now-bare body
  (property does not exist), so this cannot ship silently.
- **Mock-shape drift in tests.** The two updated mocks are the only ones returning the old
  envelope; grep confirms no other keys test reads `.data` off the api.
- Rollback is `git revert` of the single implementation commit.

## 9. Acceptance Criteria

- [x] AC-1: all four api objects call `@shared/api-client` services; no `@shared/transport`
      import remains under `keys/api/`; each method resolves the bare body typed as its slice
      type. Commit b1c8142.
- [x] AC-2: every `.data` site is converted — the nine in-slice **plus nine cross-slice**
      consumers the §5D scope missed (see D-1); `pnpm typecheck` green, changed TS files
      lint-clean (repo-wide `--max-warnings 0` red only on pre-existing `.vue` `html-indent`
      debt — D-2).
- [x] AC-3: `toKeyGroup` supplies `member_count`/`providers` defaults; `listForProject`,
      `create`, `get` return fully-populated `KeyGroup`/`KeyGroupDetail`; pinned by the
      `groupOutBare` (no member_count/providers) test asserting `member_count: 0, providers: []`.
- [x] AC-4: request bodies unchanged — the spec asserts exact verb/path/body for `upload`
      (`{provider,name,secret}`), search `upload` (`{provider,secret,config}`), `carry`,
      `addMember`, `patchMember`, `reorder`, and the `usage` `window` query.
- [x] AC-5: `pnpm test` green — 479 pass (new 24-case spec included); the two updated
      module-mock tests pass, MSW/smoke tests pass unmodified. `pnpm build` green.
- [x] AC-6: security holds — `KeyOut`/`SearchKeyOut` expose `masked_preview` only (no secret
      field); the upload body is byte-identical (asserted by AC-4 tests); no masking/logging
      path changed; AuthZ is server-side (§6). `check-security` lens: no findings.

## 10. SRS Delta

None — behavior-preserving refactor of the api-client layer.

## 11. Deviation Log

- D-1: **Nine cross-slice `.data` consumers the scope missed.** §5D listed only the nine
  in-slice sites; the keys api is also consumed via the `@slices/keys` barrel by the `agents`
  slice (`ConceptMapPanel.vue`, `AgentDetailView.vue`, `AgentListView.vue`,
  `GraphragConfigListView.vue`, `KnowledgeMapConfigDetailView.vue`,
  `KnowledgeMapConfigListView.vue`, `RagConfigDetailView.vue`, `RagConfigListView.vue`) and
  `prompt-studio` (`useConfigEditor.ts`), each unwrapping `.data` off
  `keyGroupsApi.listForProject` / `projectKeysApi.listCarried` / `keysApi.list`. `pnpm
  typecheck` surfaced all nine (`Property 'data' does not exist on …`); each got the same
  mechanical `.data` drop. `agentsApi.*` `.data` unwraps were correctly left intact (that
  slice is a separate future wrap). No design change — just a wider mechanical footprint (18
  sites, not 9). The lesson: grep the whole repo for a slice's public api usage, not just the
  owning slice, before scoping a wrap.
- D-2: **`pnpm lint` red on pre-existing `.vue` debt.** The repo-wide `--max-warnings 0` gate
  fails on 257 pre-existing `vue/html-indent` warnings in touched-but-unrelated template
  regions (confirmed present on `HEAD`). `eslint` over the changed TS files (api, composables,
  spec) emits zero warnings; the one-line `queryFn` edits in the `.vue` files did not touch
  template indentation. Not fixed here (out of scope).
- D-3: **Two module-mock tests updated (in scope, per §5E).** `KeyGroupDetailView.test.ts` and
  `SearchKeyView.test.ts` returned the old `{ data }` `AxiosResponse` envelope from their api
  mocks; switched to bare bodies so they match the wrapped api's return shape.

## 12. Follow-ups

- FU-1: if a later pass tightens `GroupOut.member_count`/`providers` to required in the
  backend response model, delete the `toKeyGroup` bridge and return the generated type
  directly.
- FU-2: remaining slice wraps (`tenancy`, `identity`, `workflow`, `agents`, `admin`,
  `prompt-studio`), each unblocked by the enum sweep.
