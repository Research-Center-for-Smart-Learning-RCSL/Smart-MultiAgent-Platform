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
- `2026-07-22-web-search-cache-project-scoping` (bugfix, draft) — `depends_on: []`. From
  `docs/audits/2026-07-22-agent-config-runtime/` F-1 (critical): the `web_search` result
  cache is keyed without tenant identity, so one project's search results are served to
  another. Confined to `contexts/agents/application/tools/web_search.py` plus its unit test;
  no migration.
- `2026-07-07-graphrag-two-axis-redesign` (feature, approved) — `depends_on: []`. This is
  a blueprint dossier: approval authorizes the target design, and its phases are meant to
  become separate `/build` dossiers (see its own §1). Open question: `docs/tasks/2026-07-07-graphrag-phase0..4b-*`
  already exist and are all `status: implemented` with overlapping `[Rxx.yy]` coverage —
  worth confirming with the user whether this blueprint's remaining scope is still live or
  its status is simply stale, before treating it as unblocked work.

## Blocked

Nothing blocked.

## In progress

- `2026-07-19-large-artifacts-silently-dropped` (bugfix) — `depends_on: []`.
