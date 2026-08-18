---
type: feature
status: implemented
created: 2026-08-18
requirements: [R30.21, R30.22, R30.09, R30.30, R30.33, R30.35, R28.02, R15.06]
depends_on: []
---

# Delegating activity start/end from the room creator to a bound agent

## 1. Summary

Today only a room's creator can start or end a structured activity: both routes gate on
`ensure_room_creator` (`backend/app/api/v1/activities.py:685,712`), and the facilitator
control in the chatroom rail is behind `v-if="isCreator"`
(`frontend/src/slices/activities/components/ActivityPanel.vue:271`). Agents read recent
activity but can never act on the activation lifecycle.

This feature lets the room creator delegate that authority, per room and per agent, to
specific bound agents together with the set of activity types the agent may run. A
delegated agent gains two room-scoped tools on its turn (`start_activity`,
`end_activity`); every other path is unchanged, and the room creator keeps unconditional
start/end and can revoke at any moment.

It also updates the shipped example agent packs and the worked-example walkthrough, which
currently describe a world where only the teacher starts a round.

## 2. Goals and Non-goals

**Goals**

- G-1 A room creator can grant one bound agent the authority to start and end activities
  in that room, scoped to a chosen set of activity types, and can revoke it.
- G-2 A granted agent can exercise that authority only through a tool call, never through
  text it emits.
- G-3 Both server-side gates that guard activation today keep applying identically on the
  agent path: reachability (`resolve_reachable_type`) and the platform governance policy
  (`ActivityPolicyService.assert_type_allowed`).
- G-4 An agent-started or agent-ended round is distinguishable in the audit trail and in
  the facilitator's panel from a human-initiated one.
- G-5 The facilitator's per-round progress counts keep reaching the facilitator, unchanged,
  when a round was started by an agent.
- G-6 The shipped example packs and `docs/examples/creative-thinking-course.md` describe
  the delegated flow accurately, including which shipped agents are and are not granted
  and why.

**Non-goals**

- N-1 **No backend rate limit, cooldown, or "not while participants are unfinished" guard**
  on the agent path. Pacing is expressed in the agent's system prompt (Q-2). This is a
  deliberate flexibility decision; §10 R-1 records what it costs.
- N-2 No delegation to a **user** other than the room creator. The room-creator gate is
  untouched for humans.
- N-3 No course schedule, no "next activity in sequence" concept. The agent picks from a
  flat allowlist.
- N-4 No workflow `NodeType` for starting an activity. The workflow engine's node set
  (`backend/contexts/workflow/domain/models.py:21-32`) is unchanged.
- N-5 No change to who may **author** or **edit** activity types (`[R30.23]`), or to the
  admin/platform surfaces.
- N-6 No change to the participant-facing panel. Participants already hydrate from the
  `activity.activation.started` room broadcast (`app/api/v1/activities.py:999-1014`) and
  from the room-scoped active-activation read; both carry the same payload regardless of
  who started the round (verified: `ActivityPanel.vue` reads `store.getActivation` and
  never inspects `startedByUserId` except to decide whether to poll progress,
  `ActivityPanel.vue:46-53`).
- N-7 Pack install still creates no chatroom and no room binding (`[R30.35]`). A pack's
  new field is advisory metadata, never an applied grant.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | How wide is the delegated authority: start only, or start and end? | **Start and end, as one switch.** | The user's call. Ending a round closes every open session for it (`activation_service.py:127`), which is why start-only was offered; the user wants an agent able to run a whole round rather than hand back halfway. One switch rather than two keeps the settings surface and the audit story simple. |
| Q-2 | Should the backend enforce pacing (cooldown between starts, refusal to end while participants are still working)? | **No.** All pacing lives in the agent's system prompt. | Explicit user decision: the flexibility belongs in the prompt, not in a platform rule that every course would have to fit. Recorded as an accepted risk (§10 R-1), not as an oversight. |
| Q-3 | Which activity types may a delegated agent run? | **A per-room, per-agent allowlist the room creator picks when binding the agent.** A pack's `binds_activity_types` seeds the default selection in the UI. | Keeps the authority decision with the person who owns the room, and makes "which unit is this agent for" a teaching decision rather than an agent attribute. |
| Q-4 | Where is the grant stored: a companion table keyed per type, or columns on `chatroom_agents`? | **Columns on `chatroom_agents`: `may_control_activities boolean` + `activity_type_allowlist jsonb`.** | The user's call, taken over the companion-table option. §5 closes the three inconsistency states it admits (a CHECK constraint for two of them, read-time resolution for the third) so the weaker shape does not become a weaker invariant. |
| Q-5 | Does the teacher have to enable a tool on the agent as well as grant the room? | **No — one step. The grant alone supplies the tools.** | Two switches in two places produce a "nothing happened, which one is off?" failure the teacher cannot diagnose. Costs a deliberate departure from the pattern where every built-in tool comes from an `agent_tools` row; §5 states it. |
| Q-6 | May an `observer`-role agent hold the grant? | **Yes, permitted; discouraged in the binding UI and in the docs.** | The asymmetry is real (an observer is silent to the class per `[R28.02]` yet would take a class-visible action), but the user wants the option available for a silent scheduling agent. The UI states the asymmetry at the moment of granting. |
| Q-7 | Should this depend on `2026-07-19-large-artifacts-silently-dropped`, which is `status: in-progress` and touches the same two files? | **No. `depends_on: []`.** | Verified: that dossier's code landed (`d038814`, `1cb730d`, `19ab5ce`); `_hydrate_oversized` and `ARTIFACT_SKIP_KEY` are in the tree (`builtin_tools.py:306`, `turn_engine.py:1784`). Only its AC-2 is unticked, and it is a behavioural check needing Docker, not outstanding code. No concurrent build, so no overlap prerequisite. |

## 4. Current State

### Who may start and end

- `POST /api/chatrooms/{id}/activity-activations` calls `ensure_room_creator`
  (`backend/app/api/v1/activities.py:685`); `PATCH .../{activation_id}/end` does the same
  (`:712`). The facilitator progress read does too (`:826`).
- `is_room_creator` (`backend/contexts/conversation/application/access.py:194-213`) is
  `created_by_user_id` equality **plus** a live project/org role, with a moderator fallback
  for pre-0041 rooms and an admin bypass. Pure guests are excluded explicitly (`:208`).
- `[R30.21]` states the rule in the SRS: *"Starting is gated by room-creator capability —
  strictly stronger than the send-message floor"* (`REQUIREMENTS.md:2180`).

### What an agent can do with activities today

- Read only. `ActivityContextProvider.query`
  (`backend/contexts/activities/application/activity_context_provider.py:38-55`) returns a
  `[Recent room activity]` block for a turn.
- The agent tool set is assembled in
  `build_agent_tools` (`backend/contexts/agents/application/runtime/builtin_tools.py:857-916`)
  by dispatching on each enabled `agent_tools` row's `tool_type`. Nothing in that set
  touches room state beyond the sandbox and the agent's own wake-up config.
- Reserved built-in names live in `BUILTIN_TOOL_NAMES`
  (`backend/contexts/agents/application/runtime/tool_registry.py:119-129`); a drift test
  asserts every built built-in is listed there (comment at `:113-118`).
- The workflow engine has no activity action node
  (`backend/contexts/workflow/domain/models.py:21-32`).

### The room-agent binding

- `chatroom_agents` carries exactly `chatroom_id`, `agent_id`, `role`
  (`backend/contexts/conversation/infrastructure/tables.py:59-79`); `role` is the PG enum
  `chatroom_agent_role` created in migration 0041, mirrored as `pg.ENUM(..., create_type=False)`.
- `ChatroomAgentRepository` has `add` / `remove` / `list` / `shared_room_by_agent`
  (`backend/contexts/conversation/infrastructure/repositories/chatroom_repo.py:261-330`).
- Binding, role change and observer unbinding are room-creator-gated
  (`backend/app/api/v1/chatrooms.py:489-493`, `:518-519`, `:552`).
- The UI is `ChatroomSettingsView.vue` (role picker at `:510`, observer note at `:523`)
  driven by `useChatroomBindings.ts` (`onSetRole` at `:112`, `saveWakeupConfig` at `:149`).

### The activation record and its broadcasts

- `activity_activations.started_by_user_id` is `NOT NULL` with an FK to `users`
  (`backend/contexts/activities/infrastructure/tables.py:109-114`).
- `ActivationService.start`
  (`backend/contexts/activities/application/activation_service.py:52-106`) resolves the
  type through `resolve_reachable_type` (`:64`), then applies the governance policy at
  `:76`, then inserts. A partial-unique conflict resolves by returning the existing
  activation when the type matches (`:83-89`), so a repeat start of the same type is
  idempotent.
- `ActivationService.end` (`:108-147`) closes every open session for the round (`:127`)
  and reports `transitioned` so a repeat end is a no-op, not a replayed event.
- `_dispatch_activation_started` and `dispatch_activation_ended` live in the API layer
  (`app/api/v1/activities.py:999`, `:1071`), so no context can call them.
- `_dispatch_activation_progress` (`:1036-1068`) publishes the facilitator's counts to
  `user_channel(activation.started_by_user_id)`, and its docstring records that a dropped
  event does **not** self-heal for that user (`:1046-1052`).

### The turn's commit boundary and its post-commit seam

- Built-in tools write on the turn's own session and do not commit: `update_wakeup` calls
  the orchestration facade and returns
  (`tool_registry.py:356-379`); the audit helper documents sharing the turn's session
  (`builtin_tools.py:649-651`).
- `_post_commit` (`turn_engine.py:632-...`) is the established scope for bookkeeping that
  must run after the turn's outcome is durable and must not be able to rewrite it. It is
  used for artifact persistence, `message.created`, `agent.finished`, the workflow signal,
  reply wake-ups and approval settlement (`turn_engine.py:2921-2957`).
- The **sink** pattern is already in use for exactly this shape of problem: `artifact_sink`
  is a list passed into `build_agent_tools` (`builtin_tools.py:864`), appended to during
  tool invocation (`:259`), and drained by the engine after the commit (`:2921`).
  `voted_approvals` is the same pattern for approvals (`turn_engine.py:1139`, `:2957`).
- `_builtin_tools` is the engine's assembly point and already receives `chatroom_id`
  (`turn_engine.py:1386-1427`).

### Example packs and the walkthrough

- `_AGENT_FIELDS` in
  `backend/contexts/agents/infrastructure/examples/catalogue.py:43-54` is a strict closed
  set: missing **or unknown** fields both raise (`:116-122`).
- `binds_activity_types` is parsed (`:189-197`) and deliberately not resolved against the
  course catalogue; the cross-check is `backend/tests/unit/test_agent_example_packs.py`
  (module docstring at `:9-14`).
- `creative-thinking-room.json` ships TA (`normal`, `every_n_messages` n=1), SA (`normal`,
  silence-triggered) and AA (`observer`); `creative-thinking-design.json` ships DA
  (`room_role: null`, both triggers off).
- DA's prompt currently asserts *"一個討論室同一時間只能有一個進行中的活動。你排的流程若需要
  連續兩個活動，要寫明教師必須先結束前一個"*
  (`creative-thinking-design.json:26`), which this feature makes half-false.
- `docs/examples/creative-thinking-course.md` states the facilitator-only flow in "Running
  a session" steps 2 and 7 (`:319-320`, `:337`) and in the "One active activity per room"
  limitation (`:380-382`).

## 5. Design

### Options considered

**Option A — grant on the room binding, tools supplied by the grant.** A per-room,
per-agent grant on `chatroom_agents`; the turn engine reads it and, when present, builds
two room-scoped tools. Authority is a room fact; capability is derived from it.

**Option B — a new `AgentToolType` the owner enables per agent.** Consistent with every
other built-in. But the authorising actor becomes "whoever may edit the agent" rather than
the teacher who owns the room, which contradicts the entire room-creator chain this feature
extends; and the same agent could not be a leader in one class and an assistant in another.

**Option C — the agent emits a directive in its message text, parsed server-side.** Rejected
outright: any participant can type that string, the agent can be induced to repeat it, and
the platform would then act on it. This is a prompt-injection path to a class-wide action.

**Option D — a new workflow node type.** The reactive-rules path (`[R30.13]`, `[R30.14]`)
already carries activity signals. But it moves the decision from the agent's judgement into
a DAG the teacher must author, which is not what was asked, and workflow has no node that
mutates room state today.

### Decision

**Option A**, with the grant stored per Q-4 and the tools supplied per Q-5.

What that gives up, stated plainly:

- Consistency with the `agent_tools` pattern. Two tools now exist that no `agent_tools` row
  produces. The mitigation is that they are still ordinary `Tool` objects in the same
  registry, still reserved in `BUILTIN_TOOL_NAMES`, and still bounded by the same
  per-turn tool-round cap; only their *source* differs, and `build_agent_tools`'s docstring
  will say so.
- Option B's ability to grant once for every room an agent joins. Deliberate: that is the
  authority-in-the-wrong-hands problem, not a convenience.

### The grant record

> **Amended at implementation — see D-11.** `ck_chatroom_agents_activity_grantor` is
> **not shipped**: it would abort an admin's GDPR hard-delete of any user who had ever
> granted activity control, with no way to clear the grant first. The invariant is
> enforced at read time instead. The paragraph below is kept as written for the record;
> read D-11 with it.

Migration `0078`, on `chatroom_agents`:

```
may_control_activities   boolean  NOT NULL DEFAULT false
activity_type_allowlist  jsonb    NOT NULL DEFAULT '[]'::jsonb
granted_by_user_id       uuid     NULL REFERENCES users(id) ON DELETE SET NULL
CONSTRAINT ck_chatroom_agents_activity_grant
  CHECK (may_control_activities = false OR jsonb_array_length(activity_type_allowlist) > 0)
CONSTRAINT ck_chatroom_agents_activity_grantor
  CHECK (may_control_activities = false OR granted_by_user_id IS NOT NULL)
```

The three inconsistency states this shape admits (raised when the option was chosen) are
each closed:

1. *granted but the allowlist is empty* — refused by `ck_chatroom_agents_activity_grant`.
2. *not granted but an allowlist remains* — permitted and harmless by design: it preserves
   the teacher's selection across a revoke/re-grant. Every read path checks
   `may_control_activities` first, and the write route clears nothing, so the residue is a
   remembered setting rather than latent authority. A test pins that a revoked grant with a
   non-empty allowlist supplies no tools.
3. *an allowlist id pointing at a deleted or unreachable type* — closed at read time, not
   in the schema. Every id is resolved through
   `ActivitiesFacade.resolve_type_for_project` before it can reach the model or the service;
   an id that does not resolve is dropped from the offered set and logged. This is the same
   gate the HTTP path applies (`activities.py:610-612`), so a stale id degrades to "that
   type is not offered", never to a cross-tenant or a deleted-type activation.

`granted_by_user_id` is the user on whose authority the agent acts, and is what the
activation records as `started_by_user_id` (see below). `ON DELETE SET NULL` combined with
`ck_chatroom_agents_activity_grantor` cannot leave a live grant with a null grantor: the
delete would violate the CHECK. Deleting a granting user therefore fails loudly rather than
silently producing an unattributable grant — acceptable because user deletion is already an
admin operation, and a grant that cannot name its grantor must not run.

Per the ORM/migration type rule in `backend/CLAUDE.md`, `tables.py` declares
`pg.JSONB` and `sa.Boolean`, matching the migration exactly.

### The activation record

Migration `0078` also adds to `activity_activations`:

```
started_by_agent_id  uuid  NULL     -- no FK, deliberately
```

- **`started_by_user_id` stays `NOT NULL` and records the granting teacher.** Two reasons.
  First, `_dispatch_activation_progress` addresses the facilitator's counts to
  `user_channel(started_by_user_id)` and has no self-healing poll for that recipient
  (`activities.py:1046-1052`); making the column nullable or agent-valued blinds the
  teacher's panel. Second, an agent acts on delegated authority, so the answer to "who is
  answerable for this round" is the person who delegated it.
- **`started_by_agent_id` carries no foreign key**, matching how `activity_types.validator_config`
  holds an `agent_id`/`binding_id` without one and validates them at the route
  (`activities.py:387-414`). `[R30.05]` forbids the activities context from importing the
  agents context; an FK would put the same coupling in the schema. A deleted agent leaves a
  historical id that resolves to nothing, which is the correct reading of "the agent that
  started this no longer exists". The direction is already enforced by a static tripwire:
  `backend/tests/unit/test_activities_no_agents_import.py` fails on any
  `contexts.agents` import anywhere under `contexts/activities`, so the column must stay a
  bare `uuid` and the name resolution must stay at the route.

`ActivationService.start` and `.end` take an optional `started_by_agent_id` and add it,
plus `via: "agent_tool"`, to the `activity.activation_started` / `activity.activation_ended`
audit metadata.

### The tools

New module `backend/contexts/agents/application/runtime/activity_tools.py` — separate from
`builtin_tools.py` so the activities dependency stays isolated and that file does not grow
another 150 lines.

```
start_activity(activity_type_key: enum[<allowed keys>])
end_activity()
```

- The `activity_type_key` schema is an **`enum` of the resolved allowed keys**, built at
  turn-assembly time. The model therefore cannot name a type outside the allowlist even in
  a malformed call, and no client-supplied UUID crosses this boundary at all. The tool
  description lists each allowed key with its display name.
- `end_activity` takes no arguments: it ends the room's one active activation. It refuses
  when that activation's type is **not** in the allowlist — an agent trusted with unit 2
  must not be able to cut short a unit 4 round the teacher started.
- Both go through `ActivitiesFacade`, so `resolve_reachable_type` and
  `assert_type_allowed` apply identically to the HTTP path (G-3). No second code path
  exists for the two gates.
- Both write on the turn's session and do **not** commit, exactly like `update_wakeup`
  (`tool_registry.py:362`). They follow the `builtin_tools` conventions: catch-all except
  that returns an `is_error` `ToolResult`, `_reraise_if_infrastructure` first, output
  through `clip_tool_output`, and an `mcp.tool_invoked` audit row via `_audit_tool_invoke`
  with the `_marked_unrecorded` treatment when the row is lost (the tools are
  side-effecting, so the existing "do not repeat the call" notice applies unchanged).
- Names `start_activity` and `end_activity` are added to `BUILTIN_TOOL_NAMES`
  (`tool_registry.py:119`), which the existing drift test in `test_builtin_tools_wiring`
  requires of any built built-in.

### Wiring and the post-commit broadcast

- `TurnEngine._builtin_tools` (`turn_engine.py:1386-1427`) resolves the grant for
  `(chatroom_id, agent.id)` through a new `ConversationFacade.activity_control_grant(...)`
  — **through the facade**, not `ChatroomAgentRepository` directly; see §9.
- Grant absent, `chatroom_id` absent (the A2A path), or every allowlisted id failing to
  resolve ⇒ no tools built. Failing closed on a grant read error is required: an error must
  not be read as "granted".
- `build_agent_tools` gains an `activity_control` parameter and an
  `activation_event_sink: list[dict] | None`, mirroring `artifact_sink`
  (`builtin_tools.py:864`). The tools append one descriptor per successful start/end.
- The engine drains the sink inside `_post_commit("activity activation events")` — after
  the commit, never before, because the broadcast must not tell a room about an activation
  that is still in an open transaction. Drain sites are every commit that can follow the
  tool loop: the reply commit (`turn_engine.py:~2914`), the empty-reply skip commit
  (`~2805`), the observer commit (`~2875`), and the A2A completion commit (`:1340`). The
  failure path rolls back, so the sink is discarded there and nothing is published.
- The drain calls the relocated dispatch helpers.

### Relocating the dispatch helpers

`_dispatch_activation_started` and `dispatch_activation_ended` move from
`app/api/v1/activities.py` to `contexts/activities/interfaces/broadcast.py`, re-exported
from the context's `interfaces` package. Both only need `Publisher` and `room_channel`, and
`room_channel` is already exported from `contexts/conversation/interfaces/__init__.py:12`,
so the move introduces no new cross-context edge. The route keeps calling them; the turn
engine now can too. `_dispatch_activation_progress` moves with them, because an agent-ended
round must still refresh the teacher's counts.

### The facilitator's view

`ActivityActivationOut` gains `started_by_agent_id` and `started_by_agent_name`. The name is
resolved at the route through `AgentsFacade`, the batch-facade-read pattern `[R30.31]`
already mandates for cross-context display attributes — never a SQL join (`[R30.09]`). It is
`null` for a human-started round. The same two fields ride the
`activity.activation.started` payload. No confidentiality question arises: an agent bound to
a room is already named on every message it sends.

## 6. Detailed Changes

**Backend — `contexts/conversation`**

- `domain/models.py`: `ChatroomAgent` gains `may_control_activities: bool = False`,
  `activity_type_allowlist: tuple[uuid.UUID, ...] = ()`, `granted_by_user_id: uuid.UUID | None = None`
  (defaulted, so the ~existing construction sites keep describing the case they describe).
  New frozen dataclass `ActivityControlGrant(agent_id, granted_by_user_id, activity_type_ids)`.
- `infrastructure/tables.py`: three new columns on `chatroom_agents`.
- `infrastructure/repositories/chatroom_repo.py`: `ChatroomAgentRepository.set_activity_grant(...)`
  and `activity_control_grant(chatroom_id, agent_id) -> ActivityControlGrant | None`
  (returns `None` when `may_control_activities` is false); `list` reads the new columns.
- `application/chatroom_service.py`: `set_agent_activity_grant(...)` with the audit emit.
- `interfaces/facade.py`: `activity_control_grant(...)` and `set_agent_activity_grant(...)`.

**Backend — `contexts/activities`**

- `domain/models.py`: `ActivityActivation` gains `started_by_agent_id: uuid.UUID | None = None`.
- `infrastructure/tables.py` + `repositories/activation_repo.py`: the new column, written by
  `create_active`, read by `get`/`get_active`.
- `application/activation_service.py`: `start`/`end` take `started_by_agent_id`, thread it
  into the row and the audit metadata.
- `interfaces/facade.py`: the same parameter on `start_activation` / `end_activation`.
- **New** `interfaces/broadcast.py`: `dispatch_activation_started`,
  `dispatch_activation_ended`, `dispatch_activation_progress`, moved verbatim from the
  route with their docstrings.

**Backend — `contexts/agents`**

- **New** `application/runtime/activity_tools.py`: `build_activity_control_tools(db, *, agent, grant, allowed_types, chatroom_id, event_sink) -> list[Tool]`.
- `application/runtime/builtin_tools.py`: `build_agent_tools` gains `activity_control` and
  `activation_event_sink`; docstring records that these two tools come from a room grant
  rather than an `agent_tools` row.
- `application/runtime/tool_registry.py`: `BUILTIN_TOOL_NAMES` gains `start_activity`,
  `end_activity`.
- `application/runtime/turn_engine.py`: grant resolution in `_builtin_tools`; sink creation
  beside `artifact_sink`; a `_drain_activation_events` helper called under `_post_commit` at
  the four commit sites named in §5.
- `application/agent_service.py`: the reserved-name guard derives from `BUILTIN_TOOL_NAMES`
  already (comment at `tool_registry.py:113-118`) — verify no second literal list needs
  updating.
- `infrastructure/examples/catalogue.py`: `_AGENT_FIELDS` gains `may_control_activities`;
  `PackAgent` gains the field; `_parse_agent` requires a boolean (a new `_require_bool`
  mirroring the course catalogue's, `activities/.../catalogue.py:122-127`).

**Backend — migration**

- `alembic/versions/0078_agent_delegated_activity_control.py`. Single transaction, both
  tables, both CHECK constraints. Downgrade drops the columns and constraints.

**API contract** — `gen:api` rerun required: **yes**.

- **New** `PATCH /api/chatrooms/{chatroom_id}/agents/{agent_id}/activity-control`,
  `ensure_room_creator`, body `{granted: bool, activity_type_ids: [uuid]}`, 204.
  Validates every id through `ActivitiesFacade.resolve_type_for_project` for the room's
  project before writing (cross-context call at the route, the precedent being
  `_assert_mcp_binding_in_project`, `activities.py:387-414`); an unresolvable id is a 422.
  `granted: true` with an empty list is a 422, matching the CHECK.
- `AgentRef` (the `GET /api/chatrooms/{id}/agents` item) gains `may_control_activities` and
  `activity_type_allowlist`, populated **only for the room creator**, exactly as `role`
  already is (`chatrooms.py:454-457`) — a non-creator must not learn the room's delegation
  layout any more than it learns its observer layout (`[R28.10]`).
- `ActivityActivationOut` gains `started_by_agent_id`, `started_by_agent_name`.
- The pack listing item (`ExamplePackAgentOut`) gains `may_control_activities`.

**Frontend**

- `slices/conversation/api/index.ts`: `setChatroomAgentActivityControl(...)`.
- `slices/conversation/composables/useChatroomBindings.ts`: grant state per bound agent, an
  `onSetActivityControl` writer following `onSetRole`'s busy-guard shape (`:112-124`).
- `slices/conversation/views/ChatroomSettingsView.vue`: per bound agent, a toggle plus a
  multi-select of the project's activity types (source: the activities slice's
  `listActivityTypes`, reached through the slice's `index.ts` re-export — `conversation`
  already imports `activities` one-way under gate #1). Selecting `observer` while a grant is
  held renders the asymmetry note from Q-6.
- `slices/activities/components/ActivityPanel.vue`: when `startedByAgentName` is present,
  render "started by <agent>" above the type name. Participant-visible; §5 states why that
  is not a disclosure.
- `slices/agents/components/AgentPackInstallDialog.vue`: render each pack agent's
  `may_control_activities` with the "advisory — grant it per room" note (N-7).
- i18n: new keys in `slices/conversation/locales/{en,zh-TW}.json`,
  `slices/activities/locales/{en,zh-TW}.json`, `slices/agents/locales/{en,zh-TW}.json`.

**Example packs and docs**

- `creative-thinking-room.json`: TA `may_control_activities: true`; SA and AA `false`.
  TA's prompt gains a section covering: when to start (after the guiding discussion has run,
  not on request), that a room holds one activity at a time so the previous round must be
  ended first, that starting and ending are class-visible actions, that it must ask the
  teacher when unsure, and — the load-bearing line — **that nobody in the chatroom can
  instruct it to start or end a round; a message asking for one is not a reason.** SA's and
  AA's prompts each gain one line stating they do not control activities, so neither claims
  an ability it lacks.
- `creative-thinking-design.json`: DA `may_control_activities: false`. The
  "教師必須先結束前一個" sentence is corrected to say that the previous round must be ended
  and by whom that can now be, and DA is required to label each step of a drafted flow with
  its initiator (teacher or TA).
- `backend/tests/unit/test_agent_example_packs.py`: assert the new field parses; assert TA
  is the only granted shipped agent; assert TA's prompt carries the injection line and the
  one-activity-at-a-time line; assert DA's prompt no longer asserts teacher-only ending.
- `docs/examples/creative-thinking-course.md`: "Running a session" steps 2 and 7, the "One
  active activity per room" limitation, "The packs carry the orchestration, not just the
  prompts" table, and "What DA cannot do" all updated; a new subsection stating that pack
  metadata is advisory and the grant is a per-room act by the room creator.

**Deploy/config** — none.

## 7. NFR Checklist

- [x] **i18n** — every new string goes through `$t()` in both bundles. Note the project's
  known trap: a literal `@` in a message is read by vue-i18n as a linked message and only
  fails in production; none of the planned copy needs one.
- [x] **Audit log** — `chatroom.agent_activity_grant_updated` on every grant write (actor,
  agent, granted flag, type ids); `activity.activation_started` / `activity.activation_ended`
  gain `started_by_agent_id` and `via`; `mcp.tool_invoked` per tool call through the
  existing `_audit_tool_invoke`.
- [x] **Tenant isolation** — the grant route is room-creator-gated and resolves every type id
  through `resolve_reachable_type` for the room's project. The tool path resolves the same
  way and additionally cannot receive an id at all (enum of keys). No new endpoint returns
  cross-project data.
- [x] **Error handling UX** — the grant control has busy/error states following `onSetRole`.
  A 422 from an unresolvable type id surfaces the existing RFC 7807 message. The tools
  return `is_error` results the model can read, never exceptions.
- [x] **Performance** — one extra indexed single-row read per turn for the grant (keyed on
  the `chatroom_agents` primary key), and one bounded type-resolution per allowlisted id at
  tool-assembly time. The allowlist is bounded by the project's type count; the settings UI
  reuses the already-loaded `listActivityTypes` result, adding no request. No N+1: the
  activation's agent-name resolution is a single batch facade read.

## 8. Security Considerations

- **Prompt injection is the primary threat, and it is why Option C was rejected.** The only
  path from model output to an activation is a structured tool call whose one argument is
  an `enum` of keys the room creator selected. A participant cannot widen that set, and a
  participant's text cannot become an argument value. The residual exposure is that a
  participant may *persuade* a granted agent to call the tool with an allowed key at a bad
  moment; that is bounded by the allowlist and mitigated in the prompt, and the shipped TA
  prompt states the rule explicitly. It is not eliminated — see §10 R-2.
- **Authority is not transitive.** The grant is `(chatroom, agent)`. Binding the same agent
  to another room grants nothing there. Unbinding the agent removes the row and the grant
  with it.
- **The governance policy gate is preserved.** `assert_type_allowed`
  (`activation_service.py:76`) is the only thing that stops an activation once an admin
  locks `expose_payload_to_agent` (`[R30.30]`), and the agent path runs the same service
  method, ordered before the insert, so no violating activation, audit row or broadcast is
  produced.
- **Fail closed on the grant read.** Any error resolving the grant yields no tools. An
  exception must never be read as authorisation.
- **Audit completeness.** An agent-initiated round is attributable to both the agent and the
  delegating user. The `_AUDIT_NOT_RECORDED` treatment already used for side-effecting tools
  applies, so a lost `mcp.tool_invoked` row is reported to the model as an error telling it
  not to retry (`builtin_tools.py:600-618`).
- **No key or secret surface** is touched. `validator_config` remains owner-confidential
  (`[R30.25]`) and is not read by either tool.
- **Observer asymmetry (accepted, Q-6).** A granted observer takes a class-visible action
  while remaining invisible to the class (`[R28.02]`). Permitted by decision; the binding UI
  states it at the moment of granting, and the shipped packs grant no observer.

## 9. Quality Notes

**Existing debt in the touched files — do not imitate, do not silently fix**

- `turn_engine.py:2284` constructs `ChatroomAgentRepository` directly, reaching into another
  context's *infrastructure* layer. The new grant read must go through `ConversationFacade`
  instead. Correcting the existing call site is out of scope (FU-1).
- `activity_context_provider.py`'s docstring says the block is "given to every agent's turn,
  not just observers", while `[R30.21]`'s neighbour `[R30.15]` still says observers only
  (`REQUIREMENTS.md:2174`). Pre-existing SRS/code drift in a file this task reads; recorded
  as FU-2, not resolved here.
- `_dispatch_activation_progress` has no self-healing path for its single recipient
  (`activities.py:1046-1052`). This task must preserve the property that keeps it working
  (§5, `started_by_user_id`), not fix the underlying fragility (FU-3).

**Patterns to follow**

- Post-commit bookkeeping: `_post_commit` scopes, one per step, each individually guarded —
  `turn_engine.py:2921-2957` is the exemplar, including its comment that a new step goes
  *inside its own* scope.
- Side-channel collection from a tool to the engine: `artifact_sink`
  (`builtin_tools.py:864`, `:259`, drained at `turn_engine.py:2921`).
- A side-effecting tool that rides the turn's transaction: `build_update_wakeup_tool`
  (`tool_registry.py:356-379`).
- A route performing a cross-context validity check the context cannot: `_assert_mcp_binding_in_project`
  (`activities.py:387-414`).
- A creator-only field omitted from a non-creator's listing: `AgentRef.role`
  (`chatrooms.py:449-457`).
- Strict catalogue parsing with a closed field set: `activities/.../catalogue.py:106-127`.
- Defaulted new dataclass fields so existing construction sites are untouched:
  `ActivitySession.activation_id` (`activities/domain/models.py:183-189`).

**Reuse inventory — use these, do not write new ones**

- `resolve_reachable_type` / `ActivitiesFacade.resolve_type_for_project` — the tenancy gate.
- `ActivityPolicyService.assert_type_allowed` — the governance gate.
- `_reraise_if_infrastructure`, `_audit_tool_invoke`, `_marked_unrecorded`,
  `clip_tool_output` (`builtin_tools.py`) — the tool-result conventions.
- `_post_commit` (`turn_engine.py:632`).
- `Publisher` + `room_channel` + `user_channel` — already imported where the helpers move to.
- `audit.emit` with `isolated=True` where an audit failure must not abort the turn's
  transaction (`builtin_tools.py:649-652`).
- `ensure_room_creator` (`conversation/interfaces/access.py`).
- Frontend: `SButton`, `SSelect`, `SEmptyState` from `@shared/ui`; `usePolicyRefusal`
  (`slices/activities/composables/usePolicyRefusal.ts`) for translating a policy refusal —
  the agent path can hit the same refusal and the panel should say the same thing.
- `useChatroomBindings`'s existing busy-guard and error-key conventions.

## 10. Risks and Rollback

- **R-1 (accepted, Q-2) — no pacing guard.** A granted agent with an aggressive wake-up can
  start and end rounds faster than a class can work. The shipped TA runs at
  `every_n_messages: n=1`, so this is not hypothetical. Three things bound it without a
  platform rule: the per-turn tool-round cap bounds calls within one turn; a repeat start of
  the same type is idempotent (`activation_service.py:83-89`) and a repeat end reports
  `transitioned: false` (`:147`); and the room creator can end the round and revoke the
  grant at any moment (D4/G-1). **Accepted as the cost of the flexibility asked for**, and
  named in the docs so a teacher deploying this knows to watch for it in the dry run.
- **R-2 — prompt-level constraints are not enforceable.** `docs/examples/creative-thinking-course.md:429-431`
  already says a test cannot establish that an agent obeys its prompt. This feature makes
  disobedience class-visible rather than merely conversational. Mitigation: the allowlist
  bounds the blast radius, and the pre-deployment dry-run checklist in that document gains
  items for it.
- **R-3 — the grant outlives the granter's authority.** Nothing revokes a grant when
  `granted_by_user_id` loses their project role or stops being the room creator; the
  activation would still record them. Revocation is the room creator's manual act. FU-4.
- **R-4 — migration reversibility.** 0078 is additive: three nullable-or-defaulted columns
  and two CHECK constraints, in one transaction. Downgrade drops them; no data is
  transformed and no existing row's meaning changes (`may_control_activities` defaults to
  false, so every existing binding is ungranted). Forward-compatible: old code ignores the
  new columns, and `started_by_agent_id` is nullable so a pre-0078 writer still inserts
  valid activations.
- **R-5 — behavioural verification.** Six consecutive dossiers in this area shipped without
  a browser check (`BOARD.md`). This one changes a class-facing flow and an agent's ability
  to act on a room. §12 requires the manual pass and names it as the close-out condition for
  the affected ACs rather than something to reason about.

## 11. Acceptance Criteria

- [x] AC-1: A room creator can grant an agent activity control with a chosen type list, and
  the grant round-trips through `GET /api/chatrooms/{id}/agents`.
- [x] AC-2: A non-creator (project member, moderator, guest) cannot write the grant, and does
  not see `may_control_activities` or `activity_type_allowlist` in the agents listing.
- [ ] AC-3: `granted: true` with an empty `activity_type_ids` is rejected (422), and the DB
  CHECK rejects the same state written directly.
  **Route half verified** (`test_granting_with_an_empty_allowlist_is_refused`); the DB CHECK half is written (`tests/integration/test_activity_grant_constraints.py`) but has never been executed — no PostgreSQL on the implementing host. Left unticked rather than claimed; see FU-9.
- [x] AC-4: A type id from another project, or a soft-deleted type, is rejected (422) by the
  grant route.
- [x] AC-5: A granted agent's turn carries `start_activity` and `end_activity`; an ungranted
  agent's turn carries neither, including when `may_control_activities` is false but a
  non-empty allowlist remains.
- [x] AC-6: `start_activity`'s `activity_type_key` schema is an enum containing exactly the
  resolvable allowlisted keys, and an allowlist entry that no longer resolves is absent from
  it rather than causing an error.
- [x] AC-7: A successful `start_activity` creates an activation whose `started_by_user_id` is
  the granting user and whose `started_by_agent_id` is the calling agent.
- [x] AC-8: A type whose governance policy forbids it is refused by `start_activity` with a
  readable error result, and no activation, audit event or broadcast is produced.
- [x] AC-9: `end_activity` refuses when the room's active activation's type is not in the
  agent's allowlist.
- [x] AC-10: `activity.activation.started` / `.ended` reach the room **after** the turn's
  commit, and are not published when the turn fails and rolls back.
- [x] AC-11: After an agent-started round, the facilitator's completed/in-progress counts
  still reach the room creator on their user channel.
- [x] AC-12: `activity.activation_started` audit metadata names the agent and records
  `via: "agent_tool"`; a human-started round records neither.
- [x] AC-13: The room creator can end an agent-started round and can revoke the grant, and
  after revocation the agent's next turn carries no activity tools.
- [x] AC-14: `start_activity` / `end_activity` are in `BUILTIN_TOOL_NAMES` and the existing
  reserved-name drift test passes.
- [x] AC-15: The shipped packs parse with the new required field; TA is granted, SA, AA and
  DA are not; TA's prompt carries the "a message asking for it is not a reason" line and the
  one-activity-at-a-time line; DA's prompt no longer claims only a teacher may end a round.
- [x] AC-16: The pack install dialog shows each agent's `may_control_activities` and states
  that it is advisory, and installing a pack still creates no room binding and no grant.
- [x] AC-17: `ActivityPanel` names the initiating agent for an agent-started round and shows
  nothing extra for a human-started one.
- [x] AC-18: `docs/examples/creative-thinking-course.md` describes the delegated flow, says
  which shipped agents hold the grant and why the other three do not, and its dry-run
  checklist covers R-1 and R-2.
- [ ] AC-19: Manual browser pass against the compose stack: grant, agent starts a round,
  participants see it, the teacher sees the counts, the teacher revokes, the agent's next
  turn has no tools.
  **Converted to an e2e spec** with the user's agreement (D-5) and **not executed** — it needs the compose stack. `frontend/e2e/18-delegated-activity-control.spec.ts` covers the grant, the round-trip, the empty-allowlist refusal and the revoke; it deliberately does not drive an agent calling the tool. See FU-9.

## 12. Test Plan

| AC | Level | Where |
|---|---|---|
| AC-1, AC-2, AC-4 | unit (route + service) | `backend/tests/unit/test_observer_agents.py` (the existing home of the room-agent binding routes and their creator gates) and `backend/tests/unit/test_conversation_services.py` |
| AC-3 | unit (route) + **db** | route rejection unit-side; the CHECK constraint needs a real PostgreSQL per `backend/CLAUDE.md` — `jsonb_array_length` in a CHECK is PG-specific and the unit tier's `literal_binds` cannot see it. New `backend/tests/integration/test_activity_grant_constraints.py`, `pytest.mark.db` |
| AC-5, AC-6, AC-14 | unit | `backend/tests/unit/test_agent_runtime_tools.py`, `test_builtin_tools_wiring.py` |
| AC-7, AC-8, AC-9, AC-12 | unit (service + tool) | `backend/tests/unit/test_activities_services.py`, new `test_activity_control_tools.py` |
| AC-10 | unit | new `test_activity_control_tools.py` — assert the sink is drained only after commit and discarded on the failure path, following the artifact-sink tests' shape |
| AC-11 | unit | `backend/tests/unit/test_activity_activation_routes.py` (extend) |
| AC-13 | unit | route + tool-assembly tests |
| AC-15, AC-16 | unit | `backend/tests/unit/test_agent_example_packs.py`, `frontend/src/slices/agents/__tests__/AgentPackInstallDialog.test.ts` |
| AC-17 | component | `frontend/src/slices/activities/__tests__/ActivityPanel.test.ts` |
| AC-18 | review | the document itself |
| layering | unit (existing, must stay green) | `backend/tests/unit/test_activities_no_agents_import.py` — the `activities` context must still import nothing from `agents` after `started_by_agent_id` lands |
| AC-19 | manual | the `frontend:verify` skill against `deploy/compose` |

Migration 0078 gets an atomicity test in the pattern established by
`2026-08-16-migration-0076-retry-safety` (upgrade and downgrade each a single transaction),
and the `SMAP_SCRATCH_DATABASE_URL` gate that dossier's D-7 wired into CI covers it.

## 13. SRS Delta

**Applied to `REQUIREMENTS.md` on 2026-08-18 at approval.** Recorded below as written.

**Amend [R30.21]** (`REQUIREMENTS.md:2180`) — replace the final sentence
*"Starting is gated by room-creator capability — strictly stronger than the send-message
floor; starting a different type while one is active is rejected until the current one is
ended."* with:

> Starting is gated by room-creator capability — strictly stronger than the send-message
> floor; starting a different type while one is active is rejected until the current one is
> ended. The room creator may additionally delegate start and end authority to specific
> agents bound to that room ([R30.37]); no other user may hold it, and the room creator's
> own authority is never removed by a delegation.

**Amend [R30.22]** (`REQUIREMENTS.md:2181`) — after *"closed by the platform when the
facilitator ends that activation"*, insert:

> — or when an agent holding delegated control ends it ([R30.37]), which has the identical
> effect.

**Add [R30.37]:**

> - **[R30.37]** A room creator may delegate activity start and end authority to an agent
>   bound to that room, as a single grant scoped to an explicit allowlist of activity types.
>   The grant is a property of the room binding, not of the agent: it confers nothing in any
>   other room and is removed when the agent is unbound. It records the granting user, and an
>   activation started under it records that user as its starting user together with the
>   agent's identity, so the delegating human remains the answerable party and the
>   facilitator's per-round reads are unaffected. A granted agent exercises the authority
>   only through a structured tool call bounded to the allowlisted types; no text an agent
>   emits, and no message any room participant sends, can start or end an activity. Both
>   server-side gates that govern a facilitator's activation — type reachability ([R30.33])
>   and the platform governance policy ([R30.30]) — apply identically on the delegated path.
>   Grant changes and delegated activations emit audit events identifying the agent. The
>   platform imposes no rate, cadence, or completion precondition on a delegated agent; that
>   is expressed in the agent's configuration, and the room creator's unconditional ability
>   to end a round and revoke the grant is the control that remains.

**Amend [R30.35]** (`REQUIREMENTS.md:2194`) — after *"each declaring the course it targets
and the activity type keys it is written for"*, insert:

> and whether it is written to hold delegated activity control ([R30.37]); that declaration
> is advisory metadata shown at install, never an applied grant, since installing a pack
> creates no room binding.

## 14. Open Questions

- OQ-1: Should a delegated agent be able to read the facilitator's completed/in-progress
  counts, so it can decide when to end a round? Today that read is room-creator-gated
  (`activities.py:826`) and reports counts only. Not required for this feature — an agent can
  judge from the transcript and the activity block — and exposing it changes what an agent
  can infer about individuals in a small room. Left out; revisit if the dry run shows agents
  ending rounds too early.
- OQ-2: Should the grant be visible to participants? Currently creator-only, mirroring
  observer bindings. A class arguably benefits from knowing the teacher delegated pacing to
  TA. Not decided; the panel's "started by <agent>" line already discloses it after the fact.

## 15. Deviation Log

- **D-1 — the ended event names the agent that ended it, not the one that started it.**
  §7 said both `activity.activation_started` and `activity.activation_ended` gain
  `started_by_agent_id`. A granted agent may end a round a *teacher* started, so on the
  ended event that key would assert something false about who started it. The ended event
  records `ended_by_agent_id` instead; both still carry `via: "agent_tool"`, which is the
  one stable key AC-12 actually needs. The stored `started_by_agent_id` is untouched by an
  end, because who started a round and who ended it are different facts.
- **D-2 — the A2A drain site named in §5 is not wired.** §5 listed "the A2A completion
  commit (`:1340`)" as a drain site. `run_input_turn` deliberately passes no `chatroom_id`
  to `_builtin_tools` (a headless turn has no room), so that path builds no activity tools
  by construction and there is never anything in a sink to drain. A drain there would be
  unreachable code. The three room commit sites are wired as specified.
- **D-3 — no progress dispatch on a delegated start or end.** §5 moved
  `_dispatch_activation_progress` partly so "an agent-ended round must still refresh the
  teacher's counts". It is not called from the engine, because the HTTP start and end do
  not call it either and G-5 asks for the counts to reach the facilitator *unchanged*.
  `useActivationProgress` re-seeds by HTTP whenever the activation id changes, which is how
  the counts arrive on both paths; ending a round moves no count in any case (closing a
  session does not set `completed_at`). Calling it only on the agent path would have made
  the two paths differ, which is the opposite of what G-5 asks. AC-11 is pinned instead on
  the property that makes the counts addressable at all — `started_by_user_id` staying the
  granting teacher.
- **D-4 — a duplicate activity-type key gets a suffixed enum value.** §5 specified "an
  `enum` of the resolved allowed keys". [R30.02] permits a project-owned type and an
  opted-in platform type to share one key, so a key is not always a unique handle, and an
  ambiguous enum value would resolve to two different worksheets. Collisions get a `#2`
  suffix via the same deterministic uniquifying loop `_mcp_tool_name_from_agent_tool` uses.
  Dropping the second one instead would silently remove a worksheet the teacher granted.
- **D-5 — AC-19's manual browser pass became an e2e spec.** Decided with the user before
  implementation. `frontend/e2e/18-delegated-activity-control.spec.ts` drives the grant
  lifecycle through the real stack (grant, round-trip across a reload, empty-allowlist
  refusal, revoke). It deliberately does **not** drive an agent calling the tool: that needs
  a live provider key and a model that chooses to call it, which is exactly what §10 R-2
  records as untestable, and stubbing the model would prove only that the stub called the
  tool. **The spec has not been executed** — see FU-9. AC-19 is left unticked rather than
  claimed.
- **D-6 — an observer's identity is withheld from the round's attribution.** Found by the
  `check-security` gate, not by the spec. §5 justified naming the agent to the whole room
  with "an agent bound to a room is already named on every message it sends". That is true
  of a `normal` agent and **false of an observer**, which sends no messages ([R28.03]) and
  is filtered out of every non-creator's agent roster ([R28.10]) — `disclose_observers`
  exists precisely so a room can decide whether the class is told one is present. Since Q-6
  permits granting an observer, the broadcast would have been the one channel that outs it,
  reachable by any participant or guest. Both `started_by_agent_id` and
  `started_by_agent_name` are now withheld for an observer-started round, on the room
  broadcast and on the room-scoped read alike, so it is indistinguishable from a
  teacher-started round on the wire. The round itself is still announced.
- **D-7 — the allowlist is capped at 100 ids.** Not in the spec. Each id costs a
  reachability query at the grant route *and another on every turn of the granted agent*,
  so an unbounded list is not a one-off cost but a permanent per-turn one on an agent that
  may wake on every message (the shipped TA runs at `n=1`). A ceiling, not a working limit.
- **D-8 — `dispatch_room_activation_progress` moved with its siblings.** §6 listed three
  functions for `interfaces/broadcast.py`; the submit path's room-scoped wrapper moved too,
  because it is the only caller of `dispatch_activation_progress` and splitting the pair
  across two modules would have left a route function reaching into the context for its
  other half.
- **D-9 — the route's `ActivityTypePublicOut` is now built from the relocated projection.**
  `_type_public_out` calls `activity_type_public_payload` rather than listing the fields a
  second time, so the HTTP response and the realtime payload cannot drift into carrying
  different fields — a field added to one and not the other is a Pydantic error at the
  route rather than a client that silently stops receiving it.
- **D-11 — `ck_chatroom_agents_activity_grantor` is not shipped.** Found by
  `/code-review` after the audit gates had passed. §5 specified it and argued that
  `ON DELETE SET NULL` plus the CHECK "cannot leave a live grant with a null grantor:
  the delete would violate the CHECK. Deleting a granting user therefore fails loudly
  rather than silently producing an unattributable grant — acceptable because user
  deletion is already an admin operation." **The last clause is wrong**, and the
  consequence is severe: `AdminService.hard_delete_user`
  (`identity/application/admin_service.py:259`) issues `DELETE FROM users`, PostgreSQL
  performs the SET NULL, and the CHECK aborts the entire GDPR erasure with an
  IntegrityError naming a table the admin has no reason to connect to the request.
  `prepare_hard_delete` clears the other FK RESTRICT references but knows nothing about
  this column, and it only hard-deletes *soft-deleted* orgs and projects — so a room in
  a project the user merely belonged to survives with its grant intact, and revoking is
  the room creator's act, which that soft-deleted account can no longer perform. The
  erasure could never be made to succeed. The constraint is dropped from 0078 (never
  applied anywhere, so edited in place rather than reversed by a 0079). The invariant it
  protected is enforced where it already was: `activity_control_grant` returns `None`
  for a null grantor, so `SET NULL` now means "the granter is gone, so this grant is
  inert" — which is both the correct reading and fail-closed. The db-tier test that
  asserted the constraint is replaced by the regression for the deletion path.
- **D-12 — the panel stands down entirely when the type listing fails.** Two defects in
  the fix for the quality gate's own finding, both found by `/code-review`. With
  `activityTypesFailed` true, `activityTypes` is empty, so `unresolvedCount` counted
  *every* stored entry as deleted and told the teacher their whole selection was gone
  off one network hiccup; and `dirty` compared the raw stored list against a draft
  narrowed to nothing, leaving Apply enabled on a write the client guard would always
  refuse for want of a selection never offered. Both now return early on the flag, and
  a grant with nothing ticked is not offered for Apply either.
- **D-13 — the allowlist is deduplicated on write.** The route deduplicated only to
  bound its validation loop; the repository wrote the raw list, so a direct API call
  repeating an id stored it twice — a repeated resolution on every turn, and a settings
  panel permanently "dirty" because its draft holds each id once.
- **D-10 — two extra `ConversationFacade` methods.** §6 named `activity_control_grant` and
  `set_agent_activity_grant`. `project_id_for_chatroom` was also needed, because the tool
  assembly must run the reachability gate against *the room's* project rather than infer it
  from `agent.project_id`; and `agent_role_in_chatroom` was needed by D-6's disclosure rule.
  Both go through the facade rather than widening the direct-repository call FU-1 records.

## 16. Follow-ups

- FU-1: `turn_engine.py:2284` reaches into `contexts.conversation.infrastructure` directly;
  route it through `ConversationFacade` as this task does for the grant read.
- FU-2: `[R30.15]` says the recent-activity block is observer-only; the implementation gives
  it to every agent's turn (`activity_context_provider.py:6-11`). Reconcile the SRS with the
  code, or the code with the SRS.
- FU-3: `_dispatch_activation_progress` has one recipient and no self-healing path; a dropped
  event leaves a stale count until remount.
- FU-4: A grant survives its granting user losing project authority or ceasing to be the room
  creator (§10 R-3).
- FU-5: The `2026-07-19-large-artifacts-silently-dropped` dossier's code shipped but its
  status is still `in-progress` on an unticked AC-2 that needs Docker; somebody with a
  working sandbox should close it (Q-7).
- FU-6: `AgentRef` is both the `GET /api/chatrooms/{id}/agents` response and the
  `POST .../agents` request body, so a client may now send `may_control_activities` /
  `activity_type_allowlist` when binding and have them silently ignored. Traced and **not
  exploitable** — `add_chatroom_agent` reads only `agent_id` and `role`, and the repository
  inserts only those — but the fields now sit in a bind request model where a future
  `**body.model_dump()` would turn them into a privilege escalation. Split the request
  model from the response model.
- FU-7: `builtin_tools._reraise_if_infrastructure` and `_marked_unrecorded` are now used by
  two modules (via lazy wrappers in `activity_tools`, to avoid an import cycle) while still
  named private. Promote them, or move them beside `clip_tool_output` in `tool_registry`,
  so the cross-module use is legitimate rather than a convention breach with an apology in
  the docstring.
- FU-8: `ConversationFacade` is at 28 methods spanning rooms, guests, messages,
  attachments and retention. It was past coherence before this change (25) and this took it
  further. Split by subdomain.
- FU-9: **The `db` / `integration` tier has never been executed for this task.** No Docker
  and no local PostgreSQL on the implementing host, so
  `tests/integration/test_activity_grant_constraints.py` (both CHECK constraints, the
  `jsonb` round trip) and `tests/integration/test_migration_0078_atomicity.py` are unrun,
  and **migration 0078 has never been applied anywhere** — the same standing caveat 0077
  carries. AC-3 is left unticked for that reason rather than claimed. The 0078 atomicity
  tests also depend on `SMAP_SCRATCH_DATABASE_URL`, which `ci.yml` sets (D-7 of
  `2026-08-16-migration-0076-retry-safety`); if that step is ever removed they go quiet
  rather than red.
- FU-10: Nothing prunes an allowlist entry whose activity type was later deleted. It is
  inert (dropped at turn assembly, and the settings panel reports the count and offers a
  one-click repair on the next Apply), but a project that deletes a type leaves stale ids
  in every room that granted it. A sweep on type delete would close it.
