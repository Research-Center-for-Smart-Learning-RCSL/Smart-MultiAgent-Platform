---
type: bugfix
status: implemented
created: 2026-07-22
requirements: [R9.16, R15.10, R15.13, R28.07]
depends_on: [2026-07-22-a2a-delivery-idempotency]
---

# A pending approval note is rendered into whatever room the approver's next turn runs in, and is destroyed whether or not that turn votes

Sources: `docs/audits/2026-07-22-agent-to-agent-orchestration/findings.md:280-309` (F-8, confirmed,
major) and `docs/audits/2026-07-22-agent-config-runtime/findings.md:1065-1099` (F-29, plausible,
minor). The config-runtime audit routed F-29 here explicitly rather than spawning its own dossier
(`findings.md:1349`); the a2a audit assigned F-8 to this slug (`findings.md:1181`).

## 1. Summary

Two defects in one function, `TurnEngine._pending_context_and_tools`
(`backend/contexts/agents/application/runtime/turn_engine.py:1563-1639`), which drains the per-agent
`pending_notify` queue at turn start and partitions what it found into "put back" and "render".

**They do not share a root cause. They are grouped by change surface**, and this dossier says so
rather than stretching one framing over two defects:

- **F-8 — the partition is kind-specific where the property it guards is kind-independent.** The
  misroute filter at `:1597-1603` tests `n.get("kind") == "released_observation"` before comparing
  the note's room to the turn's room. `approval_request` notes carry a room too
  (`backend/contexts/orchestration/application/approval_service.py:151`) and are never compared: they
  fall through to `usable` at `:1602-1603` and are rendered into the prompt at `:1622-1627`,
  including the interpolated `question` at `:1627`. An approver bound to rooms X and Y therefore
  reads room X's gate question inside a room-Y turn.
- **F-29 — the drain is unconditionally destructive with respect to whether the turn acted.**
  `pending_notify.drain` (`backend/contexts/orchestration/infrastructure/pending_notify.py:43-64`)
  is LRANGE + DELETE. Every one of the seven `_requeue_notifications` call sites restores notes only
  when the turn *failed or skipped* (`turn_engine.py:897`, `:907`, `:1605`, `:1687`, `:2079`,
  `:2096`, `:2243`). No site restores a note that a turn drained, rendered, and then simply did not
  act on. For an `approval_request` — the one note kind that carries an obligation rather than
  information — a turn that completes without calling `cast_approval_vote` consumes the ballot for
  good.

What makes them one dossier is that both are decided in the same fifteen lines, both are about the
same predicate ("may this turn consume this note?"), and a fix for either that ignores the other
produces a worse system: fixing F-8 alone routes more approval notes back onto a queue whose
consumption semantics are still lossy; fixing F-29 alone re-arms a ballot that F-8 will leak again on
the next mismatched turn. **They are complementary halves of one predicate, not one bug.**

**F-8 does not fix F-29.** After the F-8 fix, the F-29 race still fires in the two configurations
where the rooms do not disagree: a gate targeting room X whose approver takes a room-X turn inside
the dispatch window, and a headless gate (`chatroom_id: None`, `approval_service.py:151`) whose note
is room-agnostic and consumable by any turn. Stated plainly because the tempting conclusion is that
one filter change closes both.

## 2. Observed vs Expected

### F-8

**Observed.** `_pending_context_and_tools` receives the turn's room as `chatroom_id`
(`turn_engine.py:1564`) and drains by agent id only — `pending_notify.drain(agent.id)` at `:1585`,
against a key built from the agent alone (`pending_notify.py:28-29`). The partition loop at
`:1597-1603` requeues a note only when **both** conditions hold: its `kind` is
`released_observation`, **and** the room mismatches. Every other kind takes the `else` at `:1602-1603`
and lands in `usable`. The `approval_request` branch at `:1612-1627` then reads
`n.get("chatroom_id")` at `:1617` — but only to key `allowed_approvals` at `:1619`, which
`build_cast_approval_vote_tool` uses to route the eventual `approval.resolved` publish
(`backend/contexts/agents/application/runtime/tool_registry.py:265-267,287`). The room is never used
as a gate. Lines `:1622-1625` render the ballot and `:1626-1627` render
`f"  Question: {n['question']}"` unconditionally.

The question is real, author-written text interpolated with run variables:
`backend/contexts/workflow/application/executors/approval_gate.py:32` computes
`interpolate(config.get("question_template", ""), variables)` where `variables` includes
`__trigger__` = the caller's trigger payload (`:27-31`); `approval_service.py:154` copies it into the
note.

Room X ≠ room Y is a supported configuration, not a corner case: `turn_engine.py:742-749` states in
as many words that a gate's `chatroom_id` "can be an arbitrary in-project room set by the workflow
author", and nulls `knowledge_chatroom_id` at `:750-754` precisely because the approver may not be a
member of it.

**Expected.** A note addressed to a room is folded into a turn running in that room, or into nothing.
This is not a new rule — it is the rule the function's own docstring already states at `:1571-1577`:
"`pending_notify` is keyed only by agent id, not by room, so a `released_observation` note … addressed
to a *different* room than this turn … is put back immediately rather than rendered: it must never
leak that room's private content into another room's context." The docstring names one kind because
the code implements one kind. The reasoning it gives — the queue is agent-keyed, so the turn must do
the room check the queue cannot — is kind-independent on its face. [R9.16] (`REQUIREMENTS.md:449`)
folds a notify into the agent's next turn; [R28.07] (`:2066`) scopes a private release to agents "of
the same room". Nothing anywhere licenses rendering a room-bearing payload into a different room.

### F-29

**Observed.** `drain` (`pending_notify.py:43-64`) pipelines LRANGE + DELETE; its docstring at `:44-48`
describes the read-then-delete window as "best-effort context injection", which is the correct frame
for a `notify` and the wrong frame for a ballot. `run_input_turn` takes no turn lock — only `run_turn`
does (`turn_engine.py:590`, keyed `(agent, room)`) — so the driven approver turn
(`backend/app/workers/tasks/approvals.py:90-94`) and a concurrent room turn are not serialised. And
`_APPROVER_TURN_DISPATCH_DELAY_S = 2` (`approval_service.py:49`, applied at `:184`) opens a window
between the push at `:170` and the driven turn.

Whichever turn starts first takes the note. If it is a room turn that renders the ballot and declines
to vote, the note is gone: `_requeue_notifications` (`turn_engine.py:1641-1656`) is reached from no
completed path. The three completed returns — `:883` (headless), `:2179` (observer), `:2218` (room) —
requeue nothing.

The driven turn two seconds later drains an empty queue, gets `usable == []`, returns at `:1592-1593`
with no `cast_approval_vote` tool, completes, and `approvals.py:112-113` logs
`"approver turn driven"` at **info** — the same line a turn that voted produces. The `else` at `:104`
warns only on non-`completed`. The gate then waits for `approval_timeout`
(`backend/app/workers/tasks/orchestration.py:184`) and resolves `TIMEOUT_LEADER`
(`approval_service.py:264`), minutes or hours later, with nothing connecting the two.

**Expected.** A ballot survives until it is cast or its gate is settled. [R15.10]
(`REQUIREMENTS.md:767-771`) declares an approval gate's approvers; [R15.13] (`:774`) makes the
timeout the fallback for non-convergence, not the routine outcome of a lost note. `approvals.py:3-9`
states the task's whole purpose: "a pending-notify is only drained at the approver's *next* turn, and
nothing else causes one for a headless approver — so every workflow approval gate used to fall to the
timeout port." A race that silently restores the exact condition the task was written to remove
defeats it. And `approvals.py:104-111` already establishes the intended reporting standard — "a turn
that never reached the provider casts no vote … make the cause findable at the moment it happens
rather than at the timeout." A voteless `completed` is that same case and is not covered.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Do F-8 and F-29 share a root cause? | **No — grouped by change surface.** | Two independent single-line causes in one function (§5). F-8 is a predicate that tests the wrong thing; F-29 is a lifecycle that ends at the wrong moment. Recorded so no reviewer hunts for a unifying patch that does not exist, and so a partial revert is legible. |
| Q-2 | Should the room check move into `pending_notify.drain` (a room-aware drain) instead of staying in the turn engine? | **No — it stays in the turn engine.** | `pending_notify.py:11` states its own contract: "pure Redis state; no domain logic, no DB access." Which room a turn is entitled to is domain logic. A room-aware drain also cannot work: the queue is one Redis list per agent (`:28-29`), so a room filter would have to LRANGE, parse JSON, and LREM selectively — turning a two-command pipeline into a read-modify-write with no atomicity, for a check the caller already has the inputs for. It would also force this dossier to edit `pending_notify.py:43-64`, colliding head-on with the F-19 fix (§6 coordination). |
| Q-3 | Why `depends_on: [2026-07-22-a2a-delivery-idempotency]` rather than `[]`? | **Amplification prerequisite.** | The F-29 fix makes `requeue` a *normal-path* operation for the first time — today all seven call sites are failure/skip paths (§1). F-19 (`pending_notify.py:84`, `ltrim(key, 0, _MAX_PENDING - 1)` on a head-is-oldest list) discards the **newest** entries once restored-plus-concurrent exceeds `_MAX_PENDING = 50` (`:24`). Promoting requeue to the happy path raises that defect's exposure from "rare failure over cap" to "every turn while a gate is pending, over cap". The a2a-delivery dossier made the identical argument in the other direction about the turn-idempotency dossier (`docs/tasks/2026-07-22-a2a-delivery-idempotency/spec.md`: "F-19 should land before or with that dossier's C1, not after"). Its fix is one line. **Unblock clause:** if that dossier stalls, /build may cherry-pick its C1 alone into this change and record it as a D-n deviation — the dependency is on one line, not on the a2a-consumer work that dossier also carries. |
| Q-4 | Should an `approval_request` note whose room mismatches be **dropped** instead of requeued, on the grounds that the approver will be driven separately? | **No — requeue.** | The driven turn is not guaranteed: `drive_approver_turn` can exhaust its visibility budget (`approvals.py:63-76`) or be lost with its Arq job. The requeued note is the only surviving copy — nothing durable records it (`approval_service.py:170` pushes to Redis only; `approvals` has no room column, per `docs/tasks/2026-07-22-approval-gate-room-scoping/spec.md`). Requeue also matches what the code already does for the kind it does handle (`:1604-1605`). |
| Q-5 | Should the F-29 re-arm requeue an unvoted ballot unconditionally, or only while the gate is still PENDING? | **Only while PENDING**, via one `OrchestrationFacade.get_approval` read. | Unconditional requeue leaves a settled gate's note cycling through every subsequent turn until `_TTL_SECONDS = 86400` (`pending_notify.py:25`) — rendering a ballot for a gate whose `cast_approval_vote` now errors, and burning prompt tokens for 24h. The state read is the same one `approvals.py:59` already performs, through a facade `tool_registry.py:280` already imports from this layer. It also bounds the cycle: resolution garbage-collects the note. |
| Q-6 | Does the voteless-`completed` reporting half belong here or in `docs/tasks/2026-07-22-turn-outcome-reporting/`? | **Here.** | That dossier covers F-6/F-9/F-15/F-40 — publish-failure misreporting, the composer watchdog, `/compact`, and mid-round token discard. A grep of its `spec.md` for `approver`, `approval`, `cast_approval_vote` returns **no matches**; it does not touch `approvals.py` or the approval path at all. The signal the fix needs (did this turn vote?) is produced by the F-29 fix's vote sink and by nothing else, so splitting it would mean shipping the sink here and its only consumer elsewhere. |
| Q-7 | Data repair for ballots already lost or questions already leaked? | **None for either, and none is possible.** See §7. | |
| Q-8 | Does this warrant a `check-security` referral, as the sibling approval-gate dossier took? | **No.** See §6's access-control analysis. | The leaked text originates in the *approver's own project* and is authored by the workflow author. No principal gains a capability, and no second tenant's data crosses. Contrast `docs/tasks/2026-07-22-approval-gate-room-scoping/spec.md` §9.1, which referred because a caller could make the platform act inside another tenant. |

## 4. Reproduction

### F-8 — deterministic

Preconditions: project P with agent `A`; chatrooms `X` and `Y` in P, `A` bound to both; workflow `W`
in P with an `approval_gate` node whose `approvers` include `A` and whose `question_template` is
`"Approve payout of {{ __trigger__.amount }} to {{ __trigger__.who }}?"`.

1. Trigger `W` with `trigger_payload = {"chatroom_id": "<X>", "amount": "50000", "who": "Vendor Q"}`.
   The executor interpolates the question (`approval_gate.py:32`) and creates the gate against `X`
   (`:38` onward → `approval_service.py:64-131`).
2. `_notify_and_arm` pushes the note to `A` with `chatroom_id: "<X>"` and the interpolated `question`
   (`approval_service.py:146-155`, pushed at `:170`), then defers `drive_approver_turn` by 2s
   (`:179-185`).
3. Within that window, a user posts in room `Y`, firing `A`'s `every_n_messages` trigger.
4. `A`'s room-`Y` turn calls `_pending_context_and_tools(agent, Y)` (`turn_engine.py:1796-1798`).
   The partition at `:1597-1603` sees `kind == "approval_request"`, skips the room comparison
   entirely, and appends to `usable`.
5. **Observed:** `A`'s room-`Y` system prompt contains
   `"  Question: Approve payout of 50000 to Vendor Q?"` (`:1627`). `A` is free to restate it to
   room-`Y` participants, who have no relationship to room `X` or to `W`.

Control: repeat with the gate targeting room `Y`. Behaviour is identical — which is the point.
Nothing today distinguishes the two.

### F-29 — racy, ~2s window

Preconditions: as above, but `A` bound only to room `X`, and the gate targets room `X` (so F-8's
mismatch never arises and the two defects are observed independently).

1. Trigger `W`. The note is pushed and `drive_approver_turn` deferred 2s (`approval_service.py:184`).
2. Within that window a room-`X` message fires `A`'s trigger. The room turn drains the note
   (`turn_engine.py:1585`), renders the ballot (`:1622-1627`), and receives `cast_approval_vote`
   (`:1634-1638`). The model — answering a chat message — does not call it.
3. The turn returns `completed` at `:2218`. No requeue: no completed path calls
   `_requeue_notifications`.
4. Two seconds later `drive_approver_turn` runs. `drain` returns `[]`; `:1592-1593` returns
   `(None, [], [])`; the turn has no vote tool and completes.
5. **Observed:** `approvals.py:113` logs `"approver turn driven"` at info with `result=completed`,
   indistinguishable from a successful vote. The gate stays PENDING until `approval_timeout`
   (`orchestration.py:184`) resolves it `TIMEOUT_LEADER` (`approval_service.py:264`).

Step 2's declination is the one step not statically traceable, which is why the source audit rated
F-29 **plausible** rather than confirmed (`findings.md:1068-1070`). Steps 1, 3, 4 and 5 are
deterministic and are what §8 pins; the fix does not depend on how often step 2 goes either way,
because a ballot that survives a declined turn is correct under both outcomes.

## 5. Root Cause Analysis

### F-8 — one cause

1. `approval_service.py:151` puts a real room on the note. Correct: `tool_registry.py:265-267`
   documents that the room is what routes `approval.resolved`. **Not the cause.**
2. `pending_notify.py:28-29` keys the queue by agent alone, so the store cannot filter by room.
   Correct and deliberate (`:11` — "pure Redis state; no domain logic"), and the docstring at
   `turn_engine.py:1571-1573` names this as exactly why the *turn* must do the check. **Not the
   cause — it is the reason the cause matters.**
3. **Root cause — `turn_engine.py:1598`.** The predicate leads with
   `n.get("kind") == "released_observation"`. The property being guarded is "this note names a room
   that is not this turn's room", which is a property of the note's `chatroom_id` field, not of its
   `kind`. Conditioning a room check on a kind makes the guard's coverage a function of which kinds
   existed when it was written. This is the earliest link whose correction prevents the symptom:
   fix it and steps 1-2 are unchanged and the leak does not occur.
4. **Symptom.** `:1602-1603` puts the note in `usable`; `:1611` iterates it; `:1626-1627` renders the
   interpolated question into a foreign room's system prompt.

**Aggravating factor, not cause — the room is present and read three lines later.** `:1617` reads
`n.get("chatroom_id")` and `:1619` parses it to a UUID. The exact value the correct decision needs is
in hand, parsed, at the moment the wrong decision has already been made. That is what makes this a
wiring defect rather than a missing capability, and it is why the fix adds no new data.

**Aggravating factor, not cause — the docstring documents the narrow behaviour as if it were the
invariant.** `:1571-1577` gives a kind-independent *rationale* and a kind-specific *rule*. A reader
checking whether approval notes are covered finds prose that reads like a general guarantee. This
made the gap invisible to review; it did not create it.

**Provenance — a gap from the original leak fix, not a regression.** The filter was written for the
R28.07 private-release leak and is pinned by three tests, all `released_observation`
(`backend/tests/unit/test_observer_agents.py:1102-1120`, `:1123-1150`, `:1153-1177`). The
`approval_request` path is pinned by `backend/tests/unit/test_a2a_turn_dispatch.py:686-718`, which
passes the note's **own** room as the turn's room (`:689`, `:712`) — so it asserts correct
routing and has never exercised a mismatch. Neither test suite could have caught this. **Verdict:
genuinely new, and structurally invisible to the coverage that exists.**

### F-29 — one cause

1. `_APPROVER_TURN_DISPATCH_DELAY_S = 2` (`approval_service.py:49`, used at `:184`) opens the window.
   Deliberate and load-bearing — `:45-48` explains it lets `create_gate`'s enclosing transaction
   commit first. **Aggravating factor, not cause:** shrinking it narrows the window and closes
   nothing. A turn already in flight when the note is pushed drains it just as effectively, with no
   window at all.
2. `run_input_turn` takes no lock while `run_turn` does (`turn_engine.py:590`). **Aggravating
   factor, not cause**, and the source audit refuted it directly (`findings.md:1096-1099`):
   `turn_lock` is keyed `(agent, room)` and the drain happens at turn *start*, so serialising the two
   turns would still let the room turn drain first. Recorded so the fix is not mistaken for a locking
   problem.
3. **Root cause — `turn_engine.py:1639` returns `usable` as "consumed", and no completed path ever
   reconsiders it.** The consumption decision is made at drain time, before the provider call, for
   every kind alike. For a `notify` that is correct — rendering *is* delivery. For an
   `approval_request` it is not: the note represents an outstanding obligation, and rendering it is
   an *offer to act*, not the act. The seven requeue sites (`:897`, `:907`, `:1605`, `:1687`,
   `:2079`, `:2096`, `:2243`) all encode "the agent never saw them"; none encodes "the agent saw them
   and did nothing". Correcting this is what makes the ballot survive, on every path, regardless of
   which turn won the race.
4. **Symptom A.** The gate's only ballot is destroyed; `approval_timeout` becomes the resolution path.
5. **Symptom B.** `approvals.py:112-113` reports `completed` at info. **This is a consequence, not a
   second cause:** the task has no signal to report anything better, because `TurnResult`
   (`turn_engine.py:328-333`) carries `status`, `reason`, `message_id`, `text`, `tool_rounds` and
   nothing about tools invoked. The fix that produces the signal is the same fix that saves the
   ballot.

## 6. Blast Radius and Sibling Suspects

### Blast radius

**F-8.** Every deployment where an approver agent is bound to more than one room, or to any room
other than the gate's. Per gate, the window is the whole interval from the push
(`approval_service.py:170`) to whichever turn drains first — not merely the 2s dispatch delay, since
a turn already running drains at its own start. Retroactive exposure is bounded by
`_TTL_SECONDS = 86400` (`pending_notify.py:25`).

**Materially weaker than the R28.07 leak it structurally resembles**, and the difference is worth
stating precisely rather than treating "leak" as one severity:

- The leaked text is a workflow author's own `question_template` interpolated with run variables
  (`approval_gate.py:32`), not another room's private transcript. Both rooms are in the same project
  by construction — the gate room is in-project (`turn_engine.py:742-749`), and the approver's other
  room is one it is bound to in that project.
- **The gate is not starved.** `allowed_approvals` is populated regardless of room (`:1619`) and
  `build_cast_approval_vote_tool` scopes to the approval id, not the room
  (`tool_registry.py:275-279`), so the agent can still vote correctly from the wrong room. F-8 is
  pure over-disclosure with no loss of function — which is exactly why F-29's loss-of-function is a
  separate defect rather than the same one seen from another angle.
- **No principal gains a capability. No `check-security` referral (Q-8).** The set of readers is
  agents already bound to the approver's rooms, all inside one project. Contrast
  `docs/tasks/2026-07-22-approval-gate-room-scoping/spec.md` §9.1, which referred because a
  project-A caller could make the platform publish into project B. Nothing crosses a tenant boundary
  here. That dossier's fix and this one are complementary: it constrains which room a gate may
  *target*; this constrains which turn may *read* the resulting note. Neither subsumes the other —
  after its fix, room X and room Y are both in-project and F-8 still fires.

**F-29.** Approval gates whose approvers are also room-bound. Narrow: the room turn must begin inside
roughly the dispatch delay plus queue latency (a turn already in flight drained before the push, so
it never sees the note). Cost per occurrence is one gate resolving `TIMEOUT_LEADER`
(`approval_service.py:264`) after its full `timeout_seconds`, plus operator time lost to an info-level
success line. In MAJORITY/CONSENSUS mode with several approvers, one lost ballot changes the outcome
only when it was decisive.

### Sibling suspects

Swept for two shapes: **(a)** a room-bearing payload rendered into a turn without a room comparison,
and **(b)** a queue entry destroyed on read whose consumer carried an obligation it may not have
discharged.

| Site | Shape | Verdict |
|---|---|---|
| `approval_request` branch, `turn_engine.py:1612-1627` | (a) | **Confirmed — F-8.** §5. |
| `approval_request` drained, turn completes without voting (`:1639` → `:883`/`:2179`/`:2218`) | (b) | **Confirmed — F-29.** §5. |
| `released_observation` branch, `turn_engine.py:1628-1631` | (a) | **Cleared.** The filter at `:1598-1600` covers exactly this kind, in both the mismatch and the headless (`chatroom_id is None`) directions. Pinned by `test_observer_agents.py:1123-1150` (mismatch) and `:1153-1177` (headless), both asserting `drained == []` and a `requeue` call. This is the one kind that is correct today. |
| `released_observation` drained and rendered, agent does nothing | (b) | **Cleared as inapplicable.** [R28.07] (`REQUIREMENTS.md:2066`) asks that the target *see* the analysis; rendering is the delivery in full. There is no deferred obligation, so nothing to re-arm. |
| `kind: "notify"` (`backend/contexts/orchestration/application/a2a_handler.py:60-67`) | (a) | **Cleared by construction.** The payload is `{kind, from_agent, payload}` — it carries **no `chatroom_id` key at all** (`:62-66`). There is no room to misroute to. Verified as the complete producer set: repo-wide, exactly three sites call `pending_notify.push` — `a2a_handler.py:60`, `approval_service.py:170`, `backend/app/api/v1/observations.py:226`. Only the latter two attach a room (`approval_service.py:151`, `observations.py:230`). |
| `kind: "notify"` drained and destroyed | (b) | **Cleared as by design.** [R9.16] (`REQUIREMENTS.md:449`) — "`notify` is fire-and-forget". `drain`'s own docstring frames the loss window as acceptable "for best-effort context injection" (`pending_notify.py:46-48`). Correct for this kind; the defect is that the same frame was applied to a ballot. |
| Unknown-kind fallback renderer, `turn_engine.py:1632-1633` | (a) | **Cleared today, load-bearing tomorrow.** `json.dumps(n)` would dump a room-bearing note wholesale into a foreign prompt. No fourth kind exists (producer set above), so it is unreachable — but only the generalised predicate in §7 keeps it that way when one is added. This is a direct argument for fixing the predicate rather than adding a second kind-specific arm. |
| `allowed_approvals` room value → `approval.resolved` publish (`tool_registry.py:287`; `approval_service.py:439-443`) | (a) | **Cleared — not a read gate.** The room routes an outbound publish; it never admits content into a prompt. Unchanged by this fix. Its correctness is the approval-gate-room-scoping dossier's subject, not this one's. |
| Foreign gate room → the approver's room-scoped Concept Maps | (a) | **Cleared — already guarded.** `turn_engine.py:750-754` nulls `knowledge_chatroom_id` unless `is_agent_in_chatroom`, with `:742-749` naming this exact case. Note the asymmetry this dossier closes: knowledge flowing *from* the gate room into the turn was guarded; the note flowing *from* the gate room into the turn was not. |
| `_pop_queued_trigger` destructive `getdel` (`turn_engine.py:304-305`) | (b) | **Cleared here — owned elsewhere.** Same destructive-read family; it is F-30/F-39 and belongs to `docs/tasks/2026-07-22-turn-idempotency-and-locking/`. Not re-filed. |
| Every other multi-step Redis sequence in the repo | (b) | **Cleared, on someone else's evidence.** `docs/tasks/2026-07-22-turn-idempotency-and-locking/spec.md` swept them repo-wide — `pending_notify.py:36,52,81`, `tokens.py`, `presence.py`, `ratelimit.py`, `join.py`, `a2a_rendezvous.py` and eleven others — and found `turn_engine.py:304-305` the only unpipelined one. **That clearance is about pipelining atomicity only**; it says nothing about consumption semantics, which is what F-29 is. Two different questions over the same lines. |

### Coordination — read, not assumed

**`docs/tasks/2026-07-22-a2a-delivery-idempotency/spec.md` (F-19) — dependency.** It **edits**
`pending_notify.py`: its C1 replaces `:84`'s `pipe.ltrim(key, 0, _MAX_PENDING - 1)` with
`pipe.ltrim(key, -_MAX_PENDING, -1)`, and corrects the `requeue` docstring at `:68-76`; its own
file-touch table records its edits as `pending_notify.py:67-86`. This dossier edits **no
line** of `pending_notify.py` (Q-2), so there is **no textual conflict**. The dependency is semantic
and one-directional: this fix promotes `requeue` to a normal-path operation and therefore amplifies
F-19 (Q-3). Direction matters — F-19 before or with this, never after.

**`docs/tasks/2026-07-22-turn-idempotency-and-locking/spec.md` — merge adjacency, not a dependency,
and the assumption it is often given needs correcting.** It does **not** touch `pending_notify.py`'s
`drain` or `requeue`. Its own §6 lists `pending_notify.py:36,52,81` among multi-step Redis sequences
that are **cleared**, and the a2a-delivery dossier's cross-check table records its
`pending_notify.py` edits as "**none (cited only, §6)**". Two independent sources agree.

What it *does* touch, per that same table: `turn_engine.py` at `:243-324`, `:576-650`,
`:1778-1790`, `:2220-2246`, `:2652`, `:2704`. Against this dossier's edit regions:

| This dossier edits | Nearest turn-idempotency region | Separation |
|---|---|---|
| `:1566-1581` (docstring), `:1595-1607` (predicate), `:1639` (return) | `:1778-1790` | ~171 lines, disjoint |
| the three completed-return sites `:883`, `:2179`, `:2218` | `:2220-2246` | `:2218` sits **2 lines above** its region |
| `tool_registry.py:260-299`, `approvals.py:96-114` | none | disjoint by file |

So: **disjoint everywhere, with one two-line proximity at `turn_engine.py:2218-2220`** — its region
starts at the `except` on `:2220`; this dossier's edit is the `return` on `:2218`. Adjacent lines,
different statements, no shared object. Also worth naming, since neither dossier's text does: its
`:1778-1790` region is six lines above the `_pending_context_and_tools` call site at `:1796-1798`,
close enough that whichever lands second should re-read that block rather than trust a clean
three-way merge.

One semantic interaction, in this dossier's favour: that dossier's C1 wraps `run_turn` in
`try/finally` and moves `_requeue_notifications` onto the **cancellation** path, which today skips it.
That adds an eighth requeue site of the existing kind. It composes with
this fix — its site restores unseen notes, this fix's site restores seen-but-unacted ballots, and the
two conditions are disjoint. **Verdict: merge adjacency. `depends_on` unaffected in either
direction.**

**`docs/tasks/2026-07-22-observation-binding-cleanup/spec.md` (FU-1) and
`docs/tasks/2026-07-22-presence-transition-and-release-wakeup/spec.md` (FU-3) — a different
defect, and F-8 is not its cause.** Both describe a `released_observation` note for room `R`
surviving the target's unbind from `R` and then cycling as misrouted until its TTL. Read against the
code:

- Their subject is the note's **lifetime after a binding disappears** — nothing retracts it
  (`observations.py:226-235` pushes; no unbind path deletes). Their claimed *safety* rests on the
  misroute filter at `turn_engine.py:1571-1577` working, which observation-binding FU-1 says in as
  many words: "It cannot leak — the turn engine requeues room-mismatched notes explicitly to prevent
  that." For `released_observation`, it does (`:1598-1600`, pinned at
  `test_observer_agents.py:1123-1177`).
- F-8's subject is the **kinds the filter does not cover**. It is not their cause: their notes are
  correctly requeued today, and would be requeued identically if F-8 had never existed. It is not the
  same defect: theirs is a missing retraction on a covered kind; this is a missing check on an
  uncovered kind. **Different defects, no causal link in either direction, and no `depends_on`.**
- The real relationship is that F-8's fix **extends the premise their clearance already relies on** to
  the rest of the queue — after it, "room-mismatched notes are requeued" is true of every note, not
  of one kind.
- **One honest caution, in their direction.** F-8's fix creates a second population of notes that
  "linger as misrouted until TTL" — approval ballots whose driven turn never lands. That is precisely
  the condition presence FU-3 flags. Q-5's PENDING check bounds it for ballots specifically (a
  resolved gate's note is dropped rather than restored), so this dossier **narrows** rather than
  widens the family FU-3 owns — but FU-3's underlying question, whether a note should be retractable
  at all, stays open and stays theirs. Recorded here as FU-2 so whoever takes FU-3 inherits the fact
  rather than rediscovering it.

## 7. Fix Design

Backend only. Three commits, separately revertible. No migration, no API contract change, no
frontend change, no `pnpm run gen:api`.

### C1 — generalise the misroute predicate (F-8)

Replace the condition at `turn_engine.py:1598-1600`. Today it is
`kind == "released_observation" and (chatroom_id is None or note_room != chatroom_id)`. It becomes:
**a note is misrouted when it carries a `chatroom_id` and that room is not this turn's room** — that
is, `note_room is not None and (chatroom_id is None or str(note_room) != str(chatroom_id))`. A note
with no room is room-agnostic and remains usable in any turn.

Behaviour, kind by kind — the whole point is that only one cell changes:

| Note kind | Carries room? | Before | After |
|---|---|---|---|
| `released_observation` (`observations.py:229-231`) | always | requeued on mismatch/headless | **identical** |
| `approval_request`, gate has a room (`approval_service.py:151`) | yes | rendered anywhere | **requeued unless the turn is in that room** |
| `approval_request`, headless gate (`chatroom_id: None`) | no | rendered anywhere | **identical** |
| `notify` (`a2a_handler.py:62-66`) | never | rendered anywhere | **identical** |

The driven approver turn is unaffected in the room-bearing case: `approvals.py:93` passes the gate's
room into `run_input_turn`, which threads it to `_pending_context_and_tools` at `:716`, so the note's
room and the turn's room match and it renders. Verified against the whole chain
`approval_service.py:183` → `approvals.py:35,93` → `turn_engine.py:660,716`.

Correct the docstring at `:1571-1577` in the same commit to state the general rule rather than the
`released_observation` special case (§11).

### C2 — make consumption conditional on action, for ballots only (F-29)

1. **A vote sink.** `build_cast_approval_vote_tool` (`tool_registry.py:256-299`) gains an optional
   `voted: set[uuid.UUID] | None` parameter; `_invoke` adds `approval_id` to it immediately after
   `cast_approval_vote` returns (`:282-288`), so only a recorded ballot counts. Follows the
   `artifact_sink` precedent — a mutable collection passed down and read after the turn
   (`turn_engine.py:1805-1808`).
2. **Thread it.** `_pending_context_and_tools` creates the set, passes it at `:1636-1638`, and returns
   it as a **fourth tuple element**. Both call sites update (`:716`, `:1796-1798`).
3. **Re-arm at the three completed returns** — `:883` (headless), `:2179` (observer), `:2218` (room) —
   through one new helper, `_settle_pending_approvals(agent, pending_notes, voted)`: for each consumed
   `approval_request` note whose `approval_id` is absent from `voted`, read the gate via
   `OrchestrationFacade(db).get_approval` (the read `approvals.py:59` already performs, through the
   facade `tool_registry.py:280` already imports at this layer) and requeue only while
   `state == ApprovalState.PENDING` (Q-5). Best-effort, matching `_requeue_notifications`' own posture
   at `:1650-1656` — a Redis or DB hiccup must never fail a committed turn.

*Rejected alternative — replace the tuple with a `_PendingContext` dataclass* (the `_Acquisition`
precedent at `turn_engine.py:344-353`). Cleaner as a type, but it rewrites two production call sites
and five test unpack sites (`test_a2a_turn_dispatch.py:712`, `:726`;
`test_observer_agents.py:1114`, `:1144`, `:1173`) while three other dossiers hold edits in this file.
Minimal diff wins here; recorded as FU-3.

### C3 — report what the turn actually did (F-29, symptom B)

`TurnResult` (`turn_engine.py:328-333`) gains `approvals_voted: int = 0`, set from the sink at the
three completed returns. `drive_approver_turn` (`approvals.py:96-114`) then distinguishes three
outcomes instead of two: voted (info), completed-without-voting (**warning**, stating that the ballot
was re-armed or the gate already settled), and non-`completed` (the existing warning at `:111`). This
is the standard `:104-111` already sets — "make the cause findable at the moment it happens rather
than at the timeout" — applied to the case it does not currently cover.

### Why this corrects rather than masks

**C1.** The correction sits at the same link §5 names as the cause. After it, the note still carries a
room, the queue is still agent-keyed, and the drain still returns every kind — and the leak does not
occur. Nothing downstream compensates for anything upstream. It also removes a *class* rather than an
instance: the predicate now tests the property that actually matters, so the unknown-kind fallback at
`:1632-1633` and any fourth note kind are covered without a fourth arm. The masking alternatives are
explicitly rejected:

- *Add a second `and kind == "approval_request"` arm.* Fixes this instance and leaves the same trap
  for the next kind — the defect is that coverage tracks kinds at all.
- *Stop putting the room on the note.* Breaks `approval.resolved` routing (`tool_registry.py:287`) and
  discards the value the correct check needs.
- *Strip only the `question` line at `:1626-1627`.* Suppresses the visible payload while still
  consuming another room's note in this turn — leaving F-29's loss in place with the leak merely
  quieter. Symptom removal, not correction.

**C2.** The obligation and its discharge become the same transaction: a ballot is consumed exactly
when it is cast. That is a property of the note's semantics, not of which turn won a race — so it
holds for every ordering, including the two configurations C1 cannot reach (matching rooms, headless
gate). The masking alternatives:

- *Shrink `_APPROVER_TURN_DISPATCH_DELAY_S`.* Narrows a window that is not the cause (§5, factor 1); a
  turn already in flight has no window at all.
- *Lock `run_input_turn` against `run_turn`.* Refuted by the source audit (`findings.md:1096-1099`):
  the drain is at turn start, so serialising still lets the room turn drain first.
- *Have `drive_approver_turn` re-push the note before its turn.* Compensates downstream for an
  upstream loss, and double-renders whenever nothing was lost.

### Data repair

**None, for either finding, and none is possible. Stated precisely because "no repair" must be a
decision, not an omission.**

- **F-8 — lost ballots.** A dropped note was `DEL`ed from Redis (`pending_notify.py:54`) with no
  durable copy: the push is Redis-only (`approval_service.py:170`) and `approvals` has no room column
  or note record. There is
  nothing to read to reconstruct what was lost, and gates whose ballots were lost have already settled
  `TIMEOUT_LEADER` (`approval_service.py:264`). **Re-notifying a settled gate would be a second
  defect** — the identical position the a2a-delivery dossier reached for F-19, reached
  independently here from the same facts.
- **F-8 — leaked questions.** The disclosure has already happened, into a prompt and possibly into a
  room transcript. No migration can unsay it. Any leaked question is workflow-author text within one
  project (§6), so no cross-tenant notification obligation arises. Queues still holding pre-fix notes
  age out within `_TTL_SECONDS = 86400` (`pending_notify.py:25`) and are correctly routed the moment
  the fix deploys.
- **F-29 — in-flight queues.** No state is wrong. A note currently parked is a valid note; after
  deploy it is consumed under the corrected rule. Zero writes.

**No Alembic revision is part of this dossier. If /build finds itself writing one, the change has
drifted out of scope and must stop.**

## 8. Regression Test Plan

Failing tests first. Every test states why it fails today. Tier check: all four files exist under
`backend/tests/unit/` (confirmed present, alongside `integration/`, `wiring/` and `e2e/` — this
dossier adds to `unit/` only, since every assertion is over a pure function's partition or a worker's
wiring, both already covered by existing in-file harnesses).

### The failing test comes first

**T-1 (fails today) — `backend/tests/unit/test_a2a_turn_dispatch.py`**, new
`test_pending_context_requeues_approval_request_for_a_different_room`. Model it on the existing
mismatch test for the other kind (`test_observer_agents.py:1123-1150`): drain returns one
`approval_request` note carrying `chatroom_id = room_a`; call
`_pending_context_and_tools(agent, room_b)`; capture `pending_notify.requeue`. Assert `block is None`,
`tools == []`, `drained == []`, and `requeue == [(agent.id, notes)]`. **Fails today** on every
assertion: `turn_engine.py:1598` requires `kind == "released_observation"`, so the note takes the
`else` at `:1602-1603`, `:1622-1625` renders the ballot, `:1634-1638` builds the vote tool, and
nothing is requeued. This is F-8's minimal statement.

### F-8, remaining

**T-2 (fails today) — same file**, `test_pending_context_does_not_render_foreign_gate_question`.
Same setup with `question` set to a sentinel; assert the sentinel appears in no returned block.
**Fails today** — `:1626-1627` appends `f"  Question: {n['question']}"` with no room check. Separate
from T-1 because T-1 pins the *routing decision* and this pins the *disclosure*, and a future partial
fix could satisfy one without the other.

**T-3 (fails today) — same file**, `test_pending_context_requeues_approval_request_on_headless_turn`.
Room-bearing note, `chatroom_id=None`. **Fails today** — same `:1598` short-circuit. Mirrors the
existing headless case for the covered kind (`test_observer_agents.py:1153-1177`), which is the
symmetry the fix restores.

**T-4 (passes today, guard against over-correction) — same file**,
`test_pending_context_renders_headless_gate_approval_in_any_room`. Note with `chatroom_id: None`
(the headless gate `approval_service.py:151` produces); assert it renders and yields the vote tool in
a turn with an arbitrary room. Passes today for the wrong reason (no check at all); after C1 it is the
only thing proving the predicate keys on *presence* of a room, not on kind. **Without it, "requeue
every `approval_request` whose room ≠ this turn's" would pass T-1 through T-3 and silently break every
headless gate.**

**T-5 (passes today, guard) — same file**, `test_pending_context_renders_a2a_notify_in_any_room`.
A `{kind: "notify"}` note (`a2a_handler.py:62-66`) renders in any room and in a headless turn. Passes
today; pins [R9.16]'s room-agnostic delivery against a predicate that over-reaches into the kind that
must never be filtered.

**T-6 (passes today, must keep passing) — `test_a2a_turn_dispatch.py:686-718`,
`test_pending_context_adds_approval_tool`, unmodified.** It passes the note's own room as the turn's
room (`:689`, `:712`), so C1 leaves it green. **If /build has to edit it, the predicate is wrong** —
it is the in-room positive control, and it must survive untouched.

**T-7 (passes today, must keep passing) — `test_observer_agents.py:1102-1120`, `:1123-1150`,
`:1153-1177`, unmodified.** The three `released_observation` cases. C1 must be a strict generalisation:
every `released_observation` outcome is byte-identical before and after (§7's table). Any edit to
these three is a regression signal, not a test update.

### F-29

**T-8 (fails today) — `backend/tests/unit/test_a2a_turn_dispatch.py`**,
`test_unvoted_approval_note_is_requeued_when_gate_still_pending`. Drive a completed turn whose drained
notes include an `approval_request` for a PENDING gate, with the vote sink left empty; assert
`pending_notify.requeue` is called with exactly that note. **Fails today** — no completed path calls
`_requeue_notifications` (`:883`, `:2179`, `:2218` all return directly), and there is no sink to
consult. This is F-29's minimal statement.

**T-9 (fails today) — same file**, `test_voted_approval_note_is_not_requeued`. Same setup, sink
containing the approval id; assert **no** requeue. **Fails today** for the mirror reason: the sink does
not exist, so `build_cast_approval_vote_tool` has nowhere to record the vote
(`tool_registry.py:282-288` returns a `ToolResult` and nothing else). Load-bearing — without it, T-8
is satisfied by requeueing unconditionally, which is Q-5's rejected design and would cycle settled
gates' notes for 24h.

**T-10 (fails today) — same file**, `test_unvoted_approval_note_for_resolved_gate_is_dropped`. Empty
sink, gate `APPROVED`; assert no requeue. **Fails today** — nothing reads gate state on any completed
path. Pins Q-5's PENDING condition, which is also what bounds the FU-3 family (§6).

**T-11 (fails today) — `backend/tests/unit/test_approval_gate_fixes.py`**,
`test_drive_approver_turn_warns_when_turn_completed_without_voting`. Reuse the file's `_wire_task`
harness (`:67-101`) with `turn_result` a `completed` `TurnResult` carrying `approvals_voted == 0`;
assert the task's return value distinguishes it from a voting turn and that the warning fires. **Fails
today** — `approvals.py:104` branches on `result.status` alone, so a voteless completion takes the
`else` at `:112-113` and logs `"approver turn driven"` at info; and `TurnResult`
(`turn_engine.py:328-333`) has no field that could tell them apart. Primary home chosen because this
file already owns the task's five behavioural tests (`:104-174`) and its module docstring (`:3-9`)
states the contract this test defends.

**T-12 (passes today, must keep passing) — `test_approval_gate_fixes.py:104-124`,
`test_drive_approver_turn_runs_headless_turn`, unmodified.** Pins that a voting turn still reports
`"completed"` and still threads `chatroom_id` (`:122`). If `TurnResult` gains a required field this
fails to construct — the signal that `approvals_voted` must default to `0` (`turn_engine.py:328-333`).

### Not added

No integration or e2e test. F-8's reproduction spans a workflow run, an Arq deferral and two agent
turns; F-29's turns on a ~2s race that a test would have to fake anyway. The unit assertions pin both
decision points exactly, and the one step that is genuinely non-deterministic — whether a model
chooses to vote (`findings.md:1068-1070`) — is the one step no test tier can pin. No frontend test:
no frontend change.

**Gates:** `pytest -q`, `ruff check .`, `ruff format --check .`, `mypy .` in `backend/`.

## 9. Risks and Rollback

| Risk | Assessment and mitigation |
|---|---|
| **A room-bearing ballot becomes unreachable if `drive_approver_turn` never lands and the approver is not bound to the gate's room.** The real cost of C1: today any room turn would opportunistically consume it. | Accepted, and it is the correct trade — that opportunistic path *is* F-8's leak and F-29's loss. Backstops: the task retries five times over visibility failures (`approvals.py:63-72`); `approval_timeout` resolves the gate (`orchestration.py:184` → `approval_service.py:241-267`); the note expires (`pending_notify.py:25`). Recorded as FU-1 because the deeper answer — a durable ballot rather than a Redis note — is a design change, not a bugfix. |
| **C2 makes `requeue` a normal-path operation, amplifying F-19.** | The reason for `depends_on` (Q-3). Requeue batches stay bounded by `_MAX_PENDING = 50` (`pending_notify.py:24`), below which the LTRIM is a no-op — so the exposure is real but capped. Fully closed once F-19's one-line fix lands. |
| **The re-arm could cycle a ballot through many turns, re-rendering it each time.** | Bounded on both axes: by gate state (Q-5 — resolution drops it) and by `timeout_seconds`, after which `approval_timeout` settles the gate. Each cycle costs the ballot lines at `:1622-1627`, not the whole note history. Its cost is *lower* than today's silent loss, which costs a full `timeout_seconds` of a parked workflow run. |
| **Adding a fourth tuple element to `_pending_context_and_tools` touches five test unpack sites.** | Named in §7 (`test_a2a_turn_dispatch.py:712`, `:726`; `test_observer_agents.py:1114`, `:1144`, `:1173`). Mechanical; the dataclass alternative is FU-3. |
| **Merge adjacency at `turn_engine.py:2218-2220`.** | Two lines from the turn-idempotency dossier's `:2220-2246` region (§6). Whichever lands second re-reads that block and `:1778-1798`, and re-runs both dossiers' test files. No `depends_on` in either direction. |
| **Over-correction breaking headless gates.** The most likely way to get C1 wrong is to requeue every `approval_request` whose room ≠ the turn's, which sends a `None`-room note back forever. | T-4 exists solely to catch this and is called out in §8 as the load-bearing guard. |
| **Rollback.** | Three independent commits. No migration, no schema change, no API contract change, no persisted state written by the fix, nothing for a rollback to reconcile — a direct corollary of §7's data-repair position. Reverting C1 restores the leak; reverting C2 restores the loss; reverting C3 restores only the misleading log line. C3 may be reverted alone; C2 may not be reverted while C3 stands, since C3 reads the field C2 introduces. |

## 10. Acceptance Criteria

- [x] **AC-1**: T-1 (§8) fails against current code and passes after C1.
- [x] **AC-2**: a note carrying a `chatroom_id` is requeued, not rendered, in any turn whose room
      differs — including a headless turn — **regardless of its `kind`**; and a note carrying no
      `chatroom_id` is rendered in every turn, also regardless of kind.
- [x] **AC-3**: no foreign gate's `question` text appears in any rendered notify block (T-2).
- [x] **AC-4**: the three existing `released_observation` tests
      (`test_observer_agents.py:1102-1120`, `:1123-1150`, `:1153-1177`) and the in-room approval test
      (`test_a2a_turn_dispatch.py:686-718`) pass **unmodified** — C1 is a strict generalisation.
- [x] **AC-5**: the driven approver turn still receives and renders its own gate's note and still
      obtains `cast_approval_vote`, for both a room-bearing and a headless gate (T-4, T-12).
- [x] **AC-6**: T-8 fails against current code and passes after C2: a completed turn that drained an
      `approval_request` and cast no vote requeues that note while the gate is PENDING.
- [x] **AC-7**: a cast vote consumes its note (T-9), and an unvoted note for a resolved gate is
      dropped rather than requeued (T-10).
- [x] **AC-8**: the re-arm is applied at **all three** completed returns — `turn_engine.py:883`,
      `:2179`, `:2218` — not only the room path.
- [x] **AC-9**: `drive_approver_turn` distinguishes a voting turn from a voteless completion and warns
      on the latter (T-11); `TurnResult.approvals_voted` defaults to `0` so no existing construction
      site breaks.
- [x] **AC-10**: `pending_notify.py` is **unmodified by this dossier** (Q-2) — the file's only pending
      change is `2026-07-22-a2a-delivery-idempotency`'s C1.
- [x] **AC-11**: **no Alembic revision, no backfill, no data-mutating script** is added.
- [x] **AC-12**: the `_pending_context_and_tools` docstring (`:1566-1581`) states the general rule
      (§11); no comment still describes the room check as `released_observation`-specific.
- [x] **AC-13**: `pytest -q`, `ruff check .`, `ruff format --check .`, `mypy .` pass in `backend/`.

## 11. SRS Delta

**None.** `REQUIREMENTS.md` is correct as written and is the intent source that convicts the code.
[R9.16] (`:449`) makes `notify` fire-and-forget, which the fix preserves exactly (§7's table, T-5).
[R28.07] (`:2066`) scopes a private release to agents "of the same room" — the rule this fix extends
to the note kind it was never applied to. [R15.10] (`:767-771`) and [R15.13] (`:774`) define the gate
and make the timeout the fallback for non-convergence, not for a lost ballot. Nothing requires
amendment.

**Two in-code corrections are required, because the defect originates in one of them.**

1. `turn_engine.py:1571-1577` gives a kind-independent rationale and states a kind-specific rule
   (§5). Rewrite it to state what the code will then do: *a note that names a room is folded into a
   turn running in that room, or put back — this applies to every note kind, because the queue is
   keyed by agent alone and only the turn knows its room. A note that names no room (an A2A `notify`,
   a headless gate) is room-agnostic and always usable.* Also amend `:1579-1581`, which describes the
   returned notes as "consumed this turn": after C2 they are consumed only if acted upon.
2. `pending_notify.py:44-48`'s `drain` docstring frames the read-then-delete window as acceptable "for
   best-effort context injection". True for a `notify`, false for a ballot — this is the frame that
   produced F-29. Add one sentence recording that the store deliberately makes no delivery guarantee,
   and that any note carrying an obligation must be re-armed by its caller. **This is a comment-only
   change to a file whose code `2026-07-22-a2a-delivery-idempotency` is editing at `:68-76`; if that
   dossier has landed, fold the sentence into the docstring as it then stands rather than reverting
   its wording** (AC-10 covers code, not comments).

## 12. Deviation Log

**D-1 — `run_input_turn` hardcoded `chatroom_id=None` into `_pending_context_and_tools`; fixed in this dossier's scope, not deferred.**
§7 C1's own verification paragraph claims: "The driven approver turn is unaffected in the
room-bearing case: `approvals.py:93` passes the gate's room into `run_input_turn`, which
threads it to `_pending_context_and_tools` at `:716`." Freshness re-verification at
`/build` Step 2 found this false against current code: `run_input_turn`
(`turn_engine.py:725` at spec time, now `:734`) called
`self._pending_context_and_tools(agent, None)` — hardcoded, ignoring the `chatroom_id`
parameter the function receives and already threads to knowledge resolution three lines
later. `git log -L` confirmed this predates the source audits this dossier is built from
(present since at least 2026-07-17) — not a regression introduced by a dossier that
landed since the spec was written.

This was silent under the pre-fix code because `approval_request` notes were never
room-checked at all (F-8's own bug), so the hardcoded `None` never mattered. Once C1's
generalized predicate applies the room check to every kind that carries a room, this
hardcoding would have made every driven approver turn for a **room-bound** gate treat its
own gate's note as foreign to itself (`chatroom_id=None` passed in, but the note carries a
real room) and requeue it instead of rendering it — breaking AC-5 outright: a driven turn
for a room-bound gate would never obtain `cast_approval_vote`, and (compounding with C2)
the note would simply cycle via `_settle_pending_approvals` until the gate's timeout.

Presented to the user as a stop-and-report per Step 2's hard rule (a design assumption
this dossier's own correctness argument depends on did not hold). User chose to expand
scope now (2026-07-26) rather than defer to a follow-up or send back to `/spec`, given the
fix is a one-line correction (thread `chatroom_id` instead of `None`) directly load-bearing
for this dossier's own AC-5, in the same function family C1 already touches. Fixed at
`turn_engine.py`'s `run_input_turn` (the `_pending_context_and_tools` call site) plus its
docstring; regression test `test_run_input_turn_renders_own_gate_approval_in_matching_room`
added to `test_a2a_turn_dispatch.py`, asserting both the rendered question and the
`cast_approval_vote` tool's presence in the registry snapshot for a room-bound gate.

## 13. Follow-ups

- **FU-1** — An approval ballot exists only as a Redis list entry (`approval_service.py:170` →
  `pending_notify.py:32-40`) with a 24h TTL and no durable record; `approvals` persists no note and no
  room (`docs/tasks/2026-07-22-approval-gate-room-scoping/spec.md`, its FU-6). After C1, a
  room-bearing ballot is reachable only from the gate's room or the driven turn, so a lost Arq job is
  a lost ballot with nothing to reconstruct from — which is also why §7 can offer no data repair.
  Persisting the ballot (or the gate's room) would make it re-derivable and would let a reconciler
  re-arm approvers. Design work, deliberately out of scope; it converges with that dossier's FU-6 and
  should be taken with it.
- **FU-2** — Notes are pushed but never retracted. After C1, a requeued approval ballot joins the
  population that "lingers as misrouted until its TTL", which is exactly what
  `docs/tasks/2026-07-22-presence-transition-and-release-wakeup/spec.md` (FU-3) and
  `docs/tasks/2026-07-22-observation-binding-cleanup/spec.md` (FU-1) flag for
  `released_observation`. Q-5's PENDING check bounds the ballot case specifically, so this dossier
  narrows rather than widens the family — but the general question (should a note be retractable when
  its addressee can no longer receive it?) stays open and stays with FU-3. Recorded so its owner
  inherits the fact.
- **FU-3** — `_pending_context_and_tools` returns a 4-tuple after C2. A `_PendingContext` dataclass
  (the `_Acquisition` precedent, `turn_engine.py:344-353`) would read better; deferred because three
  dossiers currently hold edits in this file and the churn would land on five test unpack sites (§7).
  Worth doing once the 2026-07-22 batch has merged.
- **FU-4** — The unknown-kind fallback at `turn_engine.py:1632-1633` `json.dumps`es an entire note
  into the prompt. Unreachable today (three producers, three known kinds — §6) and made safe by C1's
  room check, but a fourth kind would still get a raw JSON dump where the two known kinds get shaped
  prose (`:1622-1631`). A `_log.warning` on the unknown branch would surface the omission at the
  moment a kind is added, instead of in a prompt.
- **FU-5** — `drive_approver_turn` still has no abstain signal: after C3 it can *report* that no vote
  was cast, but the gate has no way to learn it and still waits out `timeout_seconds`
  (`approval_service.py:241-267`). `approvals.py:109` already records this as FU-5 of its own work.
  C3 makes the condition observable, which is the prerequisite for acting on it — the natural next
  step is letting a genuinely-abstaining approver settle its slot early.
- **FU-6** — `_settle_pending_approvals` (`turn_engine.py`) calls `OrchestrationFacade.get_approval`
  once per unvoted approval note in a loop rather than batching. Flagged by the Definition of Done's
  quality gate (Info-level, not blocking): realistic cardinality is 1-2 notes per turn, so the N+1
  shape has negligible practical cost today. If a future change raises that cardinality
  (e.g. an agent bound to many concurrent gates), add a facade method that accepts a list of ids.
</content>
