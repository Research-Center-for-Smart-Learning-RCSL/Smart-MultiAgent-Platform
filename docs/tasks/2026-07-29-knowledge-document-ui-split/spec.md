---
type: refactor
status: approved
created: 2026-07-29
requirements: [R24.04, R24.05, R24.24]
depends_on: [2026-07-22-reingest-allowlist-propagation]
---

# Split knowledge document behavior from oversized detail views

## 1. Summary

Extract RAG and Knowledge Map document/upload behavior, tabs and settings from two
oversized views while preserving product-specific semantics and fixing authoritative
reconciliation after a partially successful upload batch.

## 2. Motivation

`RagConfigDetailView.vue` and `KnowledgeMapConfigDetailView.vue` are about 877 and 841
lines and combine routing, forms, queries, uploads, allowlists, deletion and live state,
contrary to R24.04. Both invalidate only after the whole upload batch, so an earlier
success followed by an error leaves stale cached documents.

## 3. Non-goals

- No API, schema or user-visible workflow change.
- No generic mega-composable or second socket engine.
- Keep sequential upload and stop on first error.
- No partial-success toast.

## 4. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | One product composable? | No; use `useRagDocuments` and `useKnowmapDocuments`, optionally one private small batch helper. | Products have distinct effects. |
| Q-2 | Partial success? | Reconcile accepted files in `finally`, retain existing error toast, no success toast. | Fixes stale state without new UX. |

## 5. Current vs Target Structure

Views retain route/config/header/tab orchestration. Family composables own document
queries, selection, allowlist editing, upload/delete and reconciliation. Family document
and settings tabs use narrow props/events. Existing sockets, query keys, API wrappers,
forms and `shared/ui/typedTable` remain authoritative.

## 6. Characterization Test Plan

Add two-file success-then-conflict/422/network, all-success, first-file-failure, exact
agent IDs, delete/set-agents invalidation, Knowledge Map owner/config/build rewatch,
modal reset and socket non-duplication tests.

## 7. Migration Steps

1. Extract composables and partial-batch reconciliation.
2. Replace local typed-table helpers.
3. Extract family document tabs.
4. Extract family settings tabs.
5. Reduce detail views to orchestration, running tests after every step.

## 8. Risks and Rollback

Risks: lost owner gate/build rewatch, duplicate sockets, query drift, watcher
initialization and modal leakage. Each extraction is independently revertible.

## 9. Acceptance Criteria

- [ ] AC-1: Existing behavior/tests remain unchanged except stale-cache correction.
- [ ] AC-2: Partial batches reconcile every accepted file after a later failure.
- [ ] AC-3: Family composables own document behavior.
- [ ] AC-4: Focused family components own document/settings UI.
- [ ] AC-5: Detail views retain orchestration, not upload/allowlist business logic.
- [ ] AC-6: No duplicate socket/query engine or boundary violation.
- [ ] AC-7: Vitest coverage, ESLint, vue-tsc and build pass.

## 10. SRS Delta

None; behavior is preserved and structure complies with R24.04.

## 11. Deviation Log

Empty.

## 12. Follow-ups

- FU-1: Consider a shared access-picker only after both family components stabilize.

