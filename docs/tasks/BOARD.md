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
- `2026-07-22-retention-sweep-fixes` (bugfix, draft) — `depends_on: []`. a2a F-17, F-42 plus
  verification-gap V-5. **Resolves a duplicate hand-off**: two audits routed the same purge-audit
  finding to two different slugs; this consolidates under one and records why.
- `2026-07-22-workflow-capability-enforcement` (bugfix, draft) — `depends_on: []`. a2a F-13 plus
  config F-21: the three `workflow_capabilities` flags are stored, displayed and inherited but read
  by nothing. **Blocked on Q-8** (migration posture) — enforcing without a backfill breaks every
  working approval gate on deploy.
- `2026-07-22-settings-form-reconciliation` (bugfix, draft) — `depends_on: []`. a2u F-7, F-8 plus
  verification-gap V-4. **Corrects the a2u audit's own §3 coupling note**: F-8 is not contingent on
  F-1, and the evidence is in its Q-1.
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
- `2026-07-07-graphrag-two-axis-redesign` (feature, approved) — `depends_on: []`. This is
  a blueprint dossier: approval authorizes the target design, and its phases are meant to
  become separate `/build` dossiers (see its own §1). Open question: `docs/tasks/2026-07-07-graphrag-phase0..4b-*`
  already exist and are all `status: implemented` with overlapping `[Rxx.yy]` coverage —
  worth confirming with the user whether this blueprint's remaining scope is still live or
  its status is simply stale, before treating it as unblocked work.

## Blocked


## In progress

- `2026-07-19-large-artifacts-silently-dropped` (bugfix) — `depends_on: []`.
Removed on 2026-07-28 because their own frontmatter reads `implemented` and the board only
lists unfinished work: `2026-07-22-activity-session-authz-and-validation`,
`2026-07-22-workflow-run-cancellation`, `2026-07-28-activity-schema-participant-access`.
Also removed on 2026-07-29 after implementation: `2026-07-22-reingest-allowlist-propagation`,
`2026-07-29-knowledge-ingest-concurrency-and-enqueue`,
`2026-07-29-knowledge-upload-resource-bounds`, `2026-07-29-knowledge-ingest-ports`,
and `2026-07-29-knowledge-document-ui-split`.
