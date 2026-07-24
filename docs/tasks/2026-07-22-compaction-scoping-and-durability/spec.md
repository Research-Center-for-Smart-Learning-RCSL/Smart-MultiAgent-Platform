---
type: bugfix
status: implemented
created: 2026-07-22
requirements: [R9.09, R9.10, R9.11, R13.16, R13.24, R13.25, R13.26]
depends_on: []
---

# Compaction folds one agent's history into every agent's, accepts empty summaries, and commits outside its lock

## 1. Summary

Three confirmed defects on the compaction and transcript surface. They are **not one root
cause** — different mechanisms, different layers, different fixes — but they occupy the same
120 lines and the same test seam, so fixing them separately would mean three passes over the
same code and three near-identical harnesses.

- **A (F-5)** — the *authority* to compact is per-agent, the *effect* is room-level, and the
  projection that applies the effect has no reader identity at all. An agent configured
  `context_mode=compact` folds history that every other agent in the room then loses,
  including agents configured `general`, which `[R9.09]` says must receive the entire history.
  Persisted and irreversible in effect.
- **B (F-7)** — a summarisation returning HTTP 200 with empty text is accepted and written as
  a summary, permanently eliding the folded range. The designed failure path (`CompactFailed`
  → keep history → audit, per `[R9.11]`) is well built and simply never fires, because a
  200-with-empty-text is not an exception.
- **C (F-15)** — the compaction lock is released before the summary row commits, so a second
  agent re-reads history in its own session, cannot see the uncommitted row, and folds an
  overlapping range. The FIX-11 comment asserts an invariant the construction does not provide.
- **D (V-1)** — content deleted from the transcript **survives inside the summary**. Deletion
  hard-deletes the message row and never inspects any summary's `compacted_ids`, so a folded
  message's content persists in a row that is rendered to the room, included in exports, and
  injected into every subsequent turn. Retention is defeated structurally: a summary is always
  newer than everything it folds, so a `created_at < horizon` purge removes the originals and
  leaves the copy. Added after this dossier was first drafted, from
  `docs/audits/2026-07-22-conversation-verification-gap/findings.md` V-1 (**plausible** — the
  mechanism is fully traced, but whether a given summary reproduces enough of a given deleted
  message to matter is a property of an LLM's output and is not statically decidable).

  **D is this dossier's own §7 argument read in the other direction.** §7 justifies the repair
  plan by observing that `replace_range_with_summary` only INSERTs and "the originals are
  intact". That same property is exactly why deleting an original does not delete the copy.

**One real coupling, worth exploiting:** if A is fixed by scoping summaries to their producing
agent, two concurrent compactions in one room are necessarily by *different* agents (the turn
lock already excludes same-agent concurrency), and each fold applies only to its producer's
view — so C's harmful half disappears. C must still be fixed, but its residual severity after
A is low. Recommended order: **B → C → A** (B is a few lines and stops the bleeding; C is a
transaction-scope change; A is the design change and the repair).

Source: `docs/audits/2026-07-22-agent-config-runtime/findings.md` F-5, F-7, F-15 (all major,
all confirmed).

## 2. Observed vs Expected

**A.** The decision reads agent-scoped config
(`backend/contexts/agents/application/runtime/turn_engine.py:2514-2521`); the write targets a
room-scoped resource (`:2552`), and the row records no producer —
`backend/contexts/agents/application/runtime/transcript.py:182-191` writes
`sender_type=SYSTEM, sender_id=None` with metadata `{"type", "compacted_ids"}` only; the read
applies it to everyone — `:138-143` (`load_model_history(db, *, chatroom_id, window)`, no agent
parameter), `:150-157` (unions `compacted_ids` across every summary row), `:164`.

The load-bearing missing piece is the **write**, not the read: even if the loader wanted to
scope, the persisted row does not record who produced it.

The codebase has written down "compaction is room-level" twice without noticing it contradicts
a per-agent `context_mode`: `docs/implement/N-conversation-a2a-fixes.md:1017` ("the transcript
is a room-level resource") and `backend/app/workers/tasks/conversation.py:286-290`
("Compaction is room-level … so the first live bound agent's config drives the pass"). Against
that, `REQUIREMENTS.md:416` `[R9.09]` says a `general` agent "sends the entire chat history"
and `:400` lists `context_mode` as an *agent* field, settable per agent via
`backend/app/api/v1/agents.py:82,116`. **This dossier must resolve that contradiction, not
just patch a filter.**

Corroborating that room-level is an oversight rather than a decision: `_request_ceiling`
(`turn_engine.py:206-222`) *does* branch per-mode and documents keeping `general` agents off
the compact cap. The history path has the same need and no such branch.

**B.** `backend/contexts/agents/application/runtime/summariser.py:56-59` raises on
`http_status != 200` and otherwise returns `str(result.body.get("text", ""))`. The pipeline's
failure contract is exception-based and correctly built —
`backend/contexts/agents/application/context.py:257-260` catches into `CompactFailed`, and
`turn_engine.py:2562-2565` audits `agent.compact_failed` and returns un-compacted history per
`[R9.11]` — but a 200-with-empty-text is not an exception. Reachable:
`backend/contexts/keys/infrastructure/adapters/anthropic.py:194,297` build
`"text": "".join(text_parts)`, and `adapters/base.py:48` confirms non-2xx never raises. Nothing
downstream gates it: `context.py:262-265` passes it through, `transcript.py:182-192` persists
verbatim (`messages.content_md` is `nullable=False, server_default ''` at
`backend/contexts/conversation/infrastructure/tables.py:146`, so `""` inserts fine), `:159-166`
elides forever, `turn_engine.py:1883-1887` renders a bare header.

**C.** `distributed_lock(f"compact:lock:{chatroom_id}")` opens at `turn_engine.py:2526` and
closes at `:2569`. `replace_range_with_summary` only stages —
`backend/contexts/conversation/interfaces/facade.py:161-166` states "The caller owns commit".
The first commit is `turn_engine.py:2104`, after the whole knowledge/RAG assembly at
`:1868-2104`. The post-acquire re-check at `:2531-2543` is a read under READ COMMITTED in a
different session, so it cannot see the uncommitted row. The interleave is reachable because
the turn lock is per `(agent, room)` —
`backend/contexts/agents/infrastructure/turn_lock.py:26-27`, applied at `turn_engine.py:590`.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | How should A be fixed: scope summaries per producing agent, make compaction a room-level setting, or apply a summary only to readers who would also have compacted? | **Scope summaries to their producing agent.** | It is the only option satisfying `[R9.09]` without an SRS amendment, needs **no schema migration** (`messages.metadata` is JSONB at `conversation/infrastructure/tables.py:148`), touches no frontend, and collapses most of C's harm. It also makes the pre-existing `MessageLike`/`TranscriptStore` protocol boundary (`context.py:52-83`) honest — `transcript.py:8-14`'s own docstring already claims the model-facing loader is a *projection*; it simply lacked a reader. |
| Q-2 | Why not make `context_mode` a room-level setting instead? | Rejected for this dossier — it is the honest model for a room-level transcript, but it is a spec change with wide blast radius and it removes a capability users have today. | It would touch the `agents` table and enum, the domain model, repositories, the API request/response models, the generated client (`AgentCreateIn.ts`, `AgentPatchIn.ts`, `AgentOut.ts`), the agent form and its Zod schema, both locale files, `docs/UI/06-agents.md:230-233`, and `_request_ceiling`; require a data migration with no correct answer when bound agents disagree; and need an SRS amendment, since `[R9.09]`/`[R9.10]` and `REQUIREMENTS.md:400-401,1105-1106` all place these on the Agent and `[R15.22]` inherits `context_mode` parent→sub-agent, which is meaningless if it is room-level. It would also remove the audit's stated "normal configuration" — a cheap compacting agent beside a large-context analyst. Note Q-1 is **not a dead end**: room-level compaction later becomes this option layered on top, stamping a shared producer sentinel. |
| Q-3 | Why not apply a summary only to readers whose own mode and cap would also have compacted? | Rejected — incoherent. | "Would have compacted" is a function of *this turn's* projected tokens, which change every turn and differ per reader, so a reader's history could re-materialise between turns as its projection dips below its cap — the model would see messages reappear from nowhere. It also still violates `[R9.09]`, since a `general` agent has no cap at all. |
| Q-4 | What counts as an unacceptable summary in B? | `not summary.strip()`. | Exactly the predicate the reply path already uses (`turn_engine.py:2117`, "never persist an empty agent message"), so the codebase gains one rule rather than two. `""`-only is too narrow — a lone whitespace token still destroys the range. A minimum-length floor is rejected as a *rejection* criterion: a genuinely short summary of a short range is legitimate, and a floor invents a threshold with no spec basis. If a signal is wanted there, log it, do not reject. |
| Q-5 | On an unacceptable summary, fold anyway or skip? | **Skip and keep the original history**, raising `CompactFailed`. | `[R9.11]` is explicit: on failure the system keeps the original context and logs to audit. The consequence to state plainly: in compact mode a failed compaction means the turn proceeds over-cap. That is already the existing behaviour for a raising summariser, and it is bounded by the pre-dispatch guard at `turn_engine.py:2006-2036`. |
| Q-6 | For C, commit inside the lock or hold the lock across the whole pre-stream phase? | **Commit inside the lock**, immediately after a successful `run_compact`. | Holding the lock from `:1868` through `:2104` is also correct but serialises every agent's turn in a busy room behind the slowest retrieval — the blast radius is far wider than the bug. The heartbeat (`shared_kernel/realtime/distributed_lock.py:98-107`, `ttl/3`) makes a long hold *safe*, not *free*. |
| Q-7 | **What should happen to summary rows written before the fix, which carry no producer?** | **Decided 2026-07-24: a summary with no `producer_agent_id` belongs to no one** — do not elide its range, do not inject its text. | Treating legacy rows as room-wide silently preserves today's wrong behaviour for every existing room, which is not what anyone wants after paying for this fix. Belonging-to-no-one restores full history to every agent immediately — changing what models see in existing rooms, though never what users see. Compact-mode agents simply re-fold correctly on their next turn; the re-summarisation cost is the accepted price. Fail-open-to-truth, consistent with `[R9.09]`. |
| Q-8 | Should the room-level `/compact` flag keep its current first-agent-wins behaviour under per-agent scoping? | **Decided 2026-07-24: no — a room-level `/compact` folds once for every `context_mode=compact` agent bound to the room**, each producing its own scoped summary. | `compact:pending:{chatroom_id}` is set by a room-level user action (`backend/app/api/v1/chatrooms.py:618`) and consumed by whichever agent turns first (`turn_engine.py:2607-2621`), with `conversation.py:286-290` stating outright that "the first live bound agent's config drives the pass". Under per-agent scoping, first-agent-wins would mean a user pressing a room-level control gets exactly one agent's view compacted and every other agent's untouched — a room-level affordance with an arbitrary per-agent effect. Folding for each compact agent is what the control visibly promises. **Accepted costs, stated plainly:** up to *k* summarisation calls on the user's own key per `/compact` in a *k*-compact-agent room, and a change to the one-shot flag's consumption model (it can no longer be a single room key consumed by the first turner — see §7 Q-8) plus the worker loop's return-on-first-`ok` at `conversation.py:321-324`. `general`-mode agents are unaffected: they have no compact behaviour to invoke. |
| Q-11 | **D (V-1): what policy governs content that survives inside a summary after its source message is deleted?** | **Decided 2026-07-24: D3 — accept and disclose, for user-initiated deletion only.** Deletion does not chase summaries; the limit is stated in the SRS (Q-13) and surfaced at the point of deletion (Q-14). D1 and D2 are recorded as rejected. | D1 was the dossier's recommendation and is rejected on cost: one unrelated deletion discards a summary covering hundreds of messages and forces a re-fold, so a room with routine message hygiene would re-pay for compaction repeatedly. D2 is rejected on both cost and honesty — it spends the user's BYO key on a deletion and still yields an LLM paraphrase of content that included the deleted message. D3 is chosen with the trade-off understood: the copy persists, and the mitigation is that the user is told before they delete rather than after. |
| Q-12 | Does D3 also govern the `[R13.25]` retention purge? | **Decided 2026-07-24: no.** The nightly purge **does** void any summary whose `compacted_ids` reference purged rows, using the D1 mechanism restricted to the purge path. | Manual deletion and retention purge are different promises. Manual deletion is an action a user takes and can be warned about at the moment they take it (Q-14); the 5-year retention window is a platform-level commitment with no user in the loop to disclose to. Without this, a summary — always newer than everything it folds — would carry pre-horizon content indefinitely past the retention boundary, and `[R13.25]` would be defeated by construction rather than by policy. AC-13 therefore stands as written. |
| Q-13 | How is the D3 carve-out recorded in the SRS? | **Decided 2026-07-24: a new `[R13.26]` in a new subsection `13.10 Derived content and deletion`**, leaving `[R13.24]` untouched. | `[R13.24]`'s literal text enumerates the content row, the search index, and edit history — it does not name summaries; §7's earlier claim that it is "explicit that deletion reaches derived copies" was an interpretation, and the whole point of this decision is to stop relying on one. A dedicated requirement states the exemption, its retention-purge exception (Q-12) and the disclosure obligation (Q-14) in one place, where a reader looking for deletion semantics will find it, rather than as a qualifier appended to a requirement that otherwise reads as unconditional. |
| Q-14 | Where does the D3 disclosure land, so that it is verifiable rather than aspirational? | **Decided 2026-07-24: the message-deletion confirmation dialog.** All strings through `$t()`, added to both locale files. | The only surface that reaches the user at the moment the decision is made. A `docs/` note or a badge on the summary bubble informs someone already past the choice; a confirmation dialog informs someone still making it. AC-12 is rewritten against this surface. |
| Q-15 | Is the AC-10 repair command in scope for this task? | **Decided 2026-07-24: yes, in full** — all three repairs of §7 plus D's anti-join detection, dry-run by default. | §7 already argues the empty-summary un-fold is the highest-value repair and should happen regardless of what is decided for A: it restores real conversation content that agents currently cannot see. Shipping the runtime fix without it leaves every existing room carrying empty summaries and cross-agent folds that the new code will never clean up on its own. |
| Q-9 | Should the 500-row history window issue (the a2a audit's FU-2) be folded in? | **No — keep it separate.** | Different root cause (a fixed pagination bound on an unbounded-growth table), different fix (a query-strategy change in `load_model_history`, likely plus an index), different risk profile — a two-query change affects *every* turn in *every* room, including rooms with none of these three defects. Folding it in would triple this dossier's blast radius. But note the interaction: Q-1 makes it worse in degree, since up to *k* summary rows per fold in a *k*-compact-agent room fills the window faster. It should be scheduled soon after. See FU-1. |
| Q-10 | Does this depend on any open dossier, or overlap the a2a orchestration audit? | No hard dependency, but **coordinate on C**. `depends_on: []`. | The a2a audit's `2026-07-22-turn-idempotency-and-locking/` touches turn locking, and `docs/implement/N-conversation-a2a-fixes.md` (FIX-11) is live work on this same block. Not an overlap prerequisite — different lock, different lines — but the C change should be sequenced with awareness of it. |

## 4. Reproduction

**A.** Room R; agent A bound with `context_mode=compact` and a small `context_token_cap` (2000
is permitted — `> 0` is the only lower bound, `backend/app/api/v1/agents.py:83`); agent B bound
with `context_mode=general`; history exceeding A's cap.

1. Trigger a turn for A. `_assemble_history` at `:2514` sees `compact`, crosses the cap, folds
   `m1..mN`, commits.
2. Assert a `compact_summary` row exists with `compacted_ids ⊇ {m1..mN}`.
3. Trigger a turn for B. `load_model_history(db, chatroom_id=R)` omits `m1..mN` and includes
   A's summary. `[R9.09]` violated.

**B.** A compact-mode agent whose next request crosses its cap.

1. Stub the `ProviderRouter` so `call` returns `http_status=200, body={"text": ""}`. Not
   hypothetical — `backend/tests/unit/test_provider_adapters.py:407` already asserts
   `body["text"] == ""` is a real adapter output.
2. Run `_assemble_history`. Observe a `compact_summary` row with `content_md=""`, an
   `agent.compact_run` audited as **success**, and no `agent.compact_failed`.
3. Reload: the folded range is gone and `turn_engine.py:1883-1887` renders
   `"[Earlier conversation summary]\n"` with nothing after it.
4. Field-detectable symptom: the row renders as an empty centred system divider —
   `ChatroomMessageBubble.vue:21-34` renders `sender_type === 'system'` with no content guard.

**C.** Room R, agents A and B both `context_mode=compact`, both over cap, triggered
concurrently. Two DB sessions — this is an integration reproduction, not reproducible in a
single-session unit test.

1. A's turn enters the lock at `:2526`, folds, exits at `:2569`, and blocks in
   `_assemble_agent_knowledge` (`:1922`) — inject latency, or use a room with a real RAG config.
2. B's turn acquires the now-free lock, reloads history at `:2532` in its own session under
   READ COMMITTED, does not see A's uncommitted row, and folds an overlapping range.
3. Both commit. Assert two `compact_summary` rows with intersecting `compacted_ids`, and that
   the next turn's system prompt contains two summaries.

Deterministic variant needing no timing: call `TurnEngine.run_compaction` for A on one session
while a `TurnEngine` on a second session is paused between `:2569` and `:2104`.

## 5. Root Cause Analysis

**A — scope conflation.** Per-agent authority, room-level effect, reader-blind projection. The
earliest correctable link is the **write** (`transcript.py:182-191`), which records no producer;
the reader signature (`:138-143`) is a co-conspirator, not the cause.

**B — a missing postcondition at a context boundary.** The adapter maps only *transport*
failure to the designed failure channel; *semantic* failure has no mapping
(`summariser.py:56-59`).

**C — the mutual-exclusion boundary and the transaction boundary are misaligned.** The lock
guards the summarisation call; the invariant needs the row's visibility
(`turn_engine.py:2526-2569` versus the commit at `:2104`).

**D — deletion has no knowledge of derived copies.**
`backend/contexts/conversation/application/message_service.py:339-383` performs `get`, pulls
attachment paths, `hard_delete`, MinIO removal, audit — and never references `metadata`,
`compact_summary` or `compacted_ids`. A repo-wide search for `compacted_ids` returns eleven code
sites, **none in a deletion path**. Retention is defeated by construction rather than by
oversight: `backend/contexts/conversation/application/retention_service.py:52-93` selects victims
by `created_at < horizon`, and a summary is created at fold time, so it is always newer than
every message it folds.

**Not a shared cause across A, B and C** — recorded explicitly so no one goes looking for the
one-line fix. **D is a fourth, and it is coupled to the others** in one specific way: A's
per-agent scoping changes *which readers see a summary at all*, so any redaction design for D
must be built on top of A's scoping rather than beside it.

## 6. Blast Radius and Sibling Suspects

**Room-level resource written from an agent-scoped decision:**

| Site | Verdict |
|---|---|
| `MessagesTranscriptStore` via `turn_engine.py:2552` | **Confirmed** — defect A |
| `compact:pending:{chatroom_id}` — set by a room-level user action (`chatrooms.py:618`), consumed by whichever agent turns first (`turn_engine.py:2607-2621`) | **Confirmed, same family, in scope.** `conversation.py:286-290` states the first-bound-agent behaviour outright and `run_compaction` returns on the first `ok` (`:321-324`). See Q-8. |
| `MessageService.send_agent` (`:2181`) | **Cleared** — the agent writes its own reply |
| `_dispatch_agent_reply_wakeups` (`:2217`) | **Cleared** — deliberately room-wide per `[R15.01]`/`[R11.02]`, documented at `:2214-2216` |
| `ObservationService.record` (`:2152`) | **Cleared** — observation rows are agent-scoped and `[R28.03]` keeps them out of the room |
| `_persist_artifacts` (`:2193`) | **Cleared** — bound to the agent's own reply message id |

**Provider-response emptiness unchecked:**

| Site | Verdict |
|---|---|
| `summariser.py:59` | **Confirmed** — defect B |
| `turn_engine.py:2683` | **Cleared** — guarded downstream at `:2117`, which skips persistence. Its sibling at `:2758` is F-17, a different confirmed finding owned by another dossier |
| `triple_extractor.py:115`, `knowmap_triple_extractor.py:104` | **Cleared with note** — empty text yields an empty triple list. Same silent-degradation *shape*, without the data-loss consequence: no rows are deleted, the build simply extracts nothing. See FU-2 |
| `app/workers/tasks/prompt_assistant.py:131` | **Cleared** — `isinstance`-guarded, nothing destructive |

**`distributed_lock` uses where the guarded write commits outside the lock.** The grep is
exhaustive — there are exactly **two** call sites in the backend:

| Site | Verdict |
|---|---|
| `turn_engine.py:2526` (`compact:lock:{room}`) | **Confirmed** — defect C |
| `turn_lock.py:45` via `turn_engine.py:590` | **Cleared.** The lock wraps `_run_locked` in full (`:590-616`) and every commit — `:2092`, `:2104`, `:2125`, `:2166`, `:2189`, `:2239` — is inside it. **This is the model to imitate for C.** |

## 7. Fix Design

**B, first and smallest.** In `RouterSummariser.summarise` (`summariser.py:57-59`), raise on
`not text.strip()` immediately after the status check — the module whose docstring (`:5-7`)
already promises "Any failure (exhaustion, non-2xx) propagates". `run_compact` then wraps it
into `CompactFailed` at `context.py:259-260` with zero changes, and `turn_engine.py:2562-2565`
already does the right thing. **The designed failure path becomes reachable rather than being
re-implemented.** Add a belt-and-braces guard in `context.run_compact` before
`store.replace_range_with_summary` (`context.py:262`): `context.py` is pure, and the
`Summariser` Protocol (`:62-64`) is an open extension point a second implementation could
repeat the mistake through.

**C, second.** Insert `await self._db.commit()` inside the `async with` at
`turn_engine.py:2526-2569`, after the `if not did: … return history` at `:2566-2569`. The
reload at `:2571` and the audit at `:2572` stay put.

Three transaction-scope consequences the implementer must get right:

1. Committing here also commits everything staged earlier in the turn — the `agent.turn_started`
   audit (`:1781`), the notification drain (`:1796`), workspace staging (`:1821`), the
   skill-drop report (`:1828`). This moves an existing boundary earlier rather than introducing
   a new class of behaviour; `:2099-2103` explains why the pre-stream commit exists at all, and
   `_requeue_notifications` (`:2243`) is position-independent.
2. **The `/compact` flag restore becomes a bug if not adjusted.** Today a turn that compacts and
   then fails rolls back at `:2222` (the summary vanishes) and `_restore_compact_flag` (`:2245`)
   correctly re-arms the one-shot. With the fold committed, re-arming would force a *second*
   fold of a request already served. Call `self._compact_forced_rooms.discard(chatroom_id)`
   immediately after the new commit — `_restore_compact_flag` already no-ops on a room not in
   that set (`:2626-2627`). Mirror the reasoning at `:2069-2071`. **This must land in the same
   change, not be deferred.**
3. Lock hold time is unchanged in the dominant term — the lock already spans the summarisation
   provider call; a `COMMIT` adds milliseconds.

`run_compaction` (`:2587-2605`) commits at `:2598` after `_assemble_history` returns; a double
commit is a no-op on a clean session, so no change is needed there. After the fix its
post-acquire re-check at `:2531-2543` sees the committed row, closing the residual same-agent
race.

**A, last.**

- *Write side*: `transcript.py:182-191` adds `producer_agent_id` to metadata and
  `MessagesTranscriptStore.__init__` takes `agent_id`; `turn_engine.py:2552` passes `agent.id`.
  **No migration** — `messages.metadata` is JSONB. Consider routing through
  `ConversationFacade.insert_system_message` (`conversation/interfaces/facade.py:175-196`),
  which already service-stamps `metadata["type"]`.
- *Read side*: `load_model_history(db, *, chatroom_id, for_agent_id, window)`. The `compacted`
  union at `:150-157` admits ids only from summaries whose producer matches. **The `summaries`
  list at `:162-163` must be filtered identically** — otherwise agent B reads A's summary text
  *and* the original messages, which is worse than today.
- *SoC*: keep the producer id in `messages.metadata` via the existing facade. Do **not** add a
  column to a conversation-context table from the agents context.
- *Also fixed for free*: `choose_range_to_compact` (`context.py:186-193`) receives per-agent
  filtered history, so agent B no longer skips past A's summary as if it were its own.

**Legacy rows — Q-7, decided.** A summary with no `producer_agent_id` belongs to no one — do not
elide, do not inject its text. Fail-open-to-truth, consistent with `[R9.09]`. The loader must
treat a *missing* key and a *null* value identically; neither is a match for any reader.

**Room-level `/compact` — Q-8, decided: fold once per compact-mode agent.** The one-shot flag can
no longer be a single room key consumed by the first turner, because "consumed" must now happen
*k* times. The flag becomes per-agent at the point it is set: `chatrooms.py:618` resolves the
room's `context_mode=compact` bound agents and arms one entry per agent, and each agent's turn
consumes only its own (`turn_engine.py:2607-2621`). This keeps consumption one-shot per agent —
the property `_restore_compact_flag` and §7 C-2 depend on — while making the room-level action
mean what it appears to mean.

Two consequences the implementer must carry through:

- `run_compaction`'s worker loop returns on the first `ok` (`conversation.py:321-324`) and its
  comment at `:286-290` asserts room-level compaction outright. Both must change: iterate every
  compact-mode bound agent, and correct the comment rather than leaving it contradicting the code.
- A room with no `context_mode=compact` agent arms nothing. `/compact` must then be a visible
  no-op with a reason, not a silent one — it is a user action that did nothing.

**Data repair — and the good news is that nothing was destroyed.** `replace_range_with_summary`
(`transcript.py:182-192`) performs a single INSERT. It does not UPDATE, does not set
`deleted_at`, does not touch the folded rows. The "replacement" is entirely a read-time
projection (`:159-166`). The originals are intact, still visible in the UI, still exported
(`message_repo.all_for_chatroom:272-301`), still searchable. **Repair therefore reduces to
editing summary-row metadata** — a pure JSONB update with no risk to conversation content.

Detection (no GIN index on `metadata` — `0017_messages.py:74-79` and `0034` index only
`(chatroom_id, created_at)` and `(chatroom_id, sender_id)` — so scope every query by
`chatroom_id` or accept a one-off seq scan in a maintenance window):

- **Empty summaries (B)** — exactly detectable:
  `metadata->>'type' = 'compact_summary' AND btrim(content_md) = '' AND deleted_at IS NULL`.
- **Overlapping folds (C)** — detectable: two rows in one room whose `compacted_ids` intersect.
  Legitimate compaction never overlaps, since `choose_range_to_compact` starts at the first
  un-compacted message and stops at any prior summary — so any intersection is evidence of the
  race.
- **Cross-agent folds (A)** — **not detectable from the message rows alone**; `sender_id` is
  NULL on every summary and no producer is recorded. Partial attribution is possible by
  correlating `messages.created_at` with the nearest preceding `agent.compact_run` audit for
  that room (`turn_engine.py:2572-2584`), but the audit records no summary message id, the emit
  is best-effort within the turn's transaction, and it is subject to retention. **Advisory,
  not authoritative.** Practically: you can usually tell *whether* a room has the problem (does
  it have both a `compact` and a `general` bound agent, and any summary rows?) but not
  reliably *which* agent produced which row.

Repair, as a dry-run-by-default maintenance command under `backend/smap/maintenance/` (the
package exists and is the established home):

1. **Empty summaries — un-fold.** Rename the metadata `type` (retaining
   `original_compacted_ids`) so `_is_summary` (`transcript.py:101-102`) no longer matches. One
   edit both restores the folded range to every reader and removes the empty header from the
   prompt. Consider also soft-deleting the row so the empty divider stops rendering.
   **This restores real conversation content that agents currently cannot see — highest-value
   repair; do it regardless of what is decided for A.**
2. **Overlapping folds — deduplicate**, keeping the earlier summary and voiding the later.
3. **Cross-agent folds — user's choice**: backfill `producer_agent_id` from the audit
   correlation where confident, or (recommended) void every pre-fix summary in rooms with
   heterogeneous `context_mode`, so every agent regains full history and compact-mode agents
   simply re-compact correctly on their next turn.

Retain `original_compacted_ids` in every void — it is what makes the repair itself
rollback-safe.

**D, last. Decided 2026-07-24 (Q-11, Q-12): D3 for user-initiated deletion, D1's mechanism for
the retention purge.** The two paths get different answers because they make different promises —
see Q-12. Rejected alternatives, recorded so the reasoning survives: **D1 everywhere** (void on
every deletion) fails on cost — one unrelated deletion discards a summary covering hundreds of
messages and forces a re-fold; **D2** (re-summarise without the deleted message) spends the user's
BYO key on a deletion and still yields a paraphrase of content that included the message moments
earlier, so it does not actually guarantee removal.

Three pieces, none of them large:

1. **User deletion — disclose, do not chase.** `message_service.py:339-383` is unchanged. The
   deletion confirmation dialog gains a line stating that content already folded into a
   compaction summary may persist there; strings via `$t()`, added to **both** locale files.
   This is the whole of AC-12.
2. **Retention purge — delete.** `retention_service.py:52-93` selects victims by
   `created_at < horizon`; after hard-deleting them, hard-delete every summary in the affected
   rooms whose `compacted_ids` intersects the purged ids. A summary is always newer than
   everything it folds, so it is never a victim itself — without this step `[R13.25]` is defeated
   by construction. This is AC-13.

   **Corrected during implementation (D-1).** This step originally specified the metadata-rename
   void used by the repair plan. That mechanism cannot satisfy Q-12: it stops the summary being
   applied to any model-facing view, but the summary row is itself user-visible —
   `MessageRepository.list` serves it and `ChatroomMessageBubble.vue` renders it, and
   `all_for_chatroom` exports it (see the §9 correction) — so the derived copy of the purged
   content would remain readable. Removal is the only mechanism that achieves what Q-12 decided.
   Retaining `original_compacted_ids` is not applicable to a deleted row, and rollback-safety is
   not a property the purge offers for anything else it deletes either.
3. **SRS — state the exemption.** New `[R13.26]`, §11. Without it the code relies on an unwritten
   reading of `[R13.24]`, which is the ambiguity that produced this decision in the first place.

Detection for the existing population: summaries whose `compacted_ids` reference message ids that
no longer exist. That is a straightforward anti-join and it is exact — unlike A's attribution
problem, no audit correlation is needed. Fold it into the same maintenance command; under D3 it
reports rather than repairs for user-deleted ids, but the purge-orphaned rows it finds are
genuine pre-fix breakage of piece 2 and are voided.

## 8. Regression Test Plan

Existing coverage and why it misses all three: `backend/tests/unit/test_context_compaction.py:97-147`
uses a `_FakeSummariser` that returns text or raises — never `""`, so **B is invisible to it**.
`backend/tests/unit/test_agent_runtime_transcript.py:72-96` pins elision with a single anonymous
reader, so it asserts the *current* room-wide behaviour and **will need updating** — that update
is the clearest statement of the behaviour change. `backend/tests/unit/test_turn_context_budget.py:152-184`
short-circuits `should_compact` before the lock (`:170`), so **no test ever enters the
`distributed_lock` block**. And no test anywhere constructs two agents with different
`context_mode` against one room.

**The failing test comes first** — `test_run_compact_raises_compact_failed_on_empty_summary` in
`test_context_compaction.py`: `_FakeSummariser` returning `""`; assert
`pytest.raises(CompactFailed)` and that the store was never called. **Fails today**:
`context.py:262` writes the row and returns `True`. Parametrise over `("", "   ", "\n\t")` to
pin `.strip()` semantics.

Then:

- **New `backend/tests/unit/test_runtime_summariser.py`** — no test file for `summariser.py`
  exists. `test_summarise_raises_on_empty_text_at_status_200`;
  `test_summarise_raises_on_missing_text_key` (`body={}`, guarding the `.get` default);
  `test_summarise_passes_through_valid_text` as a regression guard against over-rejecting.
- `test_turn_context_budget.py::test_empty_summary_audits_compact_failed_and_keeps_history` —
  assert the returned history equals the input and that `agent.compact_failed`, not
  `agent.compact_run`, was audited.
- `test_agent_runtime_transcript.py::test_transcript_store_records_producer_agent_id` — **fails
  today**: `transcript.py:187-190` writes only `type` and `compacted_ids`.
- `test_load_model_history_elides_only_own_agents_summary` — two agent ids, one summary
  produced by A; assert A's view elides the range and includes the summary, and B's view
  returns the full originals and **does not** include A's summary. **Fails today**: the
  parameter does not exist.
- `test_load_model_history_legacy_summary_without_producer` — pins Q-7 either way. This is the
  migration contract and must exist regardless of which policy is chosen.
- Update `test_load_model_history_elides_compacted_and_orders:72-96` to pass `for_agent_id`
  matching the producer — the minimal edit keeping the ordering assertion intact.
- **The `[R9.09]` acceptance test** —
  `test_turn_context_budget.py::test_general_mode_agent_never_sees_a_folded_range`: room history
  plus one compact-produced summary; run `_assemble_history` for a `general` agent; assert every
  original message id is present. **Fails today**: `:2506` passes no agent identity to the
  loader.
- `test_compaction_commits_before_releasing_the_room_lock` — a fake session recording an ordered
  event log plus a `distributed_lock` stub recording enter/exit; assert
  `enter → create_message → commit → exit`. **Fails today**: no commit appears between `:2552`
  and `:2569`.
- `test_forced_compact_flag_is_not_restored_after_a_committed_fold` — write it *with* the C fix;
  it pins the hazard the fix itself introduces (§7 C-2).
- **Integration** (`-m integration`) — the unit test above pins call ordering, not visibility.
  Two sessions: session 1 folds and holds before commit; session 2 attempts the lock; assert it
  blocks or skips and that exactly one summary row exists afterwards. Requires Postgres +
  Redis.

## 9. Risks and Rollback

**Does the fix change the *folded original messages'* visibility? No — not once, in any of the
three.** The user-visible transcript is served by `ConversationFacade.list_messages` →
`MessageRepository.list` (`message_repo.py:79-149`), which filters only on `chatroom_id` and
`deleted_at` and has never consulted `compacted_ids`. The elision is a model-facing projection
(`transcript.py:8-14`), so no original message appears or disappears from the feed as a result of
this work.

**Correction, and it matters for D below.** An earlier draft of this dossier stated that the
transcript change is "never what users see" without qualification. That is **wrong for the
summary row itself**: `MessageRepository.list` serves it like any other row and
`ChatroomMessageBubble.vue:22-34` renders `sender_type === 'system'` into the feed, and
`all_for_chatroom` (`message_repo.py:289-301`) applies the same filters, so the summary is
exported too. The claim is true of the *originals* and false of the *summary*. It was corrected
after `docs/audits/2026-07-22-conversation-verification-gap/findings.md` V-1 turned on exactly
that distinction. Soft-deleting an empty summary row during repair is therefore a genuinely
user-visible change — a beneficial one, since it removes an empty divider users can already see
and cannot read.

| Risk | Impact | Mitigation |
|---|---|---|
| **Model-facing history changes for existing rooms (A + repair)** — `general` agents in mixed rooms regain history they have not been seeing, so their next context jumps in size and can hit the provider's hard limit | visible behaviour change in a working room | `[R9.09]` says exactly that is the intended behaviour for `general` ("the provider will error; this is surfaced to the UI"), so it is spec-compliant — but it belongs in release notes |
| **Provider spend rises under per-agent scoping** — up to one summarisation per compact-mode agent per fold instead of one per room | real money on the user's own keys | Accepted consequence of honouring per-agent config; surface the producer in `agent.compact_run` metadata so it is attributable |
| **B makes previously-"successful" compactions fail loudly** — a provider frequently returning empty summaries now produces recurring `agent.compact_failed` audits and un-compacted turns | correct per `[R9.11]`, but converts a silent bug into a visible error | Set expectations; strictly better than silent deletion |
| **C's early commit makes the turn-started audit and notification drain durable earlier** | compensations already exist (`:2243`) | The genuine new hazard is the `/compact` double-fold — must be handled in the same change (§7 C-2) |
| **Repair scans `messages` without a metadata index** | seq scan | Scope by `chatroom_id` (served by `ix_messages_chatroom_created`) and run per-room |
| **Concurrent work on the same block** | conflicting diffs | `docs/implement/N-conversation-a2a-fixes.md` (FIX-11) and the a2a audit's turn-locking dossier are live here. Coordinate on C (Q-10) |

**Rollback.** B is two small edits with no persisted state depending on them — trivially
reversible. C is one `commit()` and one `discard()`; reverting re-opens the race but corrupts
nothing. A's code is revertible, but **rows written after the fix carry `producer_agent_id`**;
on rollback the loader ignores the extra key and reverts to room-wide elision, so post-fix
summaries would start applying to everyone. Rollback therefore reintroduces the bug including
for new data, but **destroys nothing and loses no history** — data-forward-compatible, no
down-migration needed since there is no schema change. The repair's void step is reversible
provided `original_compacted_ids` is preserved; insist on that field.

## 10. Acceptance Criteria

- [x] AC-1: `test_run_compact_raises_compact_failed_on_empty_summary` (§8) fails against
      current code and passes after the fix.
- [x] AC-2: a summarisation returning 200 with empty or whitespace-only text raises
      `CompactFailed`, no summary row is written, the original history is returned, and
      `agent.compact_failed` is audited per `[R9.11]`.
- [x] AC-3: the summary row is committed before the compaction lock is released, pinned by an
      ordering test asserting the trace `lock_enter → create_message → commit → lock_exit`.
      (Reworded per **D-1**'s sibling **D-2**: the original "exactly one summary row after a
      contended fold" ceased to be the correct invariant once A scoped summaries per producer.)
- [x] AC-4: a committed fold does not re-arm the one-shot `/compact` flag.
- [x] AC-5: a summary row records its producing agent.
- [x] AC-6: **`[R9.09]`** — an agent configured `context_mode=general` receives every original
      message in the room, regardless of what any other agent compacted.
- [x] AC-7: an agent's own summary is applied to its own view, both eliding its range and
      injecting its text; another agent's summary is applied to neither.
- [x] AC-8: legacy summaries with no producer behave per the Q-7 decision, pinned by a test.
- [x] AC-9: no **originally-posted** message changes visibility in the feed as a result of A, B
      or C — `MessageRepository.list` returns the same non-summary rows before and after, for
      every room. (Summary rows themselves are user-visible and D may deliberately change them;
      see the correction in §9.) **Verified by inspection, not by a new test**: the feed path is
      `ConversationFacade.list_messages` → `MessageRepository.list`, and the task diff touches
      neither, nor anything they call. The elision lives entirely in the model-facing loader.
- [x] AC-12: **D3 (Q-11, Q-14)** — user-initiated deletion does not alter any summary, and the
      deletion confirmation dialog discloses that content already folded into a compaction
      summary may persist there. Pinned by a frontend test asserting the dialog renders the
      disclosure, by both locale files carrying the key, and by a backend test asserting that
      deleting a folded message leaves its covering summary untouched (the *deliberate*
      behaviour under D3, so that a future change to it is a visible test change).
- [x] AC-13: **D (Q-12, amended by D-1)** — the retention purge cannot leave a summary covering
      messages it has just deleted; a room purged past its horizon retains no summary whose
      `compacted_ids` reference purged rows, the removal is a hard delete (not a metadata edit,
      which would leave the derived text readable in the feed and in exports), and the purge
      audit records how many summaries it removed.
- [x] AC-14: **Q-8** — a room-level `/compact` folds once for every `context_mode=compact` agent
      bound to the room, each producing its own scoped summary; each agent consumes its own
      one-shot arming exactly once; a room with no compact-mode agent reports the no-op rather
      than silently succeeding.
- [x] AC-10: the repair command is dry-run by default, preserves `original_compacted_ids` on
      every void, and emits an audit row per mutated row.
- [x] AC-11: `pytest -q`, `ruff check .`, `ruff format --check .` and `mypy .` pass in
      `backend/`; `pnpm test`, `pnpm lint`, `pnpm typecheck` and `pnpm build` pass in
      `frontend/` (the AC-12 disclosure is the only frontend change).

## 11. SRS Delta

**Non-empty.** A, B and C require nothing — Q-1 makes the code match `[R9.09]`/`[R9.10]` as
written, and `[R9.10]` already frames compaction as an agent action ("Use the same Agent's Key
Group"). The delta comes entirely from the Q-11 D3 decision: without it the implementation would
rest on an unwritten reading of `[R13.24]`.

Added verbatim at approval, as a new subsection after `13.9 Retention purge`:

> ### 13.10 Derived content and deletion
>
> - **[R13.26]** **Compaction summaries** (R9.10) are **derived content**: a summary's text is
>   generated from the messages it folds and may reproduce parts of them. Deletion (R13.16,
>   R13.24) removes the message row, its search index entry and its edit history, but does **not**
>   rewrite or remove any summary that folded it — the folded content may persist inside that
>   summary. The UI must disclose this at the point of deletion. **Exception:** the retention
>   purge (R13.25) *does* void every summary whose folded set intersects the purged messages, so
>   that no content survives its retention horizon in derived form; a voided summary stops being
>   applied to any model-facing view and the messages it folded become visible again to agents
>   that can still see them.

Rejected alternatives are recorded in Q-13 (amending `[R13.24]` in place; no SRS change at all)
and in Q-11 (D1 and D2, which would have required no delta but were rejected on cost and
honesty respectively).

Note for the record: Q-2's rejected option **would** have required a far wider amendment, and
Q-3's would have required describing a behaviour that is not describable.

## 12. Deviation Log

- **D-1 — the retention purge deletes the summary row instead of voiding its metadata.**
  §7's D piece 2 originally reused the repair plan's metadata rename. That cannot satisfy Q-12:
  a summary row is itself user-visible (`MessageRepository.list` serves it,
  `ChatroomMessageBubble.vue` renders it, `all_for_chatroom` exports it — the §9 correction), so
  renaming its `type` hides it from the model and from nobody else, leaving the purged content
  readable. Raised with the user before implementing; hard delete chosen. `[R13.26]` and AC-13
  were amended in the same step. `original_compacted_ids` is not retained on this path — it
  cannot be, and the purge offers no rollback for anything else it deletes either.

- **D-2 — the §8 integration test was not written.** Two reasons, the first decisive. (a) The
  invariant it was specified to assert — "exactly one summary row after a contended fold" — is
  **no longer correct after A**: two agents concurrently compacting the same room now
  legitimately produce two summaries, one scoped to each. The remaining same-agent case is
  already excluded by the per-`(agent, room)` turn lock, so it is unreachable through the turn
  path. What is left of C is precisely "the row is committed before the lock is released", and
  `test_compaction_commits_before_releasing_the_room_lock` pins that exactly, by asserting the
  ordered trace `lock_enter → create_message → commit → lock_exit`. (b) Secondary: this project's
  compose does not publish Postgres or Redis to the host, so an integration test could not have
  been executed here — writing one that has never run would have been worse than not writing it.
  AC-3 was reworded to the invariant that survives.

- **D-3 — the room-level `/compact` arming is an epoch token claimed by readers, not a set of
  per-agent keys written by the endpoint.** §7's Q-8 note proposed resolving the room's
  compact-mode agents at the point the flag is set. That is `chatroom_service.request_compaction`
  (`chatroom_service.py:273-283`) — the conversation context — and making it read agent
  `context_mode` would give the conversation context a dependency on agent configuration for the
  first time. Instead the writer stores an opaque epoch and each agent claims it once via
  `SET NX compact:consumed:{room}:{epoch}:{agent}`. Same observable behaviour, no new
  cross-context dependency, and the one-shot-per-agent property `_restore_compact_flag` needs is
  preserved. `_compact_forced_rooms` became `dict[room, marker_key]` for the same reason.

- **D-4 — `_consume_compact_flag` takes the agent and refuses for `context_mode=general`.** Not
  in the spec, but required by Q-8: the forced path bypasses the mode/cap check entirely, so a
  `general` agent that turned first would have been made to fold its own history — the exact
  violation of `[R9.09]` this dossier exists to fix.

- **D-5 — `MessagesTranscriptStore` routes through `ConversationFacade.insert_system_message`
  rather than `create_message`.** §7 listed this as "consider"; taken, so `metadata["type"]` is
  service-stamped in one place rather than in each caller.

- **D-7 — the `CompactFailed` branch now releases the forced-`/compact` claim.** Found by the
  quality gate, not by the spec. `turn_engine.py:2601` audited the failure and returned without
  releasing the claim, so a user's explicit `/compact` was consumed and never served. The shape
  is pre-existing, but B converts this branch from effectively unreachable into the *normal*
  outcome for a provider returning blank text, which makes the worsening this change's to own.
  Fixed with `test_a_failed_compaction_releases_the_forced_compact_claim`, matching what the
  adjacent `if not did` branch already did.

- **D-6 — the repair command's third repair (cross-agent folds) was dropped, and its first
  changed meaning.** Both follow from Q-7 rather than from any new discovery: a summary with no
  producer is already applied to no reader, so every pre-fix cross-agent fold is *already*
  neutralised at read time and there is nothing left to repair — those rows are counted and left
  alone, because their text is still readable by users in the feed and deleting it would destroy
  content. For the same reason the empty-summary repair no longer "restores history to every
  reader" (Q-7 did that); what it still does, and why it is kept, is remove a blank system
  divider that users can see and cannot read. The overlap repair is scoped to one producer,
  since two producers folding the same range is now normal rather than evidence of the race.

## 13. Follow-ups

- **FU-1** — Compaction summaries silently fall out of the newest-500-row history window
  (`transcript.py:43,142,146`), so a long-lived room progressively loses its oldest compacted
  context with no audit, log or user signal. Independently confirmed; also recorded as FU-2 of
  the same-day agent-to-agent orchestration audit. Q-1 makes it worse in degree, which argues
  for scheduling it soon after this dossier rather than merging it in.
- **FU-2** — `triple_extractor.py:115` and `knowmap_triple_extractor.py:104` accept empty
  provider text and extract nothing. Same silent-degradation shape as B without the data-loss
  consequence; worth a guard for symmetry.
- **FU-3** — The `TranscriptStore` protocol docstring (`context.py:76-82`) says the operation is
  "Atomic: delete the range from the model-facing view, insert a system message". It deletes
  nothing (which is why repair is possible) and is not atomic (which is defect C). A protocol
  that misdescribes its own guarantee is how C got written; correct it with the C fix.
- **FU-4** — `transcript.py:8-14` claims "every summary represents strictly older content than
  any surviving message". That holds per-producer but is what C violates across producers and
  what A quietly breaks for a `general` reader. It is the best statement of intent in the file
  and should be made true.
- **FU-6** — A room-level `/compact` in a room with no `context_mode=compact` agent now reports
  `skipped:no_compact_agents` in the worker log, but the endpoint already returned 202 and the
  user sees nothing. A user-visible signal needs a notification or a status endpoint; out of
  scope here, but it is the honest completion of AC-14's "reports the no-op".
- **FU-7** — `repair_compaction_summaries._load_summaries` pages with `LIMIT`/`OFFSET` over an
  unindexed JSONB predicate, so it re-scans per page. Acceptable for a one-off maintenance run
  against the expected population; if this ever becomes routine it wants keyset pagination or a
  partial index on `(metadata->>'type')`.
- **FU-8** — The repair command reports summaries covering deleted messages and deliberately does
  not repair them (see its module docstring). Pre-fix retention purges may have left some that
  `[R13.26]` now says should not exist, but after the fact they are indistinguishable from the
  traces a user deletion legitimately leaves. Cleaning those up needs its own decision and its
  own evidence.
- **FU-9** — *(quality gate, Introduced-Warning, deferred with the user's knowledge)* The
  compact-summary metadata contract — the `"compact_summary"` type value and the
  `compacted_ids` / `producer_agent_id` keys — is now read in four modules across two bounded
  contexts (`transcript.py:63`, `context.py:197`, `retention_service.py:30`,
  `repair_compaction_summaries.py:73`) with no owner. This work added two of the four. Deferred
  rather than fixed because the string is a *persisted* value: it exists in `messages.metadata`
  rows, so it cannot be renamed freely whether or not it is centralised, which removes the usual
  hazard of a duplicated constant. The right home is the conversation context, which owns the
  message row; the agents context would import it, matching the existing import direction.
- **FU-10** — *(security gate, MEDIUM)* `retention_service._delete_summaries_covering` issues an
  unbounded `SELECT` over the affected rooms' summaries. Bounded in practice by the 500-message
  purge chunk, and it is a background job rather than a request path, but it should chunk its
  scan the way `_live_message_ids` chunks its `IN` list.
- **FU-5** — `_assemble_history` (`turn_engine.py:2483-2585`) loads history, consumes a Redis
  flag, decides two compaction policies, takes a distributed lock, re-checks staleness,
  constructs a summariser and store, runs compaction, handles failure, audits and reloads.
  Three of this dossier's changes land inside it. The *decision* logic belongs in `context.py`,
  which is pure and already owns `should_compact`/`choose_range_to_compact`.
</content>
