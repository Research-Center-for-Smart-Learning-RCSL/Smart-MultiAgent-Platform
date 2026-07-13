---
type: refactor
status: implemented
created: 2026-07-12
requirements: [R24.13]
---

# Wrap the `prompt-studio` slice's api layer over the generated client

## 1. Summary

Final increment of the [R24.13] slice-wrap program: convert the `prompt-studio` slice's
api (one module — `api/index.ts`/`promptStudioApi`, **13 methods**) to call the generated
`PromptStudioService` (+ `ModelCatalogService` for one method) instead of the bare
`@shared/transport` `http` singleton. Like the agent-groups pattern, the methods return the
**raw `AxiosResponse`**, so consumers drop `.data` — but the sweep is tiny: **6 in-slice
sites** (queries ×4, `useConfigEditor`, `PromptAssistantPanel`), no cross-slice consumers,
and **no test-mock changes** (every prompt-studio test mocks at the MSW/HTTP layer with bare
bodies). All 13 methods map (no dead code).

The distinctive work is **scope dispatch**: 8 of the 13 methods take a `ConfigScopeRef` and
today build the URL from it (`configBase`/`templateBase`); the generated service has
*separate* methods per scope, so each wrapper must dispatch on `scope.kind`
(`user→me*`, `org→org*` with `{orgId}`, `platform→admin*`) to one of three generated
methods. A single `dispatchScope` helper removes the branching duplication. Plus: 3-variant
multipart uploads bridged by the existing `asBinaryFormField`, If-Match null-drop for
`putConfig`, and four `string`-vs-union enum type divergences resolved by boundary casts
(Q-4). The slice keeps its hand-rolled types (Q-2).

## 2. Motivation

- **[R24.13] convergence — the last holdout.** `api/index.ts` (`api/index.ts:1` imports
  `http`) hand-encodes 13 request/response shapes plus its own `configBase`/`templateBase`
  URL builders, duplicating what `pnpm run gen:api` owns. This is the **final** `http`-based
  api layer; landing it completes the program.
- **Scope-URL logic belongs to the generated client.** The hand-rolled `configBase`/
  `templateBase` (`api/index.ts:17-27`) reproduce the per-scope endpoint families the OpenAPI
  already models as distinct operations; the dispatch helper replaces string-building with
  typed method selection.

## 3. Non-goals

- **No behavior change on the wire.** Same endpoints/verbs/bodies/query params, same If-Match
  semantics (including "no header when version is null" for `putConfig`), same multipart form
  field.
- **No consumer changes beyond the 6 `.data` sites.** The wrapper keeps returning the
  hand-rolled types, so `useConfigEditor`/`useTemplateEditor`/`PromptTemplatePicker`/the views
  are otherwise untouched.
- **No slice-type rebase.** The hand-rolled types stay (Q-2; `types/index.ts` header calls the
  generated client "drift-check-only, not imported at runtime"); the four enum divergences are
  cast at the boundary.
- **No `gen:api` rerun**, no backend change.
- **No model-catalog dedup.** `getModelCatalog` duplicates the agents slice's fetch (different
  query keys + a chat-only vs chat+embedding `ModelCatalog`) — left as FU-5.

## 4. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | (settled, carried) enum widening? | See Q-4 — this slice has four `string`-vs-union divergences. | |
| Q-2 | (settled, carried) Keep hand-rolled types or alias generated? | Keep hand-rolled; cast at the boundary. | Consumers read the snake_case hand-rolled fields and switch on the narrow `scope` union (`PromptTemplatePicker`); the types header declares them the runtime source of truth. |
| Q-3 | (settled, carried) How to convert safely? | Rewrite over the generated services; `pnpm typecheck` enumerates the `.data` sites; the existing MSW view tests + a new api characterization spec guard the wire contract. | |
| Q-4 | The generated `FileOut.scan_status`, `AssistantConfigOut.scope`, `TemplateOut.scope`, `ResolvedAssistantOut.source_scope` are `string`; the hand-rolled types use narrow unions (`ScanStatus`, `PromptScope`). Bridge (validate), cast, or widen? | **Boundary cast** — annotate the wrapper `Promise<HandRolled>` and cast the resolved body (`… as ConfigEnvelope`). | These are backend **closed enums** (DB/Literal), not runtime-reconfigurable — the cast merely relocates the same unchecked assertion the old `http.get<ConfigEnvelope>` already made (the workflow-slice precedent). Contrast identity's `toCaptchaConfig`, which was hardened to *validate* because the captcha provider is an admin-reconfigurable value on the auth surface; here validation would be disproportionate for zero real risk. Widening the unions would lose the exhaustive `scope` switching consumers rely on. |
| Q-5 | `getConfig` etc. must target the right scope endpoint. Dispatch inline per method, or via a helper? | A single generic `dispatchScope(scope, { user, org, platform })` helper. | Removes 8× copies of the `kind` branch; one tested place for the `platform→admin` mapping (the non-obvious bit: `ConfigScopeRef.kind === 'platform'` → the `admin*` methods). |

## 5. Current vs Target Structure

Frontend layer direction unchanged (`slices/prompt-studio/api` → `shared/api-client`). Full
13-row mapping (incl. the 3-way dispatch per scoped method) is in the Explore artifact;
highlights:

### 5A. Mapping highlights (all 13 matched)

- **8 scoped** (getConfig/putConfig/uploadFile/deleteFile/listTemplates/createTemplate/
  patchTemplate/deleteTemplate) → `dispatchScope` to `PromptStudioService` `me*`/`org*`/
  `admin*` (org passes `{orgId: scope.orgId}`). Arg rename `id`→`templateId`.
- **4 project-scoped** (resolvedForProject/mergedTemplates/createSession) + `postMessage` →
  the corresponding `PromptStudioService` project methods.
- **`getModelCatalog`** → `ModelCatalogService.getModelCatalogApiModelCatalogGet()` (second
  service import; mirrors the agents-slice wrapper).

### 5B. Type handling

- **Directly assignable → annotate-and-return, no cast:** `createSession`
  (`SessionCreatedOut`→`SessionCreated`), `getModelCatalog` (`ModelCatalogOut`→`ModelCatalog`;
  the extra `embedding` field is a benign superset), the `void` deletes.
- **Boundary cast (Q-4):** `getConfig` (`ConfigEnvelope`), `putConfig`/`uploadFile`
  (`AssistantConfig`/`AssistantFile`), `listTemplates`/`createTemplate`/`patchTemplate`/
  `mergedTemplates` (`PromptTemplate`), `resolvedForProject` (`ResolvedAssistant`). The
  hand-rolled type is a subtype of the generated `*Out` (union ⊆ string), so the wide→narrow
  cast is direct.
- **Request bodies** map cleanly (assignable): `AssistantConfigPutInput`→`AssistantConfigPutIn`,
  `TemplateCreateInput`→`TemplateCreateIn`, `TemplatePatchInput`→`TemplatePatchIn`,
  `postMessage {content, editor_draft}`→`MessageIn`.

### 5C. Multipart uploads

`uploadFile` (3 scope variants) → the generated `me/org/adminUploadFile` methods whose
`formData` types `file` as `string` (codegen binary artifact). Bridge with the existing
`asBinaryFormField(file)` from `@shared/transport` (same as agents/conversation):
`{ file: asBinaryFormField(file) }`, org adds `{ orgId }`.

### 5D. If-Match

- `putConfig`: `ifMatch: version === null ? null : String(version)`. Verified the generated
  request core (`getHeaders` → `isDefined`) **drops null/undefined headers**, so `null`
  reproduces the old "no If-Match header when version is null" behavior.
- `patchTemplate`: `ifMatch: String(version)` (always sent, matching today).

### 5E. Consumer sweep (drop `.data`) — 6 sites, all in-slice

`pnpm typecheck` enumerates them: `queries/index.ts:29,36,44,52` (the 4 read `queryFn`s),
`composables/useConfigEditor.ts:41` (`getModelCatalog`), and
`components/PromptAssistantPanel.vue:39` (`const { data } = await createSession` →
`const created = await …; created.session_id`). The 6 mutation `mutationFn`s and `postMessage`
are await-only (no `.data`) — unaffected. **No cross-slice consumers** (the barrel re-export is
unused externally; only the *components* `PromptAssistantPanel`/`PromptTemplatePicker` are
consumed cross-slice, and they go through the query layer).

### 5F. Test updates

**No existing test changes** — all prompt-studio tests (kit.ts + the 3 view tests + the panel/
picker/socket tests) mock at the MSW/HTTP layer with bare bodies, transparent to the return-
shape change. Add `prompt-studio/api/__tests__/index.spec.ts` — request-level MSW
characterization: for each scoped method assert the three scope endpoints are hit
(user/org/platform → `/me/…`, `/orgs/{id}/…`, `/admin/…`), the `putConfig` If-Match present-
and-absent (version vs null), the `patchTemplate` If-Match, the multipart `uploadFile` field,
the `createSession`/`resolvedForProject`/`mergedTemplates`/`postMessage` project routes, and
`getModelCatalog`.

## 6. Security Considerations

Config surface handling BYO provider-key references, system prompts, and file uploads
(`check-security`: privilege/tenant scoping, file upload, user input):

- **Scope dispatch must not cross privilege boundaries.** The `platform` scope maps to the
  `admin*` endpoints; a dispatch bug could aim a user/org action at the admin endpoint.
  Mitigated: each `me/org/admin` endpoint enforces its own server-side AuthZ (a non-admin
  calling `admin*` is 403), and the characterization spec pins each scope→endpoint mapping.
- **AuthZ unchanged.** Same scoped endpoints, same path params; no authorization moves
  client-side.
- **No secret exposure.** `key_id` is a reference (not the key material); `putConfig` sends the
  same body as today; no `console.*` added, nothing logged.
- **File upload preserved.** The multipart field and endpoint are unchanged; server-side
  malware scan (`scan_status`) and extraction are untouched — the wrapper only relays the
  `File`.
- **If-Match preserved** on config/template writes (optimistic-concurrency guard).

## 7. Migration Steps

1. Rewrite `api/index.ts` over `PromptStudioService` + `ModelCatalogService`; drop the `http`
   import and the `configBase`/`templateBase`/`ifMatch` helpers; add `dispatchScope`; apply the
   Q-4 casts, §5C multipart, §5D If-Match.
2. `pnpm typecheck` → drop `.data` at the 6 sites (§5E) until green.
3. `pnpm test` → all prompt-studio tests pass unmodified; add the characterization spec (§5F).
4. `pnpm lint` (changed files) + `pnpm build`. No `gen:api`.

## 8. Risks and Rollback

- **Scope-dispatch correctness** is the main risk (wrong scope → wrong endpoint). Mitigated by
  the `dispatchScope` helper (one place), `pnpm typecheck`, and per-scope spec cases; server
  AuthZ backstops any mistake.
- **Enum casts (Q-4)** are unchecked but safe (backend-closed enums); if a backend enum ever
  gains a value, the cast silently accepts it (same as today's `http.get<T>`).
- **`putConfig` null-If-Match** relies on the core dropping null headers — verified; pinned by a
  spec case (create-with-null-version sends no If-Match).
- Rollback is `git revert`; `api/index.ts` is self-contained and the 6 sweep edits are
  mechanical.

## 9. Acceptance Criteria

- [x] AC-1: every `promptStudioApi` method calls a generated `PromptStudioService`/
      `ModelCatalogService` method; no `@shared/transport` `http` import remains in
      `prompt-studio/api/*` (only `asBinaryFormField` is imported from transport); scoped
      methods dispatch on `scope.kind` via `dispatchScope`; each resolves the bare body typed
      as its hand-rolled type. (`api/index.ts`)
- [x] AC-2: the 6 `.data` sites are converted (`queries/index.ts` ×4, `useConfigEditor.ts:41`,
      `PromptAssistantPanel.vue:39`); `pnpm typecheck` green and changed files lint clean, with
      no consumer edit beyond the six.
- [x] AC-3: scope dispatch is correct — `index.spec.ts` asserts each scoped method hits
      `/api/me/…` for `user`, `/api/orgs/{id}/…` for `org`, and `/api/admin/…` for `platform`
      (getConfig + listTemplates cover all three; the remaining scoped methods pin their route).
      Security audit independently verified all 8 scoped methods' user/org/platform triples
      against `PromptStudioService`.
- [x] AC-4: request contract preserved — `index.spec.ts` asserts the `putConfig` body + If-Match
      (present for a version, absent for null), the `patchTemplate` If-Match, and the project
      routes (createSession/resolved/merged/postMessage). Multipart `uploadFile` route+return
      asserted; see D-1 for the field-assertion deviation.
- [x] AC-5: `pnpm test` green — 123 files / 609 tests (all prompt-studio tests unmodified + the
      new 11-case characterization spec — the 12th planned case, `getModelCatalog`, was removed by
      FU-5, which rehomed the catalog fetch to the shared `useModelCatalog` composable); `pnpm
      build` green (17.45s).
- [x] AC-6: security holds — scope→endpoint mappings are pinned, request bodies/If-Match are
      byte-identical, no key material or secret is logged, file upload unchanged (§6). Security
      audit: no findings; scope-dispatch correctness confirmed for all 8 scoped methods.

## 10. SRS Delta

None — behavior-preserving refactor of the api-client layer.

## 11. Deviation Log

- D-1: The `uploadFile` characterization test asserts the scoped route + returned body, not the
  multipart form field itself (§5F/AC-4 intended the field). In the happy-dom + axios test env,
  FormData serialization surfaces the test `File`'s own `text/plain` type rather than
  `multipart/form-data`, so a field-level assertion is a test-env artifact, not a contract check.
  Mirrors the agents-slice `uploadDocumentMultipart` spec precedent (same `asBinaryFormField`
  path, proven in the agents/conversation slices in production). The wire behavior is unchanged;
  the field mapping is covered by the shared helper's existing coverage.

No wire-behavior deviations. (Note: unlike the tenancy/identity/admin slices, no OpenAPI
`limit`-default deviation applies — none of the 13 prompt-studio methods carry pagination
params.)

## 12. Follow-ups

- FU-1: (carried) merge the two `useProjectRole` composables (tenancy/workflow).
  - **Resolved** (this session). The two are not identical: tenancy's is project-keyed and already
    the canonical cross-slice resolver (agent-groups/conversation/agents consume it via
    `@slices/tenancy`); workflow's is workspace-keyed and used only by two workflow views. The
    duplication was the membership -> role core (owner lookup, `isAuthorized`, `decided`).
    Reworked workflow's composable to keep only its workspace -> project_id lookup and delegate
    role resolution to tenancy's `useProjectRole(projectId)`; the `{isAdmin, isOwner,
    isAuthorized, decided}` return shape is unchanged, so the two views need no edit. A focused
    security pass confirmed the authz semantics are byte-for-byte equivalent across all seven
    reachable states (admin short-circuit, the workspace-error and no-project_id `decided` edges,
    owner vs non-owner). Bonus: the members fetch already used `tenancyKeys.projectMembers`, so it
    now shares that cache, and workflow's direct `projectsApi`/`tenancyKeys` imports are gone —
    the authorization resolver lives in one audited place instead of two copies that could drift.
- FU-3: (carried) the pre-existing 296-warning lint debt blocking `--max-warnings=0`.
  - **Resolved** (this session). `pnpm lint` is now clean (0 warnings). The 296 broke down as:
    259 auto-fixable formatting (255 `vue/html-indent` all in one drifted file,
    `RagConfigDetailView.vue`, plus a few `max-attributes-per-line`/`attributes-order`) fixed via
    `eslint --fix` — whitespace/attribute-placement only; 27 `vue/require-default-prop`, all in
    `shared/ui` atoms on intentionally-optional value props (the rule is now off for
    `src/shared/ui/**`, mirroring the existing no-bare-strings exemption, and stays on for slices);
    and 10 `@typescript-eslint/no-unused-vars`, all genuinely dead bindings removed (a dead
    `detectBrowserLocale`, an unused `projectId`, unbound `defineField` attrs, and unused
    `reload*` destructures in the keys views). Note: the repo has Prettier (`pnpm fmt`) but no
    `eslint-config-prettier` and no Prettier CI gate, so ESLint is the authoritative formatter;
    the drifted file was reconciled to ESLint (matching the other ~200 `.vue` files), not Prettier.
    The Prettier/ESLint reconciliation is a separate latent tooling question, not this debt.
- FU-4: (carried) widen the backend `restore` `resource_type` enum to the six UI values.
- FU-5: dedup the model-catalog fetch — `prompt-studio` and `agents` both fetch `/model-catalog`
  under different query keys with divergent `ModelCatalog` shapes (chat-only vs chat+embedding);
  a shared catalog query/type belongs in a common place.
  - **Resolved** (this session). Both slices in fact fetched the *identical* endpoint/body
    (`ModelCatalogService.getModelCatalogApiModelCatalogGet()` → the full `ModelCatalogOut`),
    only under different query keys (`['agents','modelCatalog']` vs `['prompt-studio','model-catalog']`)
    — so the same immutable global catalog was cached twice. Extracted a single
    `useModelCatalog()` composable into `shared/composables/` (keyed `['model-catalog']`,
    `staleTime: Infinity`), consumed by the two agents forms and the prompt-studio config editor.
    Removed both slices' `getModelCatalog` api methods, the agents `modelCatalog` query key + its
    local composable, and the hand-rolled catalog interfaces. **Type strategy (user decision):**
    aliased the generated `ModelCatalogOut` (`export type ModelCatalog = ModelCatalogOut`) rather
    than re-declaring hand-rolled interfaces — sound here because the generated types are
    field-identical (no enum/optionality divergence to bridge, unlike the rest of the program's
    Q-2 posture). Coverage moved from the two removed api-characterization cases to a shared
    composable test that pins the dedup (two consumers → one fetch). Security audit N/A (static
    read-only global catalog; no auth/keys/tenant/upload surface).
- FU-6 (program close-out): with all `http`-based api layers now wrapped, remove the bare `http`
  singleton export from `@shared/transport` if nothing else uses it, and update the R24.13
  requirement status.
  - **Resolved** (this session, follows the prompt-studio conversion). The last external consumer
    of the barrel `http` export was `shared/composables/useIdleLogout.ts` (`loadPolicy` →
    `GET /auth/session-policy`); converted to `AuthService.sessionPolicyApiAuthSessionPolicyGet()`
    (`SessionPolicyOut` is identical to the local `SessionPolicy`, so no bridge). The `http`
    export was then removed from `shared/transport/index.ts`, and transport's own interceptor
    test repointed to `import { http } from '../axios'`. **Nuance vs the FU's "if nothing else
    uses it":** the `http` *instance* is NOT dead — `transport/tus.ts` (tus upload protocol) and
    `transport/axios.ts` (WS-ticket redemption) still import it directly from `./axios`. One
    shared composable also legitimately holds it: `shared/composables/useNetworkStatus.ts`
    deep-imports `http` from `@shared/transport/axios` for the `/healthz` liveness probe — a
    root-mounted endpoint with **no** OpenAPI (`/api`) method and thus no generated-client
    equivalent, which deliberately rides the shared interceptors so an answered probe clears the
    offline state through the success path. This is transport-adjacent infra, not a business API
    call, so it stays on the raw instance by design. Net: the singleton's cross-app *re-export*
    is gone (no slice makes a business API call through the raw axios singleton), while three
    transport-layer/infra consumers (tus, WS-ticket, healthz probe) keep the instance internal.
    R24.13's requirement text (REQUIREMENTS.md §24.4, traceability.csv, J.2 exit criteria) is
    descriptive of the now-realized target architecture and needs no textual change; those docs
    carry no status field.
