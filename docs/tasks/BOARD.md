# Task Board

Derived view over every dossier under `docs/tasks/` that is not
`implemented`/`superseded`/`abandoned`, grouped by `depends_on` + `status` per the rules
in `README.md`. If this file and a dossier's own frontmatter disagree, the frontmatter
wins — this is a cache, not a second source of truth. Maintained by `/spec` (adds a row
on dossier creation) and `/build` (moves a row on every status change).

Backfilled 2026-07-20 for the dossiers active at that date; the other ~80 dossiers under
`docs/tasks/` were already `implemented`/`superseded` and are intentionally not listed
here (see README.md's Dependencies and sequencing section for why untouched history
doesn't need a `depends_on` backfill).

## Ready now

Nothing blocking; these can start in any order relative to each other, including in
parallel.

- `2026-07-22-a2a-scope-context-wiring` (bugfix, draft) — `depends_on: []`. From
  `docs/audits/2026-07-22-agent-to-agent-orchestration/` F-9, F-24, F-25, F-26 and
  `docs/audits/2026-07-22-agent-config-runtime/` F-4. One root cause: every authorization and
  budget identity in the A2A subsystem is declared and never populated. **Carries an open user
  decision** (what "attached to a workflow run" means) that must be answered before implementation
  — it is an authorization boundary and is deliberately not guessed.
- `2026-07-22-turn-idempotency-and-locking` (bugfix, draft) — `depends_on: []`. From the a2a audit
  F-7, F-18, F-22, F-23, F-39 and the config audit F-8, F-30. Six sequenced commits; one hard
  ordering constraint (cleanup before lock-liveness). Names two textual adjacencies with the
  compaction and tool-dispatch dossiers.
- `2026-07-22-approval-resume-claim-reliability` (bugfix, draft) — `depends_on: []`. From the a2a
  audit F-31, F-32 and the config audit F-18. Approval-gate side effects dispatched pre-commit,
  plus a claim key that can expire inside its own consumer's retry budget.
- `2026-07-22-subagent-spawn-fail-fast` (bugfix, draft) — `depends_on: []`. From the a2a audit F-1
  and the config audit F-3. Makes the dead `subagent_spawn` node fail fast instead of parking for
  half an hour. **Deviates from the a2a triage**, which grouped five findings here: only one is
  actionable today, the other four are latent until sub-agent execution is built and are recorded
  as follow-ups for that feature dossier.
- `2026-07-22-compaction-scoping-and-durability` (bugfix, draft) — `depends_on: []`. From
  `docs/audits/2026-07-22-agent-config-runtime/` F-5, F-7, F-15 (all major): one agent's
  compaction truncates every other agent's history in the room (violating `[R9.09]`), an empty
  summary is accepted and permanently elides its range, and the compaction lock is released
  before the summary commits. Three independent defects on one change surface; includes a
  dry-run repair command. Two open decisions for the user in §3 (Q-7 legacy rows, Q-8 the
  room-level `/compact` flag). Coordinate with the a2a audit's turn-locking dossier.
- `2026-07-22-tool-dispatch-failure-categories` (bugfix, draft) — `depends_on: []`. From
  `docs/audits/2026-07-22-agent-config-runtime/` F-6, F-16, F-17 (all major): a tool's DB
  failure poisons the turn session and destroys an already-streamed reply, truncated tool-call
  JSON silently becomes empty arguments, and a failed final synthesis is persisted as the
  answer. Six sequenced commits; needs one empirical check before the design is fixed (§3 Q-2).
- `2026-07-22-mcp-tool-contract` (bugfix, draft) — `depends_on: []`. From
  `docs/audits/2026-07-22-agent-config-runtime/` F-12, F-14 (both major): MCP bindings store
  opaque strings, so tools are advertised with no parameter schema and an unvalidated tool name
  can brick every turn for an agent. One shared root cause; migration 0062 plus a driver change.
  Ship driver-first.
- `2026-07-22-egress-allowlist-provisioning` (bugfix, draft) — `depends_on: []`. From
  `docs/audits/2026-07-22-agent-config-runtime/` F-9 (major): nothing ever seeds the egress
  allowlist, so `web_search` — enabled by default — is denied on first use in every new
  project. **Carries a non-empty SRS Delta**: the fix seeds one hostname on search-key
  activation rather than four per project, which requires amending `[R12.16]` at approval.
  Includes a derived, insert-only backfill migration.
- `2026-07-22-prompt-assistant-delivery-recovery` (bugfix, draft) — `depends_on: []`. From
  `docs/audits/2026-07-22-agent-config-runtime/` F-13 (major): the prompt-assistant channel has
  no durable read side, so a lost frame loses a paid-for reply and can permanently disable the
  composer. Adds a session read endpoint, refetch-on-connect, and a watchdog.
- `2026-07-22-reingest-allowlist-propagation` (bugfix, draft) — `depends_on: []`. From
  `docs/audits/2026-07-22-agent-config-runtime/` F-11 (major): re-uploading a document discards
  the submitted per-agent allowlist on all four ingestion entry points, so the retry path cannot
  correct a wrong binding. Backend plus a frontend 409 handler.
- `2026-07-22-egress-redirect-classification` (bugfix, draft) — `depends_on: []`. From
  `docs/audits/2026-07-22-agent-config-runtime/` F-10 (major): a 3xx from a function tool is
  delivered to the model as a successful empty result, because the proxy deliberately does not
  follow redirects and the caller drops the `Location` header. Application-layer only; the
  egress proxy is explicitly not modified.
- `2026-07-22-model-hint-provider-routing` (bugfix, draft) — `depends_on: []`. From
  `docs/audits/2026-07-22-agent-config-runtime/` F-2 (critical): an agent's `model_hint` does
  not constrain provider routing, so a mixed-provider key group silently runs the agent on a
  different provider and model. Touches `contexts/keys` routing, `turn_engine.py` model
  resolution and payload construction, and `summariser.py`; no migration.
- `2026-07-22-join-epoch-loop-reentry` (bugfix, draft) — `depends_on: []`. a2a F-11: an `any`/`count`
  join reached by a loop back-edge fires once and stalls, because the one-shot latch is claimed at
  `fire_threshold` arrivals and released only at `total_branches`. Folds in an ALL-mode deadlock the
  audit did not name. **Recommended to land before** `wait-for-event-timer-and-join-ports`.
- `2026-07-22-a2a-event-trigger-loop-guard` (bugfix, draft) — `depends_on: []`. a2a F-4: an
  `a2a_event` trigger whose workflow calls the same agent self-amplifies without bound, one full
  agent turn per iteration on the user's own key. Carries a **non-empty SRS Delta** drafting
  `[R14.07a]`, and one open decision (Q-3, the trigger budget value).
- `2026-07-22-a2a-delivery-idempotency` (bugfix, draft) — `depends_on: []`. a2a F-5, F-19, F-20.
  Grouped by change surface only, and says so: an `XAUTOCLAIM` that reads PEL idle time as
  liveness, a `requeue` whose `LTRIM` keeps the wrong end of the queue, and a supervisor whose
  liveness key `mkstream=True` recreates.
- `2026-07-22-instruct-terminal-state-guard` (bugfix, draft) — `depends_on: []`. a2a F-15, F-16:
  the instruct terminal state is an unguarded `UPDATE`, so a completed instruct can be persisted
  as `TIMEOUT`; and the deadline job commits before enqueueing its resume, so its own retry reads
  its own write and gives up. Carries a **deliberate behaviour change** (Q-2, timeout wins).
- `2026-07-22-wakeup-trigger-state-and-bounds` (bugfix, draft) — `depends_on: []`. **a2a** F-3, F-12,
  F-14, F-21, F-38 plus config F-24. (This row previously read "a2u"; corrected — the a2u audit has
  no F-38, and its F-3/F-12 belong to `attachment-lifecycle-and-rendering` and
  `activity-session-authz-and-validation` respectively.) Silence triggers never fire for bindings created after the
  presence edge; designer soft-bounds are erased on first self-modification; `refresh_every_hours`
  is never read. Two open decisions (Q-2 clock storage, Q-3 frontend defaults).
- `2026-07-22-workflow-dispatch-reliability` (bugfix, draft) — `depends_on: []`. a2a F-33, F-34,
  F-35, F-37, F-41. Names an **unowned gap** in §13 FU-1: workflow-task retry-safety belongs to no
  dossier and needs its own.
- `2026-07-22-retention-sweep-fixes` (bugfix, draft) — `depends_on: []`. a2a F-17, F-42 plus
  verification-gap V-5. **Resolves a duplicate hand-off**: two audits routed the same purge-audit
  finding to two different slugs; this consolidates under one and records why.
- `2026-07-22-workflow-capability-enforcement` (bugfix, draft) — `depends_on: []`. a2a F-13 plus
  config F-21: the three `workflow_capabilities` flags are stored, displayed and inherited but read
  by nothing. **Blocked on Q-8** (migration posture) — enforcing without a backfill breaks every
  working approval gate on deploy.
- `2026-07-22-attachment-lifecycle-and-rendering` (bugfix, draft) — `depends_on: []`. a2u F-3, F-14
  plus verification-gap V-3. Carries an explicit **do-not-do warning**: the SVG finding's one-line
  description misattributes the defect to the backend allowlist, and following it would delete a
  security control.
- `2026-07-22-chatroom-socket-lifecycle` (bugfix, draft) — `depends_on: []`. a2u F-1, F-4, F-18.
  **Ordering conflict with `reconnect-reconciliation`** — the two dossiers reached opposite
  conclusions about which lands first; see that dossier's §3 conflict note. User decides.
- `2026-07-22-reconnect-reconciliation` (bugfix, draft) — `depends_on: []`. a2u F-11, F-13, F-17,
  F-19 plus verification-gap V-2. Adds a nullable `approvals.chatroom_id` and a room-scoped list
  endpoint. Same ordering conflict as above.
- `2026-07-22-settings-form-reconciliation` (bugfix, draft) — `depends_on: []`. a2u F-7, F-8 plus
  verification-gap V-4. **Corrects the a2u audit's own §3 coupling note**: F-8 is not contingent on
  F-1, and the evidence is in its Q-1.
- `2026-07-22-presence-transition-and-release-wakeup` (bugfix, draft) — `depends_on: []`. a2u F-5,
  F-21. Imposes a **hard constraint on the socket-lifecycle dossier**: any keepalive interval must
  stay below `_CONN_TTL_SECONDS = 150`.
- `2026-07-22-turn-outcome-reporting` (bugfix, draft) — `depends_on: []`. a2u F-6, F-9, F-15 plus
  a2a F-40. A committed reply is recorded as a failed turn when the post-commit publish raises.
  Names two test-locked decisions that must be decided, not silently edited.
- `2026-07-22-observation-binding-cleanup` (bugfix, draft) — `depends_on: []`. a2u F-10: removing
  the last observer binding hides the Observer tab entirely, stranding the creator's own analyses
  with no route to read, release or delete them. Frontend only; **no migration permitted**.
- `2026-07-22-chat-export-authz-and-polling` (bugfix, draft) — `depends_on: []`. a2u F-2, F-16.
  Permission-matrix row 19 is dead code: any room reader, including a guest, can export every
  participant's messages and edit history. **Four blocking questions (Q-1..Q-4) must be answered
  before implementation.** `check-security` referral in parallel.
- `2026-07-22-wait-for-event-timer-and-join-ports` (bugfix, draft) — `depends_on: []`. a2a F-2,
  F-36: a `timer` wait — the editor's seeded default — parks until its timeout and exits the
  failure port, and the join `timeout` port is documented, linted, rendered and produced by
  nothing. **Q-2 open**: build the join timeout or record its absence. If the user chooses to
  build it, `depends_on` becomes `[2026-07-22-join-epoch-loop-reentry]` (its Q-3).
- `2026-07-22-search-determinism-and-highlighting` (bugfix, draft) — `depends_on: []`. a2u F-22
  plus verification-gap V-6: search orders by a non-unique `rank` under `LIMIT`/`OFFSET`, so the
  same query can return a different page; and the highlight marker exists in three incompatible
  forms across backend, sanitiser and CSS. Touches DOMPurify config — §7.2 states what must not
  weaken.
- `2026-07-22-activity-session-authz-and-validation` (bugfix, draft) — `depends_on: []`. a2u F-12,
  F-20 plus verification-gap V-7: any room member (including a guest) can close another
  participant's activity session, and the stalled-validation watchdog notifies nobody.
  **AuthZ defect**; `check-security` referral is AC-13, a deliverable rather than a gate.
- `2026-07-22-pending-notify-room-routing` (bugfix, draft) —
  **`depends_on: [2026-07-22-a2a-delivery-idempotency]`** (the only non-empty one in this batch).
  a2a F-8 plus config F-29: an approval note is rendered into whatever room the approver's next
  turn runs in, and is destroyed whether or not that turn votes. The dependency is amplification,
  not code: this fix makes `requeue` a normal-path operation, which raises F-19's exposure.
- `2026-07-07-graphrag-two-axis-redesign` (feature, approved) — `depends_on: []`. This is
  a blueprint dossier: approval authorizes the target design, and its phases are meant to
  become separate `/build` dossiers (see its own §1). Open question: `docs/tasks/2026-07-07-graphrag-phase0..4b-*`
  already exist and are all `status: implemented` with overlapping `[Rxx.yy]` coverage —
  worth confirming with the user whether this blueprint's remaining scope is still live or
  its status is simply stale, before treating it as unblocked work.

## Blocked

Nothing blocked.

## In progress

- `2026-07-22-web-search-cache-project-scoping` (bugfix) — `depends_on: []`.
- `2026-07-22-workflow-run-cancellation` (bugfix) — `depends_on: []`.
- `2026-07-19-large-artifacts-silently-dropped` (bugfix) — `depends_on: []`.
