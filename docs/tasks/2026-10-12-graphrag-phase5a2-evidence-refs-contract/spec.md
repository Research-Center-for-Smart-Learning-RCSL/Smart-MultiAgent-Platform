---
type: refactor
status: in-progress
created: 2026-10-12
requirements: [R11.15, R11.23, R11a.01]
depends_on: [2026-10-12-graphrag-phase5a-evidence-refs-expand]
---

# GraphRAG Phase 5a2: switch reads to `evidence_refs`, then retire `evidence_msg_ids`

## 1. Summary

The second half of the Neo4j evidence rename begun by
`2026-10-12-graphrag-phase5a-evidence-refs-expand`. That dossier's release A dual-writes
`evidence_refs` and `evidence_msg_ids` and reads the old key, and its
`neo4j-evidence-refs` command copies every existing edge. This dossier ships two further
releases, each behind a deploy gate:

- **Release B** reads the new key while still writing both.
- **Release C** writes and reads only the new key, after which an armed clean-up removes
  the old property.

Why three releases are needed is set out in the expand dossier's Q-2.

## 2. Motivation

Release A leaves the stored data and the code both carrying two names for one value. Until
the old key is gone, every evidence write costs two properties, and the old misnomer stays
the authoritative one.

## 3. Non-goals

- The model-facing JSON key `evidence_msg_ids` in the extraction prompts and parsers
  (expand Q-4, FU-1).
- Any property other than the evidence key (expand §3).
- Removing snapshot compatibility with the old key before every snapshot that can carry
  it has expired. Snapshots live 24 h (`redis_lock.py:7`).

## 4. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Is this part of the same work as the expand dossier? | Yes. It is the contract half of the split chosen in expand Q-2, covering release B and release C as two milestones. | Requester's choice: two dossiers, the second covering both remaining releases. |
| Q-2 | What gates each release? | **B:** expand is `implemented`, release A runs on every web and worker replica, and the armed `neo4j-evidence-refs` reports 0 remaining. **C:** release B runs on every replica. **The clean-up:** release C runs on every replica and 24 h have passed since release B finished rolling out. | `depends_on` only tracks `implemented`; the deploy state is an operational fact, so each gate is a checked AC with the operator's evidence (command output, deploy record) written into the dossier. |
| Q-3 | Does this depend on another dossier? | `depends_on: [2026-10-12-graphrag-phase5a-evidence-refs-expand]`. | Logical prerequisite: B reads the key A writes and the backfill fills. |

## 5. Current vs Target Structure

**Current (after release A):** the expand dossier's §5 target.

**Release B:**

- **Reads:** return `coalesce(r.evidence_refs, r.evidence_msg_ids) AS evidence_refs`.
  The fallback covers only an edge written by N-1 code after the backfill, which the gate
  excludes.
- **Writes:** still set both keys to the same list, now computed from `evidence_refs`.
- **Snapshots:** still carry both keys.
- **Restore:** reads `coalesce(row.evidence_refs, row.evidence_msg_ids)` and writes both.

B and A interleave safely because both keep the two keys equal.

**Release C:**

- **Writes and snapshots:** carry `evidence_refs` only.
- **Reads:** return `r.evidence_refs AS evidence_refs`.
- **Restore:** still reads `coalesce(row.evidence_refs, row.evidence_msg_ids)`, so a
  snapshot taken by B or A inside its 24 h life restores intact, and writes only
  `evidence_refs`.

C and B interleave safely because B already reads the new key, and C's restore accepts
both.

**Clean-up (after C plus 24 h):**

- `neo4j-evidence-refs --remove-legacy` removes `evidence_msg_ids` per config. It runs
  under the config lock, batched, dry-run until armed, and reports what remains, reusing
  the expand command's structure.
- The restore's fallback to the old snapshot key is then deleted in the same milestone.
- `REQUIREMENTS.md:1513`'s relation sketch is corrected (expand FU-2).

## 6. Characterization Test Plan

- `tests/integration/test_neo4j_evidence_refs_expand.py` gains the release B and C cases:
  - **B:** reads prefer `evidence_refs`, writes keep both keys equal, and a B snapshot
    restores through A's restore (copied into the test) and vice versa.
  - **C:** writes leave no `evidence_msg_ids`, a C snapshot restores through B's restore,
    and a B snapshot restores through C's.
  - **Clean-up:** `--remove-legacy` dry-run counts, armed removes, re-run is a no-op, a
    busy config is skipped with a non-zero exit.

## 7. Migration Steps

1. **Milestone B:** the B read and write changes and their tests; ship release B. The
   gate in Q-2 is recorded before this starts.
2. **Milestone C:** the C changes and their tests; ship release C after B's gate.
3. **Clean-up:** after C's gate, the armed `--remove-legacy` run, then the removal of the
   old-key restore fallback, the `REQUIREMENTS.md:1513` correction, and the final
   compose-stack check of build, retrieval and admin-reset restore.

## 8. Risks and Rollback

- **Rolling back B to A** is safe: both keep the two keys equal, and A reads the old one.
- **Rolling back C to B is safe:** edges C wrote carry only the new key, and B reads and
  computes from `evidence_refs`.
- **Rolling back past B to A is unsafe once C has written:** A reads the old key, which C
  no longer sets. It would need a reverse copy, `evidence_msg_ids = evidence_refs`, first.
  The deploy record for C states this.
- **After the clean-up**, any rollback to A or earlier loses evidence for every edge. The
  clean-up is therefore the last, separately armed step.

## 9. Acceptance Criteria

- [x] AC-1: gate for B recorded — expand `implemented`, release A on every replica, the
  armed backfill's "0 remaining" output pasted here.
- [x] AC-2: release B reads `evidence_refs` and keeps both keys equal on every write; A and
  B snapshots restore across each other (real Neo4j).
- [x] AC-3: gate for C recorded — release B on every replica.
- [ ] AC-4: release C writes and reads only `evidence_refs`; B and C snapshots restore
  across each other (real Neo4j).
- [ ] AC-5: after C on every replica and 24 h, the armed `--remove-legacy` reports 0
  remaining, the old-key restore fallback is removed, and no backend code names
  `evidence_msg_ids` except the model-facing prompt and parser keys (grep check).
- [ ] AC-6: Concept Map and Knowledge Map build, retrieval and admin-reset restore behave
  as before on the compose stack after each release, observed live.
- [ ] AC-7: backend lint, typecheck and tests (db tier included) pass in CI for each
  milestone.

## 10. SRS Delta

None in substance. `REQUIREMENTS.md:1513`'s stale schema sketch is corrected to name
`evidence_refs` (a documentation fix, recorded here so it is not a silent SRS edit).

## 11. Deviation Log

Appended by /build.

- **D-1, gate for B (AC-1), 2026-11-03.** The expand dossier is `implemented`. Staging runs
  one `backend-web` and one `backend-worker` replica, both built from `main` at the phase 6
  deploy of 2026-11-03, which carries release A (`$COMPOSE ps`: both up and healthy for 8
  hours). The dry run reported `pending=9 copied=0 remaining=9`; the armed run reported:

  ```
  neo4j-evidence-refs complete dry_run=False pending=9 copied=9 remaining=0 busy=0 lost_lock=0 failed=0 unattributed=0
  ```

  Neo4j warned in both runs that `evidence_refs` was not yet a known property key, so no
  build had written through release A on staging before the backfill; the nine copied
  edges were the first to carry the new key. Task base for milestone B: `bbef403f`.
- **D-2, milestone B.** The B tests extend `test_neo4j_evidence_refs_expand.py` as §6 says,
  with a helper that makes the old key disagree with the new one, since an edge whose keys
  are equal cannot tell which one a read used. Release A's edge restore is copied into the
  test verbatim for the B-to-A round trip. The backfill command is left unchanged: its
  "unmigrated" predicate (old key set, new key missing or different) would copy the old key
  over a differing new one, which is wrong once the new key is authoritative, but under B
  every writer (A and B) keeps the keys equal, so a difference cannot arise after the gate's
  `remaining=0`. The command is not to be run again after B; the clean-up milestone replaces
  it with `--remove-legacy`.
- **D-3, release B shipped and observed (AC-2, AC-3, AC-6 for B), 2026-11-03.** Milestone B
  merged as PR #309 with backend lint, typecheck and the db tier green in CI, which carries
  AC-2's real-Neo4j cases. Staging was then deployed from `main` at `245f4125`; `$COMPOSE ps`
  showed the web, worker and their two companion services up on that build, which is the
  gate for C (AC-3). AC-6 was observed live on staging the same day, in project TEST
  PROJECT:

  - A new Concept Map on room `sim-p4-crat-20261101-092525` (config
    `e34d0f97-0191-4f54-b2f3-cdadfd73148d`) built to 32 entities and 24 relations.
  - A new Knowledge Map `ac6-5a2-release-b` (config
    `8694dc80-a57e-425c-a18f-955d232ce8f5`) with one Markdown document built to 19
    entities and 10 relations.
  - A Knowledge Map rebuild was started and the admin reset was sent while it ran, with
    `force=true` because a running build holds the lock. The audit row
    `admin.knowmap_reset` recorded `previous_state=running`, `forced=true`,
    `outcome=discarded`, which is the compensate path (`delete_by_build` then
    `restore_from_snapshot`). The graph read back 19 entities and 10 relations, the
    pre-rebuild graph.
  - Retrieval was checked through release B's own read path inside `backend-web`: a script
    called `Neo4jAsyncDriver.traverse` on each config and counted the evidence keys
    directly in Neo4j.

  ```
  concept_map: {'edges': 24, 'equal': 24, 'no_new': 0, 'no_old': 0, 'empty_new': 0} traverse_rows=5 with_evidence=5
  knowledge_map: {'edges': 8, 'equal': 8, 'no_new': 0, 'no_old': 0, 'empty_new': 0} traverse_rows=5 with_evidence=5
  ```

  Every edge carries both keys, equal and non-empty, and every traversed row returned
  evidence; a read that ignored the new key would have shown `with_evidence=0`. The
  Knowledge Map count of 8 edges was taken after one more rebuild, started to clear the
  state left by FU-1, whose completion the browser session did not observe (the session
  expired). The Knowledge Map retrieval was checked at the driver, not through an agent
  turn, because the map has no bound agent. AC-6 stays open until it is observed again
  after release C and after the clean-up.
- **D-4, milestone C.** Task base `245f4125`. Two choices the spec left open:

  - **Writes drop the old key.** Writes do not just stop setting the old key; the delta
    upsert, the replace upsert and the snapshot restore end with `REMOVE
    r.evidence_msg_ids`. Without it, an edge release B wrote and release C then
    re-MERGEd would keep a stale old key. That would contradict §8's "edges C wrote
    carry only the new key", and the clean-up would have more to remove.
  - **Writes keep the fallback.** The read is `r.evidence_refs` alone, as §5 says, but
    the write still computes its starting list as `coalesce(r.evidence_refs,
    r.evidence_msg_ids, [])`. The gate leaves no edge without the new key. If one were
    missed, removing the old key without the fallback would delete that edge's
    evidence; with it, the write migrates the edge. Both the write fallback and the
    restore fallback go in the clean-up milestone.

  The release B tests were rewritten for C in place. The old "both keys equal after a
  write" assertions now assert `(None, refs)`. The release A and pre-rename restore Cypher
  copies were replaced by release B's restore, copied verbatim for the C-to-B round trip.
  Added: a read no longer falls back to the old key, and a restore onto an edge release B
  left with both keys drops the old one. The quality audit over `245f4125...HEAD` reported
  no findings. The security audit is N/A: the diff changes only property names in
  Cypher constants and touches none of the triggering surfaces.

## 12. Follow-ups

- **FU-1, a forced admin reset during a build leaves the config `failed`.** The reset in
  D-3 returned `idle`, but the interrupted worker then failed its next lock refresh
  (`graphrag_builder.py:364`) and `_fail_phase1` (`graphrag_builder.py:603`) set the
  config to `failed` without checking that it still owned the build, so the list showed
  "failed" over a correctly restored graph. A rebuild clears it. Pre-existing, unrelated
  to the evidence rename, and outside this dossier's scope; it belongs with the admin reset
  and build lock work.
