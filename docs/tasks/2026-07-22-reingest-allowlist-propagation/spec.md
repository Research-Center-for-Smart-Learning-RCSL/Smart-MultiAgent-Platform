---
type: bugfix
status: in-progress
created: 2026-07-22
requirements: []
depends_on: []
---

# Re-uploading a document silently discards the submitted per-agent allowlist

## 1. Summary

The per-agent document allowlist is treated as a creation-time attribute of a row rather than
an attribute of the upload request, so it is passed only to the `create(...)` call on the
fresh-insert branch. Every branch that resolves to an existing row — the READY duplicate and
the non-READY re-index — returns or re-drives that row and never writes the submitted list,
even though the API has already accepted, authorized and validated it. All four ingestion
entry points share the shape.

The practical consequence is that the retry path — the one users hit *after* an ingestion
failure — cannot correct a wrong binding. A designer who uploads with the allowlist
accidentally narrowed, watches parsing fail, then re-uploads with the correct set, gets a 201
and the old list. The document is listed in the UI under a config the agent is bound to, and
the agent can never retrieve from it.

Source: `docs/audits/2026-07-22-agent-config-runtime/findings.md` F-11 (major, confirmed).

## 2. Observed vs Expected

- **Observed.** All four paths validate the list at the boundary first, so the discard happens
  strictly *after* the system has agreed the list is legitimate:

  | Entry point | Boundary validation | Fresh insert writes it | Dedup branch | Re-index branch |
  |---|---|---|---|---|
  | RAG multipart | `backend/app/api/v1/rag.py:485-490` | `ingest_service.py:180-189` | `:142-143` returns `existing` | `:144-167` calls `_index_document` |
  | RAG tus | `backend/app/api/v1/tus.py:129-146`, re-parsed at `backend/contexts/conversation/application/tus_service.py:311-329` | `rag_tus_finalizer.py:125-134` | `:85-88` | `:89-116` |
  | Knowledge Map multipart | `backend/app/api/v1/knowmap.py:510-512` | `knowmap_ingest_service.py:123-132` | `:103-104` | `:105-117` |
  | Knowledge Map tus | `tus.py:160-175` | `knowmap_tus_finalizer.py:100-109` | `:76-77` | `:78-91` |

  - The enabling condition is that `find_by_sha`
    (`backend/contexts/knowledge/infrastructure/repositories.py:189-200`;
    `knowmap_repositories.py:393-404`) matches on `(config_id, sha256)` with no status and no
    `deleted_at` predicate, so READY, INGESTING, FAILED and QUARANTINED rows all match and both
    non-fresh branches are reachable.
  - The read side excludes the agent permanently: `repositories.py:400-410` requires
    `agent_ids @> [agent_id]`.
  - The 201 body carries the *stale* `agent_ids` (`rag.py:151-163`, `knowmap.py:194-199`) but
    the frontend never compares — `RagConfigDetailView.vue:302-328` and
    `KnowledgeMapConfigDetailView.vue:314-335` show a success toast and invalidate.

- **Expected.** A retry that submits a corrected allowlist results in that allowlist being in
  force.

  **Intent source.** No `[Rxx.yy]` governs re-upload semantics, so `requirements: []` is a
  positive claim. The expected behaviour rests on an internal contradiction the codebase states
  itself: the tus design deliberately routes the allowlist through upload metadata precisely so
  "the finaliser applies it atomically on the new document (no racy post-upload PATCH)"
  (`RagConfigDetailView.vue:310-311`, mirrored at `KnowledgeMapConfigDetailView.vue:322-323`).
  The re-index branch breaks the guarantee that design was built to provide.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Should an overwrite be gated on the caller holding the same permission the PATCH endpoint requires? | **No — the check would be vacuously true.** | Every ingest entry point already requires exactly what PATCH requires: `RESOURCE_CREATE_EDIT` at the config's project **plus** Project Owner. RAG multipart `rag.py:469-477`; RAG tus create `tus.py:128`; Knowledge Map multipart `knowmap.py:507-508`; Knowledge Map tus create `tus.py:159`; versus PATCH at `rag.py:689-698` and `knowmap.py:646-660`. A caller who can upload can already call PATCH and set any list. The remaining question is user surprise, not privilege — which is what Q-2 answers. |
| Q-2 | What should happen on a **READY duplicate** whose submitted list differs from the stored one? | **409, directing the user to the PATCH endpoint.** Silent no-op when the lists are identical. | A READY document is live and serving, and its stored list may encode a deliberate decision by a different owner made through the separately-audited PATCH endpoint (`rag.document_agents_set`, `rag.py:708-723`). Silently overwriting destroys that with no record. Refusing is honest and can never widen silently. |
| Q-3 | What should happen on a **non-READY re-index**? | **Overwrite unconditionally with the submitted list.** | The prior row is an artifact of a failure the user is retrying; their submitted list is the current intent by definition. There is no successful prior state to protect — the row committed no retrievable chunks, which is the same reasoning already written down at `repositories.py:360-370` for why FAILED/QUARANTINED documents do not lock chunk params. This is also the branch the audit flags as the painful one. |
| Q-4 | Should the two branches behave the same? | **No — see Q-2 and Q-3.** | They encode different user intents. "My upload didn't work, here it is again" and "I'm re-uploading a file I may have forgotten I had" are not the same act, and only the first carries an unambiguous statement of current intent. |
| Q-5 | Should the lists be unioned instead? | **No. Rejected on security grounds.** | Union is the only proposal that can *only ever widen*. A user re-uploading specifically to **revoke** agent B's access would get a 201 and B would keep access, and the empty-list case (`agent_ids=[]`, "no agent may see this") becomes unreachable through the ingest path forever. It converts a fail-closed defect into a fail-open one. |
| Q-6 | The frontend pre-selects every bound agent on upload. Does that interact with the fix? | **Yes, decisively — and it is why Q-2 is a 409 rather than an overwrite.** | `RagConfigDetailView.vue:107-118` and `KnowledgeMapConfigDetailView.vue:167-178` seed the checkbox set to all bound agents. A Project Owner re-uploading purely to retry, without opening the allowlist panel, submits "all bound agents" by default. An unconditional overwrite on the READY branch would therefore silently widen any document deliberately narrowed via PATCH. If the 409 is later judged too costly and overwrite-everywhere is chosen instead, the pre-select default **must** change in the same PR, or the fix trades a fail-closed bug for a silent-widening one. |
| Q-7 | Does this depend on any open dossier, or overlap the a2a orchestration audit? | No. `depends_on: []`. | Checked against `BOARD.md`. The a2a audit covers orchestration and turn locking; nothing there touches the knowledge ingestion path. |
| Q-8 | Is the QUARANTINED status/scan-status defect (#8) part of this fix? | **No. Preserve current behavior and defer it to FU-2.** | The fix requires a separate security decision: resetting `scan_status` to pending can temporarily expose previously quarantined RAG chunks, while treating exact quarantined bytes as retryable conflicts with Q-3's blanket non-READY policy. The design, risk table and follow-up section already defer #8; AC-4 is therefore a non-regression boundary rather than a request to absorb it. |
| Q-9 | Do the dedup/conflict audit requirements include the multipart create-race fallback? | **Yes.** | The `IntegrityError` recovery branches also resolve an upload request to an existing row. AC-5 says every such branch is audited, so the shared resolver/audit flow must cover both initial `find_by_sha` and create-race resolution. |

## 4. Reproduction

**FAILED-retry, Knowledge Map multipart** (cleanest — `knowmap_ingest_service.py:243-256`
commits FAILED durably in its own rollback-then-commit block, so one failed request leaves a
committed row):

1. As Project Owner, create a Knowledge Map config with agents A and B bound.
2. `POST /api/knowmap-configs/{id}/documents` with `agent_ids=[A]` and a corrupt payload so
   `chunk_document` raises. The row commits `status=failed`, `agent_ids=[A]`.
3. Fix the transient cause; re-upload the byte-identical file with `agent_ids=[A, B]`.
4. `find_by_sha` (`knowmap_repositories.py:393-404`) matches the FAILED row;
   `knowmap_ingest_service.py:105-117` re-indexes; `ipt.agent_ids` is never read.
5. **Observed**: 201, `status=ready`, `agent_ids=[A]`. Agent B sees the document listed but
   `knowmap_repositories.py:601-613` never returns it.

**FAILED-retry, RAG multipart** — note a precondition the RAG path adds: per
`ingest_service.py:12-21`, a multipart first-attempt failure normally rolls the FAILED row
back with the request transaction. To reach the re-index branch the committed non-READY row
must come from elsewhere: a tus-registered document the worker marked FAILED, the
Arq-unavailable path that commits FAILED explicitly (`rag_tus_finalizer.py:191-192`), or a row
stuck INGESTING after a worker died — `find_by_sha` matches all three.

**READY-duplicate, RAG tus**: upload a >32 MB file with `rag_agent_ids=[A,B]` to READY; narrow
to `[A]` via PATCH; re-upload the identical file with `[A,B]`. `rag_tus_finalizer.py:85-88`
returns `existing` before any write, the allowlist stays `[A]`, and **no audit row of the
second upload exists at all**.

## 5. Root Cause Analysis

**Root cause: the allowlist is modelled as a property of the row rather than of the request**,
so it is threaded only into `create(...)` on the fresh-insert branch of each of the four
services (`ingest_service.py:180-189`, `rag_tus_finalizer.py:125-134`,
`knowmap_ingest_service.py:123-132`, `knowmap_tus_finalizer.py:100-109`).

**Enabling condition**: `find_by_sha` has no status or `deleted_at` predicate
(`repositories.py:189-200`), making both non-fresh branches reachable.

**Why it is invisible**: the response carries the stale value but the frontend discards it
without comparing (`RagConfigDetailView.vue:302-328`).

Fails in the restrictive direction — the stale list is always the older, already-authorized
one, so no agent gains access it did not previously have. That is why this is major and not
critical, and it is why Q-5 rejects union.

## 6. Blast Radius and Sibling Suspects

Fields the fresh-insert path establishes (`repositories.py:202-230`,
`knowmap_repositories.py:406-434`), each checked against the re-index path:

| # | Field / behaviour | Status |
|---|---|---|
| 1 | `agent_ids` | **Confirmed** — this defect |
| 2 | `filename` | **Confirmed.** The same bytes re-uploaded as `policy-v2.pdf` keep the stored `policy-v1.pdf`. Display-only, but it is the label in the document list and on retrieval citations. |
| 3 | `mime` | **Confirmed (low).** The re-upload's normalised mime (`ingest_service.py:125`, `knowmap_ingest_service.py:93`) is validated and then discarded; `_index_document` parses with the **stored** `doc.mime` (`ingest_service.py:293`, `knowmap_ingest_service.py:212`). A re-upload correcting a mis-declared type re-parses with the old parser. Bounded — a genuinely wrong parser just fails again. |
| 4 | `size_bytes` | **Cleared** — equal SHA-256 implies equal bytes. |
| 5 | `uploaded_by` | **Confirmed, by design.** The row keeps the original uploader; the retrying actor appears only in `emit_reupload_audit`. Probably correct as provenance — but state it as a decision rather than leave it implicit. |
| 6 | Re-upload audit emission | **Confirmed.** `emit_reupload_audit` fires on RAG multipart (`ingest_service.py:147-153`) and RAG tus (`rag_tus_finalizer.py:92-98`) but **not** on either Knowledge Map path. Knowledge Map re-uploads are invisible in the audit trail. |
| 7 | READY-dedup audit | **Confirmed.** No audit emission at all on the dedup branch of all four services. A duplicate upload leaves zero record that the action occurred. |
| 8 | QUARANTINED re-upload flips `status` to READY | **Confirmed.** `mark_scan` sets both `status` and `scan_status` (`repositories.py:325-340`); `find_by_sha` has no status filter, so a multipart re-upload of quarantined bytes enters the re-index branch and `_index_document` flips `status` back to READY (`ingest_service.py:379-382`) while `scan_status` stays quarantined. Retrieval still excludes it (`:405`) so it fails closed — but the UI badge reads "ready" and `count_locking_for_config` (`:360-383`) now counts it as locking the config's chunk params. The tus paths avoid this via `claim_for_reingest` (`:288-292`). |
| 9 | `ingest_attempt` on the multipart re-index path | **Confirmed (known, deferred)** — acknowledged in-code at `ingest_service.py:461-463` and `knowmap_ingest_service.py:279-281`. |
| 10 | No atomic claim on the multipart re-index path | **Confirmed (pre-existing).** The tus finalizers use `claim_for_reingest` (`repositories.py:264-301`) precisely so two concurrent re-uploads cannot both drive indexing and collide on `uq_rag_chunk_doc_idx`. Neither multipart service has any equivalent. F-23 hardened two of four call sites. |
| 11 | Blob re-`put` skipped on multipart re-index | **Confirmed (low).** Normally harmless (sha-addressed key, original put preceded the create), but if the blob was swept by `retention.py:395-411` the re-index reports READY while `minio_path` dangles. |
| 12 | Config-level settings (`chunk_strategy`, `chunk_params`, `top_k`, embedder) | **Cleared** — re-read from the fresh `cfg` on every call (`ingest_service.py:294-303`). |
| 13 | Knowledge Map corpus revision + build enqueue | **Cleared** — both fire on the re-index branch (`knowmap_ingest_service.py:230`, `:116`). |
| 14 | Returned row staleness | **Confirmed, and load-bearing for the fix.** `RagDocument`/`KnowmapDocument` are frozen dataclasses (`domain/models.py:150-165`, `domain/knowmap.py:68`). The fix must return the row refreshed *after* the allowlist write, or the 201 body still reports the old list — reproducing the exact visibility gap this defect depends on. |

Beyond the reported defect, the most valuable of these are **#7** (the dedup path is an audit
blind spot), **#8** (a quarantined document displays as ready after re-upload), **#10** (two of
four paths lack the F-23 concurrency guard) and **#6** (Knowledge Map re-uploads unaudited).

## 7. Fix Design

Extract the decision, not the pipeline:

```
resolve_existing_document(existing, submitted_agent_ids) -> ReuploadAction
```

a pure function over `(status, stored_agent_ids, submitted_agent_ids)` returning
`DEDUP_NOOP | CONFLICT | REINDEX_WITH_OVERWRITE`, unit-testable with no I/O, called
identically from all four sites. Per Q-2/Q-3: non-READY → overwrite, READY with a differing
list → conflict, READY with an identical list → no-op.

Then in each of the four services, on the branches that resolve to an existing row, call
`set_agents` (`repositories.py:232-252`, `knowmap_repositories.py:436-450`) when the decision
is `REINDEX_WITH_OVERWRITE` — the same write the PATCH endpoint already uses, which returns the
refreshed row, satisfying #14 — and emit an audit record. Use the family-specific existing
actions (`rag.document_agents_set` / `knowmap.document_agents_set`) for the allowlist write and
extend the family-specific `document_uploaded` re-upload metadata with the resolver outcome.
The multipart `IntegrityError` recovery branches must re-enter the same resolver/audit flow
rather than returning the winner directly.

**Resist the large refactor.** The four `sha → find_by_sha → READY? return : re-index / else
create` blocks are near-identical (`ingest_service.py:133-189`,
`knowmap_ingest_service.py:101-132`, `rag_tus_finalizer.py:83-134`,
`knowmap_tus_finalizer.py:74-109`) and that duplication is not hypothetical debt — it is the
*mechanism* of two prior defects (F-23 fixed in two of four; `emit_reupload_audit` added to two
of four). But the four bodies diverge for real reasons: RAG upserts Qdrant and Knowledge Map
does not; multipart indexes inline while tus enqueues a worker; Knowledge Map bumps a corpus
revision and enqueues a build. Unifying the pipeline would be a high-risk change riding on a
bugfix. Extracting the *decision* gives one place where the semantics live without merging four
different I/O pipelines.

**Also in scope, cheap and adjacent:** emit an audit row on the READY-dedup branch (#7), and
add `emit_reupload_audit` to the two Knowledge Map paths (#6). Both are one-liners that close
audit blind spots this fix would otherwise leave in place.

**Do not re-validate the list in the application layer.** `validate_agent_allowlist`
(`backend/app/api/v1/deps.py:113-149`) already runs at all four entry points before the service
is reached; it rejects agents not bound to this config and not in this project, which is the
boundary preventing an allowlist naming a foreign-project agent.

**Data repair: not possible, and a suspect report is the honest substitute.** The
submitted-and-discarded list was never persisted anywhere — `rag.document_uploaded` audit
metadata omits `agent_ids` (`ingest_service.py:206-215`, `:445-452`,
`rag_tus_finalizer.py:143-151`, and the Knowledge Map equivalents), the tus allowlist lived
only in Redis upload metadata and is deleted at `tus_service.py:334`, and the dedup branch
emits nothing at all. The intended list cannot be reconstructed. What *is* detectable is a
suspect set: documents whose `agent_ids` is a strict subset of the agents currently bound to
the parent config **and** which have no `rag.document_agents_set` audit row (so the narrow list
was never a deliberate PATCH decision), optionally intersected with rows carrying the
`reupload` marker (`ingest_service.py:451`, RAG only per #6).

Recommended: ship a **read-only** operator report emitting
`(config_id, document_id, filename, stored_agent_ids, bound_agent_ids, has_reupload_audit,
has_agents_set_audit)`, and surface it in the document list as an advisory badge ("not visible
to all bound agents") — cheap, since `RagConfigDetailView.vue:100-102` already computes
`boundAgents` and `agent_ids` is already on the wire. **Do not auto-repair**: widening an
allowlist without a human decision is a privilege grant and cannot distinguish a bug victim
from a deliberate narrowing.

Add `agent_ids` to the `document_uploaded` audit metadata as part of this fix, so the next
incident of this class is forensically recoverable. It is a list of UUIDs, not a secret.

## 8. Regression Test Plan

Cover **all four entry points**. #6 and #10 already demonstrate that fixing two of four is the
failure mode this codebase has hit twice.

**Wiring tier — multipart** (real Postgres; the ARRAY column and `find_by_sha` are what is
under test). Extend `backend/tests/wiring/test_rag_ingestion.py`; `_seed_config`,
`_ingest_service`, `_FakeBlob/_FakeEmbedder/_FakeQdrant` (`:48-120`) and `_seed_ready_doc`
(`:309-321`, already threading `agent_ids`) are directly reusable.

**The failing test comes first** — `test_reupload_of_failed_doc_applies_submitted_allowlist`:
seed a committed FAILED row with `agent_ids=[A]`, call `IngestService.ingest` with the same
bytes and `agent_ids=(A, B)`, assert the persisted row **and the returned dataclass** both
carry `[A, B]`. **Fails today**: `ingest_service.py:144-167` never touches `agent_ids`, and the
returned row is `_index_document`'s re-read (`:420`) of an unmodified row.

Then: `test_ready_duplicate_upload_with_different_allowlist` (409 per Q-2);
`test_ready_duplicate_upload_with_identical_allowlist_is_noop` (pins that the common benign
case does not start erroring). Mirror the first two against `KnowmapIngestService` —
`test_knowmap_scan_gating.py:73`
already seeds `agent_ids` and is the natural neighbour — plus
`test_knowmap_reupload_emits_reupload_audit` for #6.

**Unit tier — tus finalizers** (the assertion is which repository method was called with what;
no DB needed). `backend/tests/unit/test_tus_ingest_attempt.py` already mocks both finalizers
with `find_by_sha` returning a chosen `existing` and `claim_for_reingest` returning a scripted
sequence (`:72-104`, `:171`); reuse `_patch_common` (`:49-64`).

- `test_rag_tus_reupload_applies_submitted_allowlist` — `existing.status=FAILED`,
  `claim_returns=[1]`; assert `set_agents` called with `[A, B]`.
- `test_rag_tus_reupload_applies_allowlist_even_when_claim_returns_none` — `claim_returns=[None]`
  (worker in flight), the branch at `rag_tus_finalizer.py:114-115` that skips the re-enqueue.
  The allowlist write must still happen **and still be committed** — easy to miss.
- Both mirrored for `KnowmapTusFinalizer` (`knowmap_tus_finalizer.py:78-91`). Note that branch
  currently has **no** commit when `attempt is None`; the fix must either commit explicitly or
  rely on the request-scoped `db_session`, and the test must pin whichever is chosen.
- READY-duplicate cases for both finalizers.

**API tier** — one test per config family asserting the 201/409 body contract, since
`agent_ids` is already serialized (`rag.py:162`, `knowmap.py:198`) and is therefore the
user-visible contract. Plus the dedup-branch audit row (#7).

**Frontend** — the 409 needs a branch in `RagConfigDetailView.vue:302-328` and
`KnowledgeMapConfigDetailView.vue:314-335`; the current bare `catch { toast.error(...) }` would
surface it as a generic upload failure, which is worse than today's silent success.

## 9. Risks and Rollback

| Risk | Mitigation |
|---|---|
| **Silent widening via the all-bound-agents pre-select** — the highest-consequence risk of any overwrite semantics | Q-2's 409 on the READY branch contains it. If overwrite-everywhere is chosen instead, the pre-select default must change in the same PR (Q-6). |
| **Stale return value** — writing the allowlist but returning the pre-write frozen dataclass reproduces the visibility gap (#14) | Return the row `set_agents` yields; assert on the *returned* object in tests, not only the DB. |
| **Commit-boundary loss on the tus `claim is None` branch** — `rag_tus_finalizer.py:114-115` commits there, `knowmap_tus_finalizer.py:88-91` does not | Explicit unit test; decide and pin whether the request-scoped `db_session` commit is relied upon. |
| **Race with a concurrent PATCH** — both write `agent_ids` with no optimistic concurrency | Last-writer-wins is `set_agents`' existing behaviour (`repositories.py:232-252`). Document it as accepted rather than silently inherit it. |
| **Only some of four sites fixed** — the exact failure mode of F-23 and `emit_reupload_audit` | Tests span all four; the extracted decision helper makes divergence structurally harder. |
| **Scope creep into #8/#9/#10** | Fix F-11 plus the cheap adjacent audit gaps (#6, #7); file #8/#9/#10 separately unless deliberately absorbed. |

**Rollback.** The core change is additive — a `set_agents` call plus an audit emission on
branches that currently perform no write. No schema change, no migration, nothing to unwind:
the fix writes only the same column PATCH already writes, so post-revert rows are
indistinguishable from rows an operator set manually. If the 409 ships, the frontend handler
must deploy **before or with** the backend change, never after, or duplicate uploads surface as
a generic "upload failed".

## 10. Acceptance Criteria

- [x] AC-1: `test_reupload_of_failed_doc_applies_submitted_allowlist` (§8) fails against
      current code and passes after the fix.
- [x] AC-2: on all four entry points, re-ingesting a non-READY document applies the submitted
      allowlist to the persisted row **and** to the response body.
- [x] AC-3: a READY duplicate whose submitted list differs returns 409 naming the PATCH
      endpoint; an identical list is a silent no-op.
- [x] AC-4: this task does not change QUARANTINED re-upload semantics; the status/scan-status
      defect remains explicitly deferred to FU-2 rather than being partially fixed here.
- [x] AC-5: every branch that resolves to an existing document emits an audit row, including
      the READY-dedup branch and both Knowledge Map paths.
- [x] AC-6: `document_uploaded` audit metadata carries `agent_ids`.
- [x] AC-7: no ingest path writes an allowlist that has not passed
      `validate_agent_allowlist`.
- [x] AC-8: the frontend renders the 409 as an actionable message, not a generic upload
      failure.
- [ ] AC-9: `pytest -q`, `ruff check .`, `ruff format --check .`, `mypy .` pass in `backend/`;
      `pnpm test`, `pnpm lint`, `pnpm typecheck` pass in `frontend/`.

## 11. SRS Delta

None. No `[Rxx.yy]` governs re-upload semantics; this restores the atomicity guarantee the tus
design already claims for itself. See FU-1.

## 12. Deviation Log

No implementation deviations from the approved design.

## 13. Follow-ups

- **FU-1** — No SRS entry defines re-upload semantics for an existing document (dedup versus
  retry, and what happens to per-document settings). The policy now lives in a helper and a
  dossier; an `[Rxx.yy]` entry would give future audits something to judge against.
- **FU-2** — #8: a QUARANTINED document re-uploaded via multipart flips `status` to READY while
  `scan_status` stays quarantined, so the UI badge lies and the row starts locking the config's
  chunk params. Fails closed for retrieval. Distinct fix; file separately.
- **FU-3** — #10: neither multipart service has the `claim_for_reingest` guard the two tus
  finalizers use, so two concurrent multipart re-uploads of the same sha can both drive
  indexing. F-23 hardened two of four call sites.
- **FU-4** — #9: the multipart re-index path enqueues the rescan with the default
  `ingest_attempt=0`, so within Arq's result-retention window it dedups onto the retained prior
  verdict and never re-runs. Already acknowledged in-code as deferred.
- **FU-5** — #3: the re-upload's normalised `mime` is validated then discarded, so a re-upload
  correcting a mis-declared type re-parses with the stored parser.
- **FU-6** — #11: the multipart re-index branch never re-`put`s the blob, so a swept blob
  yields a READY document with a dangling `minio_path`.
- **FU-7** — The RAG tus finalizer commits the re-ingest claim before publishing
  `ingestion.started`, and the publish is outside its enqueue recovery block. A Redis failure
  can therefore leave the row stuck INGESTING without an ingest job. Pre-existing; make the
  notification best-effort or move it behind a recovery strategy.
- **FU-8** — RAG and Knowledge Map tus accept documents up to 1 GiB, while workers load the
  whole blob and materialize parsed text and chunks under a global worker concurrency of 50.
  Add purpose-specific byte/chunk caps, stream or spool parsing, scan before parsing, and/or
  isolate ingestion in a low-concurrency queue.
- **FU-9** — The touched backend application services directly construct concrete
  infrastructure repositories/adapters. This pre-existing dependency-inversion debt should be
  addressed as a dedicated boundary refactor, not inside this bugfix.
- **FU-10** — `RagConfigDetailView.vue` and `KnowledgeMapConfigDetailView.vue` remain oversized
  multi-responsibility views. Extract upload/document-list behavior in a dedicated frontend
  refactor rather than expanding this bugfix.

## 14. Build Verification

- Focused backend policy, service, tus-finalizer and real upload-route contract suite:
  **43 passed**.
- Backend `ruff check .`, `ruff format --check .` and `mypy .`: **passed** across 867 files.
- Frontend `pnpm test`: **167 files / 894 tests passed**; `pnpm lint`, `pnpm typecheck` and
  `pnpm build`: **passed**.
- The real-Postgres wiring tests are authored but cannot run on this host: the configured
  `postgres` hostname does not resolve and the Docker daemon is unavailable. The focused
  wiring selection therefore reports 10 environment errors after 38 non-wiring tests pass.
- The broad backend unit run reached 20% without a failure, then exceeded the six-minute
  execution window. Full `pytest -q` remains unverified, so AC-9 and dossier completion stay
  open.
</content>
