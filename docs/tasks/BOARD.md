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

- `2026-07-22-turn-idempotency-and-locking` (bugfix, draft) — `depends_on: []`. From the a2a audit
  F-7, F-18, F-22, F-23, F-39 and the config audit F-8, F-30. Six sequenced commits; one hard
  ordering constraint (cleanup before lock-liveness). Names two textual adjacencies with the
  compaction and tool-dispatch dossiers.
- `2026-07-22-subagent-spawn-fail-fast` (bugfix, draft) — `depends_on: []`. From the a2a audit F-1
  and the config audit F-3. Makes the dead `subagent_spawn` node fail fast instead of parking for
  half an hour. **Deviates from the a2a triage**, which grouped five findings here: only one is
  actionable today, the other four are latent until sub-agent execution is built and are recorded
  as follow-ups for that feature dossier.
- `2026-07-22-tool-dispatch-failure-categories` (bugfix, draft) — `depends_on: []`. From
  `docs/audits/2026-07-22-agent-config-runtime/` F-6, F-16, F-17 (all major): a tool's DB
  failure poisons the turn session and destroys an already-streamed reply, truncated tool-call
  JSON silently becomes empty arguments, and a failed final synthesis is persisted as the
  answer. Six sequenced commits; needs one empirical check before the design is fixed (§3 Q-2).
- `2026-07-22-prompt-assistant-delivery-recovery` (bugfix, draft) — `depends_on: []`. From
  `docs/audits/2026-07-22-agent-config-runtime/` F-13 (major): the prompt-assistant channel has
  no durable read side, so a lost frame loses a paid-for reply and can permanently disable the
  composer. Adds a session read endpoint, refetch-on-connect, and a watchdog.
- `2026-07-22-reingest-allowlist-propagation` (bugfix, draft) — `depends_on: []`. From
  `docs/audits/2026-07-22-agent-config-runtime/` F-11 (major): re-uploading a document discards
  the submitted per-agent allowlist on all four ingestion entry points, so the retry path cannot
  correct a wrong binding. Backend plus a frontend 409 handler.
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
- `2026-07-22-reconnect-reconciliation` (bugfix, draft) — `depends_on: []`. a2u F-11, F-13, F-17,
  F-19 plus verification-gap V-2. Adds a nullable `approvals.chatroom_id` and a room-scoped list
  endpoint. **Sequenced second** by the 2026-07-24 tie-break; must re-derive F-11/F-13 severity
  against the post-fix baseline and owns the Q-1a `onStatus` textual conflict as second merger.
- `2026-07-22-settings-form-reconciliation` (bugfix, draft) — `depends_on: []`. a2u F-7, F-8 plus
  verification-gap V-4. **Corrects the a2u audit's own §3 coupling note**: F-8 is not contingent on
  F-1, and the evidence is in its Q-1.
- `2026-07-22-presence-transition-and-release-wakeup` (bugfix, draft) — `depends_on: []`. a2u F-5,
  F-21. Imposes a **hard constraint on the socket-lifecycle dossier**: any keepalive interval must
  stay below `_CONN_TTL_SECONDS = 150`.
- `2026-07-22-turn-outcome-reporting` (bugfix, draft) — `depends_on: []`. a2u F-6, F-9, F-15 plus
  a2a F-40. A committed reply is recorded as a failed turn when the post-commit publish raises.
  Names two test-locked decisions that must be decided, not silently edited.
  **PRIORITISE — its C3 was meant to merge before or with `chatroom-socket-lifecycle`, which
  landed first on 2026-07-24.** F-15 is therefore unmasked: the socket churn used to cancel the
  client thinking-watchdog on every reconnect, so a pre-stream assembly window over 120s now
  reports a healthy turn as `timeout` every time instead of intermittently. See that dossier's
  FU-8.
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

- `2026-07-22-activity-session-authz-and-validation` (bugfix) — `depends_on: []`. a2u F-12,
  F-20 plus verification-gap V-7: activity-session AuthZ + watchdog notification + optional
  enum-array assembly.
- `2026-07-22-workflow-run-cancellation` (bugfix) — `depends_on: []`.
- `2026-07-19-large-artifacts-silently-dropped` (bugfix) — `depends_on: []`.
