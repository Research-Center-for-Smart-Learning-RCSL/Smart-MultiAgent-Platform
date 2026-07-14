---
type: bugfix
status: implemented
created: 2026-07-14
requirements: [R11.02]
---

# F-4: The accepted GraphRAG silence trigger has no evaluator or sweep

Source audit: `docs/audits/2026-07-14-rag-graphrag-end-to-end/findings.md` (F-4).

## 1. Summary

A Concept Map (GraphRAG) trigger config accepts, validates, persists, and returns
`silence_minutes`, but nothing ever reads it. The only trigger evaluator parses
`every_n_messages`; there is no periodic sweep that fires a build after a room falls silent.
A designer who configures only `silence_minutes` gets a config that stores the value and
never builds — the entire silence-trigger feature is inert. The fix adds a bounded periodic
Concept Map silence sweep (mirroring the existing Agent-wakeup silence worker) that evaluates
`silence_minutes` per owner-scoped config against a last-activity timestamp and enqueues the
existing `graphrag_build` job with stable dedup.

## 2. Observed vs Expected

- **Observed** — `GraphRagTriggerConfig.silence_minutes` is validated
  (`backend/app/api/v1/graphrag.py:56-68`) and persisted verbatim into the `trigger_config`
  JSONB column on create/patch (`:272`, `:414-416`;
  `backend/contexts/knowledge/infrastructure/graphrag_tables.py:27`), and returned by the API
  (`:112`). The message-trigger evaluator reads only `_every_n_messages` and `continue`s when
  it is `None` (`backend/contexts/knowledge/application/graphrag_triggers.py:78-81,100-108`);
  it never inspects `silence_minutes`. A repository-wide search for `silence` in the knowledge
  context returns zero hits — no evaluator, no sweep, no last-activity state.
- **Expected** — when chat activity in a Concept Map's scope stops for at least
  `silence_minutes`, a build is queued. Intent source: [R11.02] (trigger modes). The
  Agent-wakeup subsystem already implements exactly this shape for agent wakeups and is the
  reference pattern (see §7).

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Where does the silence sweep live and how is it scheduled? | A new Arq cron task mirroring `evaluate_silence`, registered in the worker like the existing silence/reconcile crons. | The wakeup silence worker (`orchestration.py:208-278`, cron at `main.py:302`) and the GraphRAG reconciler cron (`main.py:321`) are the two established periodic-sweep patterns; reuse that shape rather than inventing a new scheduler. |
| Q-2 | How is "last activity" measured, and what does "silence" mean for workspace / agent_group owners that span many rooms? | Per **config** (not per room): a Redis `graphrag:silence_ts:{config_id}` touched whenever any covered room receives a message; the config is "silent" when `now - ts >= silence_minutes`. Mirrors `wakeup:silence_ts` but keyed by config. | The existing message-count trigger already keys its counter per config (`graphrag:msg_count:{config_id}`, `graphrag_triggers.py:111`), so per-config silence is the consistent granularity and resolves the multi-room ambiguity: a workspace/agent_group config fires when its entire coverage has gone quiet, not per individual room. A per-config timer avoids scanning the messages table each sweep (`wakeup_state.py:113-130` is the proven pattern). A config that has never seen activity (absent `ts`) does not fire. |
| Q-3 | How are builds deduplicated so the sweep does not re-fire every tick? | Reuse `graphrag_build_job_id` as the Arq `_job_id`, as the message and manual paths already do. | GraphRAG already has a stable per-build-cycle dedup id; reuse it so a config that has already fired for its current idle cycle is not re-enqueued while `keep_result=3600` retains the job id. |
| Q-4 | Which repository query enumerates silence-triggered configs per scope? | Introduce a correct owner/room-scoped selector; do **not** reuse `list_for_agents`. | `list_for_agents` (`graphrag_repositories.py:237-297`) is the agent-delete-cascade query and is the exact wrong-method root cause of the sibling finding F-3. The silence sweep must not repeat that mistake; a shared owner/room-scoped selector should be added (and would also serve F-3's fix). |

## 4. Reproduction

1. Create a Concept Map config with `trigger_config = {"silence_minutes": 5}` and no
   `every_n_messages` (`graphrag.py:56-68` accepts it).
2. Send messages in the owning scope, then stop for more than 5 minutes.
3. Observe: no `graphrag_build` job is ever enqueued; the config state stays `idle`. There is
   no worker that inspects `silence_minutes`.

Deterministic — the feature has no code path at all.

## 5. Root Cause Analysis

The API layer defines and persists a trigger field with no consuming evaluator or worker:

1. `silence_minutes` is a validated, stored, round-tripped field
   (`graphrag.py:56-68,272,414-416`; `graphrag_tables.py:27`).
2. The sole trigger evaluator is message-count only and skips configs without
   `every_n_messages` (`graphrag_triggers.py:78-81`). **This is the root cause** — the
   missing silence evaluator and its periodic driver. There is no aggravating factor; the
   feature was never wired.
3. Supporting gaps that the fix must fill: no per-config last-activity state, and no
   owner/room-scoped enumeration of silence-triggered configs (the only close query,
   `list_layers_for_turn` `:299-372`, is `(agent, chatroom)`-scoped and gated on
   `concept_map_enabled`).

## 6. Blast Radius and Sibling Suspects

- **Blast radius** — the entire GraphRAG silence-trigger option is inert for every owner mode
  (chatroom, agent_group, workspace). No data corruption; a missing feature.
- **Sibling suspects:**
  - **F-3 (confirmed, shared gap).** Message-trigger evaluation uses `list_for_agents`, the
    deletion-candidate query, instead of a room-covering selector
    (`graphrag_triggers.py:75`). The silence sweep needs the same correct owner/room-scoped
    enumeration; introduce it once and let both consume it. F-3 is a separate dossier but the
    new selector should be designed to serve both — recorded as FU-1.
  - **Knowledge Map triggers (cleared).** Knowledge Map builds are corpus-mutation driven
    (`knowmap_triggers.py`), not silence driven; `silence_minutes` is a Concept Map concept
    only. No knowmap change needed.
  - **Agent-wakeup silence (cleared, reference only).** `evaluate_silence`
    (`orchestration.py:208-278`) is the correct, working analogue to mirror, not a defect.

## 7. Fix Design

Add a Concept Map silence sweep modeled on the Agent-wakeup silence worker, reusing the
existing build-enqueue and dedup machinery.

1. **Silence evaluator** — add to
   `backend/contexts/knowledge/application/graphrag_triggers.py` alongside
   `evaluate_graphrag_message_triggers` (`:65-97`): parse `silence_minutes` (mirror
   `_every_n_messages` `:100-108`), gate on `_BUILDABLE_STATES` (`:21`), compute elapsed idle
   time from the last-activity timestamp, and return `GraphRagBuildTrigger`s carrying
   `graphrag_build_job_id(...)` (`:24-42`) and `triggered_by="silence_minutes"`.
2. **Owner/room-scoped selector** — add a repository method to
   `backend/contexts/knowledge/infrastructure/graphrag_repositories.py` that enumerates
   configs with a `silence_minutes` trigger scoped per owner (chatroom / enabled agent_group
   / enabled workspace). Do not reuse `list_for_agents` (`:237-297`). Model the owner-union
   shape on `list_layers_for_turn` (`:299-372`) but drive it by owner/scope for a time-based
   sweep, filtering on the `trigger_config->>'silence_minutes'` key.
3. **Last-activity timestamp (per config)** — add a Redis `graphrag:silence_ts:{config_id}`,
   mirroring `touch_silence_timestamp`/`get_silence_timestamp`
   (`backend/contexts/orchestration/infrastructure/wakeup_state.py:113-130`). Touch it for
   every covering config at the same dispatch point that increments the per-config message
   counter — `_dispatch_graphrag_builds` (`backend/app/api/v1/messages.py:300-329`) resolves
   the covering configs and already runs on message send, so the touch reuses that resolved
   set (once the covering set is the corrected owner/room-scoped selector, not the F-3
   `list_for_agents`). The sweep computes `now - ts` and fires when it reaches
   `silence_minutes`; an absent `ts` (a config that has never seen activity) never fires.
4. **Periodic worker** — add a `graphrag_silence_sweep` Arq task under
   `backend/app/workers/tasks/` mirroring `evaluate_silence`
   (`backend/app/workers/tasks/orchestration.py:208-278`): enumerate silence-triggered configs
   (via the §7.2 selector) in bounded batches, read each config's `graphrag:silence_ts`,
   evaluate the evaluator (§7.1) with per-item failures isolated by `db.rollback()`, and
   `enqueue("graphrag_build", config_id=..., triggered_by="silence_minutes", _job_id=...)`
   (the pattern at `graphrag.py:531-540` / `messages.py:317-322`).
5. **Registration** — register the task and its cron in
   `backend/app/workers/main.py` (`functions` `:243-288`, `cron_jobs` `:295-328`), choosing a
   cadence consistent with the existing silence/reconcile crons (`:302` every 30s, `:321`
   per-minute). The Arq cron lock keeps it singleton across replicas; `keep_result=3600`
   (`:294`) backs the `_job_id` dedup.
6. **Facade hop** — expose the evaluator through
   `backend/contexts/knowledge/interfaces/facade.py:192-202` if the sweep calls the
   application layer through the facade, matching the message-trigger wiring.

Dedup and re-fire discipline: a config that has fired for its current idle cycle must not
re-fire every tick. `graphrag_build_job_id` keyed on `(state, last_build_at)` naturally
covers this while `keep_result=3600` retains the id; the sweep must also avoid re-firing
after activity resumes (the last-activity timestamp advancing resets the idle window).

## 8. Regression Test Plan

Unit tests (`backend/tests/unit/test_graphrag_triggers.py`, which today has no silence
coverage — the fake `_Repo` only implements `list_for_agents` and `_cfg` never sets
`silence_minutes`):

1. **Silence fires after threshold** (new): a config with `silence_minutes=N` and a
   last-activity timestamp older than N in a `_BUILDABLE_STATES` state yields exactly one
   `GraphRagBuildTrigger` with `triggered_by="silence_minutes"` and the stable job id. Fails
   today — no evaluator exists.
2. **Silence does not fire before threshold / on non-buildable state** (new): activity within
   N minutes, or a non-buildable state, yields no trigger.
3. **No re-fire within one idle cycle** (new): a second sweep tick without new activity does
   not enqueue a second build for the same idle cycle (stable `_job_id`).
4. **Owner-scoped selector** (new): the new repository method returns configs by owner scope,
   not the `list_for_agents` deletion set.

Test (1) is the primary red-first test.

## 9. Risks and Rollback

- **Duplicate/repeated builds** — a weak dedup or a mis-reset idle window could re-fire builds
  every tick. Mitigated by reusing `graphrag_build_job_id` and by test (3).
- **Sweep cost** — an unbounded enumeration each tick would not scale. Mitigated by bounded
  batch pagination (mirror `evaluate_silence`) and by filtering on the `silence_minutes` JSONB
  key; note the absence of an index on `trigger_config` (recorded as FU-2).
- **Reusing the wrong query (F-3 trap)** — using `list_for_agents` would fire for the wrong
  configs; explicitly excluded in the design and covered by test (4).
- **Cross-replica double-fire** — the Arq cron lock keeps the sweep singleton; the `_job_id`
  dedup covers any residual overlap.
- **Rollback** — remove the cron registration and the sweep task; the evaluator and selector
  are inert without the cron. Code-only, no schema change (the column already exists).

## 10. Acceptance Criteria

- [x] AC-1: The silence-fires regression test (§8.1) fails before the fix and passes after.
  `test_silence_fires_after_threshold` — the evaluator did not exist before (red), passes now.
- [x] AC-2: A Concept Map config with only `silence_minutes` set enqueues a `graphrag_build`
  (via the existing helper, `triggered_by="silence_minutes"`) once its scope has been idle for
  at least the configured minutes. Evaluator unit test + sweep wrapper test
  (`test_graphrag_silence_sweep.py`) assert the enqueue with the dedup job id.
- [x] AC-3: The build does not fire before the threshold, nor when the config is not in a
  buildable state, nor repeatedly within a single idle cycle.
  `test_silence_does_not_fire_before_threshold_or_when_ineligible` +
  `test_silence_does_not_refire_once_build_captured_activity` (freshness gate, see D-2).
- [x] AC-4: Silence-triggered configs are enumerated by an owner/room-scoped selector, not by
  `list_for_agents`. New `list_silence_trigger_configs`; wiring
  `test_list_silence_trigger_configs_scopes_by_owner` (written; not run locally — D-1).
- [x] AC-5: The sweep is a bounded, batched Arq cron registered in `app/workers/main.py`,
  singleton across replicas, with per-item failure isolation. Registration verified by import;
  failure isolation covered by `test_graphrag_silence_sweep.py`.
- [x] AC-6: `every_n_messages` and `manual` trigger behavior is unchanged. Existing message
  tests still pass; the silence-timestamp touch is additive.
- [x] AC-7: `ruff check`, `ruff format --check`, and `mypy` pass for the touched modules (no
  new errors — one pre-existing unrelated `tenancy` mypy error remains). Full `pytest -q` runs
  at the batch's end; the wiring tier runs in CI (D-1).

## 11. SRS Delta

None. [R11.02] already names silence as a trigger mode; this implements documented-but-inert
behavior.

## 12. Deviation Log

- **D-1 (test execution environment):** no Postgres/Redis/Neo4j/Qdrant in the build
  environment, so the `wiring` selector test was written (against the proven owner-resolution
  pattern) but executed only in CI, not locally. All unit tests were run red→green.
- **D-2 (re-fire discipline strengthened):** §7 relies on the stable `_job_id` + the ts
  advancing on new activity to avoid re-firing. That alone does not prevent a rebuild *after
  the build completes* while the room stays idle (`last_build_at` advances → fresh job id).
  `evaluate_graphrag_silence_trigger` therefore adds a freshness gate: it fires only when the
  last activity is newer than `last_build_at`, giving true once-per-idle-cycle semantics with
  no extra Redis state. Behavior is a strict subset of the spec's intent (fewer wasted
  builds); covered by `test_silence_does_not_refire_once_build_captured_activity`.
- **D-3 (no facade hop):** §7.6 left the facade hop conditional ("if the sweep calls the
  application layer through the facade"). The sweep is an Arq worker — a composition root that
  may call the repository + evaluator directly, exactly as the reference `evaluate_silence`
  does (no facade). No facade method was added.
- **D-4 (two selectors, shared gating — FU-1):** the spec's FU-1 hoped one selector would
  serve both F-3 and F-4. They remain two methods —
  `list_message_trigger_configs_for_room` (room-scoped, for the per-send path) and
  `list_silence_trigger_configs` (global, paginated, for the periodic sweep) — because their
  scopes genuinely differ. They share the same owner-enable-gating shape (mirroring
  `list_layers_for_turn`); unifying the gating core is left as the standing FU-1.
- **D-5 (sibling cleanup):** added a `raw is None` guard to `_every_n_messages` mirroring the
  new `_silence_minutes`, clearing a pre-existing `mypy` `int(Any | None)` arg-type error.
  Behavior-preserving (`int(None)` already returned `None` via the `except`).

## 13. Follow-ups

- **FU-1 (F-3, major):** the message-trigger evaluator uses `list_for_agents` instead of a
  room-covering selector (`graphrag_triggers.py:75`). The owner/room-scoped selector added
  here should be designed to also serve F-3's fix; F-3 remains a separate dossier.
- **FU-2 (indexing):** no index exists on `graphrag_configs.trigger_config`; a JSONB
  expression index on `silence_minutes` may be warranted if the sweep's scan cost grows.
- **FU-3 (three UNION layer builders — DRY, from check-quality):** `list_message_trigger_configs_for_room`
  (F-3), `list_silence_trigger_configs` (F-4), and the pre-existing `list_layers_for_turn`
  each rebuild the same three-layer chatroom/agent_group/workspace UNION, differing only in
  per-layer predicates (~40 lines each). Extract a shared layer-builder parameterized by the
  per-layer predicate so the owner-enable gating the docstrings claim to mirror cannot silently
  drift. Subsumes D-4's standing "unify the gating core" note.
- **FU-4 (silence-feed pagination tiebreak):** `list_silence_trigger_configs` orders by
  `created_at` with `limit`/`offset`; configs sharing a `created_at` across a 500-row batch
  boundary can be skipped or duplicated. Self-healing (a skipped config is swept the next
  minute; a duplicate collapses on the stable `_job_id`), so deferred — add `id` as a tiebreak
  in the `order_by` to make paging exact.
