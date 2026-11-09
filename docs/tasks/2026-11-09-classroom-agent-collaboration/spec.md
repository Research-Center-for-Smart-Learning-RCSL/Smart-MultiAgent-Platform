---
type: feature
status: in-progress
created: 2026-11-09
requirements: [R28.01, R28.03, R28.07, R28.13, R28.16, R33.02, R33.10, R13.72, R15.09, R29.01]
depends_on: []
---

# Classroom agent collaboration: alert reaction time, analyst notices, peer reply review, and dashboard-driven agent design

## 1. Summary

The NSTC undergraduate research proposal (`_projects_documents/大專生國科會計畫.pdf`) describes a four-agent classroom: a Design Agent (DA) that turns teacher-set parameters into the other agents' prompts, a Teacher Agent (TA) that leads the room, a Student Agent (SA) that must ask the TA for approval before it speaks, and an Analytics Agent (AA) that alerts the human teacher and sends the TA internal notices about content or strategy errors. Research question 2 (RQ2) measures the time between an AA alert and the teacher's response. The platform today hosts all four agents as independent chatroom agents, and none of these four mechanisms exists. This dossier adds them for teachers running SMAP classrooms and for the research team analysing the exported data: a per-room classroom role on agent bindings, alert flags on observations with recorded view and action times, an analyst-to-teacher-agent notice channel with a recorded reaction, a held-reply review in which the TA approves, defers, modifies or rejects each SA reply, and a dashboard panel in which the DA drafts TA and SA prompts that the teacher previews and applies. The work ships as one dossier in four milestones (Q-1, Q-7).

The 2026-09-13 assessment `docs/assessments/nstc-undergrad-proposal-fit-assessment.md` lists reaction time as gap 5.4 (:229-236), the DA parameter engine as missing (§2.3), and the AA-to-TA flow as unbuilt (:272-281). Parts of that assessment are stale (dashboard, scoring, and export have since shipped); this dossier re-verified every claim it relies on.

## 2. Goals and Non-goals

**Goals**

- A room manager can mark a room's agent bindings as the room's teacher agent, a peer agent, or an analyst, per room, without editing the agents.
- An analyst can flag an observation as an alert. For each alert the platform records when it was raised, when its room creator first saw it, and when and how the creator first acted on it, and the research export carries these times.
- An analyst can send the room's teacher agent a private notice naming a content or strategy error and a suggestion. The teacher agent is woken, keeps the final say, and records its reaction. The room creator sees every notice and reaction in a collaboration log on the dashboard.
- A peer agent's reply in a room with a teacher agent is never shown to students before the teacher agent decides on it. The decision is approve, defer, modify or reject, always with a reason, and every decision is logged and exported.
- A project owner can fill a structured form on the dashboard, have the DA draft TA and SA system prompts from it, compare them with the current prompts, and apply them in one step. Every system-prompt change keeps a revision that can be restored.

**Non-goals**

- No human-teacher approval of peer replies; the TA is the approver, as the proposal describes (Q-2). The human teacher already has release and room moderation.
- No change to how observations are released ([R28.06], [R28.07]) or who may see them ([R28.03]).
- No agent-to-agent tool beyond the two analyst/teacher tools and the one review tool defined here. Chatroom agents still cannot call A2A (`OrchestrationFacade.a2a_call`), and a teacher agent still cannot instruct a peer agent.
- No automatic writes to an agent's configuration by any LLM. The DA drafts; only the teacher's apply writes (Q-6).
- No in-dashboard simulation sandbox, no on-platform pre/post tests (CRAT, 新編創造思考測驗) or NASA-TLX instrument; these stay off-platform.
- No computed reaction-time statistics or charts on the dashboard; the export carries raw times and the analysis happens off-platform (FU-1).
- No change to example-pack install semantics: installing still never overwrites an existing agent (`backend/contexts/agents/application/example_service.py:233-247`).
- No retrofitting of alert flags onto observations recorded before this change.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | One dossier or an initiative of four? | One dossier, built in four milestones with one PR each. | User's choice. The four mechanisms share the classroom role designation and the collaboration log, so one approval keeps their contracts consistent; milestones keep each PR revertible, as in `2026-11-08-research-data-integrity`. |
| Q-2 | How does "SA asks TA before speaking" work? | The SA's reply is held (not streamed, not persisted as a message) and a dedicated TA review turn runs at once. The TA must call `review_peer_reply` with approve, defer, modify or reject and a reason. No decision within the review deadline expires the reply unposted. | User's choice over an asynchronous review on the TA's next turn (minutes of delay, reply out of context) and over human approval (not the proposal's design). Fail closed: an unreviewed peer reply never reaches students. |
| Q-3 | What is an AA alert, and what is the teacher's reaction? | The analyst marks an observation as an alert through `present_observation`. Recorded: raised (creation time), first viewed by the room creator (reported by the client when the alert is actually rendered on an open surface, stored server-side), and first action with its kind (release, delete, creator's next room message, activity start or end in that room). | User's choice. Treating every observation as an alert would mix routine summaries into RQ2; recording only views would not measure "time to act". |
| Q-4 | How does an AA notice reach the TA, and how is the reaction captured? | New analyst tool `notify_teacher_agent`; the notice is stored durably, queued into the TA's next-turn context, and wakes the TA with trigger `analyst_notice`. The TA records its reaction with `respond_to_notice` (accepted, partly accepted, declined, with reason). | User's choice over delivery without waking (late correction) and over today's manual release (proposal's direct notice not met). |
| Q-5 | How does a room say which agent is TA, SA, AA? | A per-room `classroom_role` on the room-agent binding (`none`, `teacher`, `peer`, `analyst`), set by the room manager, at most one teacher per room. | User's choice over a role fixed on the agent (one agent could not play different roles in different rooms) and pairwise rules (heaviest UI and validation for no current need). |
| Q-6 | Where does the DA run, and how is its output written? | A "Design classroom agents" panel on the workspace dashboard. The DA is called outside chat with a platform output contract; the teacher compares current and proposed prompts and applies explicitly. Writes go through the normal agent patch with optimistic concurrency, and every system-prompt change records a revision. | User's choice over automatic write (an LLM overwrites working prompts unreviewed) and an in-chat write tool (misses the dashboard parameters and gives an LLM a config-writing tool). |
| Q-7 | Milestone order? | M1 roles and alert reaction times; M2 analyst notices and collaboration log; M3 peer reply review; M4 DA design panel and prompt revisions. | User's choice. M1 lays the role designation M2 and M3 need; M4 is independent. |
| Q-8 | What may a held peer turn do besides produce text? | A peer binding under review is offered only read-only tools (`READ_ONLY_TOOL_NAMES`, `backend/contexts/agents/application/runtime/tool_registry.py:157`). Activity control, canvas writes and `update_wakeup` are withheld. | A rejected reply must leave nothing behind; tool side effects commit in the turn's transaction before any review could run. Proposed at spec time as the only design consistent with Q-2's fail-closed rule; confirmed by the user at approval. |
| Q-9 | What happens to a peer agent's replies when the room has no teacher agent (for example the teacher binding was removed)? | They are held and expire unposted, and the room's agent settings show a warning on the peer binding. | Fail closed, consistent with Q-2. Posting directly would silently change the research condition. Confirmed by the user at approval. |
| Q-10 | Should this depend on `2026-07-19-large-artifacts-silently-dropped` (in progress), since both edit `turn_engine.py`? | No dependency. | That dossier changes the `code_exec` artifact path; this one adds a peer routing branch beside the observer branch and does not touch artifact handling, so the edits do not overlap. User's choice. |

## 4. Current State

**Room bindings and roles.** A room's agents are rows of `chatroom_agents` keyed by `(chatroom_id, agent_id)` (`backend/contexts/conversation/infrastructure/tables.py:76-163`). The only role is `normal | observer` (PG enum `chatroom_agent_role`, [R28.01]); there is no notion of teacher, peer or analyst agent anywhere in the binding model. Per-room capability grants live as columns on the same row (`may_control_activities`, `may_read_drafts`, canvas flags, `granted_by_user_id`, :110-151) and are written through `PATCH /chatrooms/{id}/agents/{agent_id}/activity-control` (`backend/app/api/v1/chatrooms.py:971-975`). Each binding carries an incarnation `binding_id` (:152-157).

**Turn pipeline.** Every room turn is the arq job `wakeup_agent` (`backend/app/workers/tasks/orchestration.py:38`), which applies room, binding, trigger, class-closed and autostop guards (:86-162; explicit triggers `mention` and `release` skip the autonomous checks, `backend/contexts/orchestration/domain/models.py:261`) and calls `TurnEngine.run_turn` (`backend/contexts/agents/application/runtime/turn_engine.py:1698`). Inside `_run_locked` (:3124) an observer binding gets `room = None` so nothing streams to the room (:3241); a normal binding streams `agent.token` frames during `_stream_with_tools` (:5016). After synthesis, the stale-reply recheck may run (`_recheck_reply`, :4890; [R13.72]). An observer's output is recorded with `ObservationService.record` (:3895); a normal binding's output is persisted with `MessageService.send_agent` (:3951), followed by post-commit broadcasts and `_dispatch_agent_reply_wakeups` (:4241). The window between synthesis and persistence is the only place a hold can sit, and the observer branch is the existing precedent for output that never reaches the room.

**Agent-private context.** The Redis list `a2a:pending_notify:{agent_id}` (`backend/contexts/orchestration/infrastructure/pending_notify.py:9,29`) is drained at the start of the recipient's next turn by `_pending_context_and_tools` (`turn_engine.py:2898`), which re-queues notes for other rooms and renders an `[Incoming notifications]` block; `released_observation` notes render at :2968. The system prompt is assembled by `_SystemBlocks` (:1495). The queue is not durable and writes no record. Today it is fed by the creator's private release (`backend/app/api/v1/observations.py:274`), workflow A2A notify, and workflow approval ballots.

**Tools.** Chatroom agent tools are assembled in `TurnEngine._builtin_tools` (`turn_engine.py:2140`), with reserved names in `BUILTIN_TOOL_NAMES` (`tool_registry.py:119-150`). Observers additionally get `present_observation` (`backend/contexts/agents/application/runtime/observer_tools.py:142`), whose argument is a block array ([R28.16]). Grant-gated tools follow a resolve/build pattern (`activity_tools.py:69-110`, `draft_tools.py:269`). No chatroom tool sends anything to another agent; A2A `call` and `issue_instruct` are reachable only from workflow executors (`backend/contexts/workflow/application/executors/agent_invocation.py:51`, `executors/instruct.py:62`).

**Existing approvals do not fit.** `ApprovalService` (`backend/contexts/orchestration/application/approval_service.py`) votes approve or reject only, requires a `workflow_run_id` (`backend/contexts/orchestration/infrastructure/tables.py:49-54`), and is created only by the workflow `approval_gate` executor.

**Observations and alerts.** `agent_observations` (`backend/contexts/conversation/infrastructure/tables.py:165-196`) has no severity, alert, or seen column; block kinds are fixed (`observation_blocks.py:66-68`). The only teacher-action timestamp is `released_at` / `released_by_user_id`, set once by compare-and-swap (`observation_service.py:169-176`). Observation events go only to the creator's user channel ([R28.13]). The observer panel's unread badge is client-only state that resets when the panel opens (`frontend/src/slices/conversation/composables/useObservations.ts:160-225`) and is never persisted. The dashboard's "stalled rooms" alert is computed in the browser (`frontend/src/slices/dashboard/components/DashboardOverview.vue:61-94`) and has no raised-at time. No table anywhere records a user viewing an item, apart from `notifications.read_at` (`backend/contexts/notification/infrastructure/tables.py:10-27`).

**Research export.** `research_export_builder.py` (`backend/contexts/conversation/application/`) writes `submissions/<type>.csv` (UTF-8 BOM, formula-guarded cells via `_cell()`, :449-452), `observations.json`, `transcripts.json` and `manifest.json` (`schema_version` 2, :10-14) from rows the worker gathers through context facades (`backend/app/workers/tasks/conversation.py:422-507`). Only the computed project owner may export ([R33.10]).

**Agent configuration.** `PATCH /agents/{agent_id}` (`backend/app/api/v1/agents.py:793-862`) requires `If-Match`, checks `RESOURCE_CREATE_EDIT` (project owner or org owner, `backend/shared_kernel/auth/permissions.py:222-225`) and calls `AgentService.patch` (`backend/contexts/agents/application/agent_service.py:737-929`). `system_prompt` is a single column (`backend/contexts/agents/infrastructure/tables.py:44`) and earlier prompts are not kept; the audit event `agent.edited` lists field names only (`agent_service.py:917-928`). `AgentsFacade.patch_agent` (`backend/contexts/agents/interfaces/facade.py:179-202`) is the cross-context entry point.

**Design Agent and prompt assistant.** The DA is a chat agent from `crat-word-puzzle-design.json` (`backend/contexts/agents/infrastructure/examples/packs/`), whose prompt tells the teacher to copy drafted prompts by hand. The prompt assistant ([R29]) drafts one prompt in chat on a pinned key and applies it only to the editor form, client-side (`frontend/src/slices/prompt-studio/components/PromptAssistantPanel.vue:101-111`, `frontend/src/slices/agents/views/AgentDetailView.vue:654-655`). Packs have no parameter substitution (`catalogue.py:42-55`). The non-chat LLM pattern on an agent's key group is `_call_judge` (`backend/app/workers/tasks/activities.py:224-300`).

## 5. Design

### Options considered

**Option A: reuse workflow approvals and A2A for the review and the notice.** Bind each room to a workflow run, create approval gates for SA replies, send AA notices as A2A NOTIFY. Trade-offs: approvals need a workflow run and have no defer or modify outcome; NOTIFY is undurable and unlogged; the classroom rooms run no workflows. Every requirement would need a schema change in orchestration anyway, while dragging workflow semantics into chat.

**Option B: room-scoped collaboration records in the conversation context, driven by the existing turn pipeline.** A classroom role on the binding; durable `analyst_notices` and `peer_reply_reviews` tables owned by the conversation context next to `agent_observations`; the hold and review implemented as a new turn outcome beside the observer branch; delivery to agents through the existing `pending_notify` context block plus a durable record. Trade-offs: adds turn-engine branches (mitigated by putting the logic in new modules and keeping `turn_engine.py` changes to thin hooks), and doubles the LLM cost of a peer reply.

**Option C: prompt-only.** Instruct SA, TA and AA through prompts to behave as if approvals and notices existed. Trade-offs: nothing is enforced or logged; the research data would be unverifiable. Rejected.

### Decision

Option B. The records are room-scoped facts with the same audience as observations (the room creator), so the conversation context owns them, and the research export already reads that context. The hold reuses the observer path's "no room channel" rule, which is already proven not to leak tokens. `pending_notify` stays the delivery mechanism into an agent's prompt because the turn engine already renders, scopes and re-queues it; durability comes from the new tables, which are the source of truth, while the Redis note is only a delivery hint carrying an id. Given up: workflow-level reuse of these approvals, and a generic agent-to-agent messaging tool. Both are deliberately out of scope (§2).

**Milestone 1: classroom roles and alert reaction times.**

`chatroom_agents.classroom_role` is a new PG enum `classroom_agent_role` (`none`, `teacher`, `peer`, `analyst`), default `none`. Constraints: `teacher` and `peer` require binding role `normal`; `analyst` requires `observer`; a partial unique index allows one `teacher` per room. Changing role between normal and observer resets `classroom_role` to `none`. It is set through the existing room agent settings endpoint family by whoever may manage the room's agents, audited as `chatroom.classroom_role_changed`.

`present_observation` gains two optional arguments, `alert` (boolean) and `alert_reason` (plain text, at most 200 characters), offered only to analyst bindings. `agent_observations` gains `is_alert`, `alert_reason`, `first_viewed_at`, `first_viewed_via` (`panel` or `dashboard`), `first_action_at` and `first_action_kind` (`released`, `deleted`, `room_message`, `activity_started`, `activity_ended`). Every "first" column is set once, by a conditional update where it is still NULL, so concurrent reports keep the earliest.

A new creator-gated endpoint `POST /api/chatrooms/{id}/observations/viewed` takes up to 50 observation ids and a `via`, and records the view for alerts not yet viewed. The client calls it when an alert card is actually rendered on an open observer panel, or in the dashboard alert list (below); a closed panel or a background tab does not count (`document.visibilityState`). First action is recorded server-side: release and delete in `ObservationService`; the creator's next message in the room and the next activity start or end by that creator in the room, through a post-commit hook wired in `app/wiring/` so that conversation and activities do not import each other. A room action closes every open alert in that room raised before it.

The dashboard gains an alerts list (`GET /api/workspaces/{id}/dashboard/alerts`) showing alerts only from rooms the caller created (admins see all), because observation content is creator-only ([R28.03]). The research export gains `alerts.csv`: room code, analyst agent name, alert reason, raised, first viewed, view channel, first action, action kind, and the two latencies in seconds. Pre-change observations have `is_alert = false` and never appear in it.

**Milestone 2: analyst notices and the collaboration log.**

An analyst binding in a room that has a teacher binding is offered `notify_teacher_agent(error_kind, error, suggestion, about_message_id?)`. `error_kind` is `content` or `strategy`; `error` and `suggestion` are plain text capped at 1000 characters each; `about_message_id`, if given, must be a message in the same room sent by that room's teacher or peer agent. The target is always the room's teacher binding; the tool takes no target argument. A row in `analyst_notices` (room, analyst agent, teacher agent, binding incarnations, kind, texts, subject message, status `pending`, created) is written in the turn's transaction. After commit a note `{kind: "analyst_notice", notice_id, chatroom_id}` is pushed to the teacher's `pending_notify` and `wakeup_agent` is enqueued with trigger `analyst_notice`. That trigger is autonomous, not explicit: autostop and the turn rate limit apply, so an analyst/teacher exchange cannot loop. Each room accepts at most 3 notices per 10 minutes and one pending notice per subject message; over the cap the tool returns a refusal the analyst sees.

On the teacher's turn the notice renders as a `[Notice from the analyst agent]` block, loaded from the table by id and framed as data written by another agent, never as instructions. The teacher gets `respond_to_notice(notice_id, decision, reason)` with decision `accepted`, `partly_accepted` or `declined` and a reason of at most 500 characters; one response per notice. A notice with no response 15 minutes after creation becomes `unanswered` at read time (no sweeper needed). The teacher's room reply, if any, is a normal message; `respond_to_notice` is read-only for recheck purposes. Students never see a notice.

`GET /api/workspaces/{id}/dashboard/collaboration` lists notices and (from M3) peer reviews, newest first, paginated, for rooms the caller created (admins all). The dashboard renders it as a collaboration log with the error, suggestion, the agent's decision and reason, and links to the room. Export: `analyst_notices.csv`.

**Milestone 3: peer reply review.**

A peer binding's turn runs like an observer's as far as the room is concerned: `room = None`, so no tokens stream and nothing is broadcast; only `agent.thinking` and `agent.finished` are emitted to the room, so students see the agent is busy. The turn is offered read-only tools only (Q-8). After synthesis and the stale-reply recheck, a non-empty draft is written to `peer_reply_reviews` (room, peer agent, teacher agent, incarnations, draft text, trigger message, status `pending`, created, attempt 1) instead of a message, and the job `review_peer_reply` is enqueued.

The review job runs a headless teacher turn: the teacher's own system prompt, the room transcript as a normal turn would load it, a platform-authored `[Peer reply awaiting your review]` block carrying the draft as quoted data, and exactly one tool, `review_peer_reply(review_id, decision, reason, revised_text?, defer_minutes?)`. The turn's text output is discarded; it never posts. The teacher's key group pays. The job has a 60-second deadline and `max_tries=1`.

- `approve`: the draft is posted as the peer agent's message with `metadata.peer_review_id`, through `MessageService.send_agent`, followed by the normal broadcasts and agent-reply wakeups.
- `modify`: `revised_text` (required, at most the message body cap) is posted as the peer agent's message, with `metadata.peer_review_id` and `metadata.peer_review_modified = true`.
- `reject`: nothing is posted; a note goes to the peer's `pending_notify` so its next turn knows its last draft was not shown and why.
- `defer`: `defer_minutes` is an integer from 1 to 10; the job re-enqueues itself with that delay and attempt + 1, and the next review sees the refreshed transcript. A third deferral expires the review.
- No tool call by the deadline, a failed call, a missing teacher binding (Q-9), or a changed peer or teacher incarnation at decision time: status `expired`, nothing posted.

The posted message is written only if the review row's status moves from `pending` to the decision by compare-and-swap, so a retried or duplicated job cannot post twice. Students never see the draft, the reason, or that a review happened. Export: `peer_reviews.csv` with draft, decision, reason, revised text, timings and the posted message's id code.

**Milestone 4: Design Agent panel and prompt revisions.**

`agent_prompt_revisions` (agents context) records every change of an agent's `system_prompt`, whatever the source: agent, version after the change, prompt text, actor, source (`manual`, `design`, `restore`), optional design-run id, created. `AgentService.patch` writes it in the same transaction. The agent page lists revisions and can restore one (a restore is itself a revision). The first revision of a pre-existing agent is written lazily on its first change, capturing the prompt being replaced.

The workspace dashboard gains a "Design classroom agents" panel for project owners. The form takes: design agent (an agent of the project), target teacher and peer agents (agents of the project), activity type (types reachable from the project), difficulty (introductory, intermediate, advanced), number of rounds (1 to 6), peer deliberate-error pattern (none, one in three), tone (warm, neutral, concise), and notes (at most 2000 characters). Submitting creates a `classroom_design_runs` row and enqueues `run_classroom_design`, which calls the design agent through the provider router on its key group, following `_call_judge`, with the agent's own system prompt, a platform-authored output contract, the form values as a JSON data block, and the target agents' current prompts. The contract asks for JSON with `teacher_system_prompt`, `peer_system_prompt` and `notes`; each prompt is capped at 32,000 characters and an unparsable or oversized reply fails the run with a reason. No tools are offered.

The panel shows, per target, the current prompt and the proposed prompt side by side (read-only), plus the DA's notes. "Apply" sends both targets with the versions the teacher saw; the server patches each agent through `AgentsFacade.patch_agent` with optimistic concurrency, in one transaction, recording `source = design` revisions and the audit event `classroom_design.applied`. If either agent changed since the run was shown, nothing is written and the teacher sees a conflict. A run can be applied once. The DA pack prompt is updated to point to the panel; already-installed DA agents are unaffected (Non-goals).

## 6. Detailed Changes

- **Backend**
  - `contexts/conversation`: `ClassroomRole` domain enum; binding read/write for `classroom_role` with the constraints in §5; `ObservationService` alert fields, `record_views`, `record_first_action`; new `AnalystNoticeService` and `PeerReviewService` (application) with repositories and tables; facade methods for the turn engine, the dashboard and the export (`classroom_roles_for_room`, `create_analyst_notice`, `respond_to_notice`, `create_peer_review`, `decide_peer_review`, `list_collaboration_for_rooms`, `list_alerts_for_rooms`, export listing methods).
  - `contexts/agents/application/runtime`: new modules `collaboration_tools.py` (`notify_teacher_agent`, `respond_to_notice`, `review_peer_reply`) and `peer_review.py` (headless review turn); `observer_tools.py` gains the alert arguments; `turn_engine.py` gains thin hooks: peer binding routing beside the observer branch (:3241, :3882-3951), read-only tool filter for peers, notice block rendering in `_pending_context_and_tools` (:2898-2981). New names added to `BUILTIN_TOOL_NAMES`; `respond_to_notice` added to `READ_ONLY_TOOL_NAMES`.
  - `contexts/agents`: `agent_prompt_revisions` table, repository, `AgentService.patch` hook, restore service method; `AgentsFacade.list_prompt_revisions`, `restore_prompt_revision`, `apply_design` (both agents, one transaction).
  - `contexts/dashboard`: read services for alerts and collaboration log, composed from conversation facade reads over the caller's created rooms.
  - New `classroom_design_runs` table and service (agents context, since it writes agents and calls an agent).
  - Workers: `review_peer_reply` and `run_classroom_design` in `app/workers/tasks/`, registered in `app/workers/main.py` with `max_tries=1`; `wakeup_agent` accepts trigger `analyst_notice`.
  - `app/wiring/`: post-commit hooks for the creator's room message and activity start or end that record alert first actions.
  - Research export: `alerts.csv`, `analyst_notices.csv`, `peer_reviews.csv`; `SCHEMA_VERSION` 3; manifest counts and notes.
  - Migrations: yes, one per milestone (0110 classroom role and alert columns; 0111 analyst notices; 0112 peer reviews; 0113 prompt revisions and design runs). All additive.
- **API contract**: new `PATCH .../agents/{agent_id}/classroom-role`, `POST /chatrooms/{id}/observations/viewed`, `GET /workspaces/{id}/dashboard/alerts`, `GET /workspaces/{id}/dashboard/collaboration`, `GET /agents/{id}/prompt-revisions`, `POST /agents/{id}/prompt-revisions/{rev}/restore`, `POST /projects/{id}/classroom-design/runs`, `GET .../runs/{run_id}`, `POST .../runs/{run_id}/apply`; observation DTO gains alert fields. `gen:api` rerun required: yes, per milestone.
- **Frontend**
  - `slices/conversation`: room agent settings gain a classroom role selector with the teacher-uniqueness and peer-without-teacher warnings; `ObservationCard` shows the alert flag and reason; `useObservations` reports alert views when the panel is open and the page is visible.
  - `slices/dashboard`: alerts list (also reports views with `via = dashboard`), collaboration log, "Design classroom agents" panel with run status, side-by-side preview and apply/conflict handling.
  - `slices/agents`: prompt revision list and restore on `AgentDetailView`.
  - i18n keys in both locale files for every new string.
- **Deploy/config**: no new env vars. The two new jobs use the default worker queue.

## 7. NFR Checklist

- [ ] i18n: every new label, decision name, warning and error goes through `$t()` in `en` and `zh-TW`; `@` in any message escaped as `{'@'}`.
- [ ] Audit log: `chatroom.classroom_role_changed`, `analyst_notice.created`, `analyst_notice.responded`, `peer_review.decided`, `agent.prompt_restored`, `classroom_design.applied`; metadata carries ids and decision kinds only, never notice, draft or prompt text (in line with [R28.11]).
- [ ] Tenant isolation: every new endpoint resolves room or workspace access first; alert, notice and review reads are restricted to the room creator (admins bypass), matching observations; design runs and prompt revisions require `RESOURCE_CREATE_EDIT` on the agent's project, and the run's agents must all belong to the run's project.
- [ ] Error handling UX: run pending, failed (with reason) and conflict states; empty states for alerts and the collaboration log; RFC 7807 codes for role constraint violations, notice cap, stale apply.
- [ ] Performance: alert, notice and review lists are paginated (default 50) and indexed by `(chatroom_id, created_at)`; view reports batch up to 50 ids; each peer reply costs one extra LLM call on the teacher's key group, and each notice one teacher turn, bounded by the per-room notice cap and autostop.

## 8. Security Considerations

This change adds agent tools and LLM prompt surfaces, so Part F of `check-security`'s dimensions applies in full.

- **Prompt-injection chain.** Student text reaches the analyst, whose notice reaches the teacher agent; student text reaches the peer agent, whose draft reaches the teacher's review turn. Both the notice and the draft are rendered in platform-authored blocks that state the content was written by another agent and is data, and the tools they enable are narrow: `respond_to_notice` writes only the notice's decision, `review_peer_reply` acts only on the review id it was created for. Neither tool can reach agent configs, keys, other rooms or other agents. The review turn has no other tools.
- **Cross-room isolation.** Tool arguments carrying ids (notice, review, message) are checked against the turn's room and binding incarnation; a note naming another room is re-queued by the existing logic, and the tables are the source of truth.
- **Leak to students.** A peer binding's turn has no room channel, so neither tokens nor tool events reach the room before a decision. Notices, drafts, reasons and alert content are delivered only on the creator's channel and the creator-gated REST reads.
- **Config writes.** No LLM can write an agent's configuration. The design run returns text; only the authenticated owner's apply writes, with optimistic concurrency against the versions they saw. Design output is capped and contains no executable surface.
- **Billing abuse.** The review turn bills the teacher agent's key group, the design run the design agent's; both are bounded (peer replies by the peer's own rate limits, design runs by one in flight per project).
- **View spoofing.** `observations/viewed` only accepts ids from the caller's own rooms and only sets an unset column; a creator can falsify their own reaction time and nothing else. This is recorded as a known limitation of client-reported views in the export notes.

## 9. Quality Notes

- **Existing debt.** `PATCH /agents/{id}` calls `AgentService` directly from the route (`backend/app/api/v1/agents.py:803`); new routes must go through facades and must not copy this. `AgentsFacade.patch_agent` takes no `request_id` (`facade.py:179-202`); M4 adds it rather than working around it. `turn_engine.py` exceeds 5,000 lines; the new behavior lives in new modules with thin hooks. `pending_notify` is Redis-only (`pending_notify.py:9`) and must not become the record of anything. Not fixed here; recorded as FU-2.
- **Patterns to follow.** Grant-gated tool resolve/build: `activity_tools.py:69-110`, `draft_tools.py:269`; observer output routing: `turn_engine.py:3241, 3882-3951`; one-shot compare-and-swap: `observation_service.py:169-176`; non-chat LLM call on an agent key group: `backend/app/workers/tasks/activities.py:224-300`; export CSV: `research_export_builder.py:247-277`; creator gate: `backend/app/api/v1/observations.py:134-142`.
- **Reuse inventory.** `ObservationService`, `is_room_creator` (`backend/contexts/conversation/application/access.py:539-563`), `pending_notify.push`, `_pending_context_and_tools` rendering, `READ_ONLY_TOOL_NAMES`, `build_router` / `ProviderRequest`, `subject_code` and `_cell()` in the export, `visible_rooms_in_workspace` for the dashboard, `RoomWakeupPanel.vue`'s "designer value versus room value" layout for the side-by-side preview, `SCard`, `SEmptyState`, `SStatusBadge`, `useConfirmDialog`, TanStack queries per slice.

## 10. Risks and Rollback

- **Students wait longer for the peer agent.** One extra LLM call per peer reply; the 60-second deadline bounds the worst case. Mitigation: `agent.thinking` stays visible.
- **Teacher agent overload.** Notices and reviews add teacher-agent calls. Mitigation: notice cap, autostop, review turns separate from the teacher's room turns.
- **Client-reported views.** A view depends on the client reporting it; a teacher whose client fails to report shows no view. The export notes say so; first action is server-recorded and does not depend on the client.
- **Prompt overwrite.** Mitigated by preview, explicit apply, optimistic concurrency, and revisions with restore.
- **Rollback.** Each migration is additive and reversible (drop columns, tables, enum); the features are inert while no binding has a classroom role, so reverting a milestone's code leaves earlier milestones working.

## 11. Acceptance Criteria

**M1**
- [ ] AC-1: A room manager can set a binding's classroom role; a second teacher in the same room, a peer or teacher on an observer binding, and an analyst on a normal binding are rejected with a problem response; switching a binding between normal and observer resets its classroom role.
- [ ] AC-2: An analyst's `present_observation` call with `alert: true` stores `is_alert` and `alert_reason`; a non-analyst observer is not offered the alert arguments.
- [ ] AC-3: Reporting a view for an alert sets `first_viewed_at` and `first_viewed_via` once; a second report, a report by a non-creator, and a report for another room's observation leave it unchanged.
- [ ] AC-4: The first of release, delete, the creator's next room message, or the creator's next activity start or end in the room sets `first_action_at` and `first_action_kind` on every open alert in that room raised before it, and later actions do not change it.
- [ ] AC-5: The dashboard alerts list shows only alerts from rooms the caller created (all for an admin), and shown alerts are reported as viewed with `via = dashboard`.
- [ ] AC-6: The research export contains `alerts.csv` with the columns in §5, manifest `schema_version` 3, and no full UUID or display name.

**M2**
- [ ] AC-7: An analyst binding in a room with a teacher binding is offered `notify_teacher_agent`; without a teacher binding, or on a non-analyst binding, it is not.
- [ ] AC-8: A notice creates a durable row, wakes the teacher with trigger `analyst_notice`, and the teacher's next turn contains the notice block; the notice never appears in the room transcript or on the room channel.
- [ ] AC-9: The teacher's `respond_to_notice` records decision and reason once; a notice without a response for 15 minutes reads as `unanswered`.
- [ ] AC-10: The fourth notice in a room within 10 minutes, and a second pending notice about the same message, are refused.
- [ ] AC-11: The dashboard collaboration log lists notices with error, suggestion, decision and reason for rooms the caller created; the export contains `analyst_notices.csv`.

**M3**
- [ ] AC-12: A peer binding's turn in a room with a teacher binding emits no `agent.token` frames and no `message.created` before a decision, and is offered only read-only tools.
- [ ] AC-13: `approve` posts the draft, `modify` posts the revised text, both as the peer agent with the review id in metadata; `reject` posts nothing and the peer's next turn is told its draft was not shown.
- [ ] AC-14: `defer` re-reviews after the given minutes with the refreshed transcript; a third deferral, a missed deadline, or a missing teacher binding expires the review unposted.
- [ ] AC-15: A duplicated or retried review job posts at most one message.
- [ ] AC-16: Peer reviews appear in the dashboard collaboration log and in `peer_reviews.csv`.

**M4**
- [ ] AC-17: Every change of an agent's `system_prompt` records a revision with its source, and restoring a revision sets the prompt back and records a `restore` revision.
- [ ] AC-18: A project owner can submit the design form; the run calls the design agent on its key group with no tools and stores both proposed prompts, or fails with a reason on an unparsable or oversized reply.
- [ ] AC-19: The panel shows current and proposed prompts side by side; apply writes both agents in one transaction with `design` revisions; if either agent changed since the run was shown, nothing is written and the teacher sees a conflict; a run cannot be applied twice.
- [ ] AC-20: A non-owner, or a run naming an agent from another project, is refused.

**End to end**
- [ ] AC-21: On staging, a simulator scenario in a room with teacher, peer and analyst bindings produces at least one alert with view and action times, one notice with a teacher response, and peer reviews with at least two different decisions, and the export's three new CSVs open in Excel with readable Chinese.

## 12. Test Plan

- Unit (`backend/tests/unit/`): classroom role constraint validation; tool offering per role (AC-2, AC-7, AC-12); notice cap and subject uniqueness (AC-10); review decision state machine including defer limit, deadline, missing teacher and incarnation change (AC-13, AC-14); design output contract parsing and caps (AC-18); export CSV builders (AC-6, AC-11, AC-16).
- Integration against Postgres (`backend/tests/integration/`): first-view and first-action compare-and-swap under concurrent calls (AC-3, AC-4); review compare-and-swap with a duplicated job (AC-15); apply transaction and stale-version conflict (AC-19); prompt revisions and restore (AC-17); creator and project gates on every new endpoint (AC-5, AC-11, AC-20).
- Turn-engine tests with a fake provider: peer turn emits no tokens and no message before decision (AC-12); notice block present on the teacher's next turn and absent from the room (AC-8).
- Frontend (Vitest): role selector constraints and warnings; alert view reporting only when the panel is open and the page is visible; collaboration log and design panel states (AC-1, AC-3, AC-5, AC-19).
- Manual on staging (AC-21): a new simulator scenario under `frontend/sim/scenarios/`, plus the export opened in Excel.

## 13. SRS Delta

New section, appended after §33:

> ## 34. Classroom Agent Collaboration
>
> Added by `docs/tasks/2026-11-09-classroom-agent-collaboration/`.
>
> - **[R34.01]** A chatroom agent binding carries a classroom role `none | teacher | peer | analyst` (default `none`), set per room by whoever may manage the room's agents and audited. `teacher` and `peer` require binding role `normal`; `analyst` requires `observer`; a room has at most one `teacher`. Changing a binding's role between normal and observer resets its classroom role to `none`.
> - **[R34.02]** An analyst binding's `present_observation` accepts an `alert` flag and a short plain-text reason. For each alert the platform records, once each, when the room creator first viewed it (the client reports an alert rendered on an open, visible surface: the observer panel or the dashboard alert list) and when and how the creator first acted on it (release, delete, the creator's next message in the room, or the creator's next activity start or end in the room). A room action closes every open alert in that room raised before it.
> - **[R34.03]** The dashboard lists alerts, and the collaboration log lists analyst notices and peer reviews, only for rooms the caller created; admins see all. Alert, notice and review content follows the confidentiality of observations ([R28.03]).
> - **[R34.04]** An analyst binding in a room with a teacher binding may send that teacher binding a notice with an error kind (`content | strategy`), an error description and a suggestion, optionally about a message of the room's teacher or peer agent. Notices are stored durably, wake the teacher binding through an autonomous trigger subject to autostop, and are capped at 3 per room per 10 minutes and one pending per subject message. Students never see a notice.
> - **[R34.05]** The teacher binding records its reaction to a notice once, as `accepted | partly_accepted | declined` with a reason. A notice with no reaction 15 minutes after creation reads as `unanswered`. The teacher agent keeps the final say over what it tells the room.
> - **[R34.06]** A peer binding's reply is never shown to the room before the room's teacher binding decides on it. Its turn streams nothing to the room and is offered only read-only tools. The teacher binding's review turn sees the draft and the room transcript and decides `approve | defer | modify | reject` with a reason; `approve` posts the draft and `modify` posts the teacher's revised text, both as the peer agent; `reject` posts nothing and tells the peer agent; `defer` re-reviews after 1 to 10 minutes, at most twice. No decision within 60 seconds, a missing teacher binding, or a changed binding expires the reply unposted. A review posts at most one message.
> - **[R34.07]** Every change of an agent's system prompt records a revision with its source (`manual | design | restore`) and actor. Users who may edit the agent can list revisions and restore one.
> - **[R34.08]** A Project Owner can have a design agent of the project draft teacher and peer system prompts from a structured form on the workspace dashboard. The design agent runs on its own key group with no tools; its output is shown beside the current prompts and written only when the owner applies it, to both agents in one transaction, refused if either agent changed since the draft was shown. No LLM writes an agent's configuration.

Amendment to **[R33.10]**, appended to the end of the requirement:

> The bundle also contains `alerts.csv` ([R34.02]), `analyst_notices.csv` ([R34.04], [R34.05]) and `peer_reviews.csv` ([R34.06]), each UTF-8 with a byte-order mark and identified by the same room, agent and participant codes as the other files.

## 14. Open Questions

- Whether the dashboard should later show reaction-time summaries (median, distribution) once the research team has fixed its analysis; recorded as FU-1.
- Whether `modify` should be visible to the human teacher in the room itself (for example a marker on the message visible only to the creator); currently it is visible only in the collaboration log and export.
- The proposal's three-minute AA latency target is a property of the analyst's wake-up configuration, not of this change; whether the CRAT room pack's analyst wake-up should be retuned is a pack-content question for the research team.

## 15. Deviation Log

Appended by /build. Empty means the implementation matches this spec exactly.

## 16. Follow-ups

- FU-1: Reaction-time summaries on the dashboard.
- FU-2: Pre-existing debt in touched files: route-level `AgentService` call in `backend/app/api/v1/agents.py:803`; `turn_engine.py` size; `pending_notify` non-durability for the other notes it carries (released observations, approval ballots).
