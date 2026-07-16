---
type: audit
status: draft
created: 2026-07-17
requirements: [R7.04, R9.10, R10.04, R10.05, R10.06, R10.08, R10.09, R10.11, R11.01, R11.02, R11.04, R11.08, R11.11, R11.12, R11.14, R11.17, R11.19, R11.20, R24.23]
---

# RAG / GraphRAG 2026-07-14 Remediation Verification

Source audit: `docs/audits/2026-07-14-rag-graphrag-end-to-end/findings.md`.

Area: the same File RAG, Knowledge Map, Concept Map, graph-build/reconciliation,
Agent runtime, tenancy teardown, WebSocket, and frontend recovery paths. This is a
current-code verification, not a review of task-dossier status.

## Verdict

The remediation is substantial, but not complete. The original failure scenarios are
fully repaired for F-1, F-2, F-5, F-7, F-8, F-9, F-13--F-15, F-17--F-23, F-25,
F-27, and F-28. F-3, F-4, F-6, F-10--F-12, F-16, F-24, and F-26 remain only
partially repaired through the confirmed error paths below.

| Original finding | Current result | Verification basis |
| --- | --- | --- |
| F-1 | fixed | Pinned calls now scope-check at `provider_router.py:585-613`; save-time checks cover embed and rerank selection in `config_service.py:105-164`. |
| F-2 | fixed | One owner-aware predicate covers REST and sockets in `interfaces/config_access.py:39-83` and `interfaces/ws_config_route.py:71-108`. |
| F-3, F-4 | partial | New F-1. |
| F-5 | fixed | Current Knowledge Map selectors and scan worker require the clean verdict; regression seams are in `tests/wiring/test_knowmap_scan_gating.py:81-102`. |
| F-6, F-10 | partial | New F-2. |
| F-7 | fixed | Failed compensation retains recovery material and state; `graphrag_reconciler.py:439-455` and `tests/unit/test_graphrag_builder.py:1161-1207`. |
| F-8 | fixed | The sweep unions Qdrant IDs with Neo4j IDs at `graphrag_reconciler.py:239-260`. |
| F-9 | fixed | Retry IDs are deterministic and covered in `tests/unit/test_graphrag_builder.py:871-967`. |
| F-11 | partial | New F-3. |
| F-12 | partial | New F-4. |
| F-13 | fixed | Indexed model changes are rejected in `graphrag_config_service.py:407-426` and `knowmap_config_service.py:198-211`. |
| F-14 | fixed | Builder-key changes detach colliding consumers atomically; `knowmap_config_service.py:179-249`. |
| F-15 | fixed | Headless turns use the shared knowledge assembly at `turn_engine.py:602-643`. |
| F-16 | partial | New F-5. |
| F-17 | fixed | Compact-room planning includes fixed context, tools, input, and reserve at `turn_engine.py:1220-1229,1838-1878`. |
| F-18 | fixed | Soft deletes unbind Agents through the facade in `config_service.py:398-429` and `knowmap_config_service.py:295-324`. |
| F-19 | fixed | The keyless BGE path is accepted and constructed in `rag_context_provider.py:135-165,235-250`. |
| F-20 | fixed | Existing-document chunk parameters are immutable in `config_service.py:296-311` and `knowmap_config_service.py:158-171`. |
| F-21, F-28 | fixed | RAG socket state is reconstructed from documents on mount/reconnect at `useRagConfigSocket.ts:94-105,150-188`. |
| F-22 | fixed | Detail view continuously watches and re-watches automatic builds at `KnowledgeMapConfigDetailView.vue:110-123,335-357`. |
| F-23 | fixed | Terminal-only ingestion-attempt claims and attempt-scoped job IDs are present in `rag_tus_finalizer.py:99-116,175-186`. |
| F-24 | partial | New F-7. |
| F-25 | fixed | The connection watchdog invokes live authorization through `ws_config_route.py:84-108`. |
| F-26 | partial | New F-6. |
| F-27 | fixed | `SKIPPED` advances revision/rebuild in `workers/tasks/knowmap.py:226-270`. |

## F-1: Agentless rooms still suppress chatroom/workspace Concept Map triggers

- **Severity**: major
- **Verdict**: confirmed
- **Original findings**: F-3, F-4
- **Evidence**: `backend/app/api/v1/messages.py:301-306`; `backend/contexts/knowledge/application/graphrag_triggers.py:111-121`; `backend/contexts/knowledge/infrastructure/graphrag_repositories.py:305-375`; `backend/tests/unit/test_graphrag_triggers.py:105-163`.
- **Failure scenario**: Create a chatroom- or enabled-workspace-owned Concept Map with `every_n_messages=1` or `silence_minutes`, but bind no Agent to the room. A user message persists, then `_dispatch_graphrag_builds` returns before evaluation because `bound_agent_ids` is empty. Even if called directly, the evaluator returns before the room selector. The selector itself supports an empty agent list for the chatroom/workspace layers, so neither its counter nor its silence clock is touched and no build can fire.
- **Blast radius**: all agentless rooms using either typed owner layer; automatic Concept Map building remains inert for the configuration F-3/F-4 was intended to repair.
- **Intent source**: [R11.02], [R11.08].

## F-2: A failed Knowledge Map replacement can publish a readable, partially replaced graph

- **Severity**: major
- **Verdict**: confirmed
- **Original findings**: F-6, F-10
- **Evidence**: `backend/contexts/knowledge/application/graphrag_builder.py:323-342,529-562`; `backend/contexts/knowledge/infrastructure/neo4j_driver.py:203-210`; `backend/contexts/knowledge/domain/graphrag.py:91-96`; `backend/contexts/knowledge/application/graphrag_retrieve.py:105-112`.
- **Failure scenario**: A full-corpus Knowledge Map rebuild successfully applies current triples, then `remove_stale_for_build` fails. Those calls use separate Neo4j sessions, so the first mutation has already committed. The catch marks the config `FAILED` and deletes the snapshot/current-build pointer. Retrieval gates only `RUNNING`, `NEO4J_COMMITTED`, and `FAILED_COMPENSATING`, so it then serves the `FAILED` graph: new triples plus stale triples, with no compensating recovery path.
- **Blast radius**: deleted/quarantined knowledge can remain visible, and reads can observe a graph that did not reach an atomic replacement state.
- **Intent source**: [R11.04], [R11.12].

## F-3: A failed final collection teardown releases the embedding-dimension pin too early

- **Severity**: major
- **Verdict**: confirmed
- **Original finding**: F-11
- **Evidence**: `backend/app/api/v1/rag.py:367-388`; `backend/app/api/v1/knowmap.py:345-364`; `backend/contexts/knowledge/application/config_service.py:469-477`; `backend/contexts/knowledge/application/knowmap_config_service.py:400-418`; `backend/contexts/knowledge/infrastructure/qdrant_store.py:73-92`; `backend/tests/unit/test_embedding_pin.py:213-346`.
- **Failure scenario**: Delete the final config while Qdrant is unavailable. The durable pin is cleared before external teardown, and the teardown logs/swallows its Qdrant error. Creating a config at another dimension now succeeds, but the retained collection still has the old dimension and rejects the later ingestion/build.
- **Blast radius**: File RAG and Knowledge Map projects can again accept a configuration that cannot index data after an external-store deletion failure.
- **Intent source**: [R10.05], [R11.19].

## F-4: A post-build finalization failure can still lose a committed Knowledge Map corpus revision

- **Severity**: major
- **Verdict**: confirmed
- **Original finding**: F-12
- **Evidence**: `backend/contexts/knowledge/application/knowmap_triggers.py:18-63`; `backend/contexts/knowledge/infrastructure/knowmap_repositories.py:234-250`; `backend/app/workers/tasks/knowmap.py:420-468`; `backend/tests/unit/test_knowmap_build_dedup.py:140-167`.
- **Failure scenario**: Build A snapshots revision N. While it runs, a mutation commits revision N+1. A completes, but its post-build `_finalize_build_revision` call fails transiently. The worker only logs the exception; it has no durable retry/outbox. Revision N+1 remains unbuilt until another mutation or a manual rebuild.
- **Blast radius**: a committed corpus change can be absent indefinitely from the Knowledge Map, preserving the original lost-change outcome on the repair's failure path.
- **Intent source**: [R11.12].

## F-5: Headless turns still bypass the cross-source knowledge budget

- **Severity**: major
- **Verdict**: confirmed
- **Original finding**: F-16
- **Evidence**: `backend/contexts/agents/application/runtime/turn_engine.py:602-643,1254-1290,2231-2248`; `docs/tasks/2026-07-14-knowledge-context-token-budget/spec.md:296-302,333-336`; `backend/tests/unit/test_turn_context_budget.py:72-152`.
- **Failure scenario**: Invoke an Agent headlessly (A2A or approval path) with File RAG and Knowledge Map attached, large fixed prompt/tool definitions, and a broad retrieved corpus. The shared helper is called with `budget=None`, which deliberately leaves all knowledge blocks uncapped. The room-turn allocator and its final payload safety check do not run, so the provider request can exceed its context limit.
- **Blast radius**: headless knowledge-enabled turns can fail from oversized context despite the F-16 repair for normal room turns.
- **Intent source**: [R9.10], [R11.19].

## F-6: `admin_reset` treats an expired recovery snapshot as a successful discard

- **Severity**: major
- **Verdict**: confirmed
- **Original finding**: F-26
- **Evidence**: `backend/contexts/knowledge/application/graphrag_config_service.py:535-605`; `backend/contexts/knowledge/application/graphrag_reconciler.py:422-438`; `backend/contexts/knowledge/infrastructure/redis_lock.py:6-9,123-171`; `backend/tests/unit/test_graphrag_reset.py:185-205`.
- **Failure scenario**: A stuck build retains a current-build pointer after its 24-hour snapshot expires. `admin_reset` deletes the build's Neo4j rows, skips `restore_from_snapshot` because the snapshot is `None`, clears recovery state, and publishes `IDLE` with `outcome=discarded`. The reconciler treats precisely that state as `compensation_unavailable` and terminal `FAILED`.
- **Blast radius**: an administrator can make a partially changed graph appear healthy while permanently losing the pre-build graph state.
- **Intent source**: [R11.04].

## F-7: Tenancy teardown can erase live source infrastructure before the hard deletion commits

- **Severity**: minor
- **Verdict**: plausible
- **Original finding**: F-24
- **Evidence**: `backend/app/workers/tasks/retention.py:219-245,610-623`.
- **Failure scenario**: The retention policy purges MinIO blobs and the Qdrant collection, then a later database operation in its surrounding transaction fails. The transaction rolls back the hard deletion but external destruction cannot roll back, leaving the retained project row without its File RAG/Knowledge Map source data.
- **Blast radius**: a recoverable retention transaction can cause data loss rather than only delaying cleanup.
- **Intent source**: [R10.06], F-24's tenant-lifecycle intent.

## Verification limits

Focused backend tests could not run because `backend/.venv` lacks `pytest-asyncio`: pytest rejects the configured `asyncio_mode` and all async-marked test modules at collection. Focused frontend tests could not run because `pnpm` tried to remove `node_modules` and aborted without a TTY. Existing targeted test code was inspected, but this audit does not claim a live suite pass. Real PostgreSQL, Redis, Qdrant, Neo4j, MinIO, ClamAV, provider, browser E2E, and retention integration paths were not available in this host session.

## Hand-off

Do not close the 2026-07-14 audit as fully remediated. Create bugfix dossiers for F-1 through F-6 before declaring the associated original findings fixed. Triage F-7 as a retention-transaction design decision; if durable external work is required, use an outbox/teardown state rather than irreversible work inside the database transaction.
