---
type: refactor
status: approved
created: 2026-07-10
requirements: [R24.13]
supersedes:
---

# Wrap the generated api-client, slice by slice — conversion playbook + agent-groups (FU-1 increment 1)

## 1. Summary

The [R24.13] gap that the OpenAPI-resync pilot
(`docs/tasks/2026-07-10-openapi-resync-generated-client-auth/spec.md`) closed for the
`notifications` slice is still open for the other nine slices with `api/` folders: they
hand-roll response types and call `@shared/transport`'s `http` directly instead of
wrapping the generated `@shared/api-client`. The pilot proved the pattern and wired the
auth/session behavior onto the bare `axios` singleton the generated client resolves to,
so conversions are now safe. A cross-slice analysis (six Explore agents, 2026-07-10)
found the slices are **not** a uniform mechanical sweep — they differ in return-shape
convention, enum-widening exposure, cross-service reach, and even missing generated
operations — so this program is sequenced one slice at a time, cleanest first, behind a
shared playbook. This dossier (a) writes that reusable **Conversion Playbook** and (b)
converts `agent-groups`, the smallest field-identical slice, as increment 1.

## 2. Motivation

- **[R24.13] violation, nine slices wide.** `REQUIREMENTS.md:1788`: "Slice `api/`
  folders wrap [the generated client] into use-case-shaped calls." `agent-groups`
  violates it directly: `frontend/src/slices/agent-groups/api/index.ts:1` imports `http`
  from `@shared/transport` and every method (`api/index.ts:26-48`) calls
  `http.get/post/patch/put/delete` against a hand-rolled interface
  (`AgentGroup`/`AgentGroupMembers`/`ConceptMapStatus`, `api/index.ts:7-23`) that
  duplicates the generated `AgentGroupOut`/`AgentGroupMembersOut`/
  `app__api__v1__agent_groups__ConceptMapStatusOut` models — two sources of truth for
  one schema, silently divergible. The generated `AgentGroupsService` covering all nine
  endpoints already exists (`frontend/src/shared/api-client/services/AgentGroupsService.ts:15-247`).
- **Duplicated-model debt (check-quality: single-source-of-truth).** The hand-rolled
  `AgentGroup` (`api/index.ts:7-13`) is a byte-for-byte remirror of the backend
  `AgentGroupOut` (`backend/app/api/v1/agent_groups.py:36-44`); the same schema is
  authored three times (backend Pydantic, generated model, hand-rolled interface).
- **The auth blocker is already gone.** The generated client's request pipeline now
  carries bearer-token injection, silent 401-refresh, `problem+json` typed errors, and
  `WITH_CREDENTIALS` because the pilot registered the same interceptors on the bare
  `axios` singleton and set `OpenAPI.WITH_CREDENTIALS = true`
  (`frontend/src/shared/transport/axios.ts:223-236`). This is the premise that makes the
  wrap safe; without it a converted call would send unauthenticated
  (pilot §2 / D-summary).

## 3. Non-goals

- **No externally observable behavior change.** `agentGroupsApi.list`/`.get`/`.create`/
  `.rename`/`.remove`/`.listMembers`/`.addMember`/`.removeMember`/`.setConceptMapEnabled`
  must drive identical wire requests and yield identical rendered results. The one
  intentional structural change — methods return the response **body** instead of the
  raw `AxiosResponse` — is invisible past the four call sites that are updated in
  lockstep (§5).
- **Only `agent-groups` is converted here.** The other eight slices
  (`conversation`, `admin`, `keys`, `tenancy`, `identity`, `workflow`, `agents`,
  `prompt-studio`) are out of scope — see Follow-ups for the sequenced order and each
  slice's known complications.
- **No backend change in this increment.** The enum-widening policy (Q-2) requires
  backend edits only where a generated `*Out` widens a literal union to `string`;
  `agent-groups` has **no** such field (`AgentGroupOut` is
  `{concept_map_enabled: boolean; created_at, id, name, project_id: string}`,
  `frontend/src/shared/api-client/models/AgentGroupOut.ts:5-11`), so no backend edit is
  in scope now.
- **No `WsManager` / WebSocket work.** Real-time surfaces go through the `WsManager`
  singleton per `REQUIREMENTS.md:1789` ([R24.14]), never `http`, so they are outside
  the api-layer wrap by definition.
- **No idempotency wiring.** No slice sets a per-call `X-Idempotent` header today (grep:
  it appears only in `frontend/src/shared/transport/axios.ts` and its test), and the
  generated client cannot express one anyway (playbook P6). Nothing to preserve here.
- **Not changing the generated client, its codegen config, or `check:openapi-drift`.**

## 4. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Scope this dossier to the whole eight-slice conversion, or a smaller increment? | Playbook + `agent-groups` only; the other seven become sequenced follow-up dossiers, cleanest first. | User pick. The analysis shows each slice needs its own characterization tests and type decisions; one mega-build spanning enum breaks, cross-service calls, orphaned endpoints, and 100+ call-site edits would be unreviewable. Prove the pattern on the smallest field-identical slice first. |
| Q-2 | When a generated `*Out` widens a hand-rolled literal union (e.g. `role`/`provider`/`state`/`status`) to bare `string`, how to preserve call-site type safety? | Fix the backend OpenAPI to emit real enums (Pydantic `Literal`/`Enum`), then `pnpm run gen:api`, then re-export the generated model. | User pick. Removes the duplication at the true source rather than papering over it with local casts; the codegen then carries the narrowing for every slice. Applied per-slice as widened fields appear — **N/A for `agent-groups`** (no widened field). |
| Q-3 | For slices whose api methods currently return the raw `AxiosResponse<T>`, how to handle the return shape? | Return the unwrapped body and edit call sites to drop `.data`. | User pick. Matches the `notifications` pilot (`notifications/api/index.ts:19` returns `Promise<Notification[]>`) and [R24.13]'s "use-case-shaped calls" intent; retires the `AxiosResponse` leak rather than re-wrapping to hide it. |

## 5. Current vs Target Structure

### 5A. The Conversion Playbook (reusable across all follow-up slices)

This section is the durable deliverable; each follow-up slice dossier references it by
`path:line`. It codifies the constraints established by the generated-client-conventions
analysis (cited inline).

- **P1 — Wrap shape.** Import `<Domain>Service` and the `*Out`/`*In` models from
  `@shared/api-client`; re-export the models under the slice-local names call sites
  already use (`export type AgentGroup = AgentGroupOut`); rewrite each method as a thin
  wrapper calling `Service.method({ ...pathParams, ...query, requestBody })`. Reference:
  `notifications/api/index.ts:1-32`.
- **P2 — Options object.** Every generated method takes a **single** options object
  (`--useOptions`, `frontend/package.json:14`); path/query/header/body are keys on it.
  Methods with only optional params still require passing `{}` (the arg itself is not
  optional). Reference: `frontend/src/shared/api-client/services/AgentGroupsService.ts:48-52`.
- **P3 — Return shape (Q-3).** Generated methods resolve to the **body** `T`
  (`frontend/src/shared/api-client/core/request.ts:317` `resolve(result.body)`), not
  `AxiosResponse<T>`. For a slice whose api layer currently returns the raw response,
  every `.data` call site must be updated in the same commit. Slices that already unwrap
  (`notifications`, `conversation`, `admin`, `workflow`) are signature-compatible
  drop-ins.
- **P4 — Query params.** `request.ts` drops `undefined` and `null` query values but
  **keeps empty string** (serialized `key=`, `request.ts:16-18` / `:65-79`) — pass
  `undefined`, never `''`, for "absent" (the `notifications` D-3 lesson). List methods
  default `limit = 100` and emit it explicitly; confirm the backend `PaginationParams`
  default matches (`backend/app/api/v1/deps.py:18` = 100) so the added `limit=100` is
  inert.
- **P5 — Enum widening (Q-2).** Where a generated `*Out` types a field as bare `string`
  that the hand-rolled type narrowed to a literal union, amend the backend response model
  to a `Literal`/`Enum`, regenerate, then re-export the now-narrowed generated model. Do
  not cast around it in the wrapper.
- **P6 — Headers / idempotency.** The generated method exposes a header field only for
  header params **declared in the OpenAPI spec** (e.g. `If-Match` → `ifMatch`,
  `AgentGroupsService.ts:70-89` has none; `OrgsService.ts:116-140` shows the `ifMatch`
  precedent). There is **no** generic per-call header passthrough, so a per-call
  `X-Idempotent` sentinel cannot be sent through a generated method. Any endpoint that
  needs idempotency stays on `http` (with `{ headers: { 'X-Idempotent': 'true' } }`)
  until the backend declares the header param — **none exists today.**
- **P7 — Errors.** Because the response interceptor runs on the bare singleton first and
  throws `@shared/errors` typed errors that carry no `.response`, the generated
  `request.ts` re-throws them unchanged — converted calls get the **same**
  `AuthError`/`NetworkError`/`RateLimitError`/`ValidationError` + silent-refresh as
  `http` (`axios.ts:140-203`, `request.ts:226-232`). **Sole divergence:** a non-2xx whose
  body is not valid `problem+json` reaches `request.ts`'s own `core/ApiError` instead of
  `@shared/errors`'s `ApiError` (`request.ts:252-283`). A slice with such an endpoint
  must normalize it; `agent-groups` endpoints all return `problem+json`, so N/A.
- **P8 — Cancellation.** The generated client cancels only via
  `CancelablePromise.cancel()`; there is no `AbortSignal` option
  (`frontend/src/shared/api-client/core/ApiRequestOptions.ts:5-17`). A wrapper feeding a
  TanStack Query `queryFn` loses signal-based cancellation unless bridged. `agent-groups`
  uses no cancellation, so N/A.
- **P9 — Characterization-first.** No existing slice test asserts request bodies or query
  params, so before converting a slice, add request-level characterization tests
  (verb/path/body/query via MSW request capture) that pass against the current code; keep
  the existing MSW view tests passing unmodified.

### 5B. `agent-groups` before → after

**Before:**
- `frontend/src/slices/agent-groups/api/index.ts:1` imports `http`; nine methods
  (`api/index.ts:26-48`) return `http.<verb><T>(...)` = `Promise<AxiosResponse<T>>`
  against hand-rolled `AgentGroup`/`AgentGroupMembers`/`ConceptMapStatus`
  (`api/index.ts:7-23`).
- Four call sites unwrap `.data`: `views/AgentGroupListView.vue:49`
  (`(await agentGroupsApi.list(projectId)).data`), `:67`
  (`(await agentGroupsApi.create(projectId, values)).data`),
  `views/AgentGroupDetailView.vue:37` (`(await agentGroupsApi.get(groupId)).data`), `:53`
  (`(await agentGroupsApi.listMembers(groupId)).data.members`). The other five calls
  (`rename` `List:100`, `remove` `List:120`, `addMember` `Detail:76`, `removeMember`
  `Detail:85`, `setConceptMapEnabled` `Detail:103`) are fire-and-forget mutations whose
  results are unused.
- Barrel re-exports the type: `agent-groups/index.ts:6` `export type { AgentGroup }`.
  No cross-slice importer of `AgentGroup` exists today (grep of `slices/**`), but the
  export name is preserved for compatibility.

**After:**
- `api/index.ts` imports `AgentGroupsService` and the generated models from
  `@shared/api-client`; re-exports `AgentGroup = AgentGroupOut`,
  `AgentGroupMembers = AgentGroupMembersOut`,
  `ConceptMapStatus = app__api__v1__agent_groups__ConceptMapStatusOut` (the namespaced
  name is hidden behind the local alias). No `@shared/transport` import remains.
- Each method calls its generated counterpart and returns the body:
  `list` → `listGroupsApiProjectsProjectIdAgentGroupsGet({ projectId })`;
  `create` → `createGroupApiProjectsProjectIdAgentGroupsPost({ projectId, requestBody: payload })`;
  `get` → `getGroupApiAgentGroupsGroupIdGet({ groupId })`;
  `rename` → `renameGroupApiAgentGroupsGroupIdPatch({ groupId, requestBody: payload })`;
  `remove` → `deleteGroupApiAgentGroupsGroupIdDelete({ groupId })`;
  `listMembers` → `listMembersApiAgentGroupsGroupIdMembersGet({ groupId })`;
  `addMember` → `addMemberApiAgentGroupsGroupIdMembersPost({ groupId, requestBody: { agent_id: agentId } })`;
  `removeMember` → `removeMemberApiAgentGroupsGroupIdMembersAgentIdDelete({ groupId, agentId })`;
  `setConceptMapEnabled` → `setConceptMapEnabledApiAgentGroupsGroupIdConceptMapEnabledPut({ groupId, requestBody: { enabled } })`.
  (`create`/`rename` keep accepting the zod-inferred `AgentGroupCreateInput`/
  `AgentGroupUpdateInput` from `../types/schemas`, structurally `{ name }`, matching
  `AgentGroupCreateIn`/`AgentGroupUpdateIn`.)
- The four `.data` call sites drop `.data` (and `Detail:53` becomes
  `(await agentGroupsApi.listMembers(groupId)).members`).

**Model parity (source-of-truth verified, all field-identical — no drift):**
`AgentGroupOut` ≡ local `AgentGroup` (`AgentGroupOut.ts:5-11` vs `api/index.ts:7-13`);
`AgentGroupMembersOut` ≡ `AgentGroupMembers` (`{ members: string[] }`);
`app__api__v1__agent_groups__ConceptMapStatusOut` ≡ `ConceptMapStatus`
(`{ concept_map_enabled: boolean; group_id: string }`); `ConceptMapEnabledIn` =
`{ enabled: boolean }`; `AgentGroupMemberIn` = `{ agent_id: string }`. Backend origin:
`backend/app/api/v1/agent_groups.py:36-72`.

**Dependency direction:** `slices/agent-groups/api` swaps a `slices → shared/transport`
edge for a `slices → shared/api-client` edge — both permitted (`slices → shared`). No new
cross-slice or upward edge; CLAUDE.md layer order preserved.

## 6. Characterization Test Plan

**Existing coverage to keep green (do not modify):**
`frontend/src/slices/agent-groups/__tests__/AgentGroupListView.test.ts` and
`AgentGroupDetailView.test.ts` are MSW-at-the-HTTP-layer view tests: they stub
`/api/...` paths returning JSON bodies (`AgentGroupListView.test.ts:40-44,90-92`;
`AgentGroupDetailView.test.ts:36-44`) and assert rendered text / control state. They do
**not** mock the `../api` module with `{ data }`-shaped values, so after the conversion
(call sites drop `.data`; generated client returns the body) they pass **unmodified** —
this is AC-1's evidence. Note both use real-`setTimeout` `settle()` helpers
(`AgentGroupListView.test.ts:47-50`, 120/160 ms) — pre-existing, untouched (FU-8).

**New — `frontend/src/slices/agent-groups/api/__tests__/index.spec.ts`** (the slice has
no api-layer test today). Written before conversion; must pass against the **current**
`http`-based code and continue passing unmodified after. Use the shared MSW server
(`frontend/tests/mocks/server.ts`) with `server.use()` per test, capturing each outbound
request and asserting the invariant wire facts (verb, path, JSON body, query) — return
shape is deliberately excluded because it is what changes (Q-3):

- `list(projectId)` → `GET /api/projects/{projectId}/agent-groups`. Assert path + verb.
  Pin the query: current code sends none; post-conversion sends `limit=100` (backend
  default is 100, `deps.py:18`, so inert). Assert the request reaches the same endpoint;
  record the `limit=100` addition as an accepted, inert wire delta (D-log if the
  implementer chooses to assert it exactly).
- `create(projectId, { name })` → `POST /api/projects/{projectId}/agent-groups`, body
  `{ name }`.
- `get(groupId)` → `GET /api/agent-groups/{groupId}`.
- `rename(groupId, { name })` → `PATCH /api/agent-groups/{groupId}`, body `{ name }`.
- `remove(groupId)` → `DELETE /api/agent-groups/{groupId}`.
- `listMembers(groupId)` → `GET /api/agent-groups/{groupId}/members`.
- `addMember(groupId, agentId)` → `POST /api/agent-groups/{groupId}/members`, body
  `{ agent_id: agentId }`.
- `removeMember(groupId, agentId)` → `DELETE /api/agent-groups/{groupId}/members/{agentId}`.
- `setConceptMapEnabled(groupId, enabled)` →
  `PUT /api/agent-groups/{groupId}/concept-map-enabled`, body `{ enabled }`.

These nine request-level assertions are the safety net that makes the conversion
mechanical: a wrong generated method name, a body-shape mistake, or a path typo fails
here regardless of what the view renders.

## 7. Migration Steps

Each step leaves `pnpm test` / `pnpm typecheck` / `pnpm lint` (scoped) green.

1. Write §5A — the Conversion Playbook — into this dossier's committed form (already
   here); no code.
2. Write `agent-groups/api/__tests__/index.spec.ts` (§6); confirm green against the
   current `http`-based `api/index.ts`.
3. Convert `agent-groups/api/index.ts` to wrap `AgentGroupsService`, re-exporting the
   generated models under the slice-local names. Drop the `@shared/transport` import.
4. Update the four `.data` call sites (`AgentGroupListView.vue:49,67`,
   `AgentGroupDetailView.vue:37,53`). Confirm the step-2 characterization tests pass
   **unmodified**, and the existing view tests pass **unmodified** (AC-1).
5. Run the full DoD gates (§9 AC-6).

## 8. Risks and Rollback

- **Lowest-risk slice by design.** Field-identical models, no enum widening, no
  cross-service reach, no orphaned endpoints, no idempotency, no cancellation — the only
  behavioral edge is the return-shape unwrap, fenced by exactly four call sites and the
  new characterization tests.
- **`limit=100` wire addition on `list`** is the sole request change; confirmed inert
  against the backend default (`deps.py:18`). If a future backend default diverged, the
  characterization test's query assertion would flag it.
- Rollback is `git revert` of the two commits (tests; conversion+call-sites). No
  migration, no contract change, no data.

## 9. Acceptance Criteria

- [ ] AC-1: no externally observable behavior change — `AgentGroupListView.test.ts` and
      `AgentGroupDetailView.test.ts` pass **unmodified** after the conversion.
- [ ] AC-2: the [R24.13] violation is gone for `agent-groups` — `api/index.ts` imports
      `AgentGroupsService` + generated models and no longer imports `http` from
      `@shared/transport` (verified at `agent-groups/api/index.ts:1`); the hand-rolled
      `AgentGroup`/`AgentGroupMembers`/`ConceptMapStatus` interfaces are replaced by
      re-exports of the generated models.
- [ ] AC-3: `agent-groups/api/__tests__/index.spec.ts` (9 methods, request-level
      assertions) passes against the pre-conversion code and continues passing unmodified
      after.
- [ ] AC-4: all four `.data` call sites are updated; a repo grep for
      `agentGroupsApi.*\)\.data` returns zero matches; `pnpm typecheck` is green.
- [ ] AC-5: the Conversion Playbook (§5A) is committed as the reference the follow-up
      slice dossiers cite.
- [ ] AC-6: mechanical gates pass — `pnpm test` (full suite), `pnpm typecheck`, `pnpm lint`
      (scoped to touched files, per pilot D-7 precedent), `pnpm build`.
- [ ] AC-7: no backend change required for `agent-groups` — stated N/A with evidence
      (no generated `*Out` field is widened from a literal union; `AgentGroupOut.ts:5-11`).

## 10. SRS Delta

None — this restores [R24.13] for `agent-groups`; it defines no new behavior.

## 11. Deviation Log

Appended by /build.

## 12. Follow-ups

Remaining slices, sequenced cleanest-first; each gets its own dossier citing §5A:

- FU-1: **`conversation`** (~30 methods) — already unwraps `.data`, so a true
  signature-compatible drop-in; only touch-ups are the `getChatroomPresence`
  `.user_ids` map (`conversation/api/index.ts:244-248`) and `releaseObservation`'s
  discriminated union relaxing to the flatter generated `ReleaseIn`. Multipart
  (`uploadSingleShot`) and `If-Match` are natively supported by the generated client.
- FU-2: **`admin`** (~25 methods) — already unwraps. Complications: `queryAudit`/
  `exportAudit` need snake_case→camelCase arg remapping (not a pass-through);
  `exportAudit` return type regresses to `Record<string,any>`; `restoreResource` needs
  the `'user'|'org'|'project'` enum; `resetGraphrag` lives in `GraphragAdminService`.
- FU-3: **`keys`** (5 files) — first enum-fix slice (Q-2/P5): `provider`/`test_status`
  widened to `string` in `KeyOut`/`KeyListOut`/`SearchKeyOut` break `CapabilityChip`
  and `CAPABILITIES` indexing; also `.data` unwrapping in the composables and the
  `project_count`/`member_count`/`providers` field-drift.
- FU-4: **`tenancy`** (3 files, ~30 methods) — enum widening plus **real field drift**:
  `ProjectOut` lacks `owner_name` (read at `ProjectListView.vue:106`,
  `ProjectDetailView.vue:90`) and `ProjectMemberOut` lacks `is_inherited` (read at
  `ProjectMembersView.vue:81`, `useMemberActions.ts:40`); 15 `.data` call sites;
  `restore` returns `void` vs typed. Needs the backend-field investigation in FU-9.
- FU-5: **`identity`** (~14 methods) — `auth.ts` returns raw `AxiosResponse`; six
  `{ data }` call sites (`session.ts:27,33,66`, `SessionsView.vue:77`,
  `RegisterView.vue:49`, `ProfileView.vue:37`). `login`/`refresh`/`logout` are safe on
  the instrumented singleton but `refresh` must not be redirected to transport-private
  `refreshHttp`; keep empty `{ requestBody: {} }` bodies.
- FU-6: **`workflow`** (~19 methods) — CRUD/runs mostly clean, but six orchestration
  methods degrade to `Record<string,any>` (erasing `Approval`/`Instruction`/etc.),
  `triggerRun`/`dryRun`/`cancelRun` lose their named `{run_id}`/`{status}` returns, and
  the two `*AgentWakeupConfig` methods need response transforms reaching into
  `AgentsService`.
- FU-7: **`agents`** (~40 methods) — largest and mixed-transport: ~30 convert, but the
  five MCP-binding methods (`/agents/{id}/mcp*`) and two builtin-tools methods
  (`/agents/{id}/builtin-tools`) have **no generated operation** and must stay on `http`;
  ~40 `.data` call sites. Blocked partly by FU-10.
- FU-8: **`prompt-studio`** (~13 methods) — heaviest structurally: each of eight methods
  fans out to a 3-way `me`/`org`/`admin` generated call; `getModelCatalog` is
  cross-service (`ModelCatalogService`); converting contradicts the "drift-check-only,
  not imported at runtime" note (`prompt-studio/types/index.ts:1-3`) — update that note.

Backend-spec gaps surfaced by the analysis (own dossiers, not blocking the above):

- FU-9: **Tenancy response-model fields.** Investigate why `owner_name`
  (`ProjectListView.vue:106`) and `is_inherited` (`ProjectMembersView.vue:81`) are read
  on the frontend but absent from `ProjectOut`/`ProjectMemberOut` in the OpenAPI spec —
  either the backend returns undeclared fields (schema bug) or the frontend reads fields
  that are always `undefined`.
- FU-10: **Agents' seven orphaned endpoints.** `/agents/{id}/mcp*` (5) and
  `/agents/{id}/builtin-tools` (2) have no generated operation — determine whether they
  are unregistered in the OpenAPI export or superseded by the unified `/tools` surface,
  and either register them (so FU-7 can convert them) or document them as
  permanently-`http`.
- FU-11: **Degraded `Record<string,any>` return types.** Workflow orchestration reads,
  `triggerRun`/`dryRun`/`cancelRun`, and admin `exportAudit` lose their typed returns
  because the backend response models are untyped in the OpenAPI. Type them at the
  source so FU-2/FU-6 keep call-site types.
- FU-12: **`agent-groups` view-test real-timer `settle()` helpers**
  (`AgentGroupListView.test.ts:47-50`, `AgentGroupDetailView.test.ts:47-50`) use
  wall-clock `setTimeout` waits — the same fragility class as the Landing flake fixed in
  this session. Not observed flaking; worth a deterministic rewrite if it ever does.
</content>
</invoke>
