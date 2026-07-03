---
type: audit
status: draft
created: 2026-07-03
requirements: [R13.16, R13.20, R13.24, R15.05b, R15.02]
---

# Conversation Vertical — Functional Bug Audit

Area: backend `contexts/conversation` (+ chatroom WS endpoint, wakeup/silence path in
`orchestration`) and frontend `slices/conversation`. Quick sweep, three lenses
(concurrency/async, isolation-as-correctness, event/notification flow). Every candidate
below was put through an independent adversarial verification pass whose job was to
refute it; only survivors are listed, with severity re-assessed by the verifier (not the
finder).

## Coverage and boundaries

**Verified clean** (checked, no defect — recorded so this list isn't read as
"everything else is unexamined"):
- Cross-room read paths other than the message cursor: permalink, attachment download,
  export download, search, observations all correctly re-derive room scope from the
  row's own `chatroom_id`.
- Guest enrollment is room-scoped; retention purge is intentionally platform-wide and
  groups audit by `chatroom_id` correctly.
- Observation events are correctly emitted off-room on the user channel (the recent
  observer-wave fix holds).
- Per-room WS channel subscription makes `message.updated`/`message.deleted` cross-room
  bleed structurally impossible.

**Not covered** (out of this sweep's depth/scope): the three remaining lenses (state &
lifecycle, boundary inputs, error paths) were not run; TUS upload internals, GraphRAG,
and the agent turn engine were only read at their conversation-facing emit sites.

---

## F-1: Silence-trigger wakeup fires into an empty room after an unclean disconnect

- **Severity**: major
- **Verdict**: confirmed (all six refutation avenues failed)
- **Evidence**: `backend/contexts/conversation/infrastructure/presence.py:180-181`
  (`scrub_stale_presence` drops the roster member via raw `SREM`, never calls
  `evaluate_presence_change`); `backend/app/api/ws/chatroom.py:130-142` (clean-close
  path *does* call it); `backend/contexts/orchestration/application/wakeup_service.py:200-201`
  (stale `is_silence_active` gate) and `:217-220` (roster re-check sits inside
  `if not cfg.allow_self_open:`, so it is skipped when `allow_self_open=true`);
  `wakeup_state.py:143-144` (`set_silence_active(...,False)` reachable only via the clean
  path).
- **Failure scenario**: A room has exactly one live user plus a bound agent with
  `silence_minutes.enabled=true` and `allow_self_open=true`; `is_silence_active` is set.
  The user's browser crashes with no WS close frame, so `evaluate_presence_change(has_live_users=False)`
  never runs and the flag stays true. After ~150s the conns key lapses; the retention
  cron's `scrub_stale_presence` removes the orphan from the roster but leaves the silence
  state untouched. The wakeup evaluator then passes the stale `is_silence_active` gate,
  skips the roster re-check (because `allow_self_open`), and fires — the agent posts into
  an empty room and spends the user's Vault-encrypted provider key. Bounded by
  `autostop_rounds`, once per silence window until autostop. `allow_self_open=false`
  agents are incidentally shielded because their re-check reads the now-empty roster.
- **Blast radius**: any room whose last member drops uncleanly with a self-opening,
  silence-enabled agent bound. Cost impact (burns BYO provider keys), behavior asymmetric
  on disconnect cleanliness.
- **Intent source**: [R15.05b] ("when the live-user set becomes empty, the silence timer
  pauses"), [R15.02].
- **Fix direction**: have `scrub_stale_presence` (or its retention wrapper) route through
  `evaluate_presence_change` when a removal empties the roster, so silence state is reset
  on unclean disconnects the same as clean ones; independently, consider making
  `evaluate_silence_trigger`'s liveness re-check unconditional rather than gated on
  `allow_self_open`.

## F-2: Deleted message can be resurrected by an in-flight replay delta

- **Severity**: minor-to-moderate (moderate impact, low likelihood)
- **Verdict**: confirmed (mechanism corrected during verification)
- **Evidence**: `frontend/src/slices/conversation/composables/useChatroomSocket.ts:100-106`
  (`applyMessageCreated` appends with only an in-cache id dedup, no deleted-state check),
  `:165-173` (`message.deleted` handler filters cache but does not bump
  `replayGeneration`), `:75-78` (generation bumped only by `replayDelta` itself);
  backend delete is a hard delete and `list(since=)` filters `deleted_at IS NULL`
  (`message_repo.py:87-90`).
- **Failure scenario**: Cursor at C; message M (newer than C) is created →
  `message.created` triggers `replayDelta` GET `since=C`; the backend SELECT reads M while
  still live; M is then hard-deleted and its `message.deleted` WS frame reaches the client
  *before* the delta HTTP response (a no-op filter, M not yet cached); the delta resolves
  returning [M] and `applyMessageCreated` appends M for the first time. M stays visible
  until a refetch/reload. No tombstone/deleted-id guard exists.
- **Blast radius**: any viewer during a create-then-immediate-delete within one delta
  round-trip; surfaces explicitly deleted (moderation/privacy) content.
- **Intent source**: [R13.16], [R13.24].
- **Fix direction**: keep a short-lived tombstone set of recently deleted ids and have
  `applyMessageCreated` skip any id in it; or bump `replayGeneration` on `message.deleted`
  so an older in-flight delta is discarded.

## F-3: Message list cursor anchor is not scoped by chatroom_id

- **Severity**: minor-to-moderate (correctness/robustness; NOT a cross-room leak)
- **Verdict**: confirmed by two independent verifiers; the finder's "leak" framing was
  refuted.
- **Evidence**: `backend/contexts/conversation/infrastructure/repositories/message_repo.py:96-98`
  (before) and `:116-118` (since) resolve the anchor on `messages.c.id == cursor` with no
  `chatroom_id` predicate; the outer page query *is* room-scoped (`:88`). Contrast the
  fixed sibling `observation_repo.py:105-113` (scoped, with a comment stating a foreign-room
  id "must 404 like any other missing cursor") pinned by
  `test_observer_agents.py:852`. No upstream guard (`messages.py`, facade, `access.py`
  only validate the room, pass the cursor through); no message-cursor test exists.
- **Failure scenario**: A member of rooms A and B calls
  `GET /api/chatrooms/{A}/messages?since=<id from B>`. The anchor resolves against the
  room-B row (so the intended not-found → 422 never fires) and room A's window is anchored
  to room B's `created_at`/`id`. The outer filter keeps every returned row in room A —
  **no cross-room data is exposed** — but the delta window is wrong, so room-A messages
  between the true and foreign anchor positions can be skipped or re-returned. Triggers
  only when a client supplies a foreign/wrong cursor.
- **Blast radius**: incorrect pagination for a cross-member using a wrong cursor; the
  unfixed twin of the shipped observation fix (parity gap).
- **Intent source**: [R13.20], internal consistency with commit 604953d.
- **Fix direction**: AND `chatroom_id == chatroom_id` into both anchor lookups, mirroring
  `observation_repo.py`; add the message-cursor regression test that the observation side
  already has.

## F-4: `clearTyping` references an undefined `typing`, throwing at runtime

- **Severity**: minor
- **Verdict**: confirmed (verified directly against source)
- **Evidence**: `frontend/src/slices/conversation/stores/conversation.ts:30` declares the
  field `typingUsers`; `:69` and `:71` read/write `typing.value` — an identifier declared
  nowhere in the module or project (verified: the only other `typing` occurrences are the
  `typing.start`/`.stop` string literals and CSS classes). Sole caller
  `useChatroomSocket.ts:94` inside `resyncPresence`, whose try/catch (`:91-97`) swallows
  the `ReferenceError`.
- **Failure scenario**: A sees "B is typing…"; A's socket drops and B's `typing.stop` is
  lost during the outage; A reconnects → `resyncPresence()` runs `setPresence`
  (succeeds), then `clearTyping` throws and is swallowed → the stale indicator is never
  cleared. Self-heals when B next types, leaves, or A unmounts the room.
- **Blast radius**: cosmetic stale typing indicator after a reconnect that missed a
  `typing.stop`. See FU-1 — this shipped because the typecheck gate is inert; a working
  gate would reject `typing.value` as TS2304.
- **Intent source**: internal consistency (R13.20 reconnect resync).
- **Fix direction**: rename both references to `typingUsers`. (Trivial; bundle with the
  FU-1 gate fix so the class of defect is caught mechanically thereafter.)

## F-5: `approval.requested` room-channel payload omits `workflow_run_id`

- **Severity**: minor (latent/inert — no live impact)
- **Verdict**: plausible; real field mismatch but currently unconsumed. The finder's claim
  that the workflow-channel emit carries the run id was itself wrong (neither payload body
  carries it; it lives only in the workflow channel name).
- **Evidence**: `backend/contexts/orchestration/application/approval_service.py:99-109`
  (room payload has no `workflow_run_id`); `useChatroomSocket.ts:234` reads
  `ev.workflow_run_id ?? ''`, so the stored `ApprovalWithVotes.workflow_run_id` is always
  `''`. No `.vue`/store consumer reads the field today.
- **Failure scenario**: none live; a future consumer of `approval.workflow_run_id` in the
  chatroom context would silently read `''`.
- **Blast radius**: latent only.
- **Intent source**: [R13.19] approval contract, [R15.10] approvals tied to a run.
- **Fix direction**: include `workflow_run_id` in the room-channel payload, or drop the
  field from the frontend type if the chatroom card genuinely doesn't need it.

---

## Follow-ups (out of the audit's functional scope, but surfaced)

- **FU-1 (important): the frontend typecheck gate checks nothing.** `pnpm typecheck` runs
  `vue-tsc --noEmit` against a solution-style `tsconfig.json` (`"files": []`, only
  `references`); without `--build`/`-b` this type-checks zero files and always exits 0.
  Proven empirically: `vue-tsc --build --noEmit` surfaces a flood of real pre-existing
  errors (`src/app/router.ts:58`, `src/shared/api-client/core/request.ts:7`,
  `src/shared/composables/useFocusTrap.ts`, `useToast.ts`, and more). This is the root
  cause that let F-4 ship and defeats the "type coverage >= 95%" gate in
  `frontend/CLAUDE.md`. Fixing the script to `vue-tsc --build --noEmit` will fail CI until
  the backlog of existing errors is cleared — treat as its own remediation task, not a
  one-line flip. Route to check-quality / a dedicated tooling task.
- **FU-2**: `message_repo` before/since anchor also omits `deleted_at IS NULL`; a
  soft-deleted cursor still resolves as a valid anchor (minor; fold into F-3's fix).
- **FU-3 (latent)**: `MessageService.get(message_id)` / `ConversationFacade.get_message`
  are globally unscoped; safe only because every current caller re-derives access from the
  returned row's `chatroom_id`. A future caller that trusts the row would leak cross-room —
  worth a scoping guard or a docstring contract.
- **FU-4 (latent)**: `observation_service.release` does `mark_released` CAS → message
  create → `mark_release_message` as separate awaited steps; cancellation between the CAS
  and the insert leaves the observation flagged released with no room message and no
  compensation.
- **FU-5 (minor)**: released-observation `message.created` hardcodes `sender_id: None`
  even for an agent-authored message, so the eager `clearAgentError` branch never clears
  that agent's error badge on release (recovered only via REST replay).

## Hand-off

Recommended triage order: F-1 (major, cost + correctness) → F-3 (parity with a fix the
team already shipped, cheap) → F-2 → F-4 (fold with FU-1) → F-5. FU-1 is arguably the
highest-leverage item overall but belongs to a tooling task, not a conversation bugfix.
For each finding you select, run `/spec` in bugfix mode — the F-n entry pre-fills
Observed vs Expected, evidence, and reproduction.
