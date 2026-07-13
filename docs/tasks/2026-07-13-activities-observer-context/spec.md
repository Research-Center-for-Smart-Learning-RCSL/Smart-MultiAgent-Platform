---
type: feature
status: approved
created: 2026-07-13
requirements: [R30.01, R30.10]
depends_on: [2026-07-13-activities-platform-core]
---

# Activities Observer Context — `ActivityContextProvider`

## 1. Summary

Give an OBSERVER-role agent's turn the room's structured activity events alongside the full
chat history, by adding an `ActivityContextProvider` that mirrors the existing
`_rag_context`/`_knowmap_context`/`_observer_memory_block` providers and calls the
`activities` facade. This is what makes the NSTC "Analytics Agent" able to diagnose from
**deterministic** attempt data (counts, error classes, latencies) rather than inferring them
from free-text chat. The observer still reads the full transcript; activity events are added,
not substituted. The AA's creativity rubric lives entirely in its system prompt + a scoring
tool (project config) — the platform core stays domain-agnostic. Gating is coverage-based:
the block appears only for observers in rooms that have activities, with no new `Agent`
schema field.

## 2. Goals and Non-goals

**Goals**
- An `ActivityContextProvider` (mirror `KnowledgeMapContextProvider`) with a `query(...) ->
  str | None` that returns a formatted `[Recent room activity]` block or `None`.
- Construct it once in `TurnEngine.__init__`; a delegating `_activity_context(...)` method;
  fold it into `system_parts` **only in the observer branch**.
- Gate coverage-based on `is_observer` + `ActivitiesFacade` reporting the room has activities
  (return `None` otherwise) — no `Agent`/`AgentDraft` migration.
- `ActivitiesFacade.list_recent_activity(chatroom_id, limit)` read method (added by the core
  dossier's facade; consumed here).

**Non-goals**
- Any creativity rubric/scoring — the AA's prompt + scoring tool are project config.
- A per-agent activity config field on `Agent` (rejected in favour of coverage-based gating).
- Changing the observer's output path (Observation record, severed from automation,
  `turn_engine.py:1087-1117`) — unchanged.
- Interleaving activity events into the message array — they go into a single system block
  (matches how every other context provider folds in).

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Per-agent config flag vs coverage-based gating | Coverage-based on `is_observer` + room-has-activities | Matches the stated "always-on for observers in rooms with activities"; avoids an `Agent` schema change; mirrors GraphRAG's coverage gating (`turn_engine.py:1700-1704`) |
| Q-2 | Interleave events into history or a system block | Single `[Recent room activity]` system block | Every provider (RAG/graph/knowmap/observer-memory) folds into `system_parts`; history stays a flat message list (`turn_engine.py:979-989`) |
| Q-3 | Where to inject in `_run_locked` | After the knowmap fold (`~:926`), gated on `is_observer` | Lower-risk than editing the `905-911` observer branch; `knowledge_queries` is already computed by `:917` |

## 4. Current State (verified — Agent B trace)

- **Provider pattern**: `_rag_provider`/`_graphrag_provider`/`_knowmap_provider` built once in
  `TurnEngine.__init__` (`turn_engine.py:275-299`); delegating methods `_rag_context`
  (`:1679-1685`), `_knowmap_context` (`:1711-1722`); folded into `system_parts` at `:918-926`;
  `system_text` joined at `:1001`. Provider class shape: constructor `(db, *, router,
  qdrant_url, qdrant_api_key)`, `query(...) -> str | None`, `None` guard when config/queries
  missing, whole body `try/except Exception: return None` (`knowmap_context_provider.py:126-183`;
  `rag_context_provider.py:49-158`). Shared helper `normalise_queries`
  (`knowledge/application/context_provider_text.py:17-24`).
- **Observer branch**: `is_observer` from room role (`turn_engine.py:830-835`); observer
  suppresses the room channel (`:884`); system-parts branch `:905-911` appends
  `_OBSERVER_SYSTEM_NOTE` (`:92-97`) + `_observer_memory_block` (`:1186-1204`,
  `OBSERVER_MEMORY_WINDOW=10` at `:88`) — the exact best-effort, SAVEPOINT-guarded, returns-
  `None`-on-empty pattern to mirror, formatting `"[Your previous observations]\n- (ts) text"`.
- **History**: `_assemble_history` (`:1419-1502`) → `transcript.load_model_history`
  (`transcript.py:153-181`); participant labels `_participant_labels` (`:1335-1357`). Context
  blocks are system-side, not interleaved.
- **Cross-context facade calls already the norm in `agents`**: `KeysFacade`/`KnowledgeFacade`
  as service fields (`agent_service.py:69-70, 212-213`); `ConversationFacade`/`IdentityFacade`/
  `KnowledgeFacade` in `turn_engine.py` (`:281, 1352, 1355, 1698-1702`). So
  `ActivitiesFacade(self._db).list_recent_activity(...)` is the established pattern.
- **Gating precedents**: RAG/knowmap gate via `agent.*_config_id` passed to the provider
  (`models.py:140-141`; provider `None`-guards); GraphRAG gates coverage-based via
  `KnowledgeFacade.resolve_graphrag_layers(...)` returning non-empty (`turn_engine.py:1700-1704`).
- **`activities` context does not exist until the core dossier lands** — this dossier's
  `depends_on` is that core.

## 5. Design

**Provider.** `ActivityContextProvider(db)` in `activities/application/` (co-located with the
context it reads, exposed for the engine via the facade or imported as an application provider
— follow the RAG provider's home in `knowledge/application/`). `query(*, chatroom_id, limit)
-> str | None`:
- Calls `ActivitiesFacade(db).list_recent_activity(chatroom_id, limit)`.
- Returns `None` when the room has no activity events (coverage gate) or on any exception
  (`try/except Exception: return None`) — non-fatal, exactly like the other providers.
- Formats a single block consistent with `_observer_memory_block`:
  `"[Recent room activity]\n- (ISO-ts) subject #attempt <type>: <outcome> [error_class]"`.
  Outcome is `validated ✓ / ✗ / pending / error` from the authoritative record — deterministic
  facts, no LLM inference.

**Engine wiring.**
- `TurnEngine.__init__` (~`:299`): `self._activity_provider = ActivityContextProvider(self._db)`.
- New `_activity_context(self, chatroom_id) -> str | None` (mirror `_knowmap_context`
  `:1711-1722`).
- Fold-in: after the knowmap append (`~:926`), inside a `if is_observer:` guard:
  ```
  activity_block = await self._activity_context(chatroom_id)
  if activity_block:
      system_parts.append(activity_block)
  ```
  (Injecting here, not in the `905-911` block, avoids reordering `knowledge_queries`.)

**Read shape.** `list_recent_activity` returns recent submissions (most-recent-first, bounded
`limit`, default e.g. 30) with `subject`, `attempt_no`, `type key`, `validation_status`,
`is_valid`, `error_class`, `created_at`. The observer receives full chat history (existing) +
this bounded activity window; if the project wants the *full* attempt set, the AA can pull
more via a scoring tool (config) — the always-on block stays bounded for token safety.

## 6. Detailed Changes

- **`activities/application/activity_context_provider.py`** (new) — the provider class.
- **`activities/interfaces/facade.py`** — `list_recent_activity(chatroom_id, limit)` (the core
  dossier already lists this facade read; if not yet present, add it here).
- **`agents` `turn_engine.py`** — construct provider (`__init__`), `_activity_context(...)`,
  observer-gated fold-in after `:926`. No new imports beyond `ActivitiesFacade` /
  `ActivityContextProvider`.
- No migration, no API, no frontend, no deploy change.

## 7. NFR Checklist

- [x] i18n — the block label is model-facing context, not UI; no `$t()` surface.
- [x] Audit — none added (a read for turn assembly; the observation output is audited by
  existing machinery).
- [x] Tenant isolation — the read is by `chatroom_id`; the observer is already bound to the
  room it observes. `ActivitiesFacade` enforces the same room scope.
- [x] Error handling — provider is best-effort `None` on any failure; a broken activities read
  never breaks an observer turn (mirrors `rag_context_provider.py:152-158`).
- [x] Performance — one bounded, indexed read per observer turn; block is token-capped by
  `limit`.

## 8. Security Considerations

- **Read-only, same tenant.** The provider only reads activity events for the room the
  observer is already authorized to observe; no cross-room/cross-tenant read.
- **Deterministic facts, no injection amplification.** Activity outcomes are server-computed
  scalars; rendering them as text does not introduce a new prompt-injection surface beyond the
  existing chat history the observer already ingests. Subject labels reuse the existing
  `_participant_labels` resolution (no raw PII beyond what the transcript already shows).
- **No output-path change.** The observer's result is still an Observation on the creator's
  channel, severed from automation (`turn_engine.py:1087-1117`).

## 9. Quality Notes

- **Patterns to follow:** `_observer_memory_block` (`:1186-1204`) for the best-effort,
  bounded, `None`-on-empty block; `KnowledgeMapContextProvider` for the provider contract;
  GraphRAG coverage gating for the "no config flag, gate on coverage" model.
- **Reuse inventory:** `ActivitiesFacade` (from core); the `system_parts` accumulation;
  `_participant_labels` (`:1335-1357`) if the block wants display names; `normalise_queries` is
  **not** needed (activity context needs no query text).
- **Debt to avoid:** do not add an `Agent.activity_config_id` column — coverage gating is the
  chosen model (Q-1); a column would be dead config for non-observers.

## 10. Risks and Rollback

- **Token budget** — the activity block adds to the observer's system prompt; bounded by
  `limit` and only present when activities exist. Rollback: remove the fold-in call.
- **Ordering assumption** — injecting after `:926` depends on `chatroom_id` being in scope
  there (it is, used throughout `_run_locked`). If the engine is refactored, re-verify.

## 11. Acceptance Criteria

- [ ] AC-1: For an OBSERVER agent in a room **with** activity events, the assembled system text
  contains a `[Recent room activity]` block listing recent submissions with attempt number,
  type, and deterministic outcome.
- [ ] AC-2: For an OBSERVER in a room **without** activities, no block is added (provider
  returns `None`); the turn proceeds unchanged.
- [ ] AC-3: For a **non-observer** agent, no activity block is ever added, regardless of room
  activities.
- [ ] AC-4: An exception inside `list_recent_activity` yields `None` and does not fail or
  abort the observer turn.
- [ ] AC-5: The block reflects only the observer's own room; no other room's activities appear.
- [ ] AC-6: The observer's output path (Observation, creator-channel emit) is unchanged.

## 12. Test Plan

- Unit (`test_activity_context_provider.py`): block formatting; `None` on empty; `None` on
  exception (AC-1,2,4).
- Unit/engine (`test_turn_engine_observer_activity.py`): observer-with-activities appends the
  block; observer-without does not; non-observer never does (AC-1,2,3) — mock `ActivitiesFacade`.
- Integration: observer turn in a seeded room with submissions produces the block; tenant
  isolation (AC-5); output-path unchanged (AC-6).

## 13. SRS Delta

Append to chapter **§30** (after the reactive-rules entries), continuing the numbering:

```
- **[R30.15]** An observer-role agent's turn may include a bounded, read-only context block of the room's recent structured activity events (deterministic outcomes, not inferred), in addition to the full chat history. The block is present only for observers in rooms that have activities; it never appears for non-observer agents.
- **[R30.16]** The activity context is best-effort: a failure to read activity events degrades to no block and never fails the turn. The observer's diagnostic rubric is defined by its system prompt and tools (project config), not the platform.
```

## 14. Open Questions

None blocking.

## 15. Deviation Log

Appended by /build.

## 16. Follow-ups

None.
