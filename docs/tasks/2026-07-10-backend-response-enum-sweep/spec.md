---
type: refactor
status: implemented
created: 2026-07-10
requirements: [R24.13]
supersedes:
---

# Backend response-model enum sweep — emit real OpenAPI enums for closed-domain fields

## 1. Summary

Every FastAPI `*Out` response model currently types its closed-set fields
(status/role/state/provider/kind/type) as bare `str`, so the OpenAPI contract emits
`type: string` and the generated frontend client (`@shared/api-client`) carries no
narrowing — which is exactly why converting each slice's `api/` layer to wrap the
generated client (the [R24.13] program, increment 1 done for `agent-groups`) hits
pervasive literal-union→`string` widening. This task tightens those response fields to
emit real enums, then regenerates `backend/openapi.json` + the frontend client once. It
is **behavior-preserving on the wire** (the JSON values are byte-identical; only the
schema documents the enum), and it is a **prerequisite** chosen deliberately (see Q-1):
once it lands, every remaining slice wrap — starting with `conversation` — becomes a
clean, mechanical, frontend-only drop-in with no per-slice backend work.

## 2. Motivation

- **Schema imprecision (check-quality: abstraction/type fidelity).** ~35 response-model
  fields whose value domain is provably closed are emitted as `type: string`. Each is
  backed by an existing domain `StrEnum` and/or a DB CHECK / PG ENUM, and the handler
  already emits `.value` — the schema simply fails to say so. Representative:
  `MessageOut.sender_type: str` (`backend/app/api/v1/messages.py:97`) sourced from
  `SenderType` (`backend/contexts/conversation/domain/models.py:12-15`) via a PG ENUM
  `message_sender_type` (`backend/contexts/conversation/infrastructure/tables.py:142`);
  `KeyOut.provider: str` (`keys.py:47`) from the PG-ENUM-backed `ApiKeyProvider`
  (`backend/contexts/keys/domain/providers.py:28-36`).
- **Blocks the [R24.13] slice-wrap program.** The `agent-groups` pilot
  (`docs/tasks/2026-07-10-generated-client-wrap-agent-groups/spec.md`) was clean only
  because it had no enum fields. Every other slice's hand-rolled types encode literal
  unions the frontend narrows on — e.g. `SenderType`
  (`frontend/src/slices/conversation/types/index.ts:31`, narrowed at
  `ChatroomMessageBubble.vue:23`), `ExportStatus.status`
  (`conversation/types/index.ts:129`, used as a type at `useChatroomExport.ts:16`),
  `RunState`/`StepState` (`workflow/types/index.ts:27,29`, narrowed at
  `useWorkflowRunSocket.ts:40`). Wrapping those slices against a `string`-typed generated
  model would either break the build (union-as-type sites) or silently discard the
  narrowing. Fixing the schema at the source (the settled Q-2 policy) unblocks them all.

## 3. Non-goals

- **No externally observable behavior change.** Wire values are byte-identical: each
  field is retyped to the enum whose `.value` the handler already emits, and `str,Enum`
  members serialize to their `.value`. No endpoint path, status code, or JSON value
  changes.
- **No frontend slice conversion.** The generated client is regenerated (narrower types)
  but the slices keep their hand-rolled types for now; the wraps are separate follow-up
  dossiers (the two already-wrapped slices, `notifications`/`agent-groups`, are only
  verified to still typecheck).
- **Fields whose domain is not provably closed are left as `str`** (see §5C): captcha
  `mode`/`provider`, audit `resource_type`/`action`, observation `trigger`, workflow
  `trigger_type`, graph node `type`. Constraining these could break serialization or
  misdocument the contract.
- **No structured-union or dict typing.** `ObservationOut.release_target` (a
  discriminated union) and the entire `orchestration.py` raw-`dict` surface are out of
  scope — they need nested response models, not a `str`→enum change (Follow-ups).
- **No request-model changes.** Request models/params that are already `Literal`/`Enum`
  stay as-is; this touches response (`*Out`) models only.

## 4. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | When converting slices to wrap the generated client, every slice hits pervasive `str`-widening. Handle enums per-slice, or sweep the backend first? | Backend enum sweep first, as its own task; then every slice wrap is a clean frontend-only drop-in. | User pick. Front-loads the schema fix once (one contract regen) instead of repeating full-stack enum work in each of the eight remaining slice dossiers. |
| Q-2 | (Inherited from the playbook) How to resolve generated `*Out` literal-union→`string` widening? | Fix the backend OpenAPI to emit real enums, then regen. | User pick (playbook §5A P5). This dossier is the concrete execution of that policy across all closed-domain response fields. |

## 5. Current vs Target Structure

### 5A. Target implementation pattern (per field)

For a field currently `field: str` mapped as `SomeOut(field=obj.field.value)`:
- **Retype** the response-model field to the domain enum: `field: SenderType`.
- **Change the mapping** from `obj.field.value` to `obj.field` (pass the enum member).
  Pydantic serializes a `str, Enum` member to its `.value`, so the wire output is
  unchanged, and mypy is satisfied (`SenderType` assignable to `SenderType`, whereas
  `str` is not assignable to the enum-typed field — this is why the `.value` must move
  off the mapping, not stay).
- Where the source is already an enum member (read-coerced, e.g. `RunState(row.state)` at
  `workflow/infrastructure/repositories.py:50`), the mapping just drops the `.value`.
- Import the domain enum into the router module. This mirrors the **established pattern**:
  request models already import domain enums directly (e.g.
  `KeyUploadIn.provider: ApiKeyProvider`, `keys.py:38`;
  `AgentToolCreateIn` / agent create `Literal`s), so an `app/api/v1 → contexts/*/domain`
  enum import is an existing, permitted edge for value types.
- Two special cases: (a) **`ExportStatusOut.status`/`ExportCreateOut.status`** have no
  domain enum today — define one shared `ExportJobStatus(StrEnum)` (values
  `queued|running|ready|failed`) in `contexts/conversation/application/export_service.py`
  as the single source, have the service set it, and type both response fields with it.
  (b) **`TokenPairOut.token_type`** is a single hardcoded constant — type it
  `Literal["Bearer"]` (`auth.py:154`); no enum needed. (c) **`owner_kind`** already has a
  router-local `OwnerKind = Literal[...]` (`graphrag.py:71`) used by the create body —
  reuse it for the response fields.

### 5B. SAFE fields to convert (grouped by router — full evidence in the audit agents' findings)

**identity / admin** — `UserStatus` (`contexts/identity/domain/models.py:16-20`; DB CHECK):
`UserOut.status` (`auth.py:173`), `UserSummaryOut.status` (`admin_users.py:34`),
`UserDetailOut.status` (`admin_users.py:43`). `TokenPairOut.token_type` →
`Literal["Bearer"]` (`auth.py:154`). `RateLimitPolicyOut.scope` (`admin_rate_limits.py:29`,
DB CHECK `user|ip|user_and_ip`).

**tenancy** — enums in `contexts/tenancy/domain/models.py:11-45`, all DB-CHECK-backed:
`OrgMemberOut.role` (`orgs.py:66`), `ProjectMemberOut.role` (`projects.py:58`),
`InviteOut.scope_type`/`role`/`state` (`orgs.py:74,77,78` and `invites.py:29,31,33`),
`TransferOut.state` (`orgs.py:100`), `ProjectOut.owner_type` (`projects.py:47`,
`ProjectOwnerType`).

**keys** — PG-ENUM-backed: `KeyOut`/`KeyListOut.provider` + `.test_status`
(`keys.py:47,48`; `ApiKeyProvider`, `ProbeStatus`), `GroupOut.providers` element type
(`key_groups.py:58`, `list[ApiKeyProvider]`), `SearchKeyOut.provider` + `.test_status`
(`search_keys.py:39,42`; `SearchProvider`, `ProbeStatus`).

**conversation** — `MessageOut.sender_type` (`messages.py:97`, PG ENUM),
`SearchHit.sender_type` (`search.py:26`), `AttachmentOut.status` + `.scan_status`
(`attachments.py:47,48`, inherited by `AttachmentDownloadOut`),
`ExportStatusOut.status` + `ExportCreateOut.status` (`exports.py:83,77` — new
`ExportJobStatus` StrEnum per §5A).

**workflow** — `RunOut.state` (`workflows.py:189`, `RunState`; also `ArchivedRunOut.state`
`:208` — **verify the uncoerced archive dict path** `workflows.py:279` only ever holds
`RunState` values before typing it), `StepOut.state` (`workflows.py:216`, PG ENUM
`step_state`).

**agents** — enums in `contexts/agents/domain/models.py`: `AgentOut.model_hint`
(`agents.py:123`), `.effort` (nullable, `:125`), `.prompt_strategy` (`:126`),
`.context_mode` (`:128`), `AgentToolOut.tool_type` (`agents.py:414`, 7-value
`AgentToolType`). Leave `AgentOut.model_id` as `str` (free-form BYO model id).

**knowmap / graphrag** — enums in `contexts/knowledge/domain/`:
`KnowmapConfigOut.chunk_strategy` (`knowmap.py:96`), the five `BuildState` fields
(`knowmap.py:101`, `graphrag.py:114,131,158,146` — verify `GraphRagStatusOut.state`'s
status-dict source emits `BuildState.value`), `KnowmapDocumentOut.status` + `.scan_status`
(`knowmap.py:115,116`, PG ENUMs), the three `owner_kind` fields (`graphrag.py:103,124,140`,
reuse the router `OwnerKind` literal).

**notification** — `NotificationOut.kind` (`notifications.py:20`, `NotificationKind`,
read-coerced at `contexts/notification/infrastructure/repositories.py:23`).

### 5C. Explicitly NOT converted (left as `str` — domain not provably closed)

- `CaptchaConfigOut.mode` / `.provider` (`auth.py:211,212`) — read from Vault raw config
  with only `.lower()`, no allowlist (`captcha.py:87,88`).
- `AuditEntryOut.resource_type` / `.action` (`admin_audit.py:34,33`) — free-form emitter
  strings, no enum/CHECK.
- `ObservationOut.trigger` (`observations.py:82`) — wake-up-reason vocabulary assembled
  from scattered literals, no enum/CHECK.
- `RunOut.trigger_type` / `ArchivedRunOut.trigger_type` (`workflows.py:187,207`) — echoed
  from user-authored workflow-definition JSON, unvalidated, `""` fallback possible.
- `KnowmapGraphNodeOut.type` / `GraphNodeOut.type` (`knowmap.py:129`, `graphrag.py:166`)
  — free-form LLM-classified entity category, `""` = unknown.

Each becomes a Follow-up (harden the source, then a later sweep can enum it).

### 5D. Contract regeneration

After the backend edits: `python -m scripts.export_openapi` → `backend/openapi.json`, then
`pnpm run gen:api` → `frontend/src/shared/api-client`. The OpenAPI diff must show **only**
`type: string` → `enum: [...]` (plus the TS client's union/enum types) — no path, verb,
or field-set changes. `check:openapi-drift` passes once both artifacts are committed.

**Dependency direction:** `app/api/v1/*` gains imports of `contexts/*/domain` enums — an
existing permitted edge (request models already do it); no new upward or cross-context
cycle.

## 6. Characterization Test Plan

Behavior is pinned by the **existing** backend endpoint tests, which assert the JSON
values these fields carry — they must pass **unmodified**, proving the wire output is
unchanged. Before editing, confirm the touched endpoints have such coverage; where a
converted field has no assertion on its value, add a minimal characterization test that
GETs the endpoint and asserts the exact string value (e.g. a message's
`sender_type == "agent"`, a key's `test_status == "untested"`). The frontend side is
pinned by `pnpm run gen:api` producing a reviewable diff and by the two already-wrapped
slices' tests (`notifications`, `agent-groups`) passing unmodified after regen.

Key verification that the change is inert:
- `pytest -q` green (response serialization round-trips the enum to the same `.value`).
- `mypy .` green (the `.value`→member mapping change makes the enum-typed fields
  assignable).
- The `openapi.json` diff is enum-additions-only (manual review / grep that no
  `"type": "string"` line that should be an enum remains, and no value strings changed).

## 7. Migration Steps

Built context-by-context; each backend step keeps `pytest -q`, `ruff check`, `mypy .`
green and is a commit milestone. The tree's `openapi.json` is intentionally stale until
the final regen step (the drift gate is checked at the end, not per commit).

1. **identity + admin** — `UserStatus` ×3, `TokenPairOut.token_type` Literal,
   `RateLimitPolicyOut.scope`.
2. **tenancy** — member roles, invite scope_type/role/state (orgs + invites),
   transfer state, project owner_type.
3. **keys** — key/search provider + test_status, group providers element.
4. **conversation** — sender_type ×2, attachment status/scan_status; define
   `ExportJobStatus` StrEnum and type the two export fields.
5. **workflow** — run state, step state (verify the `ArchivedRunOut.state` archive path).
6. **agents** — model_hint/effort/prompt_strategy/context_mode, tool_type.
7. **knowmap / graphrag** — chunk_strategy, BuildState ×5, document status/scan_status,
   owner_kind ×3.
8. **notification** — `NotificationOut.kind`.
9. **Regenerate the contract** — `export_openapi` + `gen:api`; review the diff is
   enum-only; run `pnpm typecheck` (verifies `notifications`/`agent-groups` still compile
   against the narrowed models) and `pnpm test`; commit `openapi.json` + the api-client;
   `check:openapi-drift` now passes.

## 8. Risks and Rollback

- **Highest risk: constraining a field whose real domain isn't closed** → response
  serialization would raise at runtime. Mitigated by the §5C exclusion list — only fields
  proven closed by a DB CHECK / PG ENUM or an enum-reconstructing read path are converted.
  Two fields flagged for a pre-conversion check: `ArchivedRunOut.state` (uncoerced archive
  dict, `workflows.py:279`) and `GraphRagStatusOut.state` (status-dict source) — confirm
  they only ever hold enum values before typing.
- **mypy assignability** — the `.value`→member mapping change is required for every field;
  missing one leaves a `str`-into-enum assignment that mypy flags (a gate, not a runtime
  bug — caught before commit).
- **Frontend regen ripple** — the two already-wrapped slices consume narrowed models;
  `NotificationOut.kind` narrows to 6 values. Verified inert by `pnpm typecheck` in step 9
  (a narrower type is assignable where the consumer read a string).
- Rollback is `git revert` per context step; the regen commit reverts the contract
  atomically.

## 9. Acceptance Criteria

- [x] AC-1: no externally observable behavior change — 1651 backend unit tests pass
      unmodified (incl. API-serialization tests asserting exact JSON values); serialization
      verified wire-identical (`UserOut(status=UserStatus.ACTIVE).model_dump(mode='json')['status']`
      → `'active'`; FastAPI `jsonable_encoder` → `'active'`). The 41 `tests/wiring/*`
      failures are pre-existing sandbox-environment failures (no live DB/Qdrant/Neo4j/SMTP;
      `socket.gaierror` at fixture setup), not regressions — see D-5.
- [x] AC-2: every SAFE field in §5B is typed as its domain enum / `Literal` and its
      handler mapping passes the enum member (not `.value`); `mypy .` shows 39 pre-existing
      errors, 0 introduced (none in touched files); `pytest -q` unit suite green;
      `ruff check .` clean. (`ruff format --check .` has pre-existing debt — see D-4.)
      Commit 9915a1d (19 files, 127+/96-).
- [x] AC-3: `backend/openapi.json` regenerated (commit fe6c462, 301+/86-); diff is
      enum-additions-only — `type: string` → `enum: [...]` (plus new `ExportJobStatus`),
      zero changed value strings, paths, or field sets; `token_type` stays `required`
      (see D-1).
- [x] AC-4: `frontend/src/shared/api-client` regenerated via `pnpm run gen:api` (commit
      da3ac75, 58 files) — 23 new named enum model files (`UserStatus`, `SenderType`,
      `RunState`, `BuildState`, `AgentToolType`, `NotificationKind`, …) plus narrowed
      inline unions; working tree clean for the regenerated paths, so `check:openapi-drift`
      is satisfied.
- [x] AC-5: `pnpm typecheck` clean and `pnpm test` green (422 pass) — the already-wrapped
      `notifications` and `agent-groups` slices compile and pass unmodified against the
      narrowed models.
- [x] AC-6: the §5C not-converted fields remain `str`, each recorded as a Follow-up
      (FU-9..FU-14); no field outside the SAFE list was touched.

## 10. SRS Delta

None — schema precision only; the documented behavior ([R24.13] wrap program) is
unchanged, this enables it.

## 11. Deviation Log

- D-1: **`TokenPairOut.token_type` kept required via `cast`.** Giving it a default
  (`= "Bearer"`) dropped it from the schema's `required` array — a contract change beyond
  "enum-only". Corrected to `token_type: Literal["Bearer"]` (no default, required) with the
  mapping `token_type=cast(Literal["Bearer"], pair.token_type)`, restoring the enum-only
  diff.
- D-2: **Five `cast(Literal[...], str)` sites** — `TokenPairOut.token_type`,
  `InviteOut.role` (orgs + invites, ×2), `owner_kind` (graphrag, ×3). The mapping source is
  a raw domain `str` (no enum member to pass), so mypy needs the cast; Pydantic still
  validates the value at runtime against the DB-CHECK / PG-ENUM-guaranteed set, so the cast
  asserts only what the storage layer already enforces. Each is commented at the site.
- D-3: **Eight per-context phases committed as 3 grouped commits** (backend enums 9915a1d /
  openapi regen fe6c462 / api-client regen da3ac75) rather than one commit per §7 step.
  Verification was holistic — `mypy`/`ruff`/`pytest`/`typecheck` run once over the full
  change — and the contract regen is atomic; per-context commits would falsely imply
  independent per-phase verification of an inseparable schema change.
- D-4: **`ruff format --check .` and `mypy .` are RED on `main` with pre-existing debt**
  (39 mypy errors across 22 files; unformatted files) — none in the files this task
  touched. Verified the change adds 0 new mypy errors and all touched files are
  format-clean. Not fixed here (out of scope; would sweep unrelated files).
- D-5: **41 `tests/wiring/*` integration tests fail in this sandbox** for lack of a live
  DB/Qdrant/Neo4j/SMTP (`socket.gaierror` at fixture DB connect) — a pre-existing
  environment limitation, not a regression. 1651 unit tests (incl. API serialization) pass.
  Step 5.4 behavioral verification via live stack is therefore N/A here — and moot, since
  no user-visible behavior changed (wire values byte-identical).
- D-6: **Security audit (Step 5.6) N/A** — schema-precision only; no authz, input
  validation, secret handling, or WebSocket surface changed; response values are unchanged
  (if anything, stricter output validation). Quality audit (Step 5.5) done as a self-review
  of the 19 mechanical backend files (generated files excluded): uniform enum retyping, no
  abstraction leak (domain enums are value types, matching the existing request-model
  import pattern), no silenced typechecker, no dead code.
- D-7: **PowerShell UTF-16 gotcha handled.** `python -m ... > openapi.json` under
  PowerShell writes UTF-16 (2× size, breaks the JSON consumer); regenerated via a direct
  `open(..., encoding='utf-8', newline='\n')` write to keep the artifact UTF-8/LF.

## 12. Follow-ups

- FU-1: **`conversation` slice wrap** (the immediate next [R24.13] increment) — now a
  clean frontend-only drop-in; also handle `ObservationOut.release_target` locally
  (keep the discriminated `ReleaseTarget` type via `Omit<ObservationOut,...>`) until FU-6.
- FU-2..FU-8: the remaining slice wraps (`admin`, `keys`, `tenancy`, `identity`,
  `workflow`, `agents`, `prompt-studio`), each now unblocked by this sweep.
- FU-9: **Harden captcha config** — validate `mode`/`provider` against an allowlist in
  `captcha.public_config()` so the response can later be enum-typed; until then the
  identity-slice wrap keeps a local union or accepts `string` for these two fields.
- FU-10: **Audit `resource_type`/`action`** — decide whether these should become
  enums (would require an emitter-side registry) or stay free-form; the admin-slice wrap
  keeps them `string`.
- FU-11: **Introduce a `WakeupTrigger` enum** as the single source for
  `ObservationOut.trigger`, then enum it.
- FU-12: **Enforce `TriggerType` at workflow save** (linter/validation) and drop the `""`
  fallback, then enum `RunOut.trigger_type`.
- FU-13: **`ObservationOut.release_target`** — replace the `dict` with a nested
  discriminated Pydantic model so the client gets a typed union instead of
  `Record<string,any>`.
- FU-14: **`orchestration.py` response models** — the router returns raw `dict[str,Any]`
  (approval mode/state, vote, instruction/instance state are untyped in OpenAPI);
  introduce `*Out` models (a larger change than this sweep) so the workflow slice's
  orchestration reads get typed.
</content>
