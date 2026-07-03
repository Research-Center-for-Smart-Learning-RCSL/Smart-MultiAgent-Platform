---
type: bugfix
status: draft
created: 2026-07-03
requirements: [R28.02, R28.04, R28.07, R28.08, R28.09, R28.10, R28.12, R28.13]
---

# Observer Agents — Backend Fix Batch (O-1..O-9)

Fixes the backend findings of the observer-agents audit
(`docs/audits/2026-07-03-observer-agents-audit/findings.md`): confirmed defects F-1 and
F-2, plus the user-ruled design tensions P-1, P-2, P-4, P-5, P-6, P-7 and the minor P-9.
Batched like the precedent conversation-bugfix dossier; each item keeps its own root
cause, fix, regression test, and AC group (O-1..O-9) so /build can commit them as
independent fix/test pairs.

## 1. Summary

Two confirmed defects: a private release that pushes observation content into Redis
before the DB transaction commits (O-1, major — phantom/double delivery), and a
role-blind wake-up path that both suppresses observer turns in empty rooms and leaks the
observer's identity to non-creator project owners via a bell notification (O-2, major).
Seven ruled changes: a separate observer autostop cap (O-3), splitting benign skips from
hard failures on the creator event channel (O-4), creator-anchoring the authority model —
observer unbind (O-5), disclosure toggle for demoted creators (O-6), membership-fresh
creator resolution (O-7) — hiding observer DTO fields from guests (O-8), and rejecting
whitespace-only content overrides (O-9).

## 2. Observed vs Expected

**O-1 (F-1) — private release pushes content pre-commit**
- Observed: `release()` RPUSHes the released note into each target's `pending_notify`
  queue before the handler commits
  (`backend/contexts/conversation/application/observation_service.py:192-202`;
  commit at `backend/app/api/v1/observations.py:144`, whose comment claims
  "Durable-commit before any dispatch"). Commit failure → DB says unreleased, agents
  already hold the content; retry double-delivers. A partial push failure fails the
  whole release after some targets got the note — the opposite durability contract from
  the room path (`observations.py:170-212`, post-commit best-effort).
- Expected: [R28.07][R28.08] and `docs/observer-agents/A-backend.md` §A.9 — all
  external side effects post-commit, best-effort; single-shot delivery.

**O-2 (F-2) — role-blind presence gate suppresses observers and leaks identity**
- Observed: observer bindings flow role-blind into the wake path
  (`backend/contexts/conversation/application/triggers.py:36-40` has no role filter →
  `backend/contexts/orchestration/application/wakeup_service.py:96-108`). In an empty
  room with `allow_self_open=false` (the default,
  `backend/contexts/orchestration/domain/models.py:129,156`), the every_n branch calls
  `_notify_wakeup_gated` (`wakeup_service.py:105`), which bells every project OWNER
  naming the agent (`:147-151`, metadata `agent_id` at `:159`) — non-creators per
  R28.02 — and skips the observer turn. The silence path has the same suppression
  without the bell: `evaluate_silence_trigger` returns False for empty rooms
  (`wakeup_service.py:200-201, 221-224`) and `on_presence_changed` pauses silence
  role-blind on empty (`:243-246`), so observers also never observe silent/empty rooms
  via silence triggers.
- Expected: [R28.09] (which agent observes is never disclosed to non-creators),
  [R28.10], [R28.04]. Observer output never enters the room, so the "don't reply into
  an empty room" presence rationale does not apply: observers wake regardless of
  presence and never trigger the gated-wakeup bell.

**O-3 (P-1) — observers share the normal autostop cap**
- Observed: observer turns bump autostop (`backend/app/workers/tasks/orchestration.py:154-155`)
  and the gate applies to observer triggers (`:104`), reset only by user messages
  (`wakeup_service.py:93-94`); in an agent-only exchange the observer stops after
  `autostop_rounds` (default 5, `models.py:110`).
- Expected: ruling Q-1 — a separate, higher observer cap so observers keep watching
  long autonomous exchanges while still bounded.

**O-4 (P-2) — benign skips and hard failures share `observation.failed`**
- Observed: `no_input`/`empty_reply` benign skips emit the same event and key as hard
  failures (`backend/contexts/agents/application/runtime/turn_engine.py:1004-1006,
  1056-1058` vs `:836-838, 854-856, 1149-1151`).
- Expected: ruling Q-2 — benign skips emit a distinct `observation.skipped` event so
  the creator UI can distinguish (unblocks frontend F-6).

**O-5 (P-4) — non-creator moderator can unbind an observer**
- Observed: `remove_chatroom_agent` is capability-gated only
  (`backend/app/api/v1/chatrooms.py:442-468`, `_require_project_cap` at `:455-460`),
  asymmetric with bind (`:398-402`) and role change (`:427-428`) which are
  creator-gated for observers.
- Expected: ruling Q-3 — removing an observer binding is creator-gated like the other
  observer binding operations.

**O-6 (P-5) — demoted creator cannot toggle disclosure**
- Observed: `patch_chatroom` runs `_require_project_cap` unconditionally before the
  per-field creator gate (`chatrooms.py:284-295`), so a creator demoted below project
  owner 403s before reaching `ensure_room_creator`.
- Expected: [R28.09] "only the creator can change it" — creator authority suffices for
  a disclosure-only patch, independent of `RESOURCE_CREATE_EDIT`.

**O-7 (P-6) — creator authority survives losing project membership**
- Observed: `is_room_creator` matches `created_by_user_id` without consulting current
  roles (`backend/contexts/conversation/application/access.py:148-155`); a user removed
  from the project (empty roles, not a guest) retains observation read/release/delete
  and role management.
- Expected: ruling Q-3 — creator authority requires current project/org membership.

**O-8 (P-7) — guests receive observer DTO fields**
- Observed: `read_chatroom` admits pure guests (`chatrooms.py:263-268`) and `_to_out`
  always serializes `created_by_user_id`, `disclose_observers`, `observers_present`
  (`chatrooms.py:115-130`); none are dropped for guests.
- Expected: ruling Q-4 / [R28.02] — guests are denied all observer surfaces including
  these fields.

**O-9 (P-9) — whitespace-only content override accepted**
- Observed: `content_override` uses `Field(min_length=1)`
  (`backend/app/api/v1/observations.py:52`); `"   "` becomes the released message body
  verbatim (`observation_service.py:138`, `:174`).
- Expected: an override that is empty after strip is rejected (422).

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | P-1: exempt observers from autostop, separate cap, or status quo? | Separate observer cap | User ruling this session; bounded cost with better coverage; default higher than normal agents. |
| Q-2 | P-2: split benign skips server-side or frontend-only mapping? | Backend split (`observation.skipped`) + frontend renders kinds | User ruling; frontend-only mapping would drift as kinds evolve. |
| Q-3 | P-4/P-5/P-6: authority model convergence? | Fully creator-anchored: unbind creator-gated, disclosure toggle capability-independent for the creator, creator requires current membership | User ruling. |
| Q-4 | P-7: hide observer fields from guests or treat the indicator as universal transparency? | Hide from guests | User ruling; fail-closed for the least-trusted reader. |
| Q-5 | One dossier or split backend/frontend? | Two dossiers (this + `2026-07-03-observer-frontend-fixes`) | Decided under delegation: different test gates, independent revert paths; mirrors the phase A/B split. |
| Q-6 | O-8 implementation: omit fields (schema change) or neutral values? | Neutral values — guests get `created_by_user_id=null`, `disclose_observers=false`, `observers_present=false` | Decided under delegation: fail-closed with no OpenAPI/schema ripple to the frontend; indistinguishable from a genuinely undisclosed room, so no existence oracle. |
| Q-7 | O-2 scope: every_n gate only, or also the silence path? | Both | Same defect class (presence suppression of out-of-band output), found during fix-design investigation; fixing one trigger and not the other leaves the coverage gap. |

## 4. Reproduction

- **O-1**: release an unreleased observation with `{target:"agents", agent_ids:[A]}`
  while forcing `db.commit()` at `observations.py:144` to fail (e.g. kill the
  connection); A's `a2a:pending_notify:{A}` Redis list holds the note, the observation
  row has `released_at IS NULL`; retry → A holds two notes.
- **O-2**: room with creator Alice, observer with default wakeup config
  (`every_n_messages` enabled, `allow_self_open=false`); all users close their tabs;
  let n messages accumulate (e.g. another agent's autonomous replies). Every project
  owner receives the AGENT_WAKEUP_GATED bell naming the observer; no observation is
  created. Silence variant: enable `silence_minutes` on the observer, empty the room —
  the trigger never fires (`wakeup_service.py:221-224`).
- **O-3**: room with two normal agents ping-ponging and one observer,
  `autostop_rounds=5`; after 5 observations the worker returns `skipped:autostop` for
  every observer wake until a human posts.
- **O-4**: make an observer turn return no usable input (`no_input`); the creator
  channel receives `observation.failed` with `kind=no_input`, indistinguishable from a
  provider failure.
- **O-5**: as a project owner who is not the room creator, `DELETE
  /chatrooms/{id}/agents/{observer_id}` → 204.
- **O-6**: creator of room R is demoted from project owner to member; `PATCH
  /chatrooms/R {"disclose_observers": false}` → 403 (capability gate).
- **O-7**: creator of room R is removed from the project entirely; `GET
  /chatrooms/R/observations` still returns rows.
- **O-8**: with `allow_guest_links=true` and one disclosed observer bound, a guest
  `GET /chatrooms/{id}` sees `observers_present=true` and `created_by_user_id`.
- **O-9**: release with `{"target":"room", "content_override":"   "}` → 200; the room
  message body is three spaces.

## 5. Root Cause Analysis

- **O-1**: the push loop was placed inside `release()` (service layer, pre-commit)
  instead of the post-commit dispatcher; `ReleaseResult`
  (`observation_service.py:46-52`) carries `target_agent_ids` but not the resolved
  content, so `_dispatch_release` could not perform the push without a service change —
  the shortcut became the bug.
- **O-2**: `list_bound_agent_ids` (`triggers.py:36-40`) discards the role that
  `ChatroomAgentRepository` already returns, and
  `WakeupService.on_message_created`/`evaluate_silence_trigger`/`on_presence_changed`
  (`wakeup_service.py:96-108, 179-226, 232-247`) were written for normal agents whose
  output lands in the room; `A-backend.md` §A.5's "observers wake like any bound agent"
  was never reconciled with the presence gate or the gated-notice bell.
- **O-3**: the autostop gate (`orchestration.py:91-108`) runs in the worker before any
  role resolution (role is resolved later in `turn_engine.py:815`); there is no
  role-differentiated limit in `WakeupConfig` (`models.py:106-112`).
- **O-4**: `_emit_observation_event` (`turn_engine.py:1185-1211`) has a single event
  name; the benign-skip sites reused it, and R28.13 named no skip event — the contract
  gap propagated into the implementation and its tests.
- **O-5**: `remove_chatroom_agent` predates the observer role and was never given the
  role-conditional creator gate its bind/patch siblings received.
- **O-6**: gate ordering — the unconditional `_require_project_cap` at `chatrooms.py:285-290`
  precedes the per-field creator gate at `:291-295`.
- **O-7**: `is_room_creator`'s `created_by_user_id` branch (`access.py:153-154`) never
  consults `access.roles` (populated by `resolve_room_access` at `access.py:80-83`).
- **O-8**: `_to_out` (`chatrooms.py:115-130`) has no viewer-awareness, and
  `read_chatroom` discards its `is_guest` flag after the read gate (`:263-268`).
- **O-9**: missing strip validation on `ReleaseIn.content_override` (`observations.py:52`).

## 6. Blast Radius and Sibling Suspects

- **O-1**: every private release under DB failure; content delivered for a release the
  DB denies. Sibling checked: the room path already defers correctly
  (`observations.py:183-202`) — cleared.
- **O-2**: observer coverage of unattended rooms (the core use case) and observer
  anonymity. Siblings: mention path is role-aware (`triggers.py:102-103`, cleared);
  `_notify_wakeup_gated` has exactly one call site (`wakeup_service.py:105`, cleared);
  silence path confirmed affected → folded in (Q-7).
- **O-3**: any observer in agent-heavy rooms. Sibling: the domain evaluator also reads
  autostop (`wakeup_service.py:212-214`) — the fix must keep worker and evaluator
  consistent.
- **O-4**: creator event consumers (frontend F-6 depends on this). Sibling: room-path
  `agent.finished` error-vs-reason contract is unaffected (observer branch only).
- **O-5/O-6/O-7**: observer setup integrity and R28.09 control. Sibling checked:
  `add_chatroom_agent`/`patch_chatroom_agent_role` already creator-gated
  (`chatrooms.py:398-402, 427-428`, cleared); observation endpoints all call
  `_require_creator` (`observations.py:93-101`, cleared — O-7's membership guard
  applies to all of them through `is_room_creator`).
- **O-8**: guest-readable rooms only. Sibling: `_to_out` callers — `list_chatrooms`
  (`chatrooms.py:214`) requires project roles (`:199-206`), pure guests excluded;
  `create_chatroom`/`patch_chatroom` not guest-reachable. Cleared, cite in tests.
- **O-9**: cosmetic release content. Sibling: `messages.py` send path has its own
  content rules (`messages.py:83-87`) — out of scope.

## 7. Fix Design

- **O-1**: add the resolved content to `ReleaseResult` (e.g. `content: str`, set where
  `targets` is resolved); delete the push loop from `release()`
  (`observation_service.py:192-202`); in `_dispatch_release`, add the private-target
  branch: for each `result.target_agent_ids`, `pending_notify.push` wrapped in
  per-target try/except logging (mirroring the room path `observations.py:183-194`),
  before the existing `result.wake` enqueue loop. Importing
  `contexts.orchestration.infrastructure.pending_notify` in the app layer follows the
  observed convention (`app/api/v1/orchestration.py:32`,
  `app/workers/tasks/orchestration.py:61`); alternatively keep a
  `service.push_release_notes(result)` helper so the orchestration-infra import stays
  in the service — /build picks whichever reads cleaner, both are precedented.
- **O-2**: role-awareness enters at the conversation edge, which already owns roles:
  - `triggers.py`: fetch rows once with roles; pass
    `observer_agent_ids: set[uuid.UUID]` through
    `OrchestrationFacade.on_message_created` (`facade.py:119-132`) into
    `WakeupService.on_message_created` (new keyword param, default empty — the three
    production callers `messages.py:273-275`, `observations.py:196`,
    `turn_engine.py:1254-1260` go through `evaluate_message_wakeups` and need no
    change; test doubles updated).
  - In the every_n branch (`wakeup_service.py:96-108`): for observer ids, skip the
    presence check and `_notify_wakeup_gated` entirely — fire when the counter hits.
  - Silence path: `evaluate_presence_change` (`triggers.py:115-133`) passes the same
    observer set so `on_presence_changed` (`wakeup_service.py:232-247`) does not pause
    silence for observers on empty rooms; the worker resolves the role before
    `evaluate_silence_trigger` (reusing `ChatroomAgentRepository.role_of`,
    `chatroom_repo.py:277-293`, needed by O-3 anyway) and passes `is_observer` so the
    empty-roster suppressions (`wakeup_service.py:200-201, 221-224`) are bypassed for
    observers.
- **O-3**: new field on `SilenceMinutesTrigger` (`models.py:106-112`):
  `observer_autostop_rounds: int = 50` (0 < value <= `AUTOSTOP_HARD_CAP`, parse-clamped
  like `:149`). In the worker (`orchestration.py:91-104`), resolve the binding role via
  `role_of` (shared with O-2's silence fix) and select the observer limit when the
  binding is an observer; mirror the same selection in the domain evaluator
  (`wakeup_service.py:212-214`).
- **O-4**: the two benign sites (`turn_engine.py:1004-1006, 1056-1058`) emit
  `observation.skipped` with the same payload shape (`chatroom_id`, `agent_id`,
  `kind: no_input | empty_reply`) via `_emit_observation_event`; hard sites unchanged.
  Update the two pinned tests (`test_observer_agents.py:622, :654`).
- **O-5**: in `remove_chatroom_agent`, resolve the binding role via
  `ChatroomAgentRepository.role_of` (or a `ConversationFacade` passthrough — match the
  handler's existing access pattern); if OBSERVER, `resolve_room_access` +
  `ensure_room_creator`, mirroring `add_chatroom_agent` (`chatrooms.py:398-402`).
  Unknown binding keeps the current behavior (idempotent remove path unchanged).
- **O-6**: in `patch_chatroom`, when `body.model_dump(exclude_unset=True).keys() ==
  {"disclose_observers"}`, skip `_require_project_cap` and require only
  `resolve_room_access` + `ensure_room_creator`; mixed patches keep the capability
  check first, then the existing per-field creator gate.
- **O-7**: in `is_room_creator` (`access.py:153-154`), the `created_by_user_id` branch
  becomes `bool(access.roles) and principal.user_id == room.created_by_user_id`. Admin
  bypass (`:148`) and the NULL-creator moderator fallback (`:155`, roles-based already)
  unchanged. `RoomAccess.roles` is the current project/org role set
  (`access.py:37-49, 80-83`).
- **O-8**: thread the guest flag into `_to_out` (new keyword, e.g.
  `viewer_is_pure_guest: bool = False`); when set, emit `created_by_user_id=None`,
  `disclose_observers=False`, `observers_present=False` (Q-6: neutral values, no schema
  change). `read_chatroom` computes `pure_guest = is_guest and not roles` from values
  it already has (`chatrooms.py:259-266`).
- **O-9**: `@field_validator("content_override", mode="before")` on `ReleaseIn` —
  strip; empty after strip → `ValueError` (422). Precedent `agents.py:64-68`.

## 8. Regression Test Plan

Failing tests first, in `backend/tests/unit/` (extend `test_observer_agents.py`,
`test_agent_trigger_wiring.py`, `test_wakeup_service.py` siblings):

- **O-1**: spy `pending_notify.push`; assert `ObservationService.release(target=agents)`
  performs zero pushes; assert `_dispatch_release` pushes once per target with the
  override-resolved content; assert one target's push failure does not prevent the
  others (best-effort).
- **O-2**: (a) every_n fires for an observer with empty presence and
  `allow_self_open=false`, and `_notify_wakeup_gated` is never called for observer
  bindings; (b) normal agents keep the current gate+bell behavior; (c) silence: an
  observer's `evaluate_silence_trigger` fires in an empty room; `on_presence_changed`
  on empty does not pause observer silence state.
- **O-3**: observer binding at `autostop_count >= autostop_rounds` but below
  `observer_autostop_rounds` still wakes; at/above the observer cap it skips; normal
  agents unchanged.
- **O-4**: amend `test_observer_turn_no_input_emits_observation_failed` (`:622`) and
  `..._empty_reply_...` (`:654`) to assert `observation.skipped` with the right kind;
  add an assertion that a hard failure still emits `observation.failed`.
- **O-5**: non-creator moderator DELETE of an observer binding → 403; creator → 204;
  non-creator moderator DELETE of a normal binding → still 204.
- **O-6**: demoted creator PATCHing only `disclose_observers` → 200; demoted creator
  PATCHing `name` → 403; non-creator owner PATCHing `disclose_observers` → 403.
- **O-7**: ex-member creator fails `ensure_room_creator` (403 on observation list);
  current-member creator passes; admin bypass intact; NULL-creator moderator fallback
  intact.
- **O-8**: pure guest `GET /chatrooms/{id}` on a disclosed-observer room returns the
  neutral triple; a member still sees real values; `list_chatrooms` remains
  role-gated (guest excluded).
- **O-9**: `content_override="   "` → 422; `" x "` accepted as `"x"` (or rejected —
  match the validator's strip semantics).

## 9. Risks and Rollback

- **O-2** carries the design risk: a new parameter threads through three signatures and
  two trigger paths; a mistake could wake normal agents into empty rooms (cost impact)
  or stop waking them at all. Mitigate with the paired assertions in the O-2 tests
  (observer fires / normal gated). Rollback: revert the O-2 commits; the param defaults
  keep old call sites working.
- **O-3** adds a config field — parse must tolerate configs without it (default), per
  the N-1 migration policy. No DB migration needed (wakeup_config is JSON).
- **O-4** is a WS contract change for a surface with one consumer (creator panel);
  frontend dossier W-4 lands the consumer. Until then, benign skips simply stop showing
  as errors — acceptable interim.
- **O-6** relaxes a capability check for one field; the test matrix in §8 pins that
  only the disclosure-only shape bypasses it.
- **O-8** changes guest-visible DTO values, not the schema — no client regen needed.
- All items are independently revertible; keep per-item commits.

## 10. Acceptance Criteria

- [ ] AC-1 (O-1): release() performs no Redis push; `_dispatch_release` pushes
      post-commit, best-effort per target, with override-resolved content. [R28.07][R28.08]
- [ ] AC-2 (O-1): the O-1 regression tests fail before the fix and pass after.
- [ ] AC-3 (O-2): an observer's every_n trigger fires with empty presence and
      `allow_self_open=false`; `_notify_wakeup_gated` never fires for observer
      bindings; normal-agent gating and bell behavior unchanged. [R28.04][R28.09][R28.10]
- [ ] AC-4 (O-2): an observer's silence trigger fires in an empty room; observer
      silence state is not paused by room-emptied presence changes.
- [ ] AC-5 (O-3): observers use `observer_autostop_rounds` (default 50, hard-capped at
      100); normal agents keep `autostop_rounds`. [R28.12]
- [ ] AC-6 (O-4): `no_input`/`empty_reply` emit `observation.skipped`; hard failures
      keep `observation.failed`; the two pinned tests are amended. [R28.13]
- [ ] AC-7 (O-5): observer unbind requires creator; normal unbind keeps moderator
      semantics. [R28.02]
- [ ] AC-8 (O-6): a disclosure-only PATCH by the creator succeeds without
      `RESOURCE_CREATE_EDIT`; mixed patches keep the capability gate. [R28.09]
- [ ] AC-9 (O-7): creator authority requires current project/org membership; admin
      bypass and NULL-creator moderator fallback unchanged. [R28.02]
- [ ] AC-10 (O-8): pure guests receive the neutral observer-field triple; members and
      creators see real values. [R28.02][R28.09]
- [ ] AC-11 (O-9): whitespace-only `content_override` → 422.
- [ ] AC-12: full backend gate green (`pytest -q`, `ruff check . && ruff format
      --check .`, `mypy .`); `check-quality` on the diff shows no new
      Introduced-Critical/Warning; `check-security` runs (AuthZ surfaces touched) with
      no findings.

## 11. SRS Delta

Applied to `REQUIREMENTS.md` §28 on approval:

- **[R28.02]** (line ~1940): append — "Creator authority additionally requires current
  project or org membership: a `created_by_user_id` match no longer grants observer
  surfaces once the user holds no role in the project. Unbinding an observer is
  creator-gated like binding and role change. Guests receive neutral values
  (`created_by_user_id=null`, `disclose_observers=false`, `observers_present=false`)
  in the chatroom DTO."
- **[R28.04]** (line ~1935): append — "Observer wake-ups are exempt from the
  empty-room presence gate on both triggers (observer output is out-of-band), and the
  presence-gated wake-up notification is never sent for observer bindings."
- **[R28.09]** (line ~1958): append — "The creator may change `disclose_observers`
  without holding `RESOURCE_CREATE_EDIT`; creator authority is sufficient for a
  disclosure-only patch."
- **[R28.12]** (line ~1946): amend the autostop clause — observers use a separate
  `observer_autostop_rounds` limit (default 50, hard cap 100) in place of
  `autostop_rounds`.
- **[R28.13]** (line ~1947): amend the event list — "`observation.started / created /
  skipped / failed / released`; `observation.skipped` carries benign kinds
  (`no_input`, `empty_reply`), `observation.failed` carries error kinds."

## 12. Deviation Log

Appended by /build.

## 13. Follow-ups

- FU-1: `read_chatroom` re-implements role/guest resolution inline instead of
  `resolve_room_access` (`chatrooms.py:250-272`) — harmonize later.
- FU-2: `_MAX_CONTENT_MD`/`_MAX_TARGET_IDS` duplicated between `observations.py:44-45`
  and `messages.py:64`.
- FU-3: `recipient_user_id` costs a DB round-trip per WS emit
  (`observation_service.py:97-105`) — acceptable v1, candidate for caching.
- FU-4: backend `CLAUDE.md` facade-only rule vs observed app-layer cross-context
  imports (`app/api/v1/orchestration.py:32` et al.) — doc/code drift to resolve.
- FU-5 (carried): `ChatroomAgentRepository.list` has no ORDER BY under offset/limit
  (`chatroom_repo.py:240-255`).
