---
type: feature
status: implemented
created: 2026-07-23
requirements: [R30.17, R30.19]
depends_on: [2026-07-23-activity-in-process-validators]
---

# Raw JSON-Schema editor for activity payload schemas

## 1. Summary

The activity-type authoring form builds `payload_schema` only through the guided
`SchemaBuilder`, which is deliberately capped at a flat object of scalar fields
(string/number/integer/boolean) — nested objects, arrays, enums, and constraints are not
expressible (authoring dossier §2 non-goals, OQ-1). This feature adds a raw/advanced
JSON-Schema editing mode alongside the builder, so an owner who needs a richer schema can
author it directly. The server already validates schema well-formedness
(`PayloadSchemaInvalid` 422), so the editor need not re-implement validity. Follows up FU-5 of
`docs/tasks/2026-07-23-activities-type-authoring-ui/`.

## 2. Goals and Non-goals

**Goals**
- A mode toggle in `ActivityTypeForm` between "Builder" and "Raw JSON" for `payload_schema`.
- Raw mode: a JSON editor (reuse `SCodeEditor` + `codeMirrorJson`) with client-side
  parse feedback; the assembled object is submitted and server-validated as today.
- Nested/complex schemas are expressible in raw mode; the builder stays flat.

**Non-goals**
- A full visual editor for nested schemas — raw JSON is the escape hatch for anything the flat
  builder can't do.
- Changing server-side schema validation (`validate_schema_wellformed`) or the submission
  renderer (the in-chatroom `SchemaForm` already degrades unknown field kinds to a JSON field
  — `slices/activities/components/schemaFields.ts:27-45`).

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Round-trip between modes? | **(a) One-way builder→raw, builder locked once raw is edited.** Builder seeds the raw editor on first switch; once the raw value has been edited, the Builder toggle is disabled for that type (switching back is blocked). No flat-representability check needed. | Makes silent loss of a nested schema impossible rather than merely guarded — the simplest design that satisfies AC-3's "no silent nested-schema loss". |
| Q-2 | Editor component? | **Recommend `SCodeEditor`** (`shared/ui/SCodeEditor.vue` + `codeMirrorJson.ts`), already used elsewhere (e.g. the MCP tool config in `AgentToolsView`). | Reuse over a bare `<textarea>`; gives JSON syntax highlighting + the existing editor UX. |
| Q-3 | Client-side JSON-Schema validity, or defer to server? | **Defer structure to server** (`PayloadSchemaInvalid` 422 already surfaced); client only checks JSON *parse* validity for immediate feedback. | The builder already relies on server validation (authoring §5); no need to bundle a JSON-Schema validator. |
| Q-4 | Can this build concurrently with `2026-07-23-activity-in-process-validators`? | **No — overlap prerequisite.** During build (2026-07-23) that task was `in-progress` with uncommitted working-tree changes on every file this feature must edit: `ActivityTypeForm.vue`, `locales/en.json`, `locales/zh-TW.json`, `__tests__/ActivityTypeForm.test.ts`. `depends_on` amended to `[2026-07-23-activity-in-process-validators]`. | Building on top of its uncommitted diff would sweep half-finished work into this task's commit (CLAUDE.md discipline) and risk clobbering the concurrent session's edits. No logical dependency — pure file overlap. |

## 4. Current State

- `SchemaBuilder.vue` emits `{type:'object', properties, required?}` from flat scalar rows and
  is the only `payload_schema` authoring path; `ActivityTypeForm` binds it via
  `@update:model-value` (authoring implementation).
- `types/schemas.ts` `payloadSchema` Zod check requires an object schema with ≥1 property.
- Server validates well-formedness at register/update; the form surfaces the 422 already.
- `SCodeEditor.vue` + `codeMirrorJson.ts` provide a reusable JSON code editor
  (`shared/ui/__tests__/SCodeEditor.test.ts` exists).

## 5. Design

### Options considered
- **Option A (chosen)** — add a segmented toggle in `ActivityTypeForm` for the
  `payload_schema` field: "Builder" (current `SchemaBuilder`) or "Raw JSON" (`SCodeEditor`
  bound to the same `payload_schema` form field). Both write the same `payload_schema` value;
  submit is unchanged. Round-trip governed by Q-1.
- **Option B** — replace the builder with a raw editor entirely. Rejected: the builder is the
  friendly default for the facilitator audience (authoring Q-2); raw is the power-user escape
  hatch, not the primary path.

### Decision
Option A, finalized with Q-1..Q-3 at approval. The `SchemaBuilder`'s emitted schema seeds the
raw editor on first switch; the raw editor's parsed JSON becomes `payload_schema`. Per Q-1(a),
once the raw value is edited the Builder toggle is disabled for that type (one-way switch), so a
nested schema can never be silently discarded by toggling back.

## 6. Detailed Changes

**Backend** — none.

**API contract** — none.

**Frontend**
- `ActivityTypeForm.vue`: a mode toggle for the `payload_schema` field; render `SchemaBuilder`
  or `SCodeEditor` accordingly; keep the Zod object-with-≥1-property check (raw mode still must
  yield an object schema); parse errors shown inline; server 422 mapping unchanged.
- Possibly extract the schema-field into a small `PayloadSchemaField.vue` wrapping the toggle +
  the two editors, to keep `ActivityTypeForm` readable.
- i18n en + zh-TW for the mode labels + parse-error message.

## 7. NFR Checklist
- [ ] i18n — new strings both locales.
- [ ] Audit log — N/A (authoring only).
- [ ] Tenant isolation — N/A (no new endpoint).
- [ ] Error handling UX — JSON parse error inline; server `PayloadSchemaInvalid` on the field.
- [ ] Performance — N/A.

## 8. Security Considerations

None — no new endpoint or data path. The raw schema is server-validated for well-formedness
exactly as the builder's output; the in-chatroom renderer already treats unknown field kinds
safely (`schemaFields.ts`). No `v-html`/eval of the schema.

## 9. Quality Notes
- Reuse `SCodeEditor`/`codeMirrorJson`; do not add a second JSON editor.
- Keep the builder untouched (it's covered by `SchemaBuilder.test.ts`); the toggle wraps it.

## 10. Risks and Rollback
- Mode round-trip losing a nested schema (Q-1) is the main UX risk — mitigated per Q-1(a) by
  locking the builder once raw is edited (one-way switch). Additive; reverting the form restores
  the builder-only path.

## 11. Acceptance Criteria
- [x] AC-1: An owner switches `payload_schema` to Raw JSON, authors a nested object schema, and
  it registers (server-accepted) and appears in the list. *Unit: `PayloadSchemaField.test.ts`
  "emits the parsed schema for a valid nested value"; `ActivityTypeForm.test.ts` "registers a
  nested payload_schema authored in raw mode". List rendering is unchanged existing behavior.*
- [x] AC-2: An invalid-JSON raw value shows a parse error inline and blocks submit; a
  well-formed-but-schema-invalid value surfaces the server `PayloadSchemaInvalid` (422) on the
  field. *Unit: `PayloadSchemaField.test.ts` "reports a parse error and blocks submit for invalid
  JSON" (emits `schemaInvalidJson` + empty-props schema). The 422 path is the unchanged existing
  server-error mapping (§6); see D-2.*
- [x] AC-3: Switching Builder→Raw seeds the editor with the builder's current schema; the
  round-trip behavior matches the Q-1 decision (no silent nested-schema loss). *Unit:
  `PayloadSchemaField.test.ts` "seeds the raw editor…", "locks the builder once the raw value is
  edited", "opens a non-flat stored schema in Raw mode with the builder locked".*
- [x] AC-4: new strings resolve en + zh-TW; lint passes. *7 keys added to both locales;
  `pnpm lint` (all 12 gates) and `pnpm typecheck` clean.*

## 12. Test Plan
- Frontend component: toggle renders `SCodeEditor` in raw mode; invalid JSON blocks submit;
  a valid nested schema is submitted as `payload_schema`; Builder→Raw seeding works.

## 13. SRS Delta

None — `[R30.17]`/`[R30.19]` (plugin host + generic form) already cover schema authoring;
raw mode is an authoring affordance, not new platform behavior.

## 14. Open Questions
- OQ-1: Should the builder eventually gain nested/array support, or is raw JSON the permanent
  home for anything non-flat? (Carried from the authoring dossier's OQ-1.)

## 15. Deviation Log

- D-1: The raw-mode parse error is surfaced through the parent `SFormField` via an
  `update:parseError` emit (a `schemaInvalidJson` i18n key), which `ActivityTypeForm` maps into a
  mode-aware `payloadSchemaError` computed that takes precedence over the `schemaEmpty` message.
  §6 said only "parse errors shown inline"; this keeps the parse message from being shadowed by
  the field's empty-schema message while `SCodeEditor`'s own lint gutter still marks the position.
- D-2: AC-2's "server `PayloadSchemaInvalid` (422) on the field" is delivered by the pre-existing
  422 mapping (`applyServerErrors` then the `configRejected` toast), which §6 froze as
  "unchanged". Whether the 422 lands on the field or as a toast depends on that existing mapping,
  not on this task — recorded as FU-1 rather than changed here.
- D-3: Built out of the originally-approved order. At build time the overlapping in-progress task
  `2026-07-23-activity-in-process-validators` held uncommitted edits on every shared file, so this
  task paused, took `depends_on: [2026-07-23-activity-in-process-validators]` (Q-4), and resumed
  on a clean tree after that task reached `implemented`.

## 16. Follow-ups

- FU-1: Confirm the server `PayloadSchemaInvalid` (422) path lands on the `payload_schema` field
  (not only a generic `configRejected` toast). Out of scope here — §6 froze the 422 mapping as
  unchanged; revisit if field-level 422 surfacing is wanted.
