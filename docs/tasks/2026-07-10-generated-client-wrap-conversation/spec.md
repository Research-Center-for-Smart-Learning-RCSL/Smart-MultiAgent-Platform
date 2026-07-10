---
type: refactor
status: draft
created: 2026-07-10
requirements: [R24.13]
supersedes:
---

# Wrap the `conversation` slice's api layer over the generated client

## 1. Summary

FU-1 of the [R24.13] slice-wrap program: convert
`frontend/src/slices/conversation/api/index.ts` (~30 functions) to call the generated
`@shared/api-client` services instead of the bare `@shared/transport` `http` singleton.
The conversation api layer **already unwraps `.data`** (every function returns a bare
body), so — unlike the agent-groups pilot which had to drop `.data` at call sites — this
is a **signature-preserving in-place body swap with zero call-site changes**. The
backend response-enum sweep (`2026-07-10-backend-response-enum-sweep`, implemented) already
narrowed the generated `*Out` unions (`SenderType`, `ExportJobStatus`, `AttachmentStatus`,
`ScanStatus`) to exactly match this slice's hand-rolled literal unions, so no per-slice
backend work remains.

The slice keeps its **hand-rolled domain types** (`Workspace`, `Chatroom`, `Message`,
`Observation`, `Attachment`, …) as the api layer's public return types. Those types encode
refinements the generated client cannot express — the discriminated `ReleaseTarget`, the
`RagSource[]` inside `Message.metadata`, the narrowed `Observation.trigger` — and they are
consumed slice-wide by the WebSocket frame handlers and stores that construct these objects
*without* going through the api layer. Rebasing them onto the generated models would couple
that WS/store surface to the generated client for no call-site benefit. Instead, the four
places where a generated `*Out` is not directly assignable to the slice type are bridged at
the api boundary only, each with a one-line comment.

## 2. Motivation

- **[R24.13] convergence.** Every slice's hand-rolled api layer should wrap the generated
  client so request/response shapes, auth (bearer + silent 401 refresh), and problem+json
  error typing come from one instrumented axios singleton
  (`shared/transport/axios.ts`) rather than being re-encoded per slice. `agent-groups`
  (implemented, commit ee7c646) is the pilot; `conversation` is the largest remaining
  slice and the next increment named in the enum-sweep dossier's FU-1.
- **Lower drift risk.** Hand-typed request/response interfaces silently rot when the
  backend contract moves; wrapping the generated services means `pnpm run gen:api` is the
  single source of truth for wire shapes, and `check:openapi-drift` guards it.

## 3. Non-goals

- **No call-site changes.** The ~30 exported function signatures are preserved
  byte-for-byte; only their bodies change. Views, stores, and composables that import them
  are untouched. (The one exception is the `guest_token` type cleanup in §5C, which touches
  two test fixtures, not production call sites.)
- **No slice-type rebase.** The hand-rolled types in
  `conversation/types/index.ts` stay hand-rolled (see §5B rationale). This is not the place
  to converge them onto the generated models.
- **No WebSocket / realtime changes.** `useChatroomSocket`, the WS event union
  (`ChatroomEvent`), and presence/typing frames are out of scope — only the REST api layer
  is wrapped.
- **No backend changes.** The enum sweep already emitted the narrow unions this slice
  needs. `ObservationOut.release_target` staying `Record<string,any>` is a known backend
  gap tracked as FU-13 of the enum-sweep dossier, bridged here by a cast, not fixed.
- **The cross-slice `Agent` type is not converged.** `listProjectAgents` /
  `listChatroomAgents` keep returning the agents-slice `Agent` / the slice-local
  `BoundAgentRef`; the agents slice is a separate future wrap.

## 4. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | (settled by the playbook) How to resolve `*Out` literal-union→`string` widening? | Backend enum sweep first, then wrap. | Done — the sweep landed, so the generated unions already match this slice's types. |
| Q-2 | Keep the slice's hand-rolled types, or re-export the generated `*Out` models as slice aliases (the agent-groups pattern)? | Keep hand-rolled; bridge the 4 divergences at the api boundary. | The types are consumed by the WS/store surface that never touches the api layer; aliasing would couple that surface to the generated client and lose the discriminated `ReleaseTarget` / `RagSource` refinements, for zero call-site benefit. |
| Q-3 | `Chatroom.guest_token` — the slice type requires it, but the contract's `ChatroomOut` has no such field (it is served only by `getGuestLink` → `GuestLinkOut`). Keep it (and cast `ChatroomOut`→`Chatroom`), or drop the phantom field? | Drop it; fix the two test fixtures that set it. | It is dead: the real API never populated it, so any production read was already `undefined`. Dropping makes `ChatroomOut` cleanly assignable and removes a latent bug. |
| Q-4 | Characterization-test scope for the ~30 wrappers? | Representative request-level MSW tests: one smoke per resource group **plus** one per non-trivial wrinkle (presence `.user_ids`, release body, `release_target` bridge, multipart upload, both `If-Match` paths, the three create shapes). | The wrappers are thin; a test per method is low-value ceremony. Pinning the URL/verb/header/body-shape and the four bridges is what actually guards the refactor. |

## 5. Current vs Target Structure

### 5A. Conversion pattern (per function)

Each `export async function fn(...): Promise<SliceType>` keeps its signature; the body
changes from `const { data } = await http.<verb>(url, ...); return data` to
`return <Service>.<method>({ ...options })`. The generated method already resolves to the
unwrapped body (`CancelablePromise<Body>`), so most wrappers collapse to a single
`return`. Service/method map:

| Slice function | Generated call | Return bridge |
|---|---|---|
| `listWorkspaces` | `WorkspacesService.listWorkspaces…Get` | direct (`WorkspaceOut[]`→`Workspace[]`) |
| `createWorkspace` | `…createWorkspace…Post` | direct (`WorkspaceCreatedOut`→`Workspace`; extra `default_chatroom_id` unused) |
| `getWorkspace` | `…readWorkspace…Get` | direct |
| `deleteWorkspace` | `…deleteWorkspace…Delete` | direct (void) |
| `setWorkspaceConceptMapEnabled` | `…setConceptMapEnabled…Put` | direct (`ConceptMapStatusOut` shape-identical) |
| `listChatrooms` | `ChatroomsService.listChatrooms…Get` | direct (after §5C `guest_token` drop) |
| `createChatroom` | `…createChatroom…Post` | direct |
| `patchChatroom` | `…patchChatroom…Patch` (`ifMatch: String(version)`) | direct |
| `getChatroom` | `…readChatroom…Get` | direct |
| `deleteChatroom` | `…deleteChatroom…Delete` | direct (void) |
| `getGuestLink` | `…readGuestLink…Get` | direct (`GuestLinkOut`→`{url}`) |
| `listProjectAgents` | `AgentsService.listProjectAgents…Get` | direct (`AgentOut`→agents-slice `Agent`; enum unions collapse to its `string` fields) |
| `listChatroomAgents` | `ChatroomsService.listChatroomAgents…Get` | **bridge B2**: map `role: r.role ?? undefined` (`AgentRef.role` is `…|null`; `BoundAgentRef.role` is optional-no-null) |
| `listChatroomMembers` | `…listChatroomMembers…Get` | direct |
| `addChatroomAgent` | `…addChatroomAgent…Post` | direct (void) |
| `setChatroomAgentRole` | `…patchChatroomAgentRole…Patch` | direct (void) |
| `removeChatroomAgent` | `…removeChatroomAgent…Delete` | direct (void) |
| `listObservations` | `ObservationsService.listObservations…Get` | **bridge B1**: map each via `toObservation` |
| `releaseObservation` | `…releaseObservation…Post` | **bridge B1** on the result; request `ReleaseBody`→`ReleaseIn` is assignable (no cast) |
| `deleteObservation` | `…deleteObservation…Delete` | direct (void) |
| `listMessages` | `MessagesService.listMessages…Get` | direct |
| `sendMessage` | `…sendMessage…Post` | direct |
| `getMessage` | `…readMessage…Get` | direct |
| `getChatroomPresence` | `ChatroomsService.getChatroomPresence…Get` | map `.then(p => p.user_ids)` |
| `editMessage` | `MessagesService.editMessage…Patch` (`ifMatch`) | direct |
| `deleteMessage` | `…deleteMessage…Delete` | direct (void) |
| `uploadSingleShot` | `AttachmentsService.createSingleShot…Post` | **bridge B3**: `formData: { file: file as unknown as string, mime }` (codegen types binary as `string`; the request core appends the `File` to `FormData` and sets multipart) |
| `getAttachment` | `…readAttachment…Get` | direct (`AttachmentDownloadOut`→`AttachmentDownload`) |
| `searchMessages` | `SearchService.searchMessages…Get` | direct |
| `createExport` | `ExportsService.createExport…Post` | direct (`ExportCreateOut`→`{job_id,status}`) |
| `getExport` | `…getExport…Get` | direct (`ExportStatusOut`→`ExportStatus`) |
| `enrollGuest` | `GuestsService.enrollGuest…Post` | direct (void) |
| `compactChatroom` | `ChatroomsService.compactChatroom…Post` | direct (ignore the `Record<string,string>` body; wrapper stays `Promise<void>`) |

### 5B. The four boundary bridges (the only non-direct wiring)

- **B1 — `Observation.release_target`.** `ObservationOut.release_target` is
  `Record<string,any> | null`; the slice's `Observation.release_target` is the discriminated
  `ReleaseTarget | null`. The runtime value *is* a `ReleaseTarget` (the backend emits the
  discriminated dict); the generated type is loose only because the backend response model
  is still an untyped dict (enum-sweep FU-13). Bridge with a small local
  `toObservation(o: ObservationOut): Observation` that returns
  `{ ...o, release_target: o.release_target as ReleaseTarget | null }`. Applied in
  `listObservations` (map) and `releaseObservation`. Delete `toObservation` when FU-13 lands.
- **B2 — `BoundAgentRef.role`.** `AgentRef.role` is `'normal' | 'observer' | null`;
  `BoundAgentRef.role` is optional and never `null` (its *absence* means "you are not the
  creator, so you are not told" — R28.10). Normalize `role: r.role ?? undefined` so the
  existing semantic (absent, not null) is preserved.
- **B3 — multipart `uploadSingleShot`.** The generated `Body_create_single_shot…` types
  `file` as `string` (openapi binary). Pass `file as unknown as string`; the request core
  builds the `FormData` and sets `multipart/form-data`, so the manual `FormData`/header
  construction is removed.
- **B4 — none.** (Placeholder intentionally empty; the request-side `ReleaseBody`→`ReleaseIn`
  widening needs no cast — both `ReleaseBody` variants are assignable to `ReleaseIn`.)

### 5C. `Chatroom.guest_token` cleanup (Q-3)

`ChatroomOut` (the contract) has no `guest_token`; the slice's `Chatroom` interface declares
`guest_token: string` (`conversation/types/index.ts:21`). It is a phantom — the chatroom read
never returned it (the token is served by `getGuestLink`). Remove the field from the
interface and delete the `guest_token: 'tok_1'` line from the two fixtures that set it
(`__tests__/ChatroomSettingsView.test.ts:59`, `__tests__/useChatroomSettings.test.ts:19`).
No production code reads it (grep-verified).

### 5D. Verified type-compatibility matrix (generated `*Out` → slice type)

All enum unions match exactly after the sweep: `SenderType`, `ExportJobStatus`,
`AttachmentStatus`, `ScanStatus` are literal-for-literal identical to the slice unions;
`Record<string,any>` is assignable to the slice's `Record<string,unknown>` metadata. Directly
assignable (no bridge): `WorkspaceOut`, `WorkspaceCreatedOut`, `ChatroomOut` (post-§5C),
`MessageOut` (incl. nested `AttachmentOut[]`), `AttachmentOut`, `AttachmentDownloadOut`,
`ExportStatusOut`, `ExportCreateOut`, `ConceptMapStatusOut`, `GuestLinkOut`,
`ChatroomMemberOut`, `SearchResponse`, `AgentOut`. Bridged: `ObservationOut` (B1),
`AgentRef` (B2), the upload body (B3).

## 6. Characterization Test Plan

New file `conversation/api/__tests__/index.spec.ts`, request-level MSW (mirroring
`agent-groups/api/__tests__/index.spec.ts`): assert method → URL, verb, headers, query, and
request/response body shape. Coverage (Q-4): a smoke per resource group (workspaces,
chatrooms, agents-binding, observations, messages, attachments, search, export, guests),
**plus** targeted cases for each bridge and header:
- `getChatroomPresence` returns the unwrapped `string[]` (not `{user_ids}`).
- `releaseObservation` sends the flat `ReleaseIn` body and yields an `Observation` whose
  `release_target` is the discriminated shape (B1).
- `listObservations` maps `release_target` through `toObservation` (B1).
- `uploadSingleShot` posts `multipart/form-data` with the file part (B3).
- `patchChatroom` / `editMessage` send `If-Match: <version>`.
- `createWorkspace` / `createExport` / `setWorkspaceConceptMapEnabled` return the expected
  shapes.

Existing slice tests (stores, views, composables) must pass **unmodified** except the two
fixture edits in §5C — that is the proof the signatures are preserved.

## 7. Migration Steps

1. Rewrite `conversation/api/index.ts`: swap every body to the generated service call per
   the §5A map; add the local `toObservation` (B1), the `role ?? undefined` map (B2), the
   upload cast (B3); drop the `@shared/transport` import.
2. §5C: remove `guest_token` from the `Chatroom` interface; fix the two fixtures.
3. Add `conversation/api/__tests__/index.spec.ts` (§6).
4. `pnpm typecheck` (proves generated→slice assignability across the slice) + `pnpm test` +
   `pnpm lint`. No `gen:api` rerun — the contract is unchanged by this frontend-only edit.

## 8. Risks and Rollback

- **A bridge cast hides a real shape drift.** Mitigated: B1/B3 are the only `as` casts, both
  narrowly scoped and documented, and B1 is covered by a test asserting the discriminated
  shape at runtime. The matrix (§5D) was verified field-by-field against the generated
  models, not assumed.
- **A slice consumer read `Chatroom.guest_token`.** Grep-verified there is none in
  production; only two test fixtures set it. Rollback of the whole task is a single
  `git revert` — the change is one file plus one type edit plus one new test file.
- **`compactChatroom` return.** The generated method resolves `Record<string,string>` (202
  body); the wrapper stays `Promise<void>` by not returning it — behavior-identical to today
  (the caller awaits completion, ignores the body).

## 9. Acceptance Criteria

- [ ] AC-1: every function in `conversation/api/index.ts` calls a `@shared/api-client`
      service; the `@shared/transport` `http` import is gone; all ~30 signatures are
      byte-identical to before (verified by the unmodified call sites still typechecking).
- [ ] AC-2: `pnpm typecheck` and `pnpm lint` are green — generated→slice assignability holds
      across the slice with only the B1/B3 documented casts.
- [ ] AC-3: the four bridges behave correctly — `getChatroomPresence` returns `string[]`;
      `releaseObservation`/`listObservations` yield `Observation` with a discriminated
      `release_target`; `BoundAgentRef.role` is absent (never `null`); `uploadSingleShot`
      posts multipart — each pinned by a test in the new spec.
- [ ] AC-4: `Chatroom.guest_token` is removed; the two fixtures are fixed; no production read
      of it exists (grep evidence in the commit).
- [ ] AC-5: `pnpm test` green — the new characterization spec passes and every existing
      conversation test passes unmodified (bar the two §5C fixture edits).

## 10. SRS Delta

None — behavior-preserving refactor of the api-client layer; the [R24.13] wrap program this
advances is already documented.

## 11. Deviation Log

Appended by /build.

## 12. Follow-ups

- FU-1: converge the agents slice (`listProjectAgents`/`listChatroomAgents` currently return
  the agents-slice `Agent` / slice-local `BoundAgentRef`) when that slice is wrapped.
- FU-2: when enum-sweep FU-13 gives `ObservationOut.release_target` a nested discriminated
  model, delete the `toObservation` bridge (B1) and return the generated type directly.
