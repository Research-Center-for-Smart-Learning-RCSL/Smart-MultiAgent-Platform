---
type: refactor
status: approved
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

- [ ] AC-1: gate for B recorded — expand `implemented`, release A on every replica, the
  armed backfill's "0 remaining" output pasted here.
- [ ] AC-2: release B reads `evidence_refs` and keeps both keys equal on every write; A and
  B snapshots restore across each other (real Neo4j).
- [ ] AC-3: gate for C recorded — release B on every replica.
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

## 12. Follow-ups

None yet.
