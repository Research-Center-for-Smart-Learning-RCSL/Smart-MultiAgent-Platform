---
type: audit
status: draft
created: 2026-07-14
requirements: [R7.04, R9.10, R10.04, R10.08, R10.09, R10.11, R11.01, R11.02, R11.04, R11.08, R11.12, R11.14, R11.17, R11.19, R11.20, R24.23]
---

# RAG / GraphRAG End-to-End Functional Bug Audit

Area: the complete designer-to-runtime knowledge path: File RAG, document-backed
Knowledge Maps, conversation-backed Concept Maps, their API/facade/service/repository
layers, ingestion and graph workers, Neo4j/Qdrant/Redis/MinIO lifecycle, Agent turn
assembly, WebSocket state recovery, and the frontend knowledge-management views.

Depth: thorough. Three independent read-only discovery lenses covered lifecycle/UI,
Agent retrieval/runtime, and isolation/concurrency/error paths. Every candidate below
survived a separate adversarial refutation pass by a reviewer other than its finder,
except findings explicitly marked `plausible`.

## Coverage and boundaries

**Intent sources checked:** `REQUIREMENTS.md` sections 7, 9, 10, 11, and 24; the
implemented GraphRAG phase dossiers under `docs/tasks/2026-07-07-graphrag-*`; the
GraphRAG Phase 3b dossier; API schemas, migrations, and existing tests.

**Executed verification:**

- Focused unit suite: 214 passed, 1,357 deselected, 2 unrelated warnings.
- The same selection including wiring tests reached 214 passing tests; 18 wiring tests
  could not start because this host cannot resolve the Compose-only `postgres` hostname.
  Those are environmental failures, not product-test failures.
- Static call-chain verification covered every finding from API or event entry point to
  persistence/external-store/runtime outcome.

**Verified clean or refuted suspects:**

- Whole Knowledge Map *config* deletion does purge Neo4j and Qdrant; F-6 is limited to
  document deletion, quarantine, reprocess, and full-corpus rebuild.
- File RAG deliberately permits `scan_status=pending`; the clean-scan defect in F-5 is
  specific to the stricter Knowledge Map contract.
- Concept Map layer retrieval correctly preserves chatroom > agent-group > workspace
  precedence inside its own 2 KB block; F-16 concerns the missing cross-product budget.
- A graph collection's runtime dimension guard prevents silent vector corruption, but it
  does not restore save-time correctness or usability after F-11.
- The suspected RAG deleted-document worker race is blocked by existing FK and cleanup
  ordering.
- Windowed graph extraction followed by one final apply/embed is an approved design, not
  a defect in this audit.

**Not covered by live integration:** real PostgreSQL/Redis/Qdrant/Neo4j/MinIO/ClamAV
services, live provider calls and billing, browser-driven E2E, performance/load, and
deployment topology. Failure scenarios were verified against code and unit seams, not a
running multi-service stack.

---

## F-1: Revoked or foreign pinned RAG keys can still issue billed provider calls

- **Severity**: critical
- **Verdict**: confirmed
- **Evidence**: embedding save validation checks project carry
  (`backend/contexts/knowledge/application/config_service.py:52-65`), but rerank
  validation does not (`:67-76`) and create/update rely on it (`:114-120,169-177`).
  Runtime constructs pinned embed/rerank adapters directly
  (`backend/contexts/knowledge/application/rag_context_provider.py:88-110`).
  `ProviderRouter.call_single_key` checks only active key and capability, never the
  `key_projects.carried` scope (`backend/contexts/keys/application/provider_router.py:578-600`).
- **Failure scenario**: Project P configures a valid carried embedding/rerank key; the
  owner leaves or withdraws it, setting `carried=false` while the API key remains active.
  The next Agent retrieval still embeds/reranks and bills the withdrawn key. Independently,
  an editor who knows another project's Cohere key UUID can attach it at config create or
  update because rerank validation has no project check.
- **Blast radius**: cross-tenant BYO-key spend, quota use, and audit attribution for every
  Agent and document using the affected config.
- **Intent source**: [R7.04], [R10.05], [R10.08], [R10.11].
- **Fix direction**: require project scope when saving both pinned keys and again at every
  pinned provider call; pass the expected project into the router or use a project-scoped
  pinned-key port.

## F-2: Private-room Concept Map graph and status bypass the room ACL

- **Severity**: critical
- **Verdict**: confirmed
- **Evidence**: Concept Map REST status and graph endpoints authorize only project
  membership (`backend/app/api/v1/graphrag.py:331-377`). The GraphRAG WebSocket uses a
  generic config-project role check
  (`backend/contexts/knowledge/interfaces/ws_config_route.py:55-82`) and never branches on
  `owner_kind`. The public room-access facade exists separately at
  `backend/contexts/conversation/interfaces/access.py:1-14` but is not called.
- **Failure scenario**: a project member who is denied read access to private chatroom R
  obtains its Concept Map config UUID, reads the graph/entities/relations and status, and
  subscribes to build updates despite being unable to read R itself.
- **Blast radius**: private-room facts derived from user and Agent messages leak to other
  project members; all chatroom-owned Concept Maps are affected.
- **Intent source**: [R11.17].
- **Fix direction**: resolve the typed owner before authorizing reads/subscriptions; use
  the room ACL for `chatroom`, and retain the documented project/enablement rules for
  `agent_group` and `workspace`.

## F-3: Every-N Concept Map triggers resolve deletion candidates instead of room coverage

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: message-trigger evaluation calls `list_for_agents`
  (`backend/contexts/knowledge/application/graphrag_triggers.py:64-95`). That repository
  method is explicitly for Agent deletion, returns only agent-group owners, and excludes a
  shared group when any other live member remains
  (`backend/contexts/knowledge/infrastructure/graphrag_repositories.py:237-297`). The
  dispatcher receives `chatroom_id` but does not use it
  (`backend/app/api/v1/messages.py:300-321`).
- **Failure scenario**: a chatroom- or workspace-owned Concept Map configured with
  `every_n_messages=1` never increments or builds. A shared A+B group map is also omitted
  for a room containing A when B is a live member elsewhere.
- **Blast radius**: automatic message-count builds fail for all new typed owner modes and
  most multi-member groups.
- **Intent source**: [R11.02], [R11.08].
- **Fix direction**: resolve configs covering the current room/message scope, not configs
  that would be deleted with the currently bound Agents.

## F-4: The accepted GraphRAG silence trigger has no evaluator or sweep

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: the API accepts and persists `silence_minutes`
  (`backend/app/api/v1/graphrag.py:56-68`), while the only Concept Map trigger evaluator
  parses `every_n_messages`
  (`backend/contexts/knowledge/application/graphrag_triggers.py:78-107`). Repository-wide
  search finds silence workers only for Agent wakeups, not GraphRAG.
- **Failure scenario**: a designer configures only `silence_minutes`; chat activity stops
  for longer than the threshold, but no build is ever queued.
- **Blast radius**: the entire GraphRAG silence-trigger feature is inert.
- **Intent source**: [R11.02].
- **Fix direction**: add a bounded periodic Concept Map silence sweep with stable job
  deduplication and owner-scoped delta evaluation.

## F-5: Pending-scan Knowledge Map documents are build- and retrieval-eligible

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: both build and Agent-visible selectors exclude only `quarantined` and
  `skipped`, thereby admitting `pending`
  (`backend/contexts/knowledge/infrastructure/knowmap_repositories.py:374-425`), despite
  comments stating never-cleanly-scanned documents must not build. Ingestion commits and
  independently queues scan and build
  (`backend/contexts/knowledge/application/knowmap_ingest_service.py:157-167`).
- **Failure scenario**: the build worker wins the race against ClamAV, indexes a pending
  document, and the graph/read path exposes it before a later quarantine verdict.
- **Blast radius**: Knowledge Map graph contents and Agent context can include documents
  that have not passed the subsystem's fail-closed malware gate.
- **Intent source**: Phase 3 AC-1 and the repository's documented clean-scan contract.
- **Fix direction**: require `scan_status=clean` for build and retrieval, and trigger the
  build only after the clean verdict (or make build depend on the scan job).

## F-6: Knowledge Map document rebuilds are additive and retain deleted knowledge

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: document delete removes PostgreSQL rows/blob and queues a rebuild
  (`backend/app/api/v1/knowmap.py:533-574`). The loader reads the current corpus
  (`backend/contexts/knowledge/infrastructure/knowmap_delta_loader.py:55-98`), but the
  builder only applies current triples
  (`backend/contexts/knowledge/application/graphrag_builder.py:273-324`). Neo4j uses
  additive `MERGE` plus evidence union and never removes absent rows
  (`backend/contexts/knowledge/infrastructure/neo4j_driver.py:115-156`). Qdrant cleanup
  removes old copies only for entity names re-embedded by the current build
  (`backend/contexts/knowledge/application/graphrag_builder.py:408-435`).
- **Failure scenario**: build from document A, then delete or quarantine A. If no source
  remains, the next full-corpus build has no triples/embeddings and touches neither store;
  graph GET continues showing A indefinitely. If a surviving relation was also supported
  by A, its unioned deleted evidence ref can make the all-source allowlist hide the still-
  valid relation.
- **Blast radius**: stale UI graph, wasted Qdrant top-k seeds, reduced recall, and permanent
  evidence/allowlist distortion until the whole config is deleted.
- **Intent source**: [R11.12], Phase 3 G6 and AC-7.
- **Fix direction**: implement replacement semantics for full-corpus Knowledge Map builds,
  including removal of absent triples/entities/vectors and recomputation of evidence refs.

## F-7: Failed Neo4j compensation is recorded as successful and made unrecoverable

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: rollback exceptions are logged and swallowed, after which the reconciler
  sets `failed` with `rolled back`, publishes terminal state, deletes the snapshot, clears
  the current build pointer, and audits `outcome=rolled_back`
  (`backend/contexts/knowledge/application/graphrag_reconciler.py:350-397`).
- **Failure scenario**: Qdrant retries exhaust and a transient Neo4j failure prevents
  delete/restore. The reconciler nevertheless destroys the only recovery material and
  advertises a successful rollback, leaving inconsistent data with no automatic retry.
- **Blast radius**: Concept Maps and Knowledge Maps share this engine; affected configs can
  remain permanently inconsistent while appearing terminally healed.
- **Intent source**: [R11.04].
- **Fix direction**: keep `failed_compensating`, snapshot, and current pointer until both
  rollback operations succeed; separately surface and retry compensation failures.

## F-8: Qdrant-only deletion orphans cannot be discovered by reconciliation

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: inline purge deletes Neo4j then Qdrant and isolates/swallow-reports each
  failure (`backend/contexts/knowledge/application/graphrag_config_service.py:493-523`;
  Knowledge Map twin at
  `backend/contexts/knowledge/application/knowmap_config_service.py:333-348`). The orphan
  sweep enumerates candidate config IDs exclusively from Neo4j
  (`backend/contexts/knowledge/application/graphrag_reconciler.py:202-218`).
- **Failure scenario**: config deletion succeeds in Neo4j but Qdrant is transiently down.
  The config disappears from Neo4j enumeration, so its retained Qdrant points are never
  revisited.
- **Blast radius**: permanent external-store leaks and unbounded vector storage after
  config/owner deletion across both graph products.
- **Intent source**: [R11.20].
- **Fix direction**: enumerate orphan config IDs from every external store (or durably
  queue failed teardown work) instead of using Neo4j as the sole discovery index.

## F-9: Phase-2 retries generate new Qdrant IDs and are not idempotent

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: each reconciliation retry creates fresh UUID4 point IDs
  (`backend/app/workers/graphrag_reconciler.py:103-113`); Qdrant upsert identity is that
  supplied point ID
  (`backend/contexts/knowledge/infrastructure/graphrag_vector_store.py:112-148`). Retrieval
  requests `top_k` before deduplicating entity names
  (`backend/contexts/knowledge/application/graphrag_retrieve.py:116-127`).
- **Failure scenario**: Qdrant commits an upsert but the client times out. Each retry adds
  another copy instead of replacing the original; duplicates consume candidate slots
  before entity dedup and reduce recall.
- **Blast radius**: storage growth and degraded graph retrieval after ambiguous network
  failures.
- **Intent source**: [R11.04] / section 11.2a's deterministic idempotent point contract.
- **Fix direction**: derive point IDs deterministically from config/build/entity (matching
  the initial builder) and run supersede cleanup after a recovered phase 2.

## F-10: Reads can observe Phase-1 graph mutations before atomic build completion

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: Neo4j changes are committed and state becomes `neo4j_committed` before
  Qdrant phase 2 (`backend/contexts/knowledge/application/graphrag_builder.py:319-405`).
  Retrieval loads any live config, searches all config points, and traverses current
  Neo4j without checking build state or an active-version marker
  (`backend/contexts/knowledge/application/graphrag_retrieve.py:82-128`).
- **Failure scenario**: a delta changes an edge for an already-vectorized entity. A turn
  between phase 1 and phase 2, or during `failed_compensating`, sees that edge even though
  the build may later roll back.
- **Blast radius**: both graph products expose partially committed or eventually reverted
  knowledge to Agents.
- **Intent source**: [R11.04].
- **Fix direction**: introduce an active graph version/build marker and query only the last
  fully committed version, or gate reads while compensation is pending without losing the
  previous active build.

## F-11: Project embedding-dimension pins disappear on delete and race on create

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: File RAG checks only live sibling configs
  (`backend/contexts/knowledge/application/config_service.py:91-106`); Knowledge Map and
  Concept Map pin lookups likewise exclude deleted rows
  (`backend/contexts/knowledge/infrastructure/knowmap_repositories.py:232-249`;
  `backend/contexts/knowledge/application/graphrag_config_service.py:149-168`). These are
  unlocked read-then-insert checks. Delete removes points, not the fixed-size project
  collection; graph `delete_collection` has no production caller
  (`backend/contexts/knowledge/infrastructure/graphrag_vector_store.py:284-318`).
- **Failure scenario**: delete the project's last config, then create one with a different
  dimension. Save succeeds, but indexing/build later fails against the old collection.
  Alternatively, two concurrent first creates both see no pin and persist conflicting
  dimensions.
- **Blast radius**: accepted but unusable File RAG, Knowledge Map, or Concept Map configs;
  one racing config can break future project configuration.
- **Intent source**: [R10.05], [R11.19].
- **Fix direction**: persist a project/collection pin independently of live configs and
  serialize its initialization/change; validate the actual Qdrant collection at save time.

## F-12: Knowledge Map job deduplication can lose a committed corpus change

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: job ID derives from caller-supplied `(state, last_build_at)`
  (`backend/contexts/knowledge/application/knowmap_triggers.py:21-61`). Multipart and tus
  ingestion load the config before lengthy processing, then enqueue using that stale
  object (`backend/contexts/knowledge/application/knowmap_ingest_service.py:97-116,157-168,258-263`;
  `backend/app/workers/tasks/knowmap.py:64-108`). The build snapshots ready documents once
  (`backend/contexts/knowledge/infrastructure/knowmap_delta_loader.py:66-70`), while Arq
  retains job IDs for one hour (`backend/app/workers/main.py:292-294`).
- **Failure scenario**: upload B reads idle/T0; build A snapshots before B is ready and
  completes at T1; B commits and enqueues the stale retained idle/T0 job ID, which Arq
  rejects as a duplicate. B is absent until another mutation/manual build. Integer-second
  timestamps also collide for distinct builds inside one second.
- **Blast radius**: concurrent/multi-file uploads and slow tus/parser paths silently leave
  documents out of the graph.
- **Intent source**: [R11.12].
- **Fix direction**: allocate a monotonic corpus revision transactionally with each
  mutation and deduplicate builds by `(config_id, target_revision)`; recheck revision after
  build completion and enqueue the next revision when needed.

## F-13: Builder-key swaps repin queries without rebuilding old vector spaces

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: Concept Map and Knowledge Map updates accept a different provider/model
  when its dimension matches, persist the new pin, and enqueue no rebuild
  (`backend/contexts/knowledge/application/graphrag_config_service.py:334-371`;
  `backend/contexts/knowledge/application/knowmap_config_service.py:117-166`). Concept Map
  delta builds re-embed only touched entities.
- **Failure scenario**: switch a config from embedding model A to same-dimension model B.
  The next query is embedded with B against vectors produced by A; Knowledge Map retains
  all old vectors, while a Concept Map becomes a mixed vector space over subsequent deltas.
- **Blast radius**: immediate, silent recall collapse for the updated config and possible
  project-wide model inconsistency.
- **Intent source**: [R11.19] (single embedding model and dimension).
- **Fix direction**: treat provider/model changes as a versioned full re-embed with atomic
  cutover, or reject them while indexed data exists.

## F-14: Knowledge Map builder-key update can collide with attached consumer keys

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: Agent attachment/key changes enforce builder group != consumer group
  (`backend/contexts/agents/application/agent_service.py:235-269,421-442`). Knowledge Map
  config update checks only project, embedding availability, and dimension, never attached
  Agents (`backend/contexts/knowledge/application/knowmap_config_service.py:126-151`).
- **Failure scenario**: Agent uses group A and is attached to a map built by B; a designer
  changes the map builder to A. The update succeeds and defeats the enforced billing/rate-
  limit split.
- **Blast radius**: every Agent already attached to the changed Knowledge Map.
- **Intent source**: [R11.01]; [R11.11] exempts Concept Maps only.
- **Fix direction**: validate the new builder group against every attached Agent in the
  same transaction, or define and implement an explicit migration/detach policy.

## F-15: Headless Agent invocations omit automatic File RAG and all Knowledge Maps

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: `run_input_turn` builds only base prompt, notifications, and tools before
  streaming (`backend/contexts/agents/application/runtime/turn_engine.py:425-459`). Room
  turns automatically query all knowledge providers (`:939-950`). A2A and approval paths
  call the headless method
  (`backend/contexts/orchestration/application/a2a_handler.py:182-186`;
  `backend/app/workers/tasks/approvals.py:88`).
- **Failure scenario**: an A2A CALL/INSTRUCT invokes an Agent whose sole answer is in its
  attached Knowledge Map; the map is never queried and no Knowledge Map tool exists. File
  RAG is also not inline; an enabled `file_search` tool is only a model-chosen partial
  fallback.
- **Blast radius**: A2A, workflow, and approval-Agent results differ from room behavior and
  silently ignore designer-attached knowledge.
- **Intent source**: [R10.09], [R11.14], Phase 3 WS3.
- **Fix direction**: add room-independent File RAG/Knowledge Map assembly to headless turns;
  keep room-scoped Concept Maps excluded unless a valid room context is supplied.

## F-16: File RAG, Knowledge Map, and Concept Map have no combined context budget

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: TurnEngine independently appends all three blocks
  (`backend/contexts/agents/application/runtime/turn_engine.py:926-950`) and forwards the
  joined system text without a cross-block allocator (`:1583-1620`). File RAG caps only
  result count and renders every full chunk
  (`backend/contexts/knowledge/application/rag_context_provider.py:128-149,183-200`); API
  `top_k` permits 100. Graph blocks are only individually capped.
- **Failure scenario**: top_k=100 with 512-token chunks plus two graph blocks and history
  exceeds the provider limit, or File RAG consumes the space that the documented narrow-
  scope precedence reserves for more specific knowledge.
- **Blast radius**: any Agent combining sources can fail a turn or unpredictably lose the
  most relevant context.
- **Intent source**: [R11.19], approved two-axis AC-18.
- **Fix direction**: implement one token-aware allocator over all knowledge blocks with a
  deterministic precedence policy and source-preserving truncation.

## F-17: Compact mode budgets history, not the assembled next request

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: `_assemble_history` projects only stored history token counts and decides
  compaction before prompt/knowledge assembly
  (`backend/contexts/agents/application/runtime/turn_engine.py:1457-1490`). Base/dynamic
  system blocks, retrieved knowledge, tools, and other turn context are assembled later
  (`:920-1031`) with no final global count.
- **Failure scenario**: a 96k compact cap sees 90k history and skips compaction; a 20k
  system/RAG prefix makes the actual next request 110k and causes an avoidable limit error.
- **Blast radius**: compact Agents with large prompts, tools, or knowledge frequently miss
  their configured safety threshold.
- **Intent source**: [R9.10].
- **Fix direction**: estimate the complete provider payload, including response reserve,
  immediately before dispatch and compact/re-budget until it fits.

## F-18: Soft-deleting File RAG or Knowledge Map configs strands attached Agents

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: delete services destroy child data and soft-delete configs but never clear
  Agent foreign keys
  (`backend/contexts/knowledge/application/config_service.py:242-280`;
  `backend/contexts/knowledge/application/knowmap_config_service.py:182-208`). `ON DELETE
  SET NULL` only fires on hard delete (`backend/alembic/versions/0012_rag.py:142-150`;
  `backend/alembic/versions/0048_knowmap.py:170-179`). Active knowledge repos hide the
  tombstone, while Agent/UI rows retain the raw UUID.
- **Failure scenario**: delete a config attached to Agents. Runtime silently loses the
  source; Agent detail retains a stale 404 link/selection, and its full-form PATCH submits
  the invalid UUID so unrelated edits fail until the user discovers and clears it
  (`frontend/src/slices/agents/views/AgentDetailView.vue:374-386,418-432,1006-1047`).
- **Blast radius**: every attached Agent becomes misleadingly configured and can become
  uneditable through the normal UI.
- **Intent source**: migration 0048's explicit deletion-unbind contract and config DELETE
  semantics.
- **Fix direction**: atomically null all Agent bindings (and reconcile dependent tool
  state) as part of soft deletion; include affected Agent IDs in audit metadata.

## F-19: The required local BGE reranker has no reachable product path

- **Severity**: major
- **Verdict**: confirmed
- **Evidence**: API and frontend schemas allow only Cohere
  (`backend/app/api/v1/rag.py:67-88`;
  `frontend/src/slices/agents/types/schemas.ts:68-78`); service requires a key/provider for
  every enabled rerank (`backend/contexts/knowledge/application/config_service.py:114-120,169-177`),
  and runtime constructs only `RouterReranker`
  (`backend/contexts/knowledge/application/rag_context_provider.py:97-111`).
  `LocalBgeReranker` exists only as an unreferenced adapter
  (`backend/contexts/knowledge/infrastructure/rerankers.py:84-120`).
- **Failure scenario**: a designer selects the SRS-promised bundled local reranker; there
  is no API/UI value that can represent it, and a crafted keyless config runs vector-only.
- **Blast radius**: the entire local-reranking option is unavailable.
- **Intent source**: [R10.08].
- **Fix direction**: add an explicit local provider choice, keyless validation branch,
  deploy/configured service URL, runtime factory, health handling, and end-to-end test.

## F-20: Editing chunk parameters leaves one config with mixed chunking semantics

- **Severity**: major
- **Verdict**: plausible
- **Evidence**: both APIs/services persist `chunk_params` patches without reprocessing
  existing documents or queuing a rebuild
  (`backend/contexts/knowledge/application/config_service.py:179-194`;
  `backend/contexts/knowledge/application/knowmap_config_service.py:126-151`), and both
  detail UIs expose the edit.
- **Failure scenario**: change chunk size/overlap/semantic threshold after documents exist.
  Old chunks/vectors/graph keep the prior policy while new uploads use the displayed new
  policy, so the singular config setting no longer describes its corpus.
- **Blast radius**: retrieval quality and evidence boundaries for all pre-existing
  documents; Knowledge Map graph rebuild does not repair the source chunks.
- **Intent source**: [R10.04] defines the strategy per config; [R11.12] names reprocess as
  a graph-change trigger. The SRS does not explicitly state whether edits are retroactive,
  so the defect remains plausible rather than confirmed.
- **Fix direction**: choose and document either immutable/prospective-only parameters or a
  versioned reprocess workflow; do not expose a silently mixed policy.

## F-21: RAG WebSocket reconnect cannot recover missed terminal ingestion state

- **Severity**: minor
- **Verdict**: confirmed
- **Evidence**: reconnect sync only lists configs and invalidates the config-list query;
  it never updates local progress or invalidates documents
  (`frontend/src/slices/agents/composables/useRagConfigSocket.ts:61-70`). The detail view
  refetches documents only after local progress reaches a terminal state
  (`frontend/src/slices/agents/views/RagConfigDetailView.vue:161-167`).
- **Failure scenario**: disconnect during indexing and miss `completed`/`failed`; reconnect
  leaves the progress bar and document status stale indefinitely until reload/another
  event.
- **Blast radius**: designer-facing ingestion status after routine reconnects.
- **Intent source**: [R24.23] reconnect resynchronization and the composable's own recovery
  contract.
- **Fix direction**: fetch authoritative document/job state on reconnect and replace local
  progress before resubscribing.

## F-22: Automatic Knowledge Map rebuilds are invisible to an idle detail page

- **Severity**: minor
- **Verdict**: confirmed
- **Evidence**: the detail page watches only when initial state is already in progress or
  after an explicit rebuild click
  (`frontend/src/slices/agents/views/KnowledgeMapConfigDetailView.vue:103-140`). Upload and
  delete success invalidate documents but do not call `watchBuild` or refresh config
  (`:287-325`). Polling operates only for already-watched configs
  (`frontend/src/slices/agents/composables/useBuildStateSocket.ts:95-128`).
- **Failure scenario**: upload/delete while the page shows idle; backend auto-build starts,
  but no subscriber/poller exists, so the badge can remain idle through completion.
- **Blast radius**: live build feedback for automatic document-change rebuilds.
- **Intent source**: Phase 4b's WebSocket + polling progress contract.
- **Fix direction**: start watching whenever a mutation can enqueue a build, or keep the
  active detail config continuously subscribed with reconnect/poll recovery.

## F-23: Failed tus reuploads can be suppressed by the retained worker job ID

- **Severity**: minor
- **Verdict**: plausible
- **Evidence**: RAG and Knowledge Map tus finalizers reuse document-only job IDs
  (`backend/contexts/knowledge/application/rag_tus_finalizer.py:149-172`;
  `backend/contexts/knowledge/application/knowmap_tus_finalizer.py:123-132`); worker results
  remain for 3,600 seconds (`backend/app/workers/main.py:292-294`), while the queue wrapper
  discards the duplicate-enqueue return (`backend/shared_kernel/queue.py:21-32`).
- **Failure scenario**: a large-file job exhausts retries and leaves the document failed;
  immediate same-SHA reupload reuses the retained ID, no new worker is queued, and the API
  reports the existing failed document as though retry scheduling succeeded.
- **Blast radius**: retry UX for >32 MB RAG and Knowledge Map uploads during the one-hour
  retention window.
- **Intent source**: the tus finalizer's documented genuine-retry behavior.
- **Fix direction**: include an ingestion-attempt/revision in job identity and treat Arq's
  duplicate return as an explicit state to surface or reschedule. Confirm against the
  deployed Arq version in an integration test before implementation.

---

## Follow-ups outside the functional findings

- **FU-1 (security)**: F-1 and F-2 require dedicated `/check-security` remediation review;
  they cross BYO-key and private-room trust boundaries and should block release.
- **FU-2 (resource hardening)**: Concept Map layer count and the final accumulated triple/
  embedding batch have no independent resource cap. The current one-apply design is
  approved, so this audit did not relabel it as a functional bug.
- **FU-3 (test environment)**: provide a documented host-mode wiring profile or run the
  18 PostgreSQL wiring tests inside Compose; current unit success cannot validate SQL
  concurrency, typed-owner queries, or real FK behavior.
- **FU-4 (dossier integrity)**: the Phase 3 Knowledge Map dossier is marked `implemented`
  while AC-3 and AC-7 remain unchecked and its deviation log records missing integration
  coverage. Route that lifecycle inconsistency to process/quality review.

## Hand-off

Recommended bugfix-spec batches, in order:

1. **Release blockers**: F-1 and F-2 as separate security-sensitive bugfix specs.
2. **Graph triggers and lifecycle**: F-3 through F-6 and F-12.
3. **2PC/reconciliation correctness**: F-7 through F-10.
4. **Embedding invariants**: F-11, F-13, and F-14.
5. **Agent runtime context**: F-15 through F-17.
6. **Configuration semantics**: F-18 through F-20.
7. **Frontend recovery**: F-21 and F-22.
8. **Upload retry**: F-23 after confirming Arq behavior in the deployed environment.

Per the audit/spec hand-off contract, selected findings become individual or explicitly
batched bugfix dossiers under `docs/tasks/`; this audit remains `draft` until triage.
