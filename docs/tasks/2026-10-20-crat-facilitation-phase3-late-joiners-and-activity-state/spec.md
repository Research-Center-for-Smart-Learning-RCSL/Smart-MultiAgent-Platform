---
type: feature
status: implemented
created: 2026-10-20
requirements: [R13.72, R13.73, R13.74, R13.75, R13.79, R28.17, R30.15, R30.25, R15.01, R15.02, R15.03, R15.05, R15.05b, R15.06, R15.06a, R15.09, R15.25, R15.26, R28.01, R28.04, R28.09, R28.10, R30.15, R30.21, R30.22, R30.37, R30.38, R30.40, R30.41, R30.42, R30.43, R30.44, R30.45]
depends_on: [2026-10-16-crat-facilitation-phase2-classroom-context-and-cadence, 2026-10-19-peer-deliberate-errors]
---

# Classroom facilitation phase 3: late joiners and activity state

## 1. Summary

Teacher agents in the three classroom course packs cannot see two things a human facilitator
relies on: that a student has just arrived, and where the class activity stands. This feature
gives every room agent a platform-computed view of both. A student's arrival becomes an
agent-only room notice and, for agents whose designer enables it, a debounced `on_join`
wake-up. Every room turn carries a bounded activity-state block: whether an activity is
running, which one and since when, and for group activities each group's open proposal with
its vote counts, recent proposal outcomes and which groups have submitted. Activation start
and end become a room-visible system message. The CRAT, character-building and
object-reinterpretation packs are rewritten to use these facts: the teacher re-orients a late
joiner in one or two sentences, and form and submission guidance follows the actual activity
state instead of being stated unconditionally.

Both gaps were observed on staging on 2026-10-20 in `研究室測試房-1020` and `功能測試房-1020`:
a joiner received no orientation, and the CRAT teacher told students the answer form was on
the right before any activity had started.

A third gap, phase 2 FU-16, is folded in (Q-12): the CRAT teacher cannot confirm a matching
submission because the submission digest agents receive names the answered fields but never
the submitted target character. An activity type may now declare a few short fields whose
values are quoted onto its activity rows, labelled as the participant's own words (M6).

**Approval state:** approved 2026-10-20 as a whole (Q-1 to Q-11, goals, non-goals, design,
ACs). The SRS Delta in §13 was applied to `REQUIREMENTS.md` at approval. The FU-16 amendment
(Q-12, §6.6, AC-21 to AC-25) was approved 2026-10-23 and its [R30.15] change applied then.
Building waits on both `depends_on` entries.

## 2. Goals and Non-goals

**Goals**

- An agent can tell, from server state, that a specific participant connected to the room
  recently, and the teacher agent can be woken by that arrival without a human typing.
- Any participant's arrival re-arms the silence clock, as [R15.02] already requires.
- Every room agent sees, on every turn, whether an activity is running, its name, mode and
  start time, which groups have submitted in the current round, and for group activities
  each group's open proposal with approval, rejection and undecided counts against the
  required approvals, plus proposals resolved in the current round.
- Everyone in the room, including late joiners and guests, sees when an activity starts and
  ends.
- The three course packs re-orient late joiners and guide discussion, activation,
  proposal, vote and submission using only facts the platform supplies.
- The CRAT teacher sees each submission's target character, marked as the participant's own
  words, so it can confirm a matching submission itself (phase 2 AC-24, FU-16).

**Non-goals**

- Showing agents who voted which way, a proposal's payload before it is submitted, or any
  per-person dissent. [R30.42] is relaxed to counts and outcomes only (Q-5).
- Attendance, lateness, arrival order, or whole-class completion claims. A join notice is a
  connection fact, not a roll call ([R13.74]).
- A human-visible "X joined" message, a join timestamp in the UI, or any change to the
  participant list UI.
- An example-pack upgrade service or automatic overwrite of installed agents (phase 2
  non-goal, kept by Q-3). Existing installs are re-synced by hand.
- An autonomous phase scheduler, deadline timing, or ending an activity without the human
  teacher's confirmation (phase 2 Q-4 stays in force).
- `on_join` for observer bindings, agent self-toggling of `on_join`, and soft bounds for it.
- Making the existing submission echo emit `message.created` (recorded as FU-1).
- Quoting long free-text fields (CRAT `reasoning`), quoting through MCP or webhook validators,
  changing the submission echo's content, or changing the one-marker-per-row rule the packs
  teach (Q-12).

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | How does an agent learn that someone joined? | **Confirmed 2026-10-20:** agent-only room notice plus an opt-in `on_join` wake trigger with a cooldown; the human UI is unchanged. | Notice alone leaves a newcomer waiting for an unrelated trigger. A visible "X joined" message reads as attendance and conflicts with the phase 2 non-goal. |
| Q-2 | How does an agent learn the activity state? | **Confirmed 2026-10-20:** a state block on every turn, plus a start/end notice. | The model rarely calls an optional status tool on its own; the observed failure was stating a form that did not exist. |
| Q-3 | How do installed example agents get the new prompts? | **Confirmed 2026-10-20:** update the pack JSON and re-sync installed agents by hand after deploy, using the documented procedure in `docs/examples/chinese-character-courses-adoption.md:99-123`. | Keeps the phase 2 non-goal against an upgrade service. |
| Q-4 | Does this depend on phase 2? | **Confirmed 2026-10-20:** yes, `depends_on: [2026-10-16-crat-facilitation-phase2-classroom-context-and-cadence]`. | Phase 2 is `in-progress` with live-validation ACs open (AC-14, AC-20 to AC-22, AC-24) against the current pack prompts; this dossier rewrites those prompts and edits `participant_facts.py`. Building after phase 2 closes keeps its classroom evidence tied to one prompt revision. |
| Q-5 | What may agents see of group proposals and votes? | **Confirmed 2026-10-20:** counts and outcomes. Per group: whether an open proposal exists, approvals, rejections and undecided against required approvals, and proposals rejected, withdrawn or expired in the current round. Never voter identities, the proposer, or the payload. | User choice. Requires amending [R30.42], which today forbids any proposal or vote on an agent surface (`REQUIREMENTS.md:2336`). Without it the CRAT teacher's D1 branch on a failed vote can never fire. Voter identity and payload stay out because they are the parts of R30.42 that protect individual students. |
| Q-6 | Who sees activation start and end? | **Confirmed 2026-10-20:** everyone, as a centred system divider. It does not name who started it, does not count toward `every_n_messages`, and does not reset autostop; it re-arms the silence clock. | Late joiners need it as much as agents. Not naming the starter avoids disclosing an observer that holds the activity grant ([R28.09]). Same wake-up treatment as the submission echo ([R15.02]). |
| Q-7 | How is join noise contained? | **Confirmed 2026-10-20:** one join per participant per room per 30 minutes; joins within one minute coalesce into one wake per agent. | User choice. A page refresh is a leave plus a join at the presence layer (`presence.py:109-134`), so a per-person window is required. Thirty minutes re-orients someone who left mid-lesson and came back. |
| Q-8 | Where is `on_join` configured? | **Confirmed 2026-10-20:** designer default on the agent's wake-up config, with a room override switch in the room wake-up settings. Not self-toggleable; not offered for observer bindings. | Matches the three-layer model in [R15.09] and the narrow room API in [R15.25]. |
| Q-9 | Does a join wake require an earlier message in the room? | **Recommended:** yes, unless `allow_self_open` is true. | [R15.05] already forbids speaking first in a room nobody has spoken in; with nothing said there is nothing to orient a newcomer to. |
| Q-11 | Does this depend on `2026-10-19-peer-deliberate-errors`? | **Confirmed 2026-10-20:** yes. | That dossier is in progress and edits the same six pack files (teacher `<步驟A1>` disclosure, peer `<刻意出錯>`, design packs). Rewriting the packs after it lands avoids two branches overwriting each other's prompt lines and pinned tests. |
| Q-12 | How does the CRAT teacher see the submitted target character (phase 2 FU-16)? | **Confirmed 2026-10-23:** the type's `filled_count_coverage` config declares `quote_fields`; each named field's value is quoted onto the row after the participant marker `—`, while the coverage text moves before the marker. Chosen over switching CRAT to `filled_count` (whose JSON fallback sorts `reasoning` first and can truncate the target out at 480 characters, and loses coverage) and over routing every result to the human teacher (which rewrites phase 2 AC-24). | The `::` marker promises agents its text "never contains" the participant's words (`activity_context_provider.py:97-103`), and five packs teach "one marker per row, the first one" (`*-room.json` teacher, peer and analyst prompts). Putting the coverage before the marker keeps both promises true without rewriting those prompts. |
| Q-10 | Does a join wake obey the autostop cap? | **Recommended:** yes, like every non-explicit trigger (`app/workers/tasks/orchestration.py:139-145`), and it does not reset autostop. | A join is not a message the room waits on. OQ-1 tracks whether a stalled teacher should still greet newcomers. |

## 4. Current State

**Presence and joins.** A WebSocket open calls `PresenceTracker.join`, which returns
`(first_connection_of_principal, roster_size)` (`backend/contexts/conversation/infrastructure/presence.py:109-134`).
`on_open` emits `presence.joined` when the first value is true but calls `_notify_presence`
only when `roster_size == 1` (`backend/app/api/ws/chatroom.py:282-299`). So only the first
person into an empty room re-arms the silence clock; any later joiner triggers nothing, which
contradicts [R15.02] (`REQUIREMENTS.md:898`, "a participant joining" is room activity).
`_notify_presence` reaches `WakeupService.on_users_present`, which only touches silence
timestamps (`backend/contexts/orchestration/application/wakeup_service.py:298-314`).
No join time is stored: presence keys hold ids only (`presence.py:46-47, 86-90`), and the only
`joined_at` is a registered guest's enrolment (`backend/contexts/conversation/infrastructure/tables.py:204`).
A refresh closes and reopens the socket, producing `presence.left` then `presence.joined`;
overlapping sockets report `added=False` (`backend/tests/unit/test_presence.py:127`).
No test covers the `roster_size == 1` gate in `chatroom.py`.

**Wake-up triggers.** `WakeupTriggers` holds exactly `every_n_messages`, `silence_minutes`
and `call_only` (`backend/contexts/orchestration/domain/models.py:246-250`); explicit triggers
are `mention` and `release` (`:243`). `from_dict` ignores unknown keys (`:261-325`), `is_inert`
considers only those three (`:364-369`), and the toggleable set is hardcoded (`:184, :190, :203`).
Room overrides cover only every-n and silence fields (`backend/contexts/orchestration/domain/room_wakeup.py:44-58, :207-214`).
The worker treats an unknown trigger as enabled and applies autostop to every non-explicit
trigger (`backend/app/workers/tasks/orchestration.py:121-145`). The turn's time line names a
wake reason only for known triggers (`backend/contexts/agents/application/runtime/turn_engine.py:583-588, :666-678`).
`claim_gated_notice` is the existing `SET NX EX` debounce (`backend/contexts/orchestration/infrastructure/wakeup_state.py:51-63`).

**Agent context.** Participant facts list connected/not connected and spoke/did not speak in
the window, with no arrival fact (`backend/contexts/agents/application/runtime/participant_facts.py:92-159`).
Agent-only notices already exist without persistence: gap notices are computed from row
timestamps and injected as `[Room notice]` user turns (`turn_engine.py:615-663`), with the
marker protected by `_defang_notice_marker` (`:523-529`).
Activity context is submissions only: `ActivityContextProvider.query` returns `None` when the
room has no submission rows (`backend/contexts/activities/application/activity_context_provider.py:110-144`),
and rows are not scoped to the current activation (`backend/contexts/activities/infrastructure/repositories/submission_repo.py:355-388`).
The provider is wired by direct import and called for normal and observer turns
(`turn_engine.py:35, :1519, :3167, :5190-5211`).

**Activation and proposals.** Start and end write audit rows and WebSocket events only; no
transcript row exists (`backend/contexts/activities/interfaces/broadcast.py:85-132`,
`backend/contexts/activities/application/activation_service.py:113-149, :171-199`). A repeated
start of the same type returns the existing activation (`activation_service.py:119-125`).
Proposals carry status open/accepted/rejected/withdrawn/expired, pinned voters and required
approvals (`backend/contexts/activities/domain/models.py:62-75, :341-391`); the repository can
list only open proposals for given groups (`backend/contexts/activities/infrastructure/repositories/proposal_repo.py:171-233`).
[R30.42] forbids any proposal or vote on an agent surface (`REQUIREMENTS.md:2336`), and the
service documents that agents never read proposals (`backend/contexts/activities/application/group_proposal_service.py:17-20, :539-542`).
System messages are written by `ConversationFacade.insert_system_message` (`backend/contexts/conversation/interfaces/facade.py:925-946`);
any non-observation system row renders to every participant as a centred divider
(`frontend/src/slices/conversation/components/ChatroomMessageBubble.vue:22-34`). The submission
echo re-arms silence through `note_room_activity` without counting toward every-n
(`facade.py:948-962`, `backend/contexts/conversation/application/triggers.py:155-179`).
Activity tools exist only for granted bindings; the only way an agent learns the state today is
a failed tool call (`backend/contexts/agents/application/runtime/activity_tools.py:69-110, :211-218, :320-321`).

**Packs.** All three room packs store each prompt as one JSON line: teacher at line 26, peer
at 48, analyst at 66 (`backend/contexts/agents/infrastructure/examples/packs/*-room.json`).
Unconditional form statements: CRAT `<步驟B1>` item 3 and `<步驟D1>` item 2; character
`<步驟A1>` and `<步驟B2>` item 2; object `<步驟B2>` item 2. CRAT `<步驟D1>` branches on a
failed consent vote the teacher cannot observe. CRAT `<活動控制>` rule 4 ("確認所有小組都已提交後才結束活動")
conflicts with phase 2 Q-4. The character and object peers stay silent "while the form is
open", a state they cannot observe; in the object course that also mutes the peer during the
D1 and D2 sharing it is meant to support. No prompt mentions newcomers. Packs are copied on
install and never updated (`backend/contexts/agents/application/example_service.py:175-297`).
Many prompt phrases are pinned by `backend/tests/unit/test_agent_example_packs.py`; none of the
form, vote or newcomer wording above is pinned.

**Submission digest (FU-16).** The CRAT type uses the in-process `filled_count_coverage`
validator with `expose_payload_to_agent: true` and `echo_includes_content: false`
(`backend/contexts/activities/infrastructure/examples/courses/chinese-character-creativity.json:7-61`).
That validator always sets `detail` to `N/M fields answered: <names>`
(`backend/app/plugins/activity_validators.py:132-173`), and `build_agent_digest` uses `detail`
instead of the payload dump whenever one is set (`backend/contexts/activities/domain/agent_digest.py:28-34`),
so no CRAT row ever carries the submitted target (phase 2 D-17). `list_recent_for_room` already
reads `payload` and `validator_kind` to decide `digest_is_computed`, and drops the payload
(`backend/contexts/activities/infrastructure/repositories/submission_repo.py:355-404`).
A computed digest renders after `::`, whose note says it "never contains" the participant's
words; a payload digest renders after `—`; a row carries one marker and it is the first
(`activity_context_provider.py:80-103, :345-354`). `_row_field` neutralises both markers in
values placed before the marker (`:281-326`). The coverage validator shares its config hooks
with `filled_count` so the two never disagree on a legal `min_filled` (`activity_validators.py:213-237`).
The CRAT teacher's `<步驟E1>` compares 「提交的目標字和題庫答案」, which today has nothing to compare.

## 5. Design

### Options considered

**Option A: prompt changes only.** Reword the packs. Cheap, but the teacher still cannot see an
arrival or the activity state, so wording can only become vaguer, not correct.

**Option B: read-only status tool plus join notice.** Agents call `get_activity_status` when
they want it. Less context per turn, but models rarely call optional tools, which is exactly the
observed failure; and a tool offered to every agent widens [R13.72]'s read-only set.

**Option C: platform facts in every turn, plus prompt changes (chosen).** Joins become an
agent-only notice and an opt-in trigger; activity state becomes a bounded system block;
activation start and end become a visible system message; packs consume the facts.

### Decision

Option C. The two failures were both "the agent could not know", so the fix is to make the
facts arrive without the agent asking. The cost is one more bounded system block per room turn
and a relaxation of [R30.42] to counts and outcomes, which the user accepted (Q-5). Given up:
a minimal-token design (Option B) and the stricter vote privacy of today's R30.42.

## 6. Detailed Changes

Implementation is sequenced into milestones, each committed and gated on its own tests:
M1 join tracking and the [R15.02] fix; M2 the `on_join` trigger; M3 activity state and
activation notices; M6 quoted submission fields (before M4, whose CRAT prompts consume it);
M4 pack prompts; M5 staging validation and manual re-sync.

### 6.1 Join tracking and the [R15.02] fix (M1)

- `chatroom.py` `on_open`: when `PresenceTracker.join` reports a first connection for the
  principal (`added`), call the presence-change path for any roster size, not only when the room
  was empty. This re-arms silence for every joiner.
- New conversation-side record of arrivals, a Redis sorted set per room
  (`ws:joins:{room_id}`, member = principal id, score = join time), written on `added`.
  A per-principal key `ws:join_seen:{room_id}:{principal_id}` with `SET NX EX 1800` decides
  whether this arrival is a new join (Q-7). Refreshes inside 30 minutes write nothing.
  The set is trimmed to the latest 50 members and expires after 6 hours.
- `ConversationFacade.recent_joins(room_id, since)` returns `(principal_id, joined_at)` pairs,
  bounded to 50, empty when Redis is unavailable (never an error).
- `turn_engine.py`: for room and observer turns, read recent joins within the transcript window
  and interleave them with gap notices as `[Room notice] [u:xxxxxxxx] Label connected to the room.`
  at their timestamp, using the same label resolution, defanging and token measurement as gap
  notices. Joins by the room creator are omitted. Headless turns receive none.
- `participant_facts.py`: a row whose principal joined within the window gains
  `joined in this window`. The header text keeps the existing "not an attendance list" wording.

### 6.2 `on_join` trigger (M2)

- `orchestration/domain/models.py`: `OnJoinTrigger(enabled: bool = False)`, added to
  `WakeupTriggers`, `from_dict`/`to_dict` and `is_inert`. Not in `ToggleableTrigger` or
  `SELF_TOGGLEABLE_TRIGGERS`. Coalescing window is a server constant of 60 seconds.
- `room_wakeup.py`: `on_join_enabled` joins `ROOM_FIELDS` (not `RUNTIME_FIELDS`) and is
  resolved in `resolve_room_wakeup`. The [R15.06a] "cannot silence both" rule is unchanged.
- `WakeupService.on_participant_joined(room_id, principal_id)`: for each normal, non-observer
  live binding whose effective config has `on_join` enabled and not `call_only`, and where the
  room has at least one message or `allow_self_open` is true (Q-9), claim
  `wakeup:join_batch:{agent}:{room}:{binding}` with `SET NX EX 60` and, if claimed, enqueue
  `wakeup_agent(..., trigger="on_join", binding_id=...)` deferred by 60 seconds. Joiners in the
  same minute share the one turn. Returns the targets for tests.
- Called from the same presence-change path as M1 when the join is new under Q-7.
- Worker: `on_join` is a non-explicit trigger; `still_on` checks it; autostop applies (Q-10).
  A completed turn counts a round as today.
- `turn_engine.py` `_WAKE_REASONS["on_join"] = "a participant joining"`.
- API: `app/api/v1/agents.py` `_validate_wakeup_config` accepts `triggers.on_join.enabled` as a
  bool and rejects other keys under it; `app/api/v1/room_wakeup.py` exposes `on_join_enabled`
  in `RoomWakeupOut`/`RoomWakeupPatchIn` with its field source. `gen:api` rerun required.
- Frontend: `shared/types/workflow.ts` (`WakeupConfig`, `DEFAULT_WAKEUP`,
  `normalizeWakeupConfig`), `shared/ui/SWakeupEditor.vue` (a switch, included in `isInert`),
  `slices/conversation/components/RoomWakeupPanel.vue` (a switch for normal bindings, hidden for
  observers), locale keys in `en.json` and `zh-TW.json`.

### 6.3 Activity state and activation notices (M3)

- New activities read `ActivitiesFacade.room_activity_state(chatroom_id)` returning a domain
  snapshot: current activation (type key and name, `individual` or `group`, consent fraction
  and started time) or the most recent ended one with its end time; for the current activation,
  groups that have submitted (group codes), open proposals per group with approvals,
  rejections, undecided and required approvals, and proposals rejected, withdrawn or expired in
  the current activation (group code, outcome and resolved time). No voter ids, no proposer and
  no payload ever leave the facade. New repository reads: proposals by activation (any
  status), with vote counts aggregated in SQL.
- `ActivityContextProvider` gains `state(chatroom_id, resolve_labels)` rendering
  `[Activity state]` with a preamble stating that counts come from the platform, that a group
  row is several people, and that the block makes no whole-class completion claim. Bounds: 30
  group rows and 1,500 estimated tokens, with explicit truncation. Rendered in a new
  `_BlockSlot.ACTIVITY_STATE` for normal and observer room turns, measured in both passes.
  When no activation has ever existed in the room, the block reads `No activity has been run
  in this room.` rather than being omitted, so "not started" is stated, not inferred.
- `ActivationService.start` (only when a new activation is created, not on the idempotent
  return at `:119-125`) and the single end cascade (`:171-199`) write a system message through
  the conversation facade with `metadata.type = "activity_activation"`, `event` of `started` or
  `ended`, and the activity type key and name. `content_md` is a canonical English sentence for
  agents and exports (`Activity "<name>" started.` / `Activity "<name>" ended.`). Neither names
  the starter. The route that committed the change re-arms silence through
  `note_room_activity` and publishes `message.created` for the row.
- Frontend `ChatroomMessageBubble.vue`: a system row with `type = "activity_activation"`
  renders a localized divider (`conversation.system.activityStarted` / `activityEnded` with the
  activity name) instead of `content_md`.

### 6.4 Course packs (M4)

For `crat-word-puzzle-room.json`, `character-building-room.json` and
`object-reinterpretation-room.json`:

- Teacher: a new `<新加入的同學>` section. On a `[Room notice] ... connected to the room.`
  for someone other than the room creator, when the current step allows talking, say in one or
  two sentences what the class is doing now and what to do next, addressed to the room, without
  words implying lateness and without asking the person to speak. During individual work or
  quiet thinking, stay silent. Uses the existing `<邀請發言>` rules unchanged.
- Teacher: every sentence that states a form exists, is open, or closes becomes conditional on
  `[Activity state]`. If no activity is running and the teacher holds `start_activity`, start it at
  the existing step; if it does not, ask the room creator to start it and do not mention the form.
- CRAT teacher `<步驟D1>`: branches on the state block: an open proposal for the group means
  remind the others to vote; a rejected or expired proposal means return to `<步驟C1>`; a
  submission means `<主流程E>`.
- CRAT teacher `<活動控制>` rule 4 becomes: end only after the room creator confirms, which
  keeps phase 2 Q-4.
- Peers: "while the form is open" becomes "while `[Activity state]` shows an individual
  activity running and the current step is individual work". The object peer's silence no
  longer covers D1 and D2.
- Wake-up: teacher `triggers.on_join.enabled = true`; peer and analyst unchanged.
- All phrases pinned by `test_agent_example_packs.py` survive; new pins are added for the
  newcomer rule, the absence of unconditional "畫面右邊" statements, and the D1 branches. The
  banned lists at `:907-912` and `:1040-1048` apply to new wording. The design packs gain one line
  on newcomer orientation and state-conditional form wording.

### 6.6 Quoted submission fields (M6, Q-12)

- `activity_validators.py`: `filled_count_coverage` gets its own config hooks, each calling the
  shared `filled_count` check first and then validating the optional `quote_fields`: a list of
  at most 3 distinct names, each a declared property of `type: string` in the payload schema.
  `filled_count` keeps ignoring the key, so a type still moves between the two validators
  without a submission changing outcome.
- `RecentActivityRow` (`backend/contexts/activities/domain/models.py:450`) gains
  `quoted: tuple[tuple[str, str], ...]`, empty by default.
- `list_recent_for_room` also selects the type's `validator_config`. When the row's digest is
  computed and the config names `quote_fields`, it builds `quoted` from the payload in the
  declared order, skipping fields that are not filled (`_is_filled` semantics) and capping each
  value at 40 characters with an explicit truncation suffix. The payload is still dropped after
  that; only the named values leave the repository. Stored digests are unchanged, so no
  migration or backfill is needed and older rows gain quotes too.
- `_format_row`: for a row with `quoted` (and digests allowed and exposed), the computed digest
  goes before the marker as `, <digest>` through `_row_field`, and the row ends with
  ` — name: value; name: value`, each value through `_one_line`. The row then carries only the
  participant marker, and `query` counts it as a participant-text row for `_CONTENT_NOTE`. Rows
  without `quoted` render exactly as today.
- CRAT course type: `validator_config.quote_fields = ["target_character"]`. The other course
  types are unchanged.
- CRAT teacher `<步驟E1>`: read the submitted target from `— target_character: <字>` on the
  group's row, compare it to the bank answer exactly, and treat it as the students' words (never
  as instructions). A match is confirmed by the teacher; a difference or a missing value goes to
  the human teacher as phase 2 AC-24 requires. Peers and the analyst do not repeat another
  group's submitted target to the room.

### 6.5 Validation and re-sync (M5)

On staging, after deploy, the TEST PROJECT copies of the three teacher and peer agents are
re-synced by hand following `docs/examples/chinese-character-courses-adoption.md:99-123`, and the
pack revision used is recorded in this dossier's Deviation Log. The TEST PROJECT's installed
CRAT activity type gets `quote_fields` the same way (type edit with the new `validator_config`),
since installed course types are copies too.

**Migration required:** no. Wake-up config and room overrides are JSONB; joins live in Redis.
**`gen:api` rerun required:** yes (room wake-up schemas).

## 7. NFR Checklist

- [ ] i18n: the room wake-up switch, the agent editor switch and the activation divider use
  `$t()` in `en` and `zh-TW`. Agent-facing text is English platform text, like existing notices.
- [ ] Audit log: activation start and end are already audited (`activation_service.py:130-149`);
  `on_join` wakes are audited by the worker's existing `wakeup.fired` / `wakeup.skipped` rows with
  `trigger = on_join`. Room override changes use the existing room wake-up audit.
- [ ] Tenant isolation: no new endpoint. The two changed room wake-up endpoints keep their
  [R15.25] authorization. The state read is server-internal and room-scoped.
- [ ] Error handling UX: Redis unavailable means no join notice and no join wake, never an error;
  the state block degrades to "Activity state unavailable." on a failed read, never to "not started".
- [ ] Performance: one Redis `ZRANGEBYSCORE` and one aggregated proposal query per room turn;
  join handling is O(bound agents) per new join and deduplicated by Q-7.

## 8. Security Considerations

- **Vote privacy ([R30.42] amended):** only counts and outcomes reach agents. The facade method
  returns a type with no voter, proposer or payload fields, and a unit test asserts the rendered
  block contains none of a proposal's voter ids or payload strings.
- **Observer confidentiality ([R28.09], [R28.10]):** activation notices never name who started
  the activity, so an observer holding the grant is not disclosed. `on_join` is not offered for
  observer bindings. Join notices go to observer turns as context only; observers still cannot
  post ([R28.01]).
- **Notice forgery:** join notices and the state block use the existing marker protections;
  participant labels are single-lined and defanged. A user display name containing
  `[Room notice]` cannot imitate one.
- **Wake amplification:** a client reconnecting in a loop produces at most one join per 30
  minutes per principal per room and one wake per minute per agent binding; autostop still
  applies.
- **Guests:** guest arrivals use the guest session id as principal, with the same windows.
- **Quoted fields (M6):** quoted values are participant text and are placed only after the
  participant marker, never under `::`, so the computed marker's "never contains their words"
  promise stays true. Values are single-lined and capped; field names come from the
  owner-authored schema, are validated as declared string properties, and pass through the
  same neutralisation as other pre-marker values. Quoting obeys `expose_payload_to_agent` and
  the platform policy re-read per turn ([R30.15]), and is limited to in-process computed rows,
  so third-party validator text never gains a quote. `quote_fields` lives in the
  owner-confidential `validator_config` ([R30.25]) and is never copied into `sub_scores`. Within
  a room, every agent sees a group's submitted target after submission, so the peer and
  analyst prompts forbid repeating it to other groups.

## 9. Quality Notes

- **Existing debt (do not imitate, do not silently fix):** the submission echo emits no
  `message.created` (`backend/app/api/v1/activities.py:1359-1421`), so it appears late for other
  participants (FU-1); `claim_gated_notice` is missing from `wakeup_state.__all__` (`:196-207`);
  trigger names are hardcoded in about 30 files instead of one registry (FU-2); the
  `broadcast.py:198-199` docstring claims agents read the WebSocket, which they do not.
- **Patterns to follow:** agent-only notices follow `_gap_notice`/`_with_gap_notices`
  (`turn_engine.py:615-663`); bounded blocks follow `render_participant_facts`
  (`participant_facts.py:92-159`) and `_SystemBlocks` measure/render (`turn_engine.py:1342-1461`);
  debounces follow `claim_gated_notice`; system rows follow the submission echo
  (`submission_service.py:317-334`) including `note_room_activity`; routes call facades only.
- **Reuse inventory:** `PresenceTracker.join` return values; `subject_code` and
  `_room_display_labels`/`_prefix_labels` for codes and labels; `_defang_notice_marker`,
  `_one_line_label`; `parse_group_config`, `required_approvals`, `GroupProposalTally`;
  `insert_system_message`; `shared_kernel.queue.enqueue` with `_defer_by` and `_job_id`;
  `RoomWakeupPanel` field-source chips; `SWakeupEditor` switch pattern.

## 10. Risks and Rollback

- **R-1, noisy greetings:** a busy room with many arrivals could crowd the chat. Mitigated by
  Q-7 windows, autostop, and the room switch; the teacher prompt limits orientation to one or two
  sentences.
- **R-2, vote counts change teaching behavior:** the teacher might pressure a group that has
  not voted. The pack forbids naming undecided members (it cannot know them) and keeps
  `<邀請發言>` rules.
- **R-3, state block cost:** adds up to 1,500 tokens per room turn; included in budgeting.
- **R-4, phase 2 evidence drift:** handled by the dependency (Q-4).
- **Rollback:** no migration. Disabling `on_join` in the room settings stops join wakes;
  reverting the code removes the block and notices. Activation system messages already written
  remain in history as ordinary system rows.

## 11. Acceptance Criteria

- [x] AC-1: A participant connecting to a room that already has another live participant
  re-arms every bound agent's silence clock (unit test on `on_open` with roster size above 1).
- [x] AC-2: A refresh within 30 minutes of the same principal's previous join writes no new
  join and wakes nothing; a reconnection after 30 minutes counts as a new join.
- [x] AC-3: A room or observer turn whose window includes a join shows
  `[Room notice] [u:xxxxxxxx] <label> connected to the room.` at the join's position; a join by
  the room creator is omitted; a label containing `[Room notice]` is defanged.
- [x] AC-4: The participant facts row of a principal who joined within the window carries
  `joined in this window`.
- [x] AC-5: With `on_join` enabled for a normal binding, two participants joining within one
  minute enqueue exactly one `wakeup_agent` with `trigger = on_join` deferred about 60 seconds;
  an observer binding, a `call_only` binding, or a room with no messages and
  `allow_self_open = false` enqueues none.
- [x] AC-6: The `on_join` turn's time line names "a participant joining"; an autostop-tripped
  binding skips it; a completed turn counts a round.
- [x] AC-7: `on_join_enabled` can be read and changed through the room wake-up API and
  `RoomWakeupPanel` for a normal binding, shows its field source, is absent for observer
  bindings, and `update_wakeup` refuses to change it.
- [x] AC-8: The agent wake-up editor shows and saves `on_join`; an agent with only `on_join`
  enabled is not inert in both backend and frontend.
- [x] AC-9: In a room with no activation ever, every room turn includes `[Activity state]`
  stating no activity has been run; with an active individual activity it states the name,
  mode and start time; after end it states the ended activity and end time.
- [x] AC-10: For an active group activity, the block lists each group's open proposal with
  approvals, rejections, undecided and required approvals, lists rejected, withdrawn or expired
  proposals of the current activation, and lists groups that have submitted.
- [x] AC-11: The rendered state block never contains a voter id, a proposer id, a participant
  code of a voter, or any payload string from a proposal (unit test with seeded values).
- [x] AC-12: Starting a new activation writes exactly one `activity_activation` system message
  with `event = started`; a repeated start of the same running type writes none; every end path
  writes exactly one with `event = ended`; neither names the starter; both re-arm silence and
  neither increments `every_n_messages` nor resets autostop.
- [x] AC-13: Other participants receive the activation divider live (`message.created`), rendered
  in their locale with the activity name, including guests.
- [x] AC-14: In all three room packs the teacher prompt has the newcomer section, contains no
  unconditional form statement, conditions starting the activity on `[Activity state]` and the
  grant, and every phrase pinned by `test_agent_example_packs.py` today still passes; banned
  phrase lists pass for the new wording.
- [x] AC-15: The CRAT teacher's `<步驟D1>` branches on an open proposal, a rejected or expired
  proposal, and a submission as shown in `[Activity state]`; `<活動控制>` ends only on the room
  creator's confirmation.
- [x] AC-16: The peers' silence rules reference `[Activity state]`; the object peer is not
  silenced during D1 and D2.
- [x] AC-17: Each teacher pack enables `on_join`; peers and analysts do not.
- [x] AC-18: Backend unit, DB and integration tests, ruff, mypy, frontend tests, lint, typecheck,
  build and `check:theme-tokens` pass in CI; `gen:api` output is committed.
- [x] AC-19: Staging: with the re-synced CRAT agents, a second account joining mid-discussion
  receives a one-to-two sentence orientation from TA within about two minutes, without being
  named as late; a refresh by that account within 30 minutes produces no second orientation.
- [x] AC-20: Staging: TA does not mention the answer form before the activity starts; after a
  group proposal is rejected, TA's next turn returns the group to discussion; after submission
  it moves to reflection.
- [x] AC-21: `filled_count_coverage` accepts `quote_fields` naming up to 3 declared string
  properties and rejects an undeclared name, a non-string property, a duplicate, more than 3
  names and a non-list value; `filled_count` accepts the same config unchanged, and the verdict,
  `error_class`, `sub_scores` and `detail` of both validators are unchanged for every payload.
- [x] AC-22: For a computed-digest row whose type quotes `target_character`, the rendered row is
  `... <type>: <outcome>, <coverage> — target_character: <value>` with exactly one marker; an
  unfilled quoted field is omitted; a value over 40 characters is truncated with the suffix; a
  value containing a newline, `::` or `—` stays on one line and cannot place a marker before the
  real one.
- [x] AC-23: No quote appears when the type has no `quote_fields`, when `expose_payload_to_agent`
  is false, when the platform policy withholds digests, or when the row's validator is not
  in-process; rows of such types render byte-for-byte as before (existing provider tests pass
  unchanged).
- [x] AC-24: The CRAT course type ships `quote_fields: ["target_character"]`; the CRAT teacher's
  `<步驟E1>` reads the quoted target and routes a difference or missing value to the human
  teacher; the CRAT peer and analyst prompts forbid repeating another group's submitted target
  (`test_agent_example_packs.py`, `test_smap_examples_catalogue.py`).
- [x] AC-25: Staging: after a group submits a matching target, TA confirms it without the human
  teacher; after a group submits a different target, TA defers to the human teacher. This closes
  phase 2 AC-24's remaining half, recorded in both dossiers.

## 12. Test Plan

- Unit (backend): `test_ws_chatroom_*.py` harness for the `on_open` gate (AC-1); `test_presence.py`
  plus a new `test_room_joins.py` for the join set and 30-minute window (AC-2); `test_turn_time_context.py`
  and `test_turn_room_notices.py` for join notices and defanging (AC-3);
  `test_participant_facts.py` (AC-4); `test_wakeup_service.py`, `test_agent_trigger_wiring.py`,
  `test_room_wakeup_resolution.py`, `test_room_wakeup_routes.py`, `test_wakeup_self_modification.py`
  (AC-5 to AC-8); `test_activity_context_provider.py` and `test_turn_system_blocks.py` (AC-9 to
  AC-11); `test_activity_activation_service.py`, `test_activity_control_tools.py` (AC-12);
  `test_agent_example_packs.py` (AC-14 to AC-17).
- DB tier: proposal aggregation by activation in `test_group_activity_constraints_db.py` (AC-10),
  executed against PostgreSQL per `backend/CLAUDE.md`.
- Frontend: `ChatroomMessageBubble.test.ts` (AC-13), `SWakeupEditor.test.ts`,
  `workflow.test.ts`, `ChatroomSettingsView.test.ts` (AC-7, AC-8).
- M6: `test_activities_services.py` validator tests (AC-21); `test_activity_context_provider.py`
  rendering and gating, including marker-injection values (AC-22, AC-23);
  `test_activity_repos.py` for building `quoted` and dropping the rest of the payload, plus a DB
  tier read of `validator_config` beside the payload (AC-22); pack and catalogue tests (AC-24).
- Manual on staging (AC-19, AC-20, AC-25), recorded with times and audit rows in the Deviation
  Log. The classroom simulator (`frontend/sim/`, scenario `crat-ac20`) can drive AC-25's two
  submissions.

## 13. SRS Delta

Amend **[R13.73]**: in "why the agent was woken (new messages, a period of silence, a mention,
or a released observation)", replace the parenthesis with "(new messages, a period of silence,
a participant joining, a mention, or a released observation)".

Amend **[R15.02]**: append "A participant joining means a principal's first live connection to
the room, whether or not others are already connected."

Replace **[R30.42]** with:

- **[R30.42]** A proposal's per-person votes are readable only by its pinned voters and the room creator. Neither the room broadcast nor the room echo carries the payload or any per-person vote. Agents may see, through the activity-state block ([R30.44]), for each group in the current activation whether it has an open proposal, that proposal's approval, rejection and undecided counts against its required approvals, and the outcome of proposals rejected, withdrawn or expired in that activation; no agent-visible surface carries a voter's identity, the proposer, a proposal's payload or any per-person dissent.

Amend **[R30.15]** (FU-16 amendment, applied 2026-10-23): append "An activity type whose
first-party coverage validator declares quoted fields additionally shows, on that type's rows,
the values of at most three declared short text fields, each length-capped and reduced to one
line, as the participant's own words after the participant marker; the server-computed
description of the submission then precedes the marker, so a row still carries exactly one
marker and server-computed text never contains participant words. Quoted values obey the same
exposure gates as every other digest."

Add:

- **[R13.79]** **Arrivals in the agent transcript.** A principal's first live connection to a room, counted at most once per principal per room in any 30-minute window, is recorded server-side. Normal and observer room turns show each recorded arrival inside the transcript window as a platform room notice naming the participant's code and label, using the room-notice channel's protections; arrivals of the room creator are omitted. The notice states a connection, not attendance or lateness, and is never shown to humans. Arrival records are bounded, expire, and are absent rather than an error when presence storage is unavailable.
- **[R15.26]** **`on_join` trigger.** A designer may enable `triggers.on_join` for an agent, and a room creator or permitted editor may override it per normal binding ([R15.25]). It is not self-toggleable and is not available to observer bindings. When an arrival other than the room creator's is recorded ([R13.79]) in a room where at least one message exists or `allow_self_open` is true, each normal binding with `on_join` effective and not `call_only` is woken once, 60 seconds later, for all arrivals in that minute. The wake is non-explicit: autostop applies and it does not reset autostop or count toward `every_n_messages`.
- **[R30.44]** **Activity state for agents.** Every normal and observer room turn includes a bounded activity-state block computed from server state: whether an activity is active in the room, its name, individual or group mode, consent fraction and start time, or else the most recently ended activity and its end time, or that none has been run; and for the current activation the groups that have submitted and the proposal facts permitted by [R30.42]. It states that it makes no whole-class completion claim, is capped at 30 group rows and 1,500 estimated tokens with stated truncation, and reads as unavailable rather than "not started" when the read fails.
- **[R30.45]** **Activation notices.** Creating an activation and ending one each write one room system message visible to every participant, naming the activity but not who started or ended it. A repeated start of an already running type writes none. The message re-arms the silence clock ([R15.02]) but is not counted by `every_n_messages` and does not reset autostop, and it is delivered live to connected participants.

## 14. Open Questions

- OQ-1: Should an `on_join` wake bypass autostop when the teacher has stalled after consecutive
  agent-only rounds? This dossier keeps autostop (Q-10); revisit after AC-19.
- OQ-2: Whether the teacher should orient a second newcomer who arrives a few minutes after
  the first, or whether one orientation per few minutes is enough. The 60-second coalescing
  window is a constant, not a setting, until classroom evidence says otherwise.

## 15. Deviation Log

- D-1. Build started 2026-10-24 on branch `feat/crat-facilitation-phase3` (base `283c3de2`) while
  `2026-10-16-crat-facilitation-phase2-classroom-context-and-cadence` is still `in-progress`, by the
  requester's decision. Q-4's purpose, keeping phase 2's classroom evidence tied to one prompt
  revision, is kept by a constraint instead of by the `depends_on` gate: the branch is not merged
  to `main` and not deployed to staging until phase 2 is `implemented`. Deploying it to staging
  would change every room on that host, including phase 2's live-run rooms, so the staging ACs
  (AC-19, AC-20, AC-25) and the M5 re-sync wait for that merge. If phase 2's runs change the pack
  prompts, this branch rebases onto them before merge.
- D-2. The room creator's own arrival does not wake `on_join` (requester's decision, 2026-10-24).
  §6.2 as approved woke on every recorded arrival, while §6.1 and [R13.79] omit the creator's
  arrival from the transcript; an agent woken by it would find no arrival to respond to and
  could only speak unprompted. The creator's arrival is still recorded and still re-arms the
  silence clock. The [R15.26] text in §13 is amended to say so, and
  `test_on_join_trigger.py::TestWakeSelection::test_the_room_creators_arrival_wakes_nothing`
  pins it.
- D-3. `[Activity state]` is a fixed text block in `_SystemBlocks`, measured and rendered in both
  passes, not a new `_BlockSlot.ACTIVITY_STATE` as §6.3 named. A slot exists for text the render
  pass supplies later than the measure pass (knowledge, the participant note); the state is read
  once before either pass and does not change within the turn, so a slot would add a second
  supply point with nothing to supply. It sits after the recent-activity block and before the
  canvas block, and `test_turn_system_blocks.py` pins that position in both passes.
- D-4. AC-10 and AC-11 are checked by unit tests locally; their `db`-tier half
  (`test_group_activity_constraints_db.py::TestRoomActivityStateQueries`) cannot run on the
  local Windows host and is verified in CI on the draft PR before those two ACs are ticked.
  CI run 40060044755 on PR #270 (head 97c7da81) passed `backend-db` (`pytest -m db`, 314
  passed), so AC-10, AC-11 and AC-18 are ticked. The same run's `dependency-audit` job failed on
  advisories for brotli 1.2.0, pypdf 6.21.1 and setuptools 83.0.0 published after `main` last
  passed at 283c3de2; this branch changes no dependency manifest, so the fix belongs on `main`.
- D-5. `ActivityTypeForm` now carries `quote_fields` through an edit, which §6.6 did not list. The
  form rebuilds `validator_config` from its own fields, so without this a rename of the CRAT type
  in the UI would silently stop quoting. A quoted field the schema edit removed or retyped is
  dropped on save rather than failing it. The form still has no control to author the list
  (FU-5).
- D-6. The CRAT teacher's `<步驟D1>` branches on counts and time order, not on a per-round state.
  One CRAT activation spans all eight rounds, so `[Activity state]` for the current activation
  lists submissions and closed proposals from earlier rounds too. The prompt therefore treats a
  group as having submitted the current round only when its submission count reaches the number
  of rounds the teacher has posted, and treats a rejected, withdrawn or expired proposal as
  current only when it is newer than the round's posting. A per-round state would need the
  activation to know its rounds, which no activity type models today (FU-6).
- D-7. The arrival notice quotes the label: `[Room notice] [u:xxxxxxxx] "Label" connected to the
  room.`, not the unquoted form §6.1 and AC-3 give. The security gate found that a guest chooses
  their own label (up to 100 characters) and that, unquoted, a sentence-shaped label read as the
  platform's own words inside a turn the participant note vouches for. Quotes are already
  stripped from model-facing labels and are stripped again in `_join_notice`, so the slot cannot
  be closed early. The three teacher prompts quote the new form and say the quoted name is
  self-chosen; `test_turn_join_notices.py` and `test_agent_example_packs.py` pin both sides.
- D-8. Merged to `main` and deployed to staging on 2026-10-24 before phase 2 is `implemented`
  (requester's decision), lifting D-1's constraint. Phase 2's open live ACs (AC-14, AC-20, AC-21,
  AC-24) are therefore observed on this dossier's pack prompts, not on the revision phase 2
  shipped; its D-19 records the same. The staging ACs here (AC-19, AC-20, AC-25) and the M5
  re-sync proceed after that deployment.
- D-9. M5 re-sync, 2026-10-24, from the packs at `95ee9a06`. The TEST PROJECT agents were patched
  through `PATCH /api/agents/{id}` and their stored prompts compared by hash with the pack: TA
  字詞拼圖 v47 to v48, SA 字詞拼圖 v14 to v15, AA 字詞拼圖 v9 to v10, TA 拆字蓋樓 v6 to v7, SA 拆字蓋樓
  v6 to v7, TA 萬物新解 v5 to v6 and SA 萬物新解 v6 to v7; the three teachers also gained `on_join`.
  AA 拆字蓋樓 and AA 萬物新解 already matched. §6.5 has the CRAT type edited with the new
  `validator_config`, but the staging type is a platform type and
  `PATCH /api/admin/activity-types/{id}` accepts only its name, payload exposure, echo and
  retention, so `quote_fields: ["target_character"]` was written by the requester in SQL on type
  `15ab862d-784f-4f54-bb37-5ed35d6644b1` (version 2). An admin path for a platform type's
  validator config does not exist yet.
- D-10. Live evidence for AC-19, AC-20 and AC-25, 2026-10-24 (times UTC+8). Classroom simulator,
  scenario `crat-phase3`, room `sim-phase3-20261024-145744`
  (`22078b95-5011-441c-93fc-9b320bac69f1`) with TA, SA and AA from D-9, the room creator and four
  registered students in one member group (three approvals required); evidence under
  `frontend/sim/runs/20261024-145744-crat-phase3/` (local, not committed).
  - AC-19: passed. The fourth student first connected at 23:05:06; TA's `on_join` wake fired at
    23:06:14 and posted to the whole room where the discussion stood (comparing 「心」 and 「火」)
    and what to check next, addressing 「剛加入的同學」 without a name and without calling anyone late.
    It ran two orientation sentences plus a question back to the group, slightly over the stated
    one to two. The same account reconnected at 23:08:21 and the audit log shows no second
    `on_join` wake.
  - AC-20: passed. TA's greeting at 23:00:14 described the game with no mention of the form; TA
    started the activity itself at 23:02:35 and mentioned the form only after the start. The
    proposal 「火」 was rejected at 23:10:42 (one approval, two rejections); TA's next turn (23:11:08)
    said the vote had not passed and asked whether the group wanted to adjust the answer or the
    reasoning before submitting again, and its following turn (23:12:10) asked the group to
    re-check the three words. It did not use the prompt's 「我們再討論一下？」 wording. After the
    accepted submission (23:13:42) TA's next turn moved to reflection on the path the group took.
  - AC-25, matching half: passed. On the submission of 「心」, TA's turn at 23:16:06 said it matched
    the reference answer and announced the three words, without the room creator; the scenario's
    fallback step for the creator, armed on a TA message naming 「老師」, never fired.
  - AC-25, differing half: not observed. After round 1's accepted proposal the panel offered the
    group no way to propose again, so round 2's 「油」 could not be submitted (D-11). AC-25 stays open.
  - Outside the ACs: in round 2 SA proposed 「水」, the reference answer, at 23:23:09; TA's next turn
    referred to it as 「另一個字」 without naming it.
- D-11. A group could not submit a second round (found by D-10, fixed on the requester's decision).
  D-6 has one CRAT activation span all eight rounds, and the backend accepts a further proposal in
  the same activation, but `ActivityPanel` hid the propose form for good once the group's proposal
  was accepted. The accepted card now offers the same explicit control as a failed one, labelled
  `activities.group.proposeNextRound`; the card still stays up after it settles.
  `ActivityPanel.test.ts` and `GroupProposalCard.test.ts` pin both labels and the form's return.
- D-12. Second live run, 2026-10-25 (times UTC+8), after D-11 was deployed (`main` at
  `b5160c30`). Same scenario and agents as D-10, room `345abfe0-560c-496f-b9e4-74686b55efd5`;
  evidence under `frontend/sim/runs/20261025-032203-crat-phase3/`.
  - AC-25: passed, both halves, so it is ticked. 「心」 was submitted at 11:37:55 and TA confirmed
    it at 11:40:07 without the room creator. 「油」 was submitted at 11:45:55; at 11:48:06 TA said
    it differed from the reference answer, gave the reference 「水」 and asked the teacher to
    judge, then relayed the creator's ruling (11:51:23).
  - AC-19: passed again. Arrival 11:29:18, `on_join` turn 11:30:27 addressed to the room
    (「剛加入的同學也可以一起接著看」), no second wake after the 11:32:33 reconnect.
  - AC-20: the form went unmentioned before the 11:27:22 start, and the accepted submission led to
    reflection (11:40:07), but **the rejection clause failed**. 「火」 was rejected at 11:34:55;
    TA's next turn (11:35:35, a silence wake) made no mention of the rejection and asked a member
    to fill in the form and submit. Only the turn after (11:36:25) said the proposal had not
    passed and asked the group to re-check. With D-10 this is one pass and one failure, so AC-20
    is unticked. Cause: `<步驟D1>` opened with the submission guidance unconditionally and read the
    submission state only after it, so a wake inside D1 replayed the guidance. The state check now
    comes first, the guidance runs only when the round has no proposal yet (case 4), and a
    rejected, withdrawn or expired proposal is answered alone, without a pointer to the form
    (`test_agent_example_packs.py::TestPhase3CoursePacks::test_crat_d1_reads_the_state_before_guiding_a_submission`).
    AC-20 waits for a re-sync of TA and a further run.
  - Outside the ACs: at 11:51:23, in the turn that asked the creator whether to continue, TA also
    announced the next bank item's clues 「主、客、詩」 before the creator decided (FU-16).
- D-13. Third live run, 2026-10-25 (times UTC+8), on the D-12 prompt (PR #276). TA 字詞拼圖 on
  TEST PROJECT was re-synced from that pack beforehand, v48 to v49, with the stored prompt's hash
  matching the pack's; no other agent changed. Room `90ec95ea-20c4-4881-86c9-397594189e39`,
  evidence under `frontend/sim/runs/20261025-041203-crat-phase3/`.
  - AC-20: passed, so it is ticked. No form mention before the 12:17:21 start. 「火」 was rejected
    at 12:24:56 and TA's next turn (12:25:36, a silence wake) opened with
    「這一題的提案沒有通過，我們先回到討論確認答案」 and asked for other candidates, with no
    pointer to the form. The accepted 「心」 (12:27:56) led to reflection at 12:30:04.
  - AC-25: passed again. TA confirmed 「心」 itself (12:30:04) and, for 「油」 (12:35:56), named the
    difference and the reference answer and asked the creator to judge (12:38:08), then relayed the
    ruling (12:41:25).
  - AC-19: passed again. Arrival about 12:19:18, `on_join` turn 12:20:23 to the whole room with no
    name; none after the reconnect. The FU-16 behaviour did not recur: TA asked the creator whether
    to continue without posting the next clues.
  - Outside the ACs: in all three runs the creator's closing 「請助教幫大家總結今天的活動」 got no
    TA turn before the activity ended two minutes later; the message trigger had not reached its
    count and no silence wake fell in the window.

## 16. Follow-ups

- FU-1: Emit `message.created` for the submission echo so other participants see it live
  (`backend/app/api/v1/activities.py:1359-1421`).
- FU-2: Replace the hardcoded trigger-name lists (about 30 files) with one registry shared by
  domain, API validation, room overrides and frontend types.
- FU-3: Correct the `broadcast.py:198-199` docstring that says agents read WebSocket events.
- FU-4: A pack revision field so classroom evidence can name the exact prompt revision
  (`catalogue.py:42-55` rejects unknown fields today).
- FU-5: An authoring control for `quote_fields` in `ActivityTypeForm` (a pick list of the
  declared text fields, at most three); today it is set through the course file or the API.
- FU-6: Rounds inside one activation (for example a `round` counter on submissions or a per-round
  activation) so `[Activity state]` can report the current CRAT round directly instead of the
  teacher prompt inferring it from counts and time order (D-6).
- FU-7: `ActivityContextProvider.query()` reads recent activity on the turn's session without a
  SAVEPOINT, so a failed read aborts the turn's transaction; `state()` got one in this task.
  Pre-existing.
- FU-8: Bound the provider cost of `on_join` across many principals. Each new principal (for
  example fresh guest sessions from a share link) can win one wake per binding per minute, and a
  turn that reaches the provider but posts nothing does not count toward autostop, so a teacher
  prompt that stays silent during individual work pays for those turns on the owner's key.
  Options: count a provider-reaching empty `on_join` turn toward autostop, a per-binding hourly
  `on_join` budget, or skipping `on_join` while the state block shows individual work. Security
  gate MEDIUM, deferred with the requester's knowledge.
- FU-9: Arrival side effects for non-arrivals. `_re_arm_silence` runs on every first connection,
  including a reconnect inside the 30-minute window, so a reader who reconnects every few minutes
  keeps `silence_minutes` from firing; a platform admin opening a tenant room is recorded as an
  arrival and can wake `on_join`. Re-arm only on a recorded arrival, and skip recording for
  principals who reach the room only through the admin bypass.
- FU-10: Bound the `[Activity state]` reads themselves. Settled proposals of one activation and
  `submitted_groups` are fetched in full and capped only when rendered; a `LIMIT` of
  `STATE_MAX_GROUP_ROWS + 1` per section with a separate count keeps the stated truncation exact.
- FU-11: Whether the CRAT teacher may name one group's submitted target in the room while other
  groups are still working. Peers and the analyst are told not to repeat it; the teacher's
  `<步驟E1>` confirms it publicly. Product decision before the prompt changes.
- FU-12: Wake-path tidy-ups from the quality gate: the room-creator lookup returns `None` on
  failure, which then shows the creator's arrivals; the join-batch claim TTL equals the defer
  window, so an arrival just after it can cost a second turn; one arrival reads the room's
  bindings two or three times; the observer `on_join_enabled` rule sits in the route rather than
  in `RoomWakeupService`.
- FU-13: Agent-text hardening from the security gate: quote each quoted-field value (`name:
  "value"`) so a value cannot fake further fields after the marker; cap `quote_fields` names
  (about 64 characters, `[A-Za-z0-9_]`); consider withholding the reject and undecided split for
  groups of two or three, where counts beside the chat can identify the dissenter.
- FU-14: An admin path for a platform activity type's `validator_config` (D-9). Today
  `quote_fields` on a platform type can be set only by the course file at seed time or by SQL.
- FU-15: Classroom simulator on Windows and behind the edge proxy. Its hidden password prompt
  shows nothing in Windows PowerShell and the typed value was not read, so the run failed with
  401 until the password came through the environment. The 503 retries and one-at-a-time startup
  connects for the edge `limit_req` are in `7029255b`.
- FU-16: The CRAT teacher announced the next round's clues while asking the room creator whether
  to continue (D-12). `<步驟E2>` leaves the move to the creator; the bank section could say the
  next item is posted only after the creator's go-ahead.
