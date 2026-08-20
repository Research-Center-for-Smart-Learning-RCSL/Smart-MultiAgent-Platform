---
type: feature
status: approved
created: 2026-08-20
requirements: [R5.05, R8.08, R13.04, R14.10, R15.10, R15.18, R16.06]
depends_on: []
---

# Room-Scoped Orchestration Reads

## 1. Summary

Every orchestration and workflow read endpoint gates on bare project membership. Two
things follow. First, the workflow backstage trace is readable by any project member,
which contradicts [R14.10] outright ("visible to Admin + Project Owners in a dedicated
backstage panel"). Second, orchestration rows that belong to a specific chat room —
approvals and agent instances both carry a `chatroom_id` — are readable by project members
who cannot open that room, including members of an `allow_project_owners_only` room today
and, once Member Groups land, members of a different group.

The fix is a dual track, decided with the user: a record that names a chat room is gated by
that room's ACL, exactly as the room's messages are; a record that names none is backstage
and follows [R14.10].

## 2. Goals and Non-goals

**Goals**

- A record carrying a `chatroom_id` is readable exactly by the people who may read that
  room (`_satisfies_room_flags`), so the gate automatically tracks every room tier,
  including the Member Group tier when it ships.
- The workflow backstage (definitions, runs, steps) matches [R14.10]: Admin and Project
  Owners.
- No in-room surface regresses. The chat room's own approval card must keep working for an
  ordinary member of that room.

**Non-goals**

- **The agent DLQ route.** `GET /api/orchestration/agents/{agent_id}/dlq` has no room
  reference, and `DlqViewer` is rendered inside `ChatroomSettingsView.vue:547`, whose
  audience has not been established. Left project-gated; §16 FU-1.
- **Instruction payload contents.** An instruction's `payload` JSONB can quote room
  content; nothing here inspects payloads. §16 FU-2.
- **Workflow definitions naming rooms.** A definition's JSONB can reference many chat
  rooms (`workflows.py:160` validates them against the project's room ids). Restricting
  the backstage to Owners covers today's exposure; per-room narrowing of a definition body
  is not attempted. §16 FU-3.
- **Write paths.** `cancel_run` already requires `CHAT_CREATE`
  (`workflows.py:586`) and approval voting is agent-driven. Only reads change.
- **Member Groups.** That is
  `2026-08-20-member-groups-and-room-visibility-isolation`; this dossier calls the same
  shared predicate and inherits the new tier for free.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | How should the project-scoped orchestration reads be narrowed? | Dual track: a record with a `chatroom_id` goes through `resolve_room_access` + `ensure_can_read`; a record without one keeps a project-scoped check. | Verified: `approvals.chatroom_id` (`orchestration/infrastructure/tables.py:79`) and `agent_instances.chatroom_id` (`:167`) are both nullable, and `instructions` (`:111-147`), `workflow_runs`, `workflow_steps` have no room column at all — so a single rule cannot cover them. |
| Q-2 | What happens to the records with no room? | Admin + Project Owner, per [R14.10]. | Found during analysis: [R14.10] (`REQUIREMENTS.md:778`) already says the trace is Admin + Project Owner only, and `workflows.py` grants it to any member (`:332`, `:354`, `:546`, `:569`, `:602`). The no-room half is therefore a defect to restore, not a new rule to invent. |
| Q-3 | Does this depend on the Member Groups dossier? | No. `depends_on: []`. | It calls `resolve_room_access` / `ensure_can_read`, which exist today. If Member Groups lands first, this picks up the new tier automatically; if it lands second, nothing here needs revisiting. The two touch no common file: that dossier changes `chatrooms.py` / `access.py` / `projects.py`, this one changes `orchestration.py` / `workflows.py`. |
| Q-4 | Is the exposure real today, before Member Groups exist? | Yes. | An `allow_project_owners_only` room's approvals are readable by any project member right now: `_assert_project_member` (`orchestration.py:52-65`) is the only gate on `GET /api/orchestration/approvals/{id}`, and it never consults the room. |

## 4. Current State

### 4.1 The gates as they stand

`orchestration.py` has exactly one authorization helper, `_assert_project_member`
(`:52-65`): admin passes, otherwise any non-empty role set on the project is enough. It is
applied at `:223`, `:246`, `:271`, `:293`, `:319`, and on `list_subagent_children`
(`:325`). Every route is id-addressed, and each resolves its project through a service
helper:

| Route | Project resolved via | Room column on the subject row |
|---|---|---|
| `GET /approvals/{approval_id}` (`:209`) | `ApprovalService.resolve_project` → `approvals.get_project_id` (`approval_service.py:536-538`) | **yes** — `approvals.chatroom_id` (`tables.py:79`) |
| `GET /workflow-runs/{id}/approvals` (`:231`) | `resolve_run_project` (`approval_service.py:540-542`) | per approval row |
| `GET /instructions/{id}` (`:257`) | `InstructService.resolve_instruction_project` — via the **issuer agent** (`instruct_service.py:372-383`) | no |
| `GET /chains/{chain_id}/instructions` (`:278`) | `resolve_chain_project` (`:385-396`) | no |
| `GET /workflow-runs/{id}/subagents` (`:304`) | `OrchestrationFacade.resolve_workflow_run_project` (`facade.py:385-390`) | **yes** — `agent_instances.chatroom_id` (`tables.py:167`) |
| `GET /instances/{parent_id}/children` (`:325`) | `SubagentService.resolve_project` (`subagent_service.py:301`) | **yes**, same table |
| `GET /agents/{agent_id}/dlq` | agent's project | no |

`workflows.py` states its own rule in the module docstring, line 4: "project membership for
read (list/get/validate/list_runs/list_steps)", implemented by `_require_member`
(`:101`) at `:332`, `:354`, `:546`, `:569`, `:602`. `workflow_steps`,
`workflow_runs`, `workflow_run_participants` and `workflows` carry no chat-room column
(`contexts/workflow/infrastructure/tables.py:17`, `:45`, `:86`, `:105`).

### 4.2 What the SRS says

- **[R14.10]** (`REQUIREMENTS.md:778`) — the trace "is stored in the DB and visible to
  Admin + Project Owners in a dedicated **backstage** panel. It is **not** surfaced in the
  chat room UI." The code grants it to every project member. This is the deviation Q-2
  restores.
- **[R16.06]** (`:882`) — the Admin UI surfaces workflow traces, sub-agent chains,
  instruction chains, approval histories. Consistent with a backstage framing.
- Nothing in the SRS says who may read an approval or an agent instance directly. That gap
  is what §13's delta fills.

### 4.3 The room-scoped path that already exists

The in-room approval surface does **not** go through `orchestration.py`'s project gate:
`chatrooms.py` imports `ApprovalWithVotesOut` and `approval_with_votes_out` and serves
approvals under the room ACL, backed by `ApprovalService.list_for_chatroom_with_votes`
(`approval_service.py:550-553`). So a correct room-scoped predicate for approvals already
exists in the codebase; this dossier extends it to the id-addressed routes rather than
inventing one.

Frontend consumers to keep working: `ApprovalCard` rendered at `ChatroomView.vue:121` for
any room member, and `DlqViewer` at `ChatroomSettingsView.vue:547`. `slices/workflow/api`
wraps the generated `OrchestrationService` (`frontend/src/shared/api-client/services/OrchestrationService.ts`).

## 5. Design

### Options considered

**Option A — dual track (chosen).** Room ACL when the subject row names a room; [R14.10]
backstage rule when it does not.

**Option B — everything to Owners.** One line per route, no branching. Rejected: it breaks
`ApprovalCard` for ordinary members of a room, which is a surface the product deliberately
has (approvals are answered in the room).

**Option C — mask the room-identifying fields, keep the project gate.** Rejected: the leak
is the record, not the field name. An approval's existence, timing and votes describe
another group's session whether or not `chatroom_id` is serialized.

### Decision

Option A, expressed as one shared helper rather than an inline branch per route:

```
ensure_can_read_orchestration_record(db, principal, *, chatroom_id, project_id)
```

- `chatroom_id is not None` → `resolve_room_access` + `ensure_can_read`. A soft-deleted or
  missing room raises `ChatroomNotFound`, which the route maps to 404 — the same answer the
  caller would get for a record that does not exist, so the branch is not an oracle.
- `chatroom_id is None` → Admin or moderator (`is_moderator_roles` over the project-scoped
  role set), matching [R14.10] and reusing the predicate the conversation context already
  shares between serialization and enforcement (`access.py:48-56`).

For `GET /workflow-runs/{id}/approvals` the subject is a **list** whose rows may name
different rooms, so the rule applies per row: rows the caller may not read are omitted, and
the response is not an error. A caller who is a moderator sees everything, which is the
existing backstage expectation.

`_assert_project_member` (`orchestration.py:52-65`) survives only for the DLQ route
(non-goal) and is otherwise deleted, so the weaker gate cannot be reached for by the next
route added.

**SoC.** `orchestration.py` is a route module and may import
`contexts.conversation.application.access` the way `chatrooms.py` does; the helper itself
lives in `contexts/conversation/interfaces/access.py`, which already re-exports the room
ACL for exactly this cross-context purpose (`knowledge/interfaces/config_access.py`
consumes it the same way). The orchestration context is not modified.

## 6. Detailed Changes

### Backend

- `contexts/conversation/interfaces/access.py` — export
  `ensure_can_read_orchestration_record` (name it for the caller, not the caller's
  context) plus a `filter_readable_by_room` helper for the list case. No new ACL logic:
  both delegate to `resolve_room_access` / `ensure_can_read` / `is_moderator_roles`.
- `contexts/orchestration/application/approval_service.py` — a `chatroom_id` accessor on
  the approval read path if the domain model does not already carry it (verify at build
  time; `ApprovalOut` at `orchestration.py:85-95` does not serialize one today).
- `contexts/orchestration/application/subagent_service.py` — the instance read path must
  expose `chatroom_id` to the route; `AgentInstanceOut` already serializes it
  (`orchestration.py:118`, `:195`).
- `app/api/v1/orchestration.py` — replace `_assert_project_member` at `:223`, `:246`,
  `:271`, `:293`, `:319` and on `list_subagent_children` with the new helper. Keep it for
  the DLQ route only.
- `app/api/v1/workflows.py` — `_require_member` becomes `_require_moderator` at `:332`,
  `:354`, `:546`, `:569` and `:602` ([R14.10]). Update the module docstring, line 4, which
  is currently the authoritative-looking statement of the wrong rule.

### API contract

No request or response model changes; `gen:api` rerun is still required because the
operation descriptions come from the docstrings. Status codes: 403 becomes possible where
200 was returned for a non-owner; 404 where the room is gone. `list_approvals_for_run`
returns a filtered list rather than 403 when only some rows are unreadable.

### Frontend

- `slices/workflow` — the backstage views must not render for a non-owner; check whether
  they already gate on role and add the guard if not. A 403 that only surfaces as an error
  toast is a regression in UX even when the server behaviour is correct.
- `ChatroomView.vue` / `ApprovalCard` — no change expected, because the room-scoped
  listing path is untouched (§4.3). **Verify, do not assume**: if any code path fetches an
  approval by id through `getApproval` for an ordinary room member, the dual track keeps it
  working, and a test should pin that.

### Deploy/config

None.

## 7. NFR Checklist

- **i18n** — no new user-facing strings expected beyond a possible "backstage is owner
  only" empty state; through `$t()` if added.
- **Audit log** — none. Reads are not audited here today and this does not change that.
- **Tenant isolation** — this dossier *is* a tenant-isolation change; every altered route
  is listed in §4.1 and every one gets an AC.
- **Error handling UX** — a member who loses access to a backstage view should meet an
  empty state or a hidden nav item, not a raw 403.
- **Performance** — the room branch adds one `resolve_room_access` per record. For the list
  route that is one per row; batch the room lookups if a run's approval count is large
  (`list_for_run` is already paginated at `orchestration.py:248`).

## 8. Security Considerations

- **The 404/403 split must not leak.** A record in a room the caller cannot read should be
  indistinguishable from a record that does not exist. Return 404 for the room-gated
  branch, and never include the room id or name in the error body.
- **Fail closed on a missing room.** `resolve_room_access` raises `ChatroomNotFound` when
  the room or its workspace or its project is gone; that must deny, not fall back to the
  project check.
- **`chatroom_id` is nullable and `ON DELETE SET NULL`** (`tables.py:81`, `:169`). A
  record whose room was deleted becomes room-less and therefore backstage — Owners only.
  That is the correct reading, and it must be a test, because the alternative (falling back
  to project membership) would silently widen access when a room is deleted.
- **The list route's filtering is confidentiality, not cosmetics.** Omitting unreadable
  rows must happen server-side in the query or immediately after it; the count and any
  pagination metadata must not reveal how many rows were withheld.
- **This closes a live hole**, not a hypothetical one (Q-4): an `allow_project_owners_only`
  room's approvals are readable by any project member today.

## 9. Quality Notes

**Existing debt in touched files:**

- `workflows.py` module docstring, line 4, states the wrong rule authoritatively. Whoever
  reads it next will copy it. Fixing it is part of AC-6.
- `orchestration.py:52-65`'s helper is named `_assert_project_member`, which describes the
  mechanism rather than the intent. Its replacement should be named for the question it
  answers.
- `instruct_service.resolve_instruction_project` resolves through the **issuer agent**
  (`:372-383`) with a comment explaining why that is sufficient. Do not "improve" it; the
  A2A scope guarantee it relies on is real.

**Patterns to follow:** `chatrooms.py` for a route module consuming the room ACL;
`knowledge/interfaces/config_access.py` for a cross-context consumer of
`resolve_room_access` that documents its fail-closed contract in the docstring.

**Reuse inventory:** `resolve_room_access`, `ensure_can_read`, `is_moderator_roles`,
`ChatroomNotFound` / `ForbiddenInRoom` and their existing error mappings,
`get_role_resolver`, `Scope`, `PaginationParams`.

## 10. Risks and Rollback

| Risk | Mitigation |
|---|---|
| An in-room surface silently breaks for ordinary members. | AC-5 and AC-8 exist for exactly this; the Playwright pass drives an ordinary member through a room with a live approval. |
| A backstage view a team relied on becomes owner-only. | It contradicted [R14.10]; call it out in the release note rather than treating the current behaviour as the contract. |
| Rollback. | No migration, no schema change. Reverting the two route modules restores the previous behaviour exactly. |

## 11. Acceptance Criteria

- [ ] AC-1: `GET /api/orchestration/approvals/{id}` for an approval whose `chatroom_id` is
      set is readable by a member of that room and refused (404) for a project member who
      cannot read the room, including an `allow_project_owners_only` room.
- [ ] AC-2: The same route, for an approval with `chatroom_id IS NULL`, is readable only by
      Admin and Project/Org Owners.
- [ ] AC-3: `GET /api/orchestration/workflow-runs/{id}/approvals` omits rows whose rooms
      the caller cannot read, returns 200 rather than 403 when some rows are omitted, and
      exposes no count of what was withheld.
- [ ] AC-4: `GET /api/orchestration/workflow-runs/{id}/subagents` and
      `GET /api/orchestration/instances/{id}/children` apply the same dual track over
      `agent_instances.chatroom_id`.
- [ ] AC-5: An approval whose room was deleted (`chatroom_id` set to NULL by the FK) is
      readable only by Admin and Owners, never by a project member.
- [ ] AC-6: `workflows.py` list/get/validate/list_runs/list_steps require Admin or
      Project/Org Owner ([R14.10]), and the module docstring states that rule.
- [ ] AC-7: `GET /api/orchestration/instructions/{id}` and `/chains/{id}/instructions`
      require Admin or Owner.
- [ ] AC-8: No in-room regression — an ordinary member of a room with a live approval still
      sees and can act on the approval card, verified in a browser or an e2e spec, not by
      reading code.
- [ ] AC-9: `_assert_project_member` has no callers other than the DLQ route.
- [ ] AC-10: Gates green — `pytest -q`, `ruff check . && ruff format --check .`, `mypy .`,
      `pnpm lint`, `pnpm typecheck`, `pnpm test`, `pnpm build`, and
      `pnpm run check:openapi-drift` after `gen:api`.

## 12. Test Plan

| AC | Level | Location |
|---|---|---|
| AC-1, AC-2, AC-3, AC-4, AC-5, AC-6, AC-7, AC-9 | unit (route, fake resolver + fake room access) | `backend/tests/unit/` beside the existing orchestration and workflow route tests |
| AC-1, AC-4 | integration | `backend/tests/integration/` — real rows, a real owners-only room, a real non-member |
| AC-5 | integration | delete the room, assert the FK nulled the column and the gate tightened |
| AC-8 | e2e | `frontend/e2e/` — ordinary member, room with a live approval |
| AC-10 | CI | `/build`'s Definition of Done |

AC-8 is the regression this change is most likely to cause, and it is the one a unit test
cannot see. It must be executed.

## 13. SRS Delta

**(a) Amend [R14.10] (`REQUIREMENTS.md:778`), making the existing rule explicit about what
enforces it:**

> - **[R14.10]** The trace (Q55) is stored in the DB and visible to Admin + Project Owners
>   in a dedicated **backstage** panel. It is **not** surfaced in the chat room UI. This is
>   enforced server-side on every workflow read endpoint (definition, run, step), not only
>   by hiding the panel.

**(b) Insert after the existing [R15.10a] (`REQUIREMENTS.md:829`), in §15.4. The number is
`R15.24` — the next free one — rather than another letter suffix, because `R15.10a` is
already taken by the `can_approve` rule; §13's `R13.20`/`R13.27`/`R13.21` ordering is the
existing precedent for placing a rule by topic rather than by number:**

> - **[R15.24]** An orchestration record that names a chat room — an approval gate bound
>   to a room, an agent instance running in one — is readable by exactly the principals who
>   may read that room under §13.2, evaluated by the same room access check. A record that
>   names no chat room is backstage and follows [R14.10]. A record whose room has been
>   deleted names no room and is therefore backstage. Listings omit records the caller may
>   not read rather than refusing the whole request, and disclose nothing about what was
>   omitted.

## 14. Open Questions

- **OQ-1** — Should a room-bound approval also be visible to a Project Owner who is not in
  the room? Under this design yes, because moderators clear every room tier
  (`_satisfies_room_flags:135-136`). That is consistent with R8.08 and with the rest of the
  platform, and is recorded here only because it is the one place where "room ACL" and
  "backstage" give the same answer for different reasons.

## 15. Deviation Log

Appended by `/build`. Empty means the implementation matches this spec exactly.

## 16. Follow-ups

- **FU-1** — `GET /api/orchestration/agents/{agent_id}/dlq` stays project-gated because
  `DlqViewer` renders inside `ChatroomSettingsView.vue:547` and that view's real audience
  has not been established. Establish it, then decide.
- **FU-2** — An instruction's `payload` JSONB may quote room content while the instruction
  itself names no room. Nothing inspects payloads; a payload-level rule would need a
  provenance field the schema does not have.
- **FU-3** — A workflow definition's JSONB can name many chat rooms
  (`workflows.py:160`). Owner-only backstage covers today's exposure; per-room narrowing
  of a definition body is a separate design.
- **FU-4** — `_require_member` in `workflows.py` and `_assert_project_member` in
  `orchestration.py` are two spellings of one idea, and this dossier leaves one of each.
  A shared, intent-named helper in `shared_kernel.auth.dependencies` would retire both.
