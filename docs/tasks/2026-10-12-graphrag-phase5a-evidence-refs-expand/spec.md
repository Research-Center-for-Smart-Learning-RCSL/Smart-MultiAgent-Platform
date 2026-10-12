---
type: refactor
status: in-progress
created: 2026-10-12
requirements: [R11.03, R11.15, R11.23, R11a.01]
depends_on: []
---

# GraphRAG Phase 5a: expand the Neo4j evidence property to `evidence_refs`

## 1. Summary

The graph engine's evidence list is a neutral, opaque reference token in Python
(`Triple.evidence_refs`, [R11.15]), but in Neo4j it is still stored as the relationship
property `evidence_msg_ids`, a name from when evidence was always a message id. Knowledge
Maps already store document tokens (`<document id>#<chunk>`) in it
(`knowmap_triple_extractor.py:7-9`). The rename was deferred by the two-axis blueprint
(`docs/tasks/2026-07-07-graphrag-two-axis-redesign`, AC-21, D-1) and never done. Under the
rolling-deploy rule ([O4.03], `docs/operations.md:142-145`: "Never rename a column in a
single migration — add new, copy, cut-over, drop old") it takes three releases. This
dossier is the first: release A writes both keys and still reads the old one, and an
operator CLI copies existing edges to the new key. Its sequel,
`2026-10-12-graphrag-phase5a2-evidence-refs-contract`, switches reads (release B) and
stops writing the old key (release C).

## 2. Motivation

- The physical name misdescribes what it holds, and the misdescription has already leaked
  past the driver: the retrieval service reads the raw alias
  `row.get("evidence_msg_ids")` (`backend/contexts/knowledge/application/graphrag_retrieve.py:159`),
  so the application layer depends on a Neo4j spelling. Phase 0 recorded the driver as
  the sole translation seam (`docs/tasks/2026-07-07-graphrag-phase0-engine-cleanup/spec.md:143-146`);
  that holds for writes only (`neo4j_driver.py:211`).
- The rename was deferred to "the document-evidence phase" because "no Neo4j migration
  harness exists" (Phase 0 Q-2, `:83`; FU-A, `:293-295`). Document evidence has landed;
  the harness is the missing piece this dossier builds.

## 3. Non-goals

- The key the extraction prompts ask the model to output, `evidence_msg_ids` in the
  model's JSON (`triple_extractor.py:51, 164`; `knowmap_triple_extractor.py:51`). It is a
  contract with the model, not stored data (Q-4); FU-1.
- Any other property or label: `graphrag_config_id` (part of every index,
  `backend/smap/bootstrap/neo4j_init.py:35-41`), `source_member_ids`, `:Entity`, `:REL`.
  The blueprint asked only for the evidence key.
- Switching reads to the new key and stopping writes of the old one: the contract dossier.
- A general Neo4j migration framework. The CLI is a one-off maintenance command beside the
  existing ones.

## 4. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | How far does the rename go? | The full physical rename, across releases. | Requester's choice. Rejected: moving only the read mapping into the driver and keeping the physical name, and dropping the rename. |
| Q-2 | How is the work split into dossiers? | Two: this expand dossier (release A plus the backfill), and a contract dossier covering release B (switch reads) and release C (stop writing the old key) as two deploy-gated milestones. | Requester's choice, after analysis showed three releases are needed: with A and B merged, an N-1 worker could refresh only the old key on an edge during the rollout while A reads a stale new key, and evidence feeds the Knowledge Map allowlist ([R11.23]). |
| Q-3 | How does the backfill run? | `python -m smap.maintenance neo4j-evidence-refs`, run by an operator: per config, under that config's build lock, batched, idempotent, dry-run until armed, reporting what remains. | Requester's choice. Observable and re-runnable, and its "0 remaining" report is the contract dossier's gate. Rejected: automatic at worker startup (unobservable, racing replicas) and lazy rewrite on next build (Concept Map delta builds never touch older edges, so the old key would never retire). |
| Q-4 | Does the model-facing JSON key change? | No. | Requester's choice. Changing it changes prompt text and can shift extraction quality without an evaluation. |
| Q-5 | Does this depend on another dossier? | `depends_on: []`. | The only other non-terminal dossier, `2026-07-19-large-artifacts-silently-dropped`, touches the code sandbox. `2026-10-12-graph-build-busy-retry` (approved) edits the build workers, not `neo4j_driver.py` or retrieval. |

## 5. Current vs Target Structure

**Current** (all in `backend/contexts/knowledge/infrastructure/neo4j_driver.py` unless
noted):

- Writes: `_EVIDENCE_SET_DELTA` (`:53-55`, Concept Map delta union),
  `_EVIDENCE_SET_REPLACE` (`:74-77`, Knowledge Map per-build reset), `_RESTORE_EDGES_CYPHER`
  (`:187`, no coalesce: a snapshot without the key nulls the property), and the UNWIND row
  key in `_triple_rows` (`:211`).
- Reads, each `RETURN r.evidence_msg_ids AS evidence_msg_ids`: `snapshot_subgraph`
  (`:254`), `list_triples_for_build` (`:375`; its consumer reads no evidence,
  `app/workers/graphrag_reconciler.py:59-74`), `traverse` (`:457`).
- Consumer of the alias: `graphrag_retrieve.py:159`.
- Snapshot: the raw `snapshot_subgraph` dict is JSON-serialized into Redis for 24 h
  (`redis_lock.py:7, 131-135, 148`) and restored by the reconciler
  (`graphrag_reconciler.py:460`) and admin reset (`graph_admin_reset.py:272`). The key
  inside the JSON is data that crosses versions.
- Tests pinning the names: `tests/integration/test_knowmap_neo4j_replacement.py:70, 79, 99,
  174-188` (real Neo4j, `db` tier); `tests/unit/test_graphrag_retrieve.py:105, 276, 284`;
  `tests/unit/test_knowmap_context_provider.py:152`.

**Target after release A:**

- Every write sets both `r.evidence_refs` and `r.evidence_msg_ids` to the same list. The
  delta union and the replace reset compute the new list from the old key (the
  authoritative one during A) and assign it to both.
- Every read returns `coalesce(r.evidence_msg_ids, r.evidence_refs) AS evidence_refs`: the
  old key is authoritative while N-1 code may be writing only it, and the alias is the
  domain name, so the physical spelling stops at the driver.
- `graphrag_retrieve.py:159` reads `row.get("evidence_refs")`.
- Snapshots carry both keys with the same value, so an N-1 restore (which reads
  `row.evidence_msg_ids`) loses nothing. Restore reads
  `coalesce(row.evidence_msg_ids, row.evidence_refs)` and writes both properties, so a
  snapshot taken by N-1 restores intact.
- New maintenance command `neo4j-evidence-refs` (`backend/smap/maintenance/`), and driver
  methods behind it:
  - **Count:** count `REL` edges where `evidence_msg_ids IS NOT NULL` and
    (`evidence_refs IS NULL OR evidence_refs <> evidence_msg_ids`), per config.
  - **Copy:** for one config, set `evidence_refs = evidence_msg_ids` on those edges in
    batches (`... WITH r LIMIT $batch SET ...`, looped).
  - **Lock:** each config is migrated holding its `graphrag:lock:{config_id}` through
    `RedisBuildLockStore` (`redis_lock.py:60-111`). A busy config is skipped and
    reported, and the command exits non-zero so the operator re-runs it.
  - **Dry run:** a dry run only counts. The command is armed by an environment variable,
    as `purge-session-dirs` is (`backend/smap/maintenance/__main__.py:38-73`; the
    `typer` flag defect it documents applies here too).
  - **Report:** what remains after the run.

## 6. Characterization Test Plan

Written before the code moves; each pins today's behavior or the cross-version contract.

- `tests/integration/test_neo4j_evidence_refs_expand.py` (`db` tier, real Neo4j):
  - A legacy edge carrying only `evidence_msg_ids` is read through `traverse` and
    `snapshot_subgraph` with its evidence intact.
  - A delta write and a replace write each leave both keys equal.
  - A snapshot taken by the new code, restored with today's `_RESTORE_EDGES_CYPHER`
    (copied into the test as the N-1 restore), keeps its evidence.
  - A snapshot in today's shape (old key only) restores intact through the new restore.
- The same module, for the CLI:
  - A dry run counts the legacy edges and changes nothing.
  - An armed run copies them and reports 0 remaining.
  - A second armed run is a no-op.
  - A config whose lock is held is skipped, reported, and exits non-zero.
- Existing tests whose assertions move to the new alias without changing their meaning:
  `test_knowmap_neo4j_replacement.py` (`snapshot[...]["evidence_refs"]`),
  `test_graphrag_retrieve.py`, `test_knowmap_context_provider.py`.

## 7. Migration Steps

1. Characterization tests above, red where they name the new behavior.
2. Driver writes: dual-write in `_EVIDENCE_SET_DELTA`, `_EVIDENCE_SET_REPLACE`,
   `_triple_rows`; tests green.
3. Driver reads and snapshot: the `evidence_refs` alias with the old-key-first coalesce;
   snapshot rows with both keys; restore accepting either. Then `graphrag_retrieve.py:159`
   reads the domain alias.
4. The count and copy driver methods and the `neo4j-evidence-refs` command, with its
   runbook entry in `docs/runbook-upgrade.md` (when to run: after release A is on every
   web and worker replica; what "0 remaining" means; that the contract dossier is gated on
   it).
5. Gates, then the §4 reproduction of the knowledge flows on the compose stack (Concept Map
   and Knowledge Map build, retrieval, a snapshot restore through admin reset) as in the
   blueprint's D-6.

## 8. Risks and Rollback

- **Write amplification:** each relation write sets one more list property. The
  evidence lists are bounded by the builders, and the write is the same `SET`.
- **Rollback of release A** to N-1 is safe. N-1 reads and writes only the old key, which A
  kept current, and N-1 restore reads the old key, which A's snapshots carry. The copied
  `evidence_refs` values are inert to N-1.
- **The backfill can only race N-1 code** if it is run before release A is everywhere. The
  runbook entry states the precondition, and the command is idempotent, so running it
  again after the rollout completes repairs any edge an N-1 writer touched.
- **An interrupted backfill** leaves some configs unmigrated. A re-run picks them up, and
  its report says how many remain.

## 9. Acceptance Criteria

- [x] AC-1: every Neo4j write of relation evidence sets `evidence_refs` and
  `evidence_msg_ids` to the same list (characterization test, real Neo4j).
  `test_a_delta_write_sets_both_keys_from_the_old_one`,
  `test_a_replace_write_sets_both_keys`; both red before `52bf3f86`.
- [x] AC-2: every driver read returns the evidence as `evidence_refs`, preferring the old
  property, and no code outside `neo4j_driver.py` names `evidence_msg_ids` as a Neo4j
  property or alias. The model-facing prompt and parser keys stay (grep check; the
  retrieval and context-provider tests pass on the new alias).
  `test_a_legacy_edge_reads_with_its_evidence`, `test_the_old_key_wins_while_both_exist`.
  Outside the driver the name remains in the two extractors' model-facing keys (Q-4) and
  in operator prose (D-4).
- [x] AC-3: a snapshot written by release A restores intact with N-1's restore Cypher, and
  an N-1 snapshot restores intact with release A's (real Neo4j).
  `test_a_release_a_snapshot_restores_with_the_n_minus_1_cypher` (green before and after:
  it pins the cross-version contract), `test_an_n_minus_1_snapshot_restores_with_release_a`
  (red before).
- [x] AC-4: `python -m smap.maintenance neo4j-evidence-refs` counts without arming, copies
  when armed, is idempotent, skips and reports a busy config with a non-zero exit, and
  reports 0 remaining after a full armed run (real Neo4j and Redis).
  `test_the_backfill_counts_copies_and_is_idempotent`,
  `test_the_backfill_skips_a_busy_config`, `test_neo4j_evidence_refs_cli.py`; also run on
  the compose stack (D-3).
- [x] AC-5: Concept Map and Knowledge Map build, retrieval and admin-reset restore behave
  as before on the compose stack, observed live. See D-3.
- [ ] AC-6: backend lint, typecheck and tests (db tier included) pass in CI;
  `docs/runbook-upgrade.md` documents the command and its precondition.

## 10. SRS Delta

None. [R11.15] already names the token `evidence_refs`; [R11.03] and §11.2a describe the
extracted triple's field, which is the model-facing key and stays (Q-4).

## 11. Deviation Log

- **D-1. The UNWIND row key is `evidence_refs`.** §5 lists the row key in `_triple_rows`
  among the writes. It is a query parameter built and consumed inside one statement, not
  stored data, so it takes the domain name outright; both properties are computed from it
  and from the stored old key.
- **D-2. Two additions to the command's contract.** `run()` takes an optional
  `config_ids` scope, used by the db-tier tests so they never touch other data in a
  shared Neo4j; the CLI has no such option and always covers every config. The CLI also
  exits non-zero when an armed run ends with anything remaining, not only when a config
  was busy: edges changed by an N-1 writer during the run leave the gate equally unmet.
- **D-3. Live verification (AC-5) on the compose stack**, branch code in `backend-web` and
  a real arq worker with the OpenAI boundary stubbed (as the blueprint's D-6):
  - A Knowledge Map with two uploaded documents built, and both builds (one on the
    busy retry of `2026-10-12-graph-build-busy-retry`) wrote every relation with
    `evidence_msg_ids = evidence_refs`. An agent turn's system block carried the chunk
    text resolved from that evidence.
  - A chatroom-owned Concept Map built from two messages. Both relations were stored
    with equal keys, `GET /api/graphrag/{id}/graph` returned them, and the next agent turn's
    system block listed them with `evidence: user: Photosynthesis feeds Plants every
    day.`, i.e. message evidence resolved through the new read alias.
  - Admin reset (`POST /api/admin/graphrag/{id}/reset`) on that Concept Map restored a
    snapshot in N-1's shape (old key only), placed in Redis with a current-build
    pointer. This drives `plan_discard`'s compensate path on a settled config
    (`graph_admin_reset.py:240-272`) rather than a build that really crashed. The
    restored edge carried both keys, and the pointer and snapshot were cleared.
  - The command, before any of the above, found 3 legacy edges written by the previous
    code on the stack (dry run: `pending=3`). Armed: `copied=3 remaining=0`, exit 0. A
    re-run: `pending=0`. A legacy edge on a config whose lock was held: `busy=1`,
    exit 1, and the lock still held by its owner. Once the lock was released, `copied=1`
    and exit 0.
- **D-4. The AC-2 grep allows operator prose.** `smap/maintenance/neo4j_evidence_refs.py`,
  the CLI help and `docs/runbook-upgrade.md` name `evidence_msg_ids`, because the command
  exists to migrate that property. None of them holds Cypher; the property is read and
  written only in `neo4j_driver.py`.
- **D-5. The runbook entry also says to run the command in a quiet period on a large
  graph.** Its count scans every `REL` relationship (no index covers the property
  comparison), once before and once after the copy.

## 12. Follow-ups

- **FU-1.** The model-facing key `evidence_msg_ids` in both extraction prompts and parsers.
  Renaming it is a prompt change and wants an extraction-quality check first.
- **FU-2.** `REQUIREMENTS.md:1513` sketches the relation as
  `{type, weight, evidence_message_ids, created_at}`, which matches no version of the
  code. The contract dossier corrects it.
- **FU-3.** Phase 0 FU-B (a one-off `SET n.project_id` backfill for entities written
  before Phase 0) has no script either. The command introduced here is the place one
  would live.
