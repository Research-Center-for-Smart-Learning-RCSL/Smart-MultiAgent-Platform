---
type: feature
status: approved
created: 2026-07-07
requirements: [R10.11, R11.12, R11.13, R11.14, R11.15, R11.20]
---

# GraphRAG Phase 3 — Knowledge Map (Axis-1 GraphRAG over documents)

## 1. Summary

Phase 3 adds the **Knowledge Map**: a designer-authored GraphRAG built from uploaded
documents rather than conversation — the Axis-1 counterpart to the Concept Map (Axis-2). It
is the **second consumer of the shared graph engine** that Phase 0 de-concreted, and so is
the payoff of the "share the engine, fork the product + domain" decision (R11.15): the Neo4j
driver, 2PC builder/state-machine, Qdrant store (with a parameterized collection prefix), and
neutral graph-domain types are reused unchanged; only a document-oriented extractor, a
document delta loader, the config/corpus tables, and a retrieval provider are new. A
Knowledge Map owns its own uploaded-document corpus (parallel to §10 file-RAG, reusing the
shared parser and chunker), builds on document-set change or explicit rebuild, is queried at
a turn as a third Axis-1 system block beside file-RAG and independent of any Concept Map, and
enforces a per-Agent allowlist by evidence provenance.

**Phase dependencies:** hard on Phase 0 (engine de-concreting via Protocols, neutral opaque
`evidence_refs`, parameterized Qdrant collection prefix, cleanup contract) and Phase 2a
(bounded windowing, per-collection embedding-dimension pin). **Independent of Phase 1 and
Phase 2b** — a Knowledge Map has no owner-layer, no multi-member group, and is non-temporal
(R11.21), so it does not touch the Concept Map owner/temporal machinery. Citations reflect the
pre-Phase-3 tree.

## 2. Goals and Non-goals

**Goals**
- G1 — A project can hold Knowledge Map configs, each owning its own uploaded-document corpus
  (`knowmap_documents` + `knowmap_chunks`), ingested through the shared parser/chunker.
- G2 — A document-oriented extractor builds a triple graph over the corpus, persisted to its
  own Neo4j subgraph and the `knowmap_{project_id}` Qdrant collection, scoped by config id,
  via the shared 2PC engine (R11.13, R11.15).
- G3 — An agent with an attached Knowledge Map (`agent.knowmap_config_id`) receives its graph
  at a turn as a third Axis-1 system block beside file-RAG, independent of the Concept Map
  (R11.14).
- G4 — Per-Agent allowlist enforced by evidence provenance: a relation is visible only if
  **every** one of its source documents is in the agent's allowlist; an entity surfaces only
  via a visible relation; empty allowlist grants nothing (R11.12, R10.11; Q-2).
- G5 — A Knowledge Map rebuilds on document-set change (upload/delete/reprocess) and on
  explicit designer rebuild; never on conversation (R11.12; Q-3).
- G6 — Deleting a Knowledge Map config or any of its documents purges the config's Neo4j
  subgraph, Qdrant points, and MinIO blobs as part of the delete op (R11.20).

**Non-goals**
- Reusing file-RAG's `rag_documents` rows: the user chose an **own** corpus (Q-1); a Knowledge
  Map is independent of whether file-RAG is configured. Shared *code* (parser, chunker,
  storage helpers), not shared *rows*.
- Temporality: Knowledge Maps are non-temporal (R11.21); no `first_seen_at`/`last_seen_at`,
  no recency weighting. The shared engine's temporal fields stay null/unused here.
- Concept Map layers/privacy tiers/multi-member (Phase 2b) — not applicable to a
  document graph.
- Frontend — Knowledge Map UI is Phase 4. Phase 3 is backend + API only.
- Incremental (delta-only) rebuilds — a build reprocesses the current document set (windowed);
  incremental-since-last-build is a follow-up (FU-1).
- OCR/page/offset provenance — the shared parser yields flat text with no offsets; provenance
  is `(knowmap_document_id, chunk_idx)`, the same granularity as file-RAG.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | How is the Knowledge Map's document source bound? | Own upload pipeline and corpus (`knowmap_documents`/`knowmap_chunks`), reusing the shared parser/chunker/MinIO/SHA-dedup building blocks. | User chose an independent document set over reusing an existing `rag_config`'s rows. Keeps the two subsystems decoupled (R11.15) at the cost of parallel corpus tables; debt is contained by reusing the shared ingestion *code*, not forking it. |
| Q-2 | How does the per-Agent allowlist apply to a graph whose entities/relations aggregate across documents? | Per-document allowlist (`knowmap_documents.agent_ids`) filtered at the **edge** level by evidence provenance: a relation is visible only if all its source documents are readable; entities surface only via a visible relation. | Most faithful to R10.11/R11.12 and the most secure (never leaks that a denied document corroborates a relation). Costs a per-turn allowed-doc resolution + an in-memory subset check per edge. Chosen over "any source readable" (leaks corroboration) and "config-level attach" (violates R11.12's per-Agent granularity). |
| Q-3 | When does a Knowledge Map rebuild? | On document-set change (upload/delete/reprocess) and on explicit designer rebuild; never conversation-triggered. | Matches R11.12 "designer-authored" and R11.21 "non-temporal." Concept Maps use message triggers; Knowledge Maps do not. |

## 4. Current State

- **The shared engine is source-agnostic (post-Phase-0).** The 2PC builder consumes a generic
  `DeltaLoader` Protocol (`graphrag_builder.py:469-476`, injected `:111,:120`); the Neo4j driver
  scopes solely by an opaque `graphrag_config_id` + `build_id` with no conversation join
  (`neo4j_driver.py:49-50,91,103,241-244`) and already stores evidence as opaque strings
  (`:119`); the Qdrant store centralizes its collection name in one helper
  (`graphrag_vector_store.py:31-33`, 7 call sites `:56,97,128,166,207,251,268`) with a
  content-neutral payload (`:85-90`); `BuildState`/`BuildResult`/`EntityHit`/`GraphRagBundle`
  are neutral (`domain/graphrag.py:21-27,74-80,94-132`). Phase 0 parameterizes the collection
  prefix and neutralizes `evidence_msg_ids` → opaque `evidence_refs` end-to-end.
- **The concrete extractor is conversation-shaped (fork target).** `LlmTripleExtractor` has a
  chat prompt (`triple_extractor.py:45-54`), a `[{id} role=...] content` renderer (`:118-122`),
  and UUID-casts evidence (`:158-165`). The `TripleExtractor` Protocol
  (`graphrag_ports.py:152-161`) is neutral except its `messages: list[DeltaMessage]` input,
  where `DeltaMessage` is chat-shaped (`id: uuid.UUID; role; content`, `:144-150`).
- **The document corpus and ingestion already exist for file-RAG (code to reuse).** Shared
  parser `MIME_TO_PARSER: dict[str, Callable[[bytes], str]]`
  (`shared_kernel/text_extraction/parsers.py:152-157`, byte→flat-str, no offsets); chunker
  `chunk_document(text, strategy, params, embedder) → list[str]`
  (`contexts/knowledge/infrastructure/chunkers.py:239-272`); MinIO storage keyed
  `{project_id}/{config_id}/{sha256}` (`ingest_service.py:77-81,294-379`); chunk model
  `rag_chunks{id, document_id, chunk_idx, text, qdrant_point_id}` unique `(document_id,
  chunk_idx)` (`tables.py:94-110`). The stable evidence token is `(document_id, chunk_idx)`,
  already the file-RAG `chunk_refs` shape (`retrieve.py:203`); `qdrant_point_id`/`rag_chunks.id`
  are regenerated on reprocess — not stable.
- **Per-Agent allowlist is a `UUID[]` column, not a join table.** `rag_documents.agent_ids`
  (`tables.py:85-90`), enforced at retrieve by `agent_ids.contains([agent_id]) AND
  status='ready' AND scan_status != 'quarantined'` (`repositories.py:321-346`), validated by
  `validate_agent_allowlist` (`rag.py:160-190`), PATCH Project-Owner-gated (`rag.py:638-707`).
- **Turn injection is a linear list of system blocks.** `turn_engine.py:900-908`: file-RAG
  block (`:904-905`) and Concept-Map graph block (`:907-908`) are appended independently,
  sharing `knowledge_queries` (`:902`); each provider is built once in `__init__`
  (`:272-284`). Agent binds via nullable `rag_config_id`/`graphrag_config_id`
  (`agents/infrastructure/tables.py:49-50`). A Knowledge Map adds a third block and a third
  `knowmap_config_id` column.
- **Lifecycle exemplar.** Commit-DB-then-best-effort-purge: `rag.py:335-395` +
  `RagConfigService.purge_documents_infra` (`config_service.py:284-370`).

## 5. Design

### Options considered

**Corpus binding (Q-1).**
- *A — build over an existing `rag_config`'s ingested documents*: zero re-upload/re-parse,
  reuse `rag_chunks` directly; but couples every Knowledge Map to a file-RAG config.
- *B — own corpus (chosen)*: `knowmap_documents`/`knowmap_chunks` + own ingest reusing the
  shared parser/chunker/MinIO. Decoupled subsystems (R11.15); parallel corpus tables are the
  cost, contained by reusing ingestion *code*.

**Allowlist enforcement (Q-2).**
- *A — config-level attach*: simplest, but an agent sees the whole map or none — violates
  R11.12's per-Agent granularity.
- *B — edge filter by evidence provenance, all-source-docs-readable (chosen)*: each edge's
  `evidence_refs` carry `knowmap_document_id`; a relation is visible only if every source doc
  is in `agent_ids`; entities surface via visible relations. Secure (no corroboration leak),
  faithful to R10.11.
- *C — edge filter, any-source-readable*: richer recall, leaks that a denied document
  supports a relation. Rejected on security.

**Extractor/loader (settled by R11.15).** Fork `DocTripleExtractor` (own document prompt +
renderer, reuse JSON parse, emit opaque `evidence_refs`, no UUID cast) and `DocDeltaLoader`
(reads `knowmap_chunks` for the config's documents), both plugging into the shared
`TripleExtractor`/`DeltaLoader` Protocols and the shared 2PC builder unchanged.

### Decision

Own corpus (B), edge-provenance allowlist with all-docs-readable (B), forked document
extractor/loader on the shared engine. Consciously given up: corpus reuse (accepted parallel
tables for subsystem independence), recall from partially-readable edges (accepted for
security), and temporality (out by R11.21).

## 6. Detailed Changes

Four ordered workstreams. WS1 (corpus) precedes WS2 (build) precedes WS3 (retrieval); WS4
(lifecycle) parallels WS3.

### WS1 — Knowledge Map config + own document corpus (R11.12, R11.13)
- **Backend/tables** (migration, continues from Phase 2b's numbering): `knowmap_configs`
  (project-scoped; `builder_key_group_id` per R11.11-analogue; embed pin columns per Phase 2a
  applied to the `knowmap_{project_id}` collection; `last_build_*` state), `knowmap_documents`
  (FK → `knowmap_configs`, `agent_ids UUID[]` allowlist mirroring `rag_documents:85-90`,
  `filename/mime/sha256/minio_path/status/scan_status`), `knowmap_chunks` (FK →
  `knowmap_documents`, `chunk_idx`, `text`, unique `(document_id, chunk_idx)`).
- **Ingest service** — a `KnowmapIngestService` that reuses the shared building blocks:
  `MIME_TO_PARSER[mime](bytes)` (`parsers.py:152`), `chunk_document` (`chunkers.py:239`),
  MinIO `knowmap-sources` bucket keyed `{project_id}/{config_id}/{sha256}`, SHA-256 dedup — the
  same pattern as `ingest_service.py:115-379`, not a fork of its rows. Do **not** duplicate the
  parser/chunker; import them.
- **API** — config CRUD, document upload (multipart + tus for large files, mirroring
  `rag.py`), `PATCH …/agents` allowlist set (Project-Owner-gated, `validate_agent_allowlist`
  idiom). Agent binding: add nullable `agents.knowmap_config_id` (parallel to
  `rag_config_id`/`graphrag_config_id`).

### WS2 — Document extractor + build over the shared engine (R11.13, R11.15)
- **`DocTripleExtractor`** (`infrastructure`) implementing the shared `TripleExtractor`
  Protocol: own document-oriented prompt and a chunk renderer (`[{knowmap_document_id}#{chunk_idx}]
  {text}`), reusing the JSON parse from `LlmTripleExtractor` but emitting `evidence_refs`
  as opaque `"{knowmap_document_id}#{chunk_idx}"` strings (no UUID cast). Source-unit shape:
  either the Phase-0-neutralized source-unit type or a chunk adapter conforming to the loader
  contract.
- **`DocDeltaLoader`** implementing the shared `DeltaLoader` Protocol: `load(config_id, since,
  mode)` iterates `knowmap_chunks` for the config's `status='ready'` documents (analogue of
  `RagChunkRepository.list_for_document`), windowed by the Phase 2a `iter_windows` for large
  corpora. A build reprocesses the full current set (Non-goal: incremental).
- **Wire the shared builder/2PC** with the `knowmap` collection prefix (Phase 0 param) and the
  two forked collaborators injected — no builder/driver/state-machine change. Embed-dimension
  pin (Phase 2a) applies to `knowmap_{project_id}`.
- **Build trigger** (Q-3): enqueue a build on document-set change (ingest success, delete,
  reprocess) with the Phase 2a `_job_id` dedup; plus an explicit `POST …/rebuild` endpoint. No
  conversation trigger.

### WS3 — Retrieval provider + allowlist edge filter (R11.14, R11.12, Q-2)
- **`KnowledgeMapContextProvider`** (mirrors `GraphRagContextProvider`), keyed on
  `agent.knowmap_config_id`, appended as a third system block at `turn_engine.py:900-908` with
  `type:"graphrag"` retained (R11.14), independent of the Concept Map provider. Built once in
  `TurnEngine.__init__`.
- **Shared retrieve engine** on the `knowmap` collection/config: Qdrant seed entities + Neo4j
  1–2 hop traverse, confidence-ranked (non-temporal — no recency).
- **Allowlist edge filter (security core, Q-2/G4):** resolve the agent's allowed
  `knowmap_document` ids once per turn (`agent_ids.contains([agent_id]) AND status='ready'`);
  for each candidate relation, decode `evidence_refs` → document ids and keep the relation only
  if **all** its source documents are allowed; surface an entity only via a kept relation.
  Evidence excerpts are hydrated from `knowmap_chunks` (not conversation), themselves gated by
  the same allowed-doc set. This closes the provenance-leak surface for documents the same way
  Phase 2b closes it for rooms.

### WS4 — Lifecycle purge (R11.20)
- Extend the Phase 0 cleanup contract: deleting a `knowmap_config` or a `knowmap_document`
  purges the config's Neo4j subgraph, `knowmap_{project_id}` Qdrant points, and MinIO blobs as
  part of the delete op (commit-then-best-effort, audit-logged), mirroring
  `rag.py:335-395`/`purge_documents_infra`. Reconciler sweep backstops orphans across the
  `knowmap` collection.

**Migrations** — `knowmap_configs`/`knowmap_documents`/`knowmap_chunks` + `agents.knowmap_config_id`;
expand-only, numbering continues after Phase 2b. `gen:api` rerun required (new endpoints); no
frontend consumes them until Phase 4.

## 7. NFR Checklist

- [x] **i18n** — N/A (backend/API only; Phase 4 owns UI copy).
- [x] **Audit log** — document upload/delete, allowlist changes, config delete + infra purge,
  and rebuilds are audited (mirrors file-RAG + R11.20).
- [x] **Tenant isolation** — Knowledge Map is project-scoped; every endpoint asserts project
  membership against the resource's own project (the `graphrag.py:250-263` idiom); the
  `knowmap_{project_id}` collection and config-id payload scope keep maps within one project.
- [x] **Error handling UX** — RFC 7807 for unsupported MIME, oversize upload, quarantined
  document, allowlist referencing an unbound agent, embedding-dimension mismatch (2a). Retrieval
  degrades to empty silently (never fails a turn).
- [x] **Performance** — build cost bounded by Phase 2a windowing over the corpus; retrieval
  adds one allowed-doc query + an in-memory per-edge subset check (bounded by traverse `LIMIT
  50`). Malware scan reused from file-RAG's `scan_status` gate.

## 8. Security Considerations

Touches file upload, tenant boundaries, per-Agent document authorization, and agent/LLM
context assembly — a Security Considerations section is required.

- **Document-provenance leak (the central control).** The allowlist edge filter (WS3) must
  gate on **all** source documents of a relation; a single denied source hides the relation.
  Verified by AC-4/AC-5. Entities must never surface except via a kept relation, or an entity
  name derived solely from a denied document would leak.
- **Upload surface.** Reuse file-RAG's MIME allowlist (`SUPPORTED_MIMES`), size gates, SHA
  dedup, and malware `scan_status` quarantine — a Knowledge Map must not index a quarantined
  document. Never parse an unlisted MIME.
- **Allowlist authority.** Setting `knowmap_documents.agent_ids` is Project-Owner-gated and
  validated against agents bound to the config (`validate_agent_allowlist` idiom); empty array
  = no access (secure-by-default, R10.11).
- **Project boundary.** `knowmap_{project_id}` collection + config-id payload + owner→project
  invariant (2a idiom) keep every map inside one project; no retrieval joins across projects.
- **Provider keys.** The builder key group resolves the embedding/extraction key via the
  Phase 0 carried-key path (`list_ordered_carried`); never across a project boundary.
- **Storage.** MinIO `knowmap-sources` blobs are project/config/sha-keyed and purged on delete
  (WS4); raw bytes never logged.

## 9. Quality Notes

- **Existing debt (do not imitate).** File-RAG builds Qdrant/MinIO clients inline in
  endpoints/services (`config_service.py:297-408`) rather than injecting them; the Knowledge
  Map should inject via the Phase 0 engine Ports instead of copying the inline construction.
  `chunk_idx` is not stable across a reprocess with different chunk params — evidence refs can
  be invalidated by a reprocess; a rebuild after reprocess is therefore mandatory (WS2 trigger
  covers it), do not treat stale refs as durable.
- **Patterns to follow.** Ingest pipeline: `IngestService` (`ingest_service.py:115-379`).
  Allowlist column + enforcement: `rag_documents.agent_ids` + `allowed_document_ids`
  (`repositories.py:321-346`). Route AuthZ: `app/api/v1/graphrag.py:207-263`. Lifecycle purge:
  `rag.py:335-395`. Extractor to mirror (then fork): `LlmTripleExtractor`
  (`triple_extractor.py`). Loader to mirror: `_DbDeltaLoader` (`app/workers/tasks/graphrag.py:44-107`).
- **Reuse inventory (import, do not re-implement).** `MIME_TO_PARSER`
  (`shared_kernel/text_extraction/parsers.py:152`); `chunk_document` (`chunkers.py:239`); the
  shared 2PC builder + Neo4j driver + Qdrant store (Phase 0 Ports); `iter_windows` (Phase 2a);
  embed-dimension pin (Phase 2a); `list_ordered_carried` (Phase 0); `validate_agent_allowlist`
  idiom (`rag.py:160-190`); the JSON triple-parse from `LlmTripleExtractor`; the cleanup
  contract (Phase 0 WS4).

## 10. Risks and Rollback

- **Corpus duplication debt (Q-1).** Own tables risk drifting from file-RAG ingestion.
  Mitigation: import the shared parser/chunker/storage helpers; a follow-up (FU-2) can extract
  a shared document-corpus capability if a third consumer appears.
- **Allowlist filter correctness** is security-critical. Mitigation: integration tests for
  all-docs-readable (AC-4), denied-source hides the edge (AC-5), entity-only-via-visible-edge.
- **Evidence-ref stability.** `chunk_idx` re-numbers on reprocess; a stale graph could point at
  re-chunked text. Mitigation: reprocess triggers a rebuild (WS2); the reconciler flags configs
  whose `last_build_at` predates a document reprocess.
- **Migrations** — expand-only, new tables + one nullable agent column; rollback drops them and
  the Knowledge Map subsystem disappears with no effect on file-RAG or Concept Maps. No
  destructive step, no data migration.
- **Engine coupling regression.** Reusing the shared engine for a second consumer could surface
  a hidden conversation assumption. Mitigation: a build+retrieve integration test on a
  documents-only Knowledge Map with zero conversation context (AC-2/AC-3).

## 11. Acceptance Criteria

- [ ] AC-1: a Knowledge Map config accepts document uploads (multipart + tus), parses via the
  shared `MIME_TO_PARSER`, chunks via `chunk_document`, and stores `knowmap_documents`/
  `knowmap_chunks` + MinIO blobs; a quarantined document is never indexed.
- [ ] AC-2: a build over a documents-only corpus (no conversation) produces a Neo4j subgraph +
  `knowmap_{project_id}` Qdrant points scoped by config id, through the shared 2PC builder with
  the forked `DocTripleExtractor`/`DocDeltaLoader`, windowed for a large corpus.
- [ ] AC-3: an agent with `knowmap_config_id` set receives the Knowledge Map as a third Axis-1
  system block beside file-RAG, independent of any Concept Map, with `type:"graphrag"` retained.
- [ ] AC-4: retrieval returns a relation only when **every** source `knowmap_document` is in the
  agent's allowlist; an entity appears only via a visible relation; empty allowlist returns
  nothing.
- [ ] AC-5: a relation with one denied source document is absent from retrieval even though its
  other sources are allowed (provenance-leak test).
- [ ] AC-6: a build is enqueued on document upload/delete/reprocess and on explicit rebuild;
  never on a conversation message; duplicate triggers dedup via the Phase 2a `_job_id`.
- [ ] AC-7: deleting a Knowledge Map config or document purges its Neo4j subgraph, `knowmap`
  Qdrant points, and MinIO blobs (audit-logged); the reconciler finds no orphan.
- [ ] AC-8: setting the document allowlist is Project-Owner-gated and rejects ids not bound to
  the config.
- [ ] AC-9: `pytest -q`, `ruff check .`, `ruff format --check .`, `mypy .` pass; `gen:api`
  regenerated.

## 12. Test Plan

- **Unit** (`backend/tests/unit/`): AC-1 (ingest reuse of shared parser/chunker), AC-4/AC-5
  (edge filter subset logic — the security core), AC-6 (trigger on doc-set change, no
  conversation trigger), AC-8 (allowlist validation + Project-Owner guard),
  `DocTripleExtractor` prompt/parse (opaque refs, no UUID cast).
- **Integration** (`-m integration`, Neo4j + Qdrant + MinIO): AC-2 (documents-only build via
  shared engine), AC-3 (three-block turn assembly), AC-5 (end-to-end provenance leak), AC-7
  (purge across stores).
- **Manual/`verify`**: upload a small doc set, build, attach to an agent, confirm the Axis-1
  block and allowlist behavior in a live turn.

## 13. SRS Delta

Apply verbatim on approval.

**Amend [R11.12]** (own corpus + build trigger):
> **[R11.12]** A Knowledge Map is a designer-authored Graph RAG built from **uploaded
> documents** (the same kind of sources as §10 file-RAG), not from conversation. It owns its
> own document corpus (`knowmap_documents`/`knowmap_chunks`, ingested through the shared parser
> and chunker), independent of any file-RAG config. It is project-scoped with a per-Agent
> allowlist, mirroring [R10.11]. It rebuilds on document-set change (upload, delete, reprocess)
> and on explicit designer rebuild, and is never triggered by conversation.

**Add [R11.23]** to §11.5 (Knowledge Map):
> **[R11.23]** A Knowledge Map's per-Agent allowlist is enforced by evidence provenance: each
> relation and entity records its source `knowmap_document` ids in its `evidence_refs`; at
> retrieval, a relation is visible to an agent only if **every** one of its source documents is
> in that agent's allowlist (`knowmap_documents.agent_ids`), and an entity is surfaced only via
> a visible relation. An empty allowlist grants no access (secure-by-default, mirroring
> [R10.11]). Evidence excerpts are hydrated from `knowmap_chunks` under the same allowed-document
> set.

## 14. Open Questions

- Q-A (non-blocking) — whether a future third graph consumer justifies extracting a shared
  document-corpus capability from file-RAG + Knowledge Map (see FU-2). Not needed now.

## 15. Deviation Log

Appended by `/build`.

## 16. Follow-ups

- FU-1 — incremental (delta-since-last-build) Knowledge Map rebuilds instead of full-corpus
  reprocessing.
- FU-2 — extract a shared document-corpus/ingestion capability if a third consumer appears
  (Q-1 debt containment).
- FU-3 — richer document provenance (page/offset) if the shared parser gains structured output.
