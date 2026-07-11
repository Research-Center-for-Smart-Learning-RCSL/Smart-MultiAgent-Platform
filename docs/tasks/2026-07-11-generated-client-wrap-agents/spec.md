---
type: refactor
status: implemented
created: 2026-07-11
requirements: [R24.13, R11.09, R11.23, R22.15]
supersedes:
---

# Wrap the `agents` slice's api layer over the generated client

## 1. Summary

The largest increment of the [R24.13] slice-wrap program: convert the `agents` slice's
single api module (`frontend/src/slices/agents/api/index.ts` — the `agentsApi` object with
**54 methods**) to call the generated `@shared/api-client` services instead of the bare
`@shared/transport` `http` singleton. Like `keys`, `agentsApi` returns the **raw
`AxiosResponse`**, so this is the **agent-groups pattern**: return the bare body and drop
`.data` at every consumer. The backend enum sweep already narrowed the generated unions
(`BuildState`, `AgentModelHint`, `ScanStatus`, `AgentToolType`, `ContextMode`, …) to match
this slice's hand-rolled ones.

**47 of the 54 methods wrap cleanly; 7 are dead code and get deleted** (Q-4): the legacy
MCP-binding methods (`listMcpBindings`, `addMcpBinding`, `patchMcpBinding`,
`deleteMcpBinding`, `testMcpBinding`) and builtin-tools methods (`getBuiltinTools`,
`setBuiltinTools`) call `/agents/{id}/mcp…` and `/agents/{id}/builtin-tools`, which **no
longer exist in the backend contract** (`openapi.json` has only the egress-allowlist
`/mcp` routes) and have **zero frontend consumers** — they were superseded by the unified
Tools API (`/agents/{id}/tools`, driven by `AgentToolsView`). Their exclusive types
(`McpBinding`, `McpBindingPatchInput`, `BuiltinToolsState`, `McpTestResult`) and the
`mcpBindingCreateSchema`/`McpBindingCreateInput` (neither re-exported nor consumed) are
deleted with them.

The surface spans eight generated services — `AgentsService` (agent CRUD + unified tools),
`RagService`, `ModelCatalogService`, `GraphragService`, `GraphragAdminService`,
`KnowmapService`, `McpService`, `AgentWorkspaceService` — plus three multipart uploads
(RAG doc, Knowmap doc, workspace file). It is a security-relevant surface (MCP bindings,
egress allowlist, agent-tool auth credentials, file uploads), so the conversion is
wiring-only by construction: request bodies (including tool `auth` secrets and the egress
allowlist) are byte-identical, only the transport path changes.

The slice keeps its **hand-rolled domain types** (`Agent`, `RagConfig`, `GraphragConfig`,
`KnowmapConfig`, `AgentTool`, `McpBinding`, `GraphView`, …) as the api's public types;
where a generated `*Out` is not directly assignable (optional-vs-required drift, like keys'
`GroupOut`), a small mapping bridge supplies the defaults. `pnpm typecheck` enumerates every
`.data` site and every assignability gap precisely (proven on the keys increment), so it
drives the consumer sweep rather than a hand-maintained list.

## 2. Motivation

- **[R24.13] convergence.** One instrumented axios singleton owns auth and problem+json
  error typing; the agents api should wrap the generated services rather than re-encode ~50
  request/response shapes by hand. `agent-groups`, `conversation`, `keys` are done.
- **Highest-drift slice.** This is the biggest hand-typed surface (RAG, GraphRAG, Knowledge
  Map, tools, MCP, workspace files); wrapping the generated services makes `pnpm run gen:api`
  the single source of truth, guarded by `check:openapi-drift`.

## 3. Non-goals

- **No behavior change on the wire.** Same endpoints/verbs/bodies. No tool credential, MCP
  reference, or egress hostname is reshaped, logged, or dropped.
- **No slice-type rebase.** The hand-rolled types stay (Q-2) — they are consumed slice-wide
  and cross-slice (agent-groups, workflow) and back the `GraphragBuildState` union,
  `GRAPHRAG_IN_PROGRESS` set, and the socket state machines.
- **No composable/query/socket re-architecture.** `useModelCatalog`, `useToolTest`, the
  build-state sockets (`useGraphragSocket`, `useKnowmapSocket`, `useRagConfigSocket`), and
  `agentKeys` keep their shape; only the `.data` unwrap at each site is removed.
- **No change to the resumable tus upload path.** Only the ≤32 MB multipart methods move to
  the generated client; `tusUpload` from `@shared/transport` is untouched.
- **No `gen:api` rerun.** Frontend-only edit; the contract is unchanged.

## 4. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | (settled) enum widening? | Backend enum sweep first, then wrap. | Done — the generated `BuildState`/`AgentModelHint`/`ScanStatus`/etc. already match this slice's unions. |
| Q-2 | Keep hand-rolled types or alias the generated models? | Keep hand-rolled; bridge divergences. | The types are consumed cross-slice (agent-groups/workflow) and back the build-state union + socket logic; and optional-vs-required drift needs a mapping regardless (as with keys' `GroupOut`). Minimal ripple. |
| Q-3 | ~54 methods, ~15 consumer files, several cross-slice — how to convert safely? | Rewrite the api over the generated services; let `pnpm typecheck` enumerate every `.data` site and assignability gap; sweep them mechanically; update module-mock tests to bare bodies. | The keys increment proved typecheck surfaces every site (incl. the ones the initial scope missed) — it is the reliable driver at this scale. |
| Q-4 | 7 methods (`*McpBinding*`, `*BuiltinTools`) hit routes absent from the contract and have zero consumers. Wrap-on-`http`, or delete? | **Delete** them plus their exclusive types/schema. | Their backend routes were removed (superseded by the unified Tools API); wrapping is impossible and keeping them on `http` preserves dead code that 404s and defeats AC-1. Deletion verified safe on both sides: frontend grep shows no consumers and no re-export; and the **backend router source** (`app/api/v1/`) exposes only `/projects/{pid}/mcp/egress-allowlist` — no `/agents/{id}/mcp` or `/builtin-tools` route exists (the remaining `builtin_tools`/`mcp` code is server-side runtime, not HTTP). User asked to verify the backend before deleting; confirmed. |

## 5. Current vs Target Structure

### 5A. Conversion pattern

`agentsApi` stays a single object with the same ~50 method names and signatures; each body
changes from `http.<verb><T>(url, ...)` (returning `AxiosResponse<T>`) to
`<Service>.<method>({ ...options })` (returning the bare body). Capability groups → service:

| Group | Service | Notes |
|---|---|---|
| Agent CRUD (`list`/`create`/`get`/`patch`/`remove`) | `AgentsService` | `patch`/`remove` pass `ifMatch: String(version)`; `list` gained optional `limit`/`offset` (omitted) |
| Unified tools (`listTools`/`addTool`/`patchTool`/`deleteTool`/`testTool`) | `AgentsService` | tool `auth`/`clear_auth` preserved (verified: `AgentToolCreateIn`/`AgentToolPatchIn` carry them) |
| RAG configs + documents | `RagService` | incl. `uploadDocumentMultipart` (formData) |
| Model catalog | `ModelCatalogService` | static global catalog, no args |
| GraphRAG configs / build / status / graph / owner-options / coverage | `GraphragService` | `getGraphragGraph(limit)` → `{ configId, limit }` query param |
| Knowledge Map configs + documents + graph + rebuild | `KnowmapService` | incl. `uploadKnowmapDocumentMultipart`; graph `limit` query param |
| Egress allowlist | `McpService` | hostname path-encoded by the generated client |
| Workspace files | `AgentWorkspaceService` | incl. `uploadWorkspaceFile` (formData) |
| ~~MCP bindings + builtin tools~~ | **deleted** | dead code, routes gone (Q-4) |

The exact generated method name per `agentsApi` method is fixed by the mapping analysis
(matched by URL) and re-verified by `pnpm typecheck`; the full 47-row table lives in the
mapping artifact, not inline here, to keep the dossier maintainable.

### 5B. Multipart uploads (three)

`uploadDocumentMultipart`, `uploadKnowmapDocumentMultipart`, `uploadWorkspaceFile` build a
browser `FormData` by hand today. The generated methods instead take a single `formData`
object the request core turns into `FormData`:
- doc uploads → `{ file: file as unknown as string, mime: file.type || 'application/octet-stream', agent_ids }`
- workspace file → `{ file: file as unknown as string, ...(path ? { path } : {}) }` (no `mime`/`agent_ids`)

The binary `file` is typed `string` (openapi binary), so it uses the same `file as unknown as string`
bridge proven in the conversation increment; the request core appends the `File` and sets the
multipart Content-Type. (Browser behavior unchanged — `form-data`'s browser field is native
`FormData`; the multipart body is not introspectable under vitest's node resolution, so those
tests assert verb/path/return, per conversation D-2.)

### 5C. Response bridges

Expected to mirror keys' one `toKeyGroup` case: any generated `*Out` with an optional field
the slice types as required (defaults populated by the backend) gets a small
`to<Type>(o): SliceType` mapping supplying the default. The exact set is surfaced by
`pnpm typecheck` during Step 7; each bridge is documented at its site and listed as a
deviation if it was not anticipated here.

### 5D. Consumer sweep (drop `.data`)

`pnpm typecheck` lists every `.data`-on-bare-body site. Known set (~15 files): agents views
(`AgentListView`, `AgentDetailView`, `RagConfigListView`, `RagConfigDetailView`,
`GraphragConfigListView`, `GraphragGraphView`, `KnowledgeMapConfigListView`,
`KnowledgeMapConfigDetailView`, `McpEgressAllowlistView`, `AgentToolsView`), composables
(`useModelCatalog`, `useGraphragSocket` → `.state`, `useKnowmapSocket` → `.last_build_state`,
`useRagConfigSocket` → `.find`), `ConceptMapPanel`, and **cross-slice**: agent-groups
(`AgentGroupDetailView`), workflow (`AgentOrchestrationView`, `utils/projectAgents.ts` →
`res.map`). Chained `.data.X` accesses collapse to `.X`; `const { data } = await …` becomes
`const x = await …`. `agentsApi.*` uploads/mutations that are awaited without reading `.data`
need no edit.

### 5E. Test updates

Any agents test that module-mocks `agentsApi` and returns the `{ data }` `AxiosResponse`
envelope must return bare bodies (mirrors keys §5E / D-3). MSW-based and smoke tests pass
unmodified. The exact set is surfaced when `pnpm test` runs.

## 6. Security Considerations

This touches MCP/egress/tools/uploads, so per the `check-security` lens:
- **Tool credentials preserved.** `AgentToolCreateIn`/`AgentToolPatchIn` carry `auth` +
  `clear_auth` (verified); the patch semantics (auth omitted = leave unchanged; `clear_auth`
  = remove) are unchanged, so no credential is silently dropped or leaked. No `*Out` returns a
  secret (tools return `config_warnings`, not stored auth).
- **MCP reference / egress hostname unchanged.** `addMcpBinding` sends `source`/`reference`/
  `allowed_tools` and the egress `hostname` byte-identical; SSRF gating stays server-side.
- **Upload bodies unchanged.** The three multipart methods send the same `file` + `agent_ids`
  / `path` parts; the per-agent allowlist (R11.23 secure-by-default) is applied server-side.
- **AuthZ unchanged.** All authorization is server-side; the client calls identical endpoints.
- **No new attack surface.** No `eval`, no dynamic URL construction beyond path params the
  generated client encodes (egress hostname included).

## 7. Migration Steps

1. Rewrite `agentsApi` in `api/index.ts` over the seven live generated services; **delete the
   7 dead methods + their exclusive types** (`McpBinding`, `McpBindingPatchInput`,
   `BuiltinToolsState`, `McpTestResult`) and the `mcpBindingCreateSchema`/`McpBindingCreateInput`
   in `types/schemas.ts`; drop the `http` import; keep the remaining type exports and the
   `RAG_MULTIPART_MAX`/`GRAPHRAG_IN_PROGRESS` consts.
2. Add the `file as unknown as string` bridge for the three multipart uploads; add any
   `to<Type>` response bridge `pnpm typecheck` demands.
3. `pnpm typecheck` → sweep every reported `.data` site (§5D), in-slice and cross-slice, until
   green.
4. `pnpm test` → update any module-mock test returning the `{ data }` envelope to bare bodies.
5. Add `agents/api/__tests__/index.spec.ts` — request-level MSW characterization across the
   capability groups (verb/path/body), the multipart uploads (verb/path/return), and any
   response bridge.
6. `pnpm lint` (changed files) + `pnpm build`. No `gen:api`.

## 8. Risks and Rollback

- **Volume.** ~50 methods + ~15 consumer files (incl. 3 cross-slice) is a large mechanical
  diff. Mitigated: `pnpm typecheck` is exhaustive — a missed `.data` or a bad method name
  cannot ship (property-does-not-exist / no-such-method errors). The keys increment validated
  this driver.
- **NO-MATCH resolved (Q-4).** The mapping analysis found exactly one class of NO-MATCH: the
  7 dead MCP-binding/builtin-tools methods, whose backend routes are gone. Resolved by
  deletion (not a `gen:api` refresh — the routes were removed on purpose). Every other method
  has a confirmed generated equivalent.
- **A response bridge masks a real absence.** As with keys' `toKeyGroup`, a default is only
  used for fields the backend always populates; each bridge is covered by a characterization
  test feeding an `*Out` without the field.
- Rollback is `git revert` of the implementation commit.

## 9. Acceptance Criteria

- [x] AC-1: every remaining `agentsApi` method calls a `@shared/api-client` service; the 7
      dead MCP-binding/builtin-tools methods + their exclusive types/schema are deleted; no
      `@shared/transport` `http` import remains in `agents/api/index.ts`; each method resolves
      the bare body typed as its slice type. *(47 methods wrap the 7 live services; only two
      residual `@shared/transport` mentions remain — both comments documenting the untouched
      tus path, no `http` import/call. Verified by grep.)*
- [x] AC-2: every `.data` site (in-slice and cross-slice) is converted; `pnpm typecheck` and
      `pnpm lint` (changed files) are green. *(typecheck clean; lint has 0 introduced warnings —
      261 pre-existing `vue/html-indent` confirmed identical at HEAD via stash comparison, D-4.)*
- [x] AC-3: the three multipart uploads post `multipart/form-data` with the file + companion
      fields and resolve the document/file body; any response bridge supplies its defaults —
      pinned by tests. *(3 bridges `toRagDocument`/`toAgentTool`/`toToolTestResult`, D-2; each
      pinned by a characterization case feeding an `*Out` missing the defaulted field.)*
- [x] AC-4: request bodies are unchanged — the characterization spec asserts verb/path/body
      for representative reads/writes across each capability group, including a tool `patch`
      carrying `auth`/`clear_auth` and an egress-allowlist add. *(25-case
      `api/__tests__/index.spec.ts`; tool add+patch assert `auth`/`clear_auth` bodies, egress
      add asserts `{ hostname, note }`, egress remove asserts the path-encoded hostname.)*
- [x] AC-5: `pnpm test` green — updated module-mock tests pass, MSW/smoke tests pass
      unmodified, the new characterization spec passes; `pnpm build` green. *(504 tests / 118
      files; the 2 socket module-mocks updated to bare bodies, D-5; build clean.)*
- [x] AC-6: security holds — no response carries a secret; tool `auth`/`clear_auth`, MCP
      reference, egress hostname, and upload bodies are byte-identical; no masking/logging path
      changed (§6). `check-security` lens: no findings. *(Independent security audit: surface
      clean, 0 Critical/Warning; auth passed verbatim to requestBody, uploads binary-appended,
      egress hostname single-encoded, no logging introduced, 7 deleted methods have zero
      consumers so removal drops no control.)*

## 10. SRS Delta

None — behavior-preserving refactor of the api-client layer.

## 11. Deviation Log

- **D-1 — cross-slice `.data` consumers (as anticipated in §5D, recording the final set).**
  Beyond the agents slice, three cross-slice files read `.data` off `agentsApi` results and
  were swept: `agent-groups/views/AgentGroupDetailView.vue`,
  `workflow/views/AgentOrchestrationView.vue`, `workflow/utils/projectAgents.ts` (`res.map`).
  No slice-boundary violation — these already imported `agentsApi` via the slice `index.ts`.
- **D-2 — three response bridges (§5C predicted "mirror keys' one case"; it was three).**
  `toRagDocument` (narrows `status`/`scan_status` `string`→union — same unchecked assertion
  the old `http.get<RagDocument[]>` already made, no runtime change), `toAgentTool`
  (`config_warnings ?? []`), `toToolTestResult` (`error ?? null`). Plus three type-only
  request-body casts (`as AgentToolCreateIn`/`as GraphRagConfigPatchIn`/`as KnowmapConfigPatchIn`)
  bridging `exactOptionalPropertyTypes` `undefined`-vs-`null` drift — verified type-only, the
  runtime payload is the unchanged zod object so `undefined` optionals stay omitted by
  `JSON.stringify` exactly as before (no undefined→null wire drift).
- **D-3 — RAG document status left as `string` server-side.** Unlike `KnowmapDocumentOut`
  (whose `status`/`scan_status` are the generated `DocumentStatus`/`ScanStatus` enums,
  assignable with no bridge), `RagDocumentOut` still types them as plain `string`, forcing
  the `toRagDocument` union cast. Behavior-preserving here; enum-typing them server-side would
  delete the bridge → FU-3.
- **D-4 — pre-existing vue lint debt not touched.** 261 `vue/html-indent` warnings across the
  touched `.vue` files are pre-existing (identical count at HEAD, confirmed by stash-compare).
  This refactor edited only `<script>` blocks, never templates, so it introduced none. Left
  as-is per Ground Rule 2 (pre-existing, doesn't block); tracked as FU-4.
- **D-5 — two socket module-mock tests updated (as anticipated in §5E).** `useGraphragSocket.test.ts`
  and `useKnowmapSocket.test.ts` module-mock `agentsApi` returning the old `{ data }` envelope;
  updated to bare bodies (`{ state: 'idle' }`, `{ last_build_state: 'idle' }`). Grep confirmed
  these were the only two agents tests with the `{ data }` shape.
- **D-6 — egress-remove encoder comment clarified (check-quality/check-security Info).** Both
  audits noted the generated client path-encodes with `encodeURI`, not the old
  `encodeURIComponent`; equivalent for DNS-valid hostnames (neither escapes `[a-z0-9.-*]`).
  Expanded the `removeEgressAllowlistEntry` comment to record the encoder shift; no code change.

## 12. Follow-ups

- FU-1: if a response bridge is needed, and a later pass tightens the corresponding `*Out`
  field to required in the backend, delete the bridge.
- FU-2: remaining slice wraps (`tenancy`, `identity`, `workflow`, `admin`, `prompt-studio`).
- FU-3: enum-type `RagDocumentOut.status`/`scan_status` server-side (as `KnowmapDocumentOut`
  already is) so `toRagDocument`'s union cast can be deleted (D-3).
- FU-4: clear the pre-existing `vue/html-indent` lint debt in the agents/agent-groups/workflow
  views (D-4) — a formatting-only pass, out of scope for this behavior-preserving refactor.
- FU-5: pin the multipart `agent_ids` on-the-wire serialization (repeated fields, the
  per-agent retrieval allowlist) in the characterization spec once the `form-data` package's
  node body is introspectable under vitest — currently those cases assert verb/path/return
  only (check-quality Info; the core's array handling was verified by reading `getFormData`).
