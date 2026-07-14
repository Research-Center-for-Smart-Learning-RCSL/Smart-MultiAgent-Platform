---
type: bugfix
status: draft
created: 2026-07-14
requirements: [R11.14]
---

# Soft-deleting a File RAG or Knowledge Map config strands attached Agents (F-18)

Source audit: `docs/audits/2026-07-14-rag-graphrag-end-to-end/findings.md` (F-18).

## 1. Summary

Deleting a File RAG or Knowledge Map config soft-deletes the config row and destroys its
child documents, but never nulls the `rag_config_id` / `knowmap_config_id` foreign keys on
Agents attached to it. The database's `ON DELETE SET NULL` constraint — added expressly to
unbind Agents on config deletion — fires only on a physical row `DELETE`, never on the
`UPDATE deleted_at` that soft-delete performs. Every attached Agent is left pointing at a
tombstoned config: retrieval silently returns no context, and — worse — the Agent detail
form resubmits the now-invalid UUID on any unrelated edit, so the Agent becomes uneditable
through the normal UI until the user discovers and clears the dangling selection by hand.

## 2. Observed vs Expected

- **Observed** —
  - `RagConfigService.soft_delete` hard-deletes child documents in batches and then
    soft-deletes the config (`UPDATE ... deleted_at`), returning docs for infra purge; it
    never touches `agents.rag_config_id`
    (`backend/contexts/knowledge/application/config_service.py:224-280`; soft-delete write
    at `backend/contexts/knowledge/infrastructure/repositories.py:179-182`). The service
    holds only a `KeysFacade` (`config_service.py:13-17,50`) — no path to Agents.
  - `KnowmapConfigService.soft_delete` is structurally identical and likewise never nulls
    `agents.knowmap_config_id`
    (`backend/contexts/knowledge/application/knowmap_config_service.py:168-208`; write at
    `backend/contexts/knowledge/infrastructure/knowmap_repositories.py:227-230`).
  - The FK constraints are `ondelete="SET NULL"`
    (`backend/alembic/versions/0012_rag.py:143-150`;
    `backend/alembic/versions/0048_knowmap.py:172-180`, whose inline comment reads "SET
    NULL so deleting a config unbinds agents"), but they trigger only on a row `DELETE`.
    The Agent columns are bare nullable UUIDs with no ORM-level FK
    (`backend/contexts/agents/infrastructure/tables.py:49,53`).
  - Runtime tolerates the dangling id (returns no context):
    `backend/contexts/knowledge/application/rag_context_provider.py:88-90`;
    `backend/contexts/knowledge/application/knowmap_context_provider.py:154-155,164`.
  - The break surfaces on the write path: the Agent detail form reseeds the stale UUID
    into the model (`frontend/src/slices/agents/views/AgentDetailView.vue:384-385`), the
    `<SSelect>` options list only active configs (`:628-636`), and a full-form PATCH spreads
    every field including the stale id (`assemblePayload`, `:418-433`; submit `:509,551`).
    The backend then re-validates and raises `RagConfigOutOfProject`
    (`backend/contexts/agents/application/agent_service.py:226-234,417-421`) or
    `KnowmapConfigOutOfProject` (`agent_service.py:236-270,429-443`), failing the unrelated
    edit. This full-form resubmission defeats the backend's own graceful-skip escape hatch
    (`require_exists=False`, `agent_service.py:261-262`), which applies only when the id is
    absent from the payload.
- **Expected** — deleting a config unbinds its Agents. The intent is explicit in migration
  0048's `SET NULL` contract and comment (`0048_knowmap.py:170-171`), mirrored for RAG
  (`0012_rag.py:143-150`), and consistent with [R11.14] (an *attached* Knowledge Map is
  queried at invocation — a deleted one must become unattached, not a dangling reference).

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Fix forward only, or also repair existing dangling bindings? | Fix forward + one-time data-repair migration. | Configs already soft-deleted in the field have stranded Agents that stay uneditable until touched by hand; a backfill nulls them at once. |
| Q-2 | Hard-delete the config so the existing SET NULL FK fires, or keep soft-delete and unbind explicitly? | Keep soft-delete; unbind explicitly. | Configs are intentionally tombstoned (audit/retention, active-repo filtering); switching to hard delete would break those contracts. |

## 4. Reproduction

1. In a project, create a File RAG config C and attach Agent A to it (`agents.rag_config_id
   = C.id`).
2. Delete config C (`DELETE /rag-configs/{C}`), which runs `RagConfigService.soft_delete`.
3. Observe `agents.rag_config_id` for A still equals `C.id`, while `rag_configs.deleted_at`
   for C is set.
4. Open Agent A in the UI, change an unrelated field (e.g. name), and save. The PATCH
   resubmits `rag_config_id=C.id`; the backend raises `RagConfigOutOfProject` and the edit
   fails. Identical steps reproduce with a Knowledge Map config and `knowmap_config_id`.

## 5. Root Cause Analysis

The causal chain:

1. Both delete services perform a soft-delete `UPDATE deleted_at`
   (`config_service.py:264` → `repositories.py:179-182`;
   `knowmap_config_service.py:195` → `knowmap_repositories.py:227-230`), not a row
   `DELETE`.
2. The Agent-unbind behavior is encoded **only** as a DB `ON DELETE SET NULL` FK
   (`0012_rag.py:149`, `0048_knowmap.py:179`), which the DB never evaluates for an
   `UPDATE`.
3. Neither service has any application-level unbind step, and neither holds a reference to
   the agents context to perform one (`config_service.py:50` constructs only `KeysFacade`).

Root cause: **the unbind contract lives solely at the physical-DELETE layer while deletion
is implemented as a soft-delete UPDATE, so nothing ever nulls the Agent foreign keys.** The
earliest correcting link is the delete service — it must perform the unbind explicitly. The
frontend full-form resubmission is an aggravating factor that turns a silent strand into a
hard edit failure, not the root cause.

## 6. Blast Radius and Sibling Suspects

- **Blast radius** — every Agent attached to any File RAG or Knowledge Map config that has
  ever been deleted: silent loss of retrieval context plus edit-blocking on the next
  full-form PATCH. Data already written: existing dangling `agents.rag_config_id` /
  `agents.knowmap_config_id` rows in every environment (addressed by the repair migration).
- **Sibling suspects**:
  - *Key Group deletion* — `agents.key_group_id` FK is `ondelete="RESTRICT"`
    (`backend/alembic/versions/0011_agents.py:66-67`) but `KeyGroupService.delete` is a
    soft-delete (`backend/contexts/keys/application/group_service.py:113-132`), so the FK
    likewise never fires. **Different class of consequence** (the column is `NOT NULL` and
    could not be nulled anyway), so it is out of scope here — noted as FU-2.
  - *GraphRAG / Concept Map configs* — carry no per-Agent FK (`graphrag_config_id` was
    dropped in `0044_graphrag_drop_agent_id.py`; resolved via owner layers at
    `turn_engine.py:1739`), and their soft-delete already nulls their own owner columns
    (`backend/contexts/knowledge/infrastructure/graphrag_repositories.py:654-669`).
    **Cleared** — not affected by this defect.
  - *Hard delete via tenancy retention* — a physical org/project delete would fire the FK
    correctly; the leak is specific to the soft-delete path. **Cleared** for this finding
    (a separate File-RAG blob/collection leak on tenancy deletion is F-24, specced
    elsewhere).

## 7. Fix Design

Perform the Agent unbind explicitly inside each config soft-delete, and backfill existing
dangling rows.

**A. Cross-context unbind (SoC-correct direction).** Add an unbind method to the agents
facade — e.g. `AgentsFacade.clear_config_bindings(project_id, *, rag_config_id=None,
knowmap_config_id=None) -> list[uuid.UUID]` in
`backend/contexts/agents/interfaces/facade.py` — that nulls the matching column for every
Agent in the project bound to that config and returns the affected Agent IDs. This mirrors
the existing knowledge/keys → agents read direction
(`AgentsFacade.count_agents_for_key_groups`, `facade.py:94`); the knowledge context must
not reach into agents tables directly.

**B. Wire it into both delete services, same transaction.** Inject an `AgentsFacade(db)`
into `RagConfigService` and `KnowmapConfigService` (same construction pattern as the
existing `KeysFacade(db)`, `config_service.py:50`; the facade takes just an `AsyncSession`,
`backend/contexts/agents/interfaces/facade.py:71-75`) and call `clear_config_bindings`
within the soft-delete unit of work, so the unbind and the `deleted_at` write commit
atomically. The unbind is project-scoped (the FK guarantees only same-project Agents
reference the config), so it cannot touch another tenant's Agents. The `UPDATE agents ...`
fires the `BEFORE UPDATE` trigger `trg_agents_bump_version`
(`backend/alembic/versions/0029_version_bump_triggers.py:46-51`), so each unbound Agent's
`version` auto-increments — desirable: a stale open edit form now fails its next save with
a clean `AgentVersionMismatch` (409) instead of a config-validation error.

**C. Audit the affected Agents.** Include the returned Agent IDs in the existing
`rag.config_deleted` / `knowmap.config_deleted` audit metadata
(`config_service.py:265-279`; `knowmap_config_service.py:196-207`) so the unbind is
traceable.

**D. Data-repair migration `0052`.** A one-time migration nulls `agents.rag_config_id`
where it references a config whose `rag_configs.deleted_at IS NOT NULL`, and
`agents.knowmap_config_id` where the referenced `knowmap_configs.deleted_at IS NOT NULL`.
This is a data-only `UPDATE`; `downgrade` is a no-op (nulled bindings cannot be
reconstructed and the pre-fix state was invalid). Forward-compatible per the backend
migration rule. Note the migration `UPDATE` also fires `trg_agents_bump_version`, bumping
`version` for each repaired Agent — expected and harmless (an old client refetches on the
next `If-Match` mismatch).

Why this corrects the root, not the symptom: the unbind removes the dangling reference at
its source, so retrieval, the detail form's field value, and the full-form PATCH all become
consistent automatically (the form reseeds `null`, the dropdown shows no selection, the
PATCH sends no stale id). No frontend change is required for correctness.

## 8. Regression Test Plan

Failing tests first, modeled on the sibling
`backend/tests/unit/test_graphrag_soft_delete_owner_clear.py`.

- New `backend/tests/unit/test_config_delete_agent_unbind.py`:
  - RAG: attach an Agent to a config, soft-delete the config, assert
    `agents.rag_config_id` is `NULL` and the affected Agent id appears in the audit
    metadata. Fails today because `RagConfigService.soft_delete` never touches the column.
  - Knowledge Map: same assertion against `agents.knowmap_config_id` and
    `KnowmapConfigService.soft_delete`.
  - Isolation: an Agent in a *different* project bound to a *different* config is untouched.
- Migration test (unit, against the repair logic): given an Agent row referencing an
  already-soft-deleted config, the `0052` upgrade nulls the binding; an Agent referencing a
  live config is left intact.

## 9. Risks and Rollback

- **Cross-context coupling** — the knowledge delete services gain an agents dependency.
  Mitigation: go through the facade only (no direct table access), keeping the SoC boundary
  intact; the direction (knowledge → agents facade) matches the existing keys → agents
  read precedent.
- **Transaction scope** — the unbind must share the soft-delete session so a failure rolls
  back both; do not split into a second commit that could leave a half-unbound state.
- **Migration on large tables** — the repair `UPDATE` is bounded by the number of Agents
  with non-null bindings (small); no batching expected, but it is a plain data `UPDATE`
  with a no-op downgrade, so rollback of the code change is clean and the migration is
  reversible-by-no-op.
- **Residual open-form race** — see FU-1; not introduced by this fix and out of scope.

## 10. Acceptance Criteria

- [ ] AC-1: the RAG unbind regression test in §8 fails before the fix and passes after.
- [ ] AC-2: the Knowledge Map unbind regression test in §8 fails before the fix and passes
      after.
- [ ] AC-3: soft-deleting a RAG config nulls `agents.rag_config_id` for every attached
      Agent in the same transaction as the `deleted_at` write; identically for
      `agents.knowmap_config_id` on Knowledge Map delete.
- [ ] AC-4: affected Agent IDs are recorded in the `rag.config_deleted` /
      `knowmap.config_deleted` audit metadata.
- [ ] AC-5: the unbind touches only Agents in the config's project (no cross-tenant
      effect), verified by the isolation test.
- [ ] AC-6: migration `0052` nulls existing dangling `rag_config_id` / `knowmap_config_id`
      bindings that reference soft-deleted configs, and leaves live-config bindings intact.
- [ ] AC-7: after the fix, an unrelated PATCH to an Agent whose config was deleted succeeds
      (no `RagConfigOutOfProject` / `KnowmapConfigOutOfProject`).
- [ ] AC-8: `pytest -q`, `ruff check .`, `ruff format --check .`, `mypy .`, and
      `alembic upgrade head` (then `downgrade -1`) succeed in `backend/`.

## 11. SRS Delta

None. The fix restores the documented `SET NULL` unbind contract (migrations 0012/0048)
and keeps [R11.14] attachment semantics consistent; no requirement changes.

## 12. Deviation Log

Appended by /build.

## 13. Follow-ups

- FU-1: even after the unbind, the Agent detail form uses a full-form PATCH that resubmits
  every field (`AgentDetailView.vue:418-433,509,551`). A config deleted while the edit form
  is open still resubmits the stale id; because the unbind bumps the Agent `version` (§7B),
  that save now fails as a clean `AgentVersionMismatch` (409) prompting a refetch, rather
  than the old `RagConfigOutOfProject` — so this is a narrow, benign TOCTOU window, not a
  persistent broken state. Optionally harden the form to send `clear_rag_config` /
  `clear_knowmap_config` when the selected id is absent from the active-config options.
- FU-2: `agents.key_group_id` has the same soft-delete-vs-`RESTRICT`-FK mismatch
  (`0011_agents.py:66-67` + soft-delete at `group_service.py:113-132`); the column is
  `NOT NULL` so the consequence differs, but the delete/FK interaction deserves its own
  review.
