---
type: feature
status: in-progress
created: 2026-11-10
requirements: [R15.28, R15.29, R30.21, R30.26, R30.37, R30.39, R30.41, R30.42, R30.43, R30.44, R33.10, R34.02, R34.06]
depends_on: []
---

# CRAT lab trial fixes: guest group votes, round briefs, submission wakes and a better teacher agent

## 1. Summary

On 2026-11-10 six lab members played the CRAT word-puzzle room on staging (room
`a3b76882-a67d-4ebd-a6ed-286f679e4249`) through a guest link, with the teacher, peer and
analyst agents of `crat-word-puzzle-room.json`. The trial exposed four platform gaps and a
poorly facilitated class. First, group voting never ran: guests cannot be in a Member Group, so
every participant got a private individual form while the teacher agent kept promising a
two-thirds vote. Second, the puzzle could not be shown in the activity panel, and one
activation spanned both puzzles, so the research export cannot tell which puzzle an answer
belongs to. Third, a valid submission woke nobody, so the class stalled until the room creator
typed. Fourth, a teacher message sent before the teacher saw an alert was recorded as the
reaction to it. The teacher agent (TA) itself wrote four times more text than all students
combined, stalled on an obvious answer for three minutes, and turned the peer agent (SA) into an
apparent liar through its own review edit. This feature fixes the platform gaps and rewrites the
CRAT teacher, peer and analyst prompts. It serves the NSTC research that runs these rooms.

## 2. Goals and Non-goals

**Goals**
- In a room with no Member Group bound, the whole room is one group: guests and registered
  participants other than the room creator can propose and vote on a group answer.
- A delegated agent or the room creator can attach a short plain-text round brief to an
  activation; it is shown at the top of the activity panel, given to agents, and exported. The
  CRAT teacher runs one activation per puzzle with the three clue characters as the brief.
- A valid submission wakes the room's activity-granted agents, debounced, so the teacher
  debriefs and moves on without the room creator.
- An alert's first action is recorded only after the room creator has viewed it.
- The CRAT teacher talks less, confirms obvious answers quickly, answers direct questions,
  handles its own review of the peer agent without hollow edits, and treats the room creator as
  the teacher, not a student.
- The peer agent learns when its reply was modified; the analyst agent can tell a teacher-edited
  peer message from one the peer wrote.
- The teacher agent does not post two replies to the same moment (the 11:58:36 / 11:58:42 pair).

**Non-goals**
- Several groups inside one room. One room is one group, matching the one-teacher-per-room
  class design; multi-group rooms keep using Member Groups.
- Letting guests join project Member Groups ([R13.28] stays members-only).
- Exporting proposals and votes to the research bundle (FU-1).
- A room setting to disable the creator-message wake ([R15.29] unchanged).
- Rewriting the character-building or object-reinterpretation teacher prompts, beyond mirroring
  any shared block the tests require to stay identical.
- Recomputing reaction times already stored.
- Changing the analyst name rule (done in commit `3e763808`, branch `fix/crat-analyst-names`).

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Guests cannot vote in groups. Build guest group voting, or require accounts plus Member Groups? | Build guest group voting. | Trials and experiments use guest links; asking every participant for an account is the friction the guest link exists to remove. |
| Q-2 | How should the puzzle reach the activity panel? | One activation per puzzle, started with a round brief. | Separates research data per puzzle for free, resets forms between puzzles, and mirrors the per-round pattern the other two teachers already use. Chosen over a "replace the brief" tool on a long activation. |
| Q-3 | Does a creator room message before the alert was viewed count as the reaction? | No: an action counts only after the creator viewed the alert. | RQ2 measures reaction to the alert; an action taken without seeing it is not one. |
| Q-4 | One dossier or several? | One dossier with milestones. | Same shape as `2026-11-09-classroom-agent-collaboration`; the prompt rewrite depends on every platform change. |
| Q-5 | Which group may guests join? | The whole room is one group when no Member Group is bound. | Smallest change; a CRAT room is one group with one teacher. Option "teacher-made groups inside a room" was about twice the work for a case the class design does not use. The original group dossier's Q-1 (`docs/tasks/2026-08-24-group-activity-submissions/spec.md:57`) rejected a room group because it cannot express several groups per room; that limit is accepted here and Member Groups remain for it. |
| Q-6 | When the peer agent's draft contains the bank answer, what does the teacher's review do? | If a student already proposed that character, approve; if not, reject the whole reply. A modified text never keeps a claim about content it removed. | Students wanted the peer to act as a teammate; reject-not-modify prevents the "I thought of one" hollow edit seen at 11:56:34. |
| Q-7 | The room creator's every message wakes the teacher. Change the platform or the prompt? | Keep the wake; the prompt answers only when addressed or given a flow instruction, and treats the creator's playing as discussion. | No platform change; [R15.29] stays as written. |

## 4. Current State

### 4.1 Trial evidence

Data from the staging database, dumped to the session scratchpad (`trial/trial.json`):
84 messages, of which the TA wrote 24 and humans wrote 52. The TA averaged 93 characters per
message, the humans 10. 13 of the 24 TA messages start with 「目前」, and 11 contain 「逐一」.
The TA's turns were triggered by `every_n_messages` 14 times, `creator_message` 6, and
`class_start`, `mention`, `analyst_notice` and `silence_minutes` once each. One activation
(`a7c7acb1`) ran from 11:47:09 and was never ended. No group proposal was created. Three
submissions, all from one guest, arrived at 11:52:23, 11:52:34 and 11:52:41, each with the same
answer. The TA did not respond to them; it next spoke at 11:53:35, after the creator wrote
「下一題」. Four peer reviews took place: three were modified and one approved. The alert raised
at 11:57:52 has `first_action_at` 11:58:34 and `first_viewed_at` 11:59:35.

### 4.2 Group mode excludes guests

- Eligibility is `live_bound ∩ member_group_ids_for_user(caller)`
  (`backend/contexts/activities/application/group_proposal_service.py:554-558`). A guest id is
  never in a Member Group, so `eligible_groups` is empty and the panel falls back to the
  individual form (`frontend/src/slices/activities/components/ActivityPanel.vue:95-97`).
- `_pin_voters` raises `MemberGroupNotBoundToRoom` when no live group is bound or
  `allow_member_groups` is off (`group_proposal_service.py:242-248`), and `NotAGroupMember` for
  a non-member (`:249-250`). The ballot is `member_group_user_ids` plus the proposer (`:252-262`).
- `activity_group_proposals.proposer_user_id` and `activity_group_proposal_votes.user_id` are
  foreign keys to `users.id` (`backend/contexts/activities/infrastructure/tables.py:338-343`,
  `:373-378`, migration `0081_group_activity_submissions.py:129-131,204`). Migration
  `0098_guest_identity_columns.py:48-57` dropped the users FKs on sessions and submissions but
  not on these two tables.
- `submit_for_group` never passes `producer_is_guest` (`submission_service.py:189-199`, default
  False at `:253`), and proposal audits omit `actor_guest_room_id`
  (`group_proposal_service.py:690-701`).
- A group session's subject is a Member Group id under the one-subject CHECK and the unique
  index on `(activation_id, subject_member_group_id)` (`0081:71,95-98`); `subject_kind` admits
  only `user` and `guest` (`0098:45`).
- The panel sets `viewerUserId` and `can-vote` from `session.me?.id`, null for a guest
  (`ActivityPanel.vue:72,87,462`); `useGroupProposal.ts:148-157` then never finds the guest's
  vote. The established guest pattern is `isGuestSession ? guestSessionId : session.me?.id`
  (`frontend/src/slices/canvas/components/CanvasCommentPopover.vue:32`).
- The voters see the full payload on the proposal card (`GroupProposalCard.vue:85-95,125-136`).
  The answer to "how can members vote without seeing the form" is therefore that group mode
  shows it; the trial never reached group mode.
- For a group type the `[Activity state]` block omits the individual progress line
  (`backend/contexts/activities/application/activity_context_provider.py:533-534`,
  `interfaces/facade.py:558-559`) and the completion wake is skipped
  (`application/platform_events.py:120-121`). In the trial the TA was told "group activity" and
  saw neither groups, proposals nor a submission count.

### 4.3 Activations carry no brief and span every puzzle

- The `start_activity` tool schema has only `activity_type_key`
  (`backend/contexts/agents/application/runtime/activity_tools.py:230-241`). On a repeat start of
  the running type the service returns the existing activation with no notice
  (`activation_service.py:136-143`), yet the tool still reports "Started"
  (`activity_tools.py:289`).
- `activity_activations` has no free-text column (`tables.py:134-178`); the latest migration is
  `0113_prompt_revisions_and_design_runs.py`.
- The human route takes only `activity_type_id` (`backend/app/api/v1/activities.py:221-222`); the
  out model and the started broadcast carry the type projection, limit and deadline
  (`activities.py:225-248`, `contexts/activities/interfaces/broadcast.py:136-153`).
- The panel shows the type name and countdown (`ActivityPanel.vue:379-395`); neither
  `ActivityHost` nor the group `SchemaForm` is keyed on the activation (`:476-499`). The activities
  slice renders no `v-html` (`frontend/eslint.config.js:291,396-421`).
- `render_activity_state` names the activity and its timing but no brief
  (`activity_context_provider.py:469-487`).
- The research export selects no activation column (`submission_repo.list_for_research_export`,
  `backend/contexts/activities/infrastructure/repositories/submission_repo.py:1004-1092`;
  `SubmissionRow`, `research_export_builder.py:144-162`; columns `:46-66`; `SCHEMA_VERSION` `:43`).
- The CRAT TA prompt makes one activation cover the class in `<活動控制>` rules 1, 4 and 5,
  `<步驟B1>` item 1, `<步驟D1>` header and case 2, `<步驟E1>` item 2, `<步驟E2>`, `<節奏規則>`
  3 and 4 and `<上課節奏>` 6 (`crat-word-puzzle-room.json:28`). Tests pin it at
  `backend/tests/unit/test_agent_example_packs.py:1666-1690,1728-1761,1145-1156`. The other two
  teachers already start per round (`:1158-1184`).

### 4.4 A submission wakes nobody

`_WAKE_REASONS` lists every trigger (`turn_engine.py:593-604`). Activity wakes exist only for a
deadline and for "every connected participant submitted" on individual types ([R15.28];
`platform_events.py:78-145`; `backend/app/wiring/platform_wakes.py:35-106`), dispatched after
commit from `_dispatch_submission` (`activities.py:1437-1508`) and from the validator worker
(`backend/app/workers/tasks/activities.py:203-211`). A single or group submission only re-arms
the silence clock (`backend/contexts/conversation/application/triggers.py:194-218`).

### 4.5 Reaction time

`ObservationRepository.mark_first_action` updates every open alert of the room with
`first_action_at IS NULL` and no view condition
(`backend/contexts/conversation/infrastructure/repositories/observation_repo.py:305-331`). It is
reached from a creator room message (`messages.py:220-223`), activity start and end
(`activities.py:990-992,1026-1029`), and per alert from release and delete
(`observation_service.py:205-207,250-252`). Views are set by `mark_viewed` (`observation_repo.py:283-303`)
through `POST /api/chatrooms/{id}/observations/viewed` (`observations.py:180-201`). The defining
text is [R34.02] and AC-4 of `docs/tasks/2026-11-09-classroom-agent-collaboration/spec.md:186`.

### 4.6 Peer review and what the agents are told

- The review turn runs under the TA's full classroom prompt plus a short English frame
  (`turn_engine.py:4441-4447`; frame `collaboration_tools.py:251-266`; tool `:269-336`). The TA
  prompt has no review section. Its 界線 4 forbids revealing the target before submission and
  rule 7 forbids judging the peer before a student responds. In the trial the peer's 「水」 was the
  bank answer for C2, so the TA removed it and kept 「我想到一個候選字」.
- A modified reply posts as the peer with `metadata.peer_review_modified`
  (`peer_review_service.py:204-213`). The peer is told only about rejections
  (`collaboration_tools.py:339-353`, `peer_review_repo.py:149-167`), and its later turns show the
  revised text as its own (`turn_engine.py:4929-4930`).
- Agent messages render as `"{label}: {content}"` with no id or metadata
  (`turn_engine.py:4928-4936`), so the analyst cannot see the modified flag and cannot see the
  message id its prompt asks it to pass to `notify_teacher_agent`.

### 4.7 The duplicate teacher reply

At 11:58:36 the TA posted a reply from an `analyst_notice` turn whose recheck ran with result
`revised`. At 11:58:42 it posted a second reply from a `creator_message` turn for the creator's
11:58:34 message. A recheck marks as covered only the rows it showed and only wakes that carry a
message id (`turn_engine.py:5203-5327`, covered check `:1737-1739`). Which of those paths let the
creator wake run is not yet established (§14).

### 4.8 Facilitation, against the prompt

The TA prompt is 15,577 characters. Its `<每一輪發言的固定結構>` requires a recap, an optional
pick-up and one question in every B1/C1 message (prompt lines 38-45). C1 moves to C2 only when a
student says 「我覺得是 X」 or similar (line 266). The A2 demo must contain a failed near-miss
(lines 214-219); the trial's near-miss 「開大」 was incoherent, and 「大開」 is a common word. D1
recognises only the group notice form `Submitted attempt #N … for <組名>` (line 303), while the
individual form is `Submitted attempt #N to <type>: valid.` (`submission_service.py:749-761`).
The language rule fixes Taiwan Mandarin (line 16), so an English speaker got only Chinese.

## 5. Design

### Options considered

**Guest groups.** (A) The whole room is one group when no Member Group is bound. (B) Teacher-made
groups inside a room, joinable by guests. (C) Guests in project Member Groups. C breaks
[R13.28]'s members-only property and the project-scoped lifecycle; B is about twice the surface
for multi-group rooms the class design does not use. A is chosen (Q-5).

**Puzzle on the panel.** (A) A round brief fixed at activation start, one activation per puzzle.
(B) A long activation with a tool that replaces the panel text and a per-submission puzzle stamp.
A is chosen (Q-2): the brief is a snapshot like the time limit ([R30.46]), and per-puzzle
separation, attempt numbering and form reset come from the existing per-activation scoping.

**Submission wake.** (A) A debounced platform wake to activity-granted bindings. (B) Rely on the
`every_n_messages` cadence. B fails exactly when students stop typing to submit. A is chosen.

**Reaction guard.** (A) The room-wide action closes only viewed alerts; release and delete record
the view themselves when none is recorded, since both are performed on the rendered alert. (B)
Record both "first action" and "first action after view". A is chosen (Q-3); B keeps the flawed
column alive.

### Decision

1. **Room group.** A group-mode type that is live in a room where no live Member Group is bound
   (or `allow_member_groups` is off) uses a room group: its id is the chatroom id and its name is
   the room name. Any participant with send access other than the room creator may propose. The
   voter set is pinned at creation as the proposer plus every human participant other than the
   room creator connected at that moment, using the same 30-second departure window as [R15.28].
   The required count is the type's fraction over that set ([R30.40] unchanged). Proposer and
   voters may be users or guests; their kinds are stored. A room with bound Member Groups behaves
   exactly as today. The room group's session reuses the group-session path with a group kind
   discriminator, and its code is a `g:` code whose legend name is the room name.
2. **Round brief.** `activity_activations.brief` (nullable text, at most 200 characters after
   trimming, no control characters except line breaks, at most 3 lines). `start_activity` gains
   an optional `brief`; the human start route gains the same field. The brief is immutable for the
   activation, carried by the active read and the started broadcast, rendered as plain text at the
   top of the panel, added to `[Activity state]` as one quoted line, and exported. A repeat start
   of the running type returns "already running; end it first to change the brief" instead of
   "Started".
3. **Per-puzzle rounds.** The CRAT TA starts an activation with the three clue characters as the
   brief when it reveals a puzzle, and ends it in the same message as the debrief. Forms are keyed
   on the activation so a new puzzle always starts empty.
4. **Submission wake.** After a valid submission (individual or group, including one validated
   asynchronously), the platform claims a per-activation window of 20 seconds and, at its end,
   wakes each binding whose activity grant allowlists the type, with a notice stating the activity
   name and the number of valid submissions in the window and in the activation so far. It is an
   explicit platform wake (bypasses autostop, not counted), is skipped when the same submission
   claimed the all-submitted wake, and is not run in a closed room ([R15.30]).
5. **Reaction guard.** `mark_first_action` adds `first_viewed_at IS NOT NULL` for the room-wide
   path. Release and delete set `first_viewed_at` (via `panel`) in the same transaction when it is
   null, then record the action.
6. **Review honesty.** The review frame states that a modified text must not claim content it no
   longer contains and that rejection is preferred to such an edit. The peer's prompt context gains
   a block listing its replies the teacher modified, with the posted text. Agent transcripts render
   a modified peer message with a marker naming the teacher agent's revision, and render message
   ids where the analyst needs them for `about_message_id`.
7. **Duplicate reply.** Reproduce the 11:58 sequence in a unit test, fix the path it exposes so a
   follow-up turn whose triggering message was already answered posts nothing.
8. **Prompts.** Rewrite the CRAT TA prompt around the trial findings (§6, M6), update the peer and
   analyst prompts, and correct the design agent's stale rules.

Given up: several groups per room without Member Groups; a pinned guest who leaves still counts
as undecided (§10); proposals and votes stay out of the export.

## 6. Detailed Changes

### M1: Reaction guard (bugfix inside the feature)
- `observation_repo.py:305-331`: view condition on the room-wide path; per-alert release and
  delete set `first_viewed_at`/`first_viewed_via='panel'` when null in the same statement or
  transaction.
- `research_export_builder.py:84-88` README note; `frontend/sim/scenarios/classroom-collab-ac21.ts:210`
  comment.
- Migration: no.

### M2: Round brief and per-puzzle activations (platform)
- Migration `0114`: `activity_activations.brief text NULL` with a CHECK on `char_length(brief) <= 200`.
  `tables.py`, `_ACTIVATION_COLS` and mapper (`activation_repo.py:17-45`), `create_active(brief=)`
  (`:106-137`), domain `ActivityActivation`.
- `ActivationService.start(brief=)` normalises and snapshots the brief; audit records only its
  presence and length. `ActivitiesFacade.start_activation(brief=)`.
- `activity_tools.py`: optional `brief` (`maxLength` 200, description: shown to every participant,
  must never contain an answer); repeat-start message fix; module docstring safety note updated.
- API: `ActivityActivationStartIn.brief`, `ActivityActivationOut.brief`, broadcast payload
  `brief`. `gen:api`: yes.
- Agents: one `Round brief: "<one line>"` line after the activity line in `render_activity_state`,
  through the existing one-line and quote-stripping helpers (`activity_context_provider.py:249-269,459-460`).
- Frontend: `ActivationView.brief`, socket mapping (`useChatroomSocket.ts:687-705`), store
  (`stores/activities.ts:115-128`), plain-text brief under the name in `ActivityPanel.vue:389-395`
  with `white-space: pre-line`, `:key="activation.id"` on `ActivityHost` and the group
  `SchemaForm`; optional brief input in the creator's start control; i18n label in en and zh-TW;
  stale comment at `ActivityPanel.vue:160-162`.
- Export: join the session's activation; add `round_code` (truncated code, never the UUID),
  `round_started_at`, `round_brief` (through `_cell`) to every submissions CSV; bump
  `SCHEMA_VERSION` with a manifest note.

### M3: Room group for guests (platform)
- Migration `0115`: drop the users FKs on `activity_group_proposals.proposer_user_id` and
  `activity_group_proposal_votes.user_id`; add `proposer_kind` and `voter_kind`
  (`text`, CHECK in `user`, `guest`, default `user`); add `group_kind`
  (`text`, CHECK in `member_group`, `room`, default `member_group`) to `activity_group_proposals`
  and to `activity_sessions` (meaningful only on group rows). Existing rows keep their defaults.
- `group_proposal_service.py`: a group source abstraction with a Member Group source (today's
  gates) and a room source (room has no live bound group, caller is not the creator, ballot from a
  presence snapshot through the conversation facade). `list_round_for_caller` returns the room
  group as the single eligible group when it applies. Proposer and voter kinds come from the
  principal. `submit_for_group` passes `producer_is_guest`; audits pass `actor_guest_room_id`.
- `submission_service._resolve_group_session` keyed on `(activation, group_kind, group id)`.
- API: `ActivityGroupProposalIn` gains `group_kind` (default `member_group`); `eligible_groups`
  items carry `kind`. `gen:api`: yes.
- State block and legend: room-group rows use the `g:` code of the chatroom id and the room name;
  the mode line says the room is one group.
- Frontend: guest identity in `ActivityPanel.vue:72,87,462` and `useGroupProposal.ts`; the card
  and propose flow pass `group_kind`; the picker hides when the only option is the room group.
- Research export: a room-group submission is `subject_kind=group` with its `g:` code.

### M4: Submission wake (platform)
- `platform_events.py`: `submission_notice`. `platform_wakes.py`: `dispatch_submission_received`
  with a Redis window claim per activation and a deferred enqueue (pattern of
  `schedule_leave_recheck`, `platform_wakes.py:95-106`). Called from `_dispatch_submission` and the
  validator worker. New trigger `submission_received` in `PLATFORM_TRIGGERS`
  (`orchestration/domain/models.py:267`) and `_WAKE_REASONS`.

### M5: Agent runtime: review honesty, transcript markers, duplicate reply
- `collaboration_tools.py:251-266,310-312`: frame and tool description text.
- `peer_review_repo`/`collaboration_tools.py`: a modified-replies block for the peer beside
  `render_rejections_block`.
- `turn_engine.py:4928-4936`: marker for `peer_review_modified` messages; message ids in the
  analyst's transcript rendering.
- Duplicate-reply regression test and fix in `turn_engine.py` (recheck/follow-up path).

### M6: Prompts (`crat-word-puzzle-room.json`, `crat-word-puzzle-design.json`)
TA rewrite, keeping every intent anchor the tests pin (one message per turn, at most one student
picked up, wait-first rule for peer proposals, the A1 disclosure sentence, never naming the
deliberate errors, never revealing the target or a full word before submission, identical shared
blocks):
- Opening: A1 in at most three sentences; answer 「這是題目嗎」-type questions directly.
- Demo: a fixed, verified demo puzzle in the prompt with a near-miss that fails on exactly one
  clue, instead of improvisation.
- Talk less: drop the fixed recap structure; speak only to add something new; no repeated
  candidate list; at most two sentences during free discussion; no undefined jargon
  (「聯想路徑」).
- Convergence: when a student proposes a character and three words, or several students give the
  same character, go straight to confirmation and submission; do not reopen a verified answer
  after the class agreed.
- Rounds: B1 calls `start_activity` with the clues as the brief; E1 debriefs every submission of
  the current activation; E2 calls `end_activity` in the same message; D1 reads any valid
  submission of the current activation as this puzzle's, individual or group; drop the
  前幾題/補交 rules; group voting is mentioned only when `[Activity state]` reports group mode.
- Creator: answer the creator only when addressed or given a flow instruction; treat the
  creator's answers as discussion without naming the creator as a student.
- Language: reply in the language the participant used.
- Review section: approve a peer candidate a student already proposed; reject (not modify) a reply
  that reveals an unproposed bank answer; never keep a claim about removed content; give the peer
  room to act as a teammate.
- `wakeup_config`: unchanged unless the M7 retrial shows otherwise (D-n).
- SA: told its replies are reviewed and may be revised; proposes candidates normally.
- AA: a peer message marked as revised is the teacher agent's wording; notices about it go to the
  teacher as the teacher's own strategy.
- DA (`crat-word-puzzle-design.json`): remove "no tool for teacher-to-peer" and "round changes
  confirmed by a human"; align the differing-answer rule with TA E1 b; fix rule numbering.

### M7: Staging retrial
- Deploy, sync the three CRAT agents on staging to the pack, run a guest-link trial (human or
  scripted) and record it in this dossier.

## 7. NFR Checklist

- [x] i18n: the panel brief label and any room-group label in en and zh-TW.
- [x] Audit log: brief presence and length on activation start; proposal and vote audits carry
  `actor_guest_room_id` for guests; the submission wake writes no audit row (like the existing
  platform wakes).
- [x] Tenant isolation: the room source admits only callers who pass room access; the presence
  snapshot is room-scoped; no new cross-project read.
- [x] Error handling UX: proposal errors for a creator trying to propose in a room group are a
  problem+json 403 with a specific type; the panel shows the brief only when present.
- [x] Performance: one presence read per proposal; the wake window caps teacher turns at one per
  20 seconds per activation; one extra join in the export query.

## 8. Security Considerations

- **Agent-authored public text.** The brief is new class-visible text written by an LLM
  ([R30.37]). It is length-bounded, plain text, rendered by interpolation (no `v-html`), and passed
  through `_cell` in the CSV. It cannot carry participant text in from a tool argument the way a
  free-text field could, but the module docstring's "no participant text becomes an argument"
  premise no longer holds and is restated.
- **Prompt injection.** Display names and the brief reach agent context; both go through the
  existing one-line and quote-stripping helpers. The peer-modified marker and message ids are
  platform-generated.
- **Guest identity in votes.** Proposal and vote routes keep `ensure_can_send`; the room source
  never trusts a client-supplied kind; a banned or revoked guest fails room access and cannot vote.
- **Answer leakage.** The brief reaches the peer and analyst; the TA prompt forbids answers in it
  and a test asserts the bank answers never appear in non-TA prompts (`test_agent_example_packs.py:1408-1411`).

## 9. Quality Notes

- **Existing debt.** `start_activity` reports "Started" on a no-op repeat start (fixed here).
  `submit_for_group` drops guest provenance (fixed here). Agent transcripts carry no message ids
  although the analyst prompt asks for one (fixed here). The design pack's rule numbering is out of
  order (fixed in M6). Proposals and votes are absent from the export (FU-1).
- **Patterns to follow.** Guest FK drop and kind columns: `0098_guest_identity_columns.py`.
  Snapshot-at-start columns: `0106` time limit. Platform wakes: `platform_events.py` and
  `platform_wakes.py`. Per-round teacher flow: the character-building teacher prompt and its tests
  (`test_agent_example_packs.py:1158-1184`). Guest identity in components:
  `CanvasCommentPopover.vue:32`.
- **Reuse inventory.** `connected_progress` presence logic (`platform_events.py:51-76`) for the
  ballot; `required_approvals` (`domain/group_consent.py:32-50`); `subject_code` for `g:` and
  round codes; `_one_line`/`_quoted` in the context provider; `_cell` in the export builder;
  `render_rejections_block` as the model for the modified block; `claim_join_batch` for the wake
  window.

## 10. Risks and Rollback

- **Pinned guest leaves.** A pinned voter who leaves stays undecided and can make a proposal
  unreachable until the 4-hour TTL. Pinning only connected participants limits this; a proposal
  that becomes unreachable is rejected by the existing rule. Recorded as FU-2.
- **LLM behaviour.** Prompt changes are verified by the M7 retrial, not by unit tests alone.
- **Migrations.** `0114` adds a nullable column (downgrade drops it). `0115` drops two FKs and adds
  defaulted columns; downgrade restores the FKs only if no guest rows exist, otherwise it refuses
  with a message (the 0098 precedent).
- **Branch order.** Commit `3e763808` edits the same pack file; merge it before M6.

## 11. Acceptance Criteria

- [x] AC-1: A creator room message sent before the alert is viewed leaves `first_action_at` null;
  the creator's next room message after the view sets it. Release or delete of an unviewed alert
  sets both `first_viewed_at` (via panel) and the action.
- [x] AC-2: `start_activity` and the human start route accept an optional brief of at most 200
  characters; longer or control-character input is refused with a clear tool error or 422; the
  brief is stored, returned by the active read, carried by the started broadcast, and shown at the
  top of the activity panel as plain text.
- [x] AC-3: `[Activity state]` shows the brief of the running activation as one quoted line for
  every agent role.
- [x] AC-4: A repeat `start_activity` of the running type returns an "already running" result and
  changes nothing.
- [x] AC-5: Each submissions CSV carries `round_code`, `round_started_at` and `round_brief`, with no
  full UUID, and the manifest `schema_version` is bumped.
- [x] AC-6: Ending an activation and starting the next in one turn clears every participant's form
  (component test keyed on activation).
- [x] AC-7: In a room with no bound Member Group, a guest proposes a group answer; other connected
  guests and users see the payload card, vote, and the proposal is accepted at the type's fraction
  of the pinned set; the room creator cannot propose or vote; the resulting submission records the
  guest proposer with `producer_kind=guest` and the session with `group_kind=room`.
- [x] AC-8: A room with a bound live Member Group behaves exactly as before (existing group tests
  pass unchanged).
- [x] AC-9: The state block for a room-group activation lists the room group's proposals and
  submissions under a `g:` code with the room name in the legend.
- [x] AC-10: A valid submission wakes each activity-granted binding once per 20-second window with
  the submission notice; several submissions within the window produce one wake; an invalid or
  pending submission produces none; a submission that claimed the all-submitted wake produces no
  second wake; a closed room produces none.
- [x] AC-11: The teacher's review frame and tool description state that a modified text must not
  claim removed content; a peer's later turn includes its modified replies; the analyst's
  transcript marks a modified peer message as the teacher agent's revision and shows message ids.
- [x] AC-12: A unit test reproducing the 11:58 sequence (an analyst-notice turn rechecking over a
  creator message, then that message's creator wake) posts one reply.
- [x] AC-13: The CRAT TA prompt meets every M6 bullet, keeps the pinned intent anchors, and the
  pack tests are updated so each M6 rule has an assertion; SA, AA and DA prompts meet their M6
  bullets.
- [ ] AC-14: Staging retrial through a guest link with at least three participants and two puzzles,
  recorded here: the panel shows each puzzle's clues; a group vote among guests submits an answer;
  the TA debriefs within 60 seconds of each accepted submission and ends the round; the TA's total
  text is at most the students' total text; no two TA messages are posted within 10 seconds of
  each other; the research export separates the two puzzles by `round_code`.

## 12. Test Plan

- AC-1: db tier `backend/tests/integration/test_classroom_roles_and_alerts_db.py` (update
  `:257-288`, `:290-304`; add the before-view case); unit fakes unchanged.
- AC-2, AC-4: unit tests for the tool schema and result, service normalisation, route validation;
  db tier for the CHECK; frontend `ActivityPanel` component test for the brief.
- AC-3: `activity_context_provider` unit test.
- AC-5: export builder unit test plus worker query db test.
- AC-6: `ActivityPanel` component test.
- AC-7 to AC-9: unit tests for the room source and pinning; db tier for the migration, FK drop and
  guest rows; route tests for guest propose and vote; frontend tests for guest `canVote`; state
  block unit test.
- AC-10: unit tests for the window claim, skip rules and notice; wiring test for both call sites.
- AC-11: unit tests for the frame text, the modified block and transcript rendering.
- AC-12: unit test in `test_reply_recheck.py`.
- AC-13: `test_agent_example_packs.py`.
- AC-14: manual staging run with the user, data read from the staging database and export.

## 13. SRS Delta

Amend **[R15.28]** by appending: "A valid submission to an active activation, individual or group,
also wakes those bindings, at most once per activation in any 20-second window, at the end of
that window, with a platform notice stating only the activity name, the number of valid
submissions in the window and in the activation; this wake is explicit on the same terms, is not
sent for a submission that produced the completion wake, and does not run in a closed room."

Amend **[R30.21]** by appending: "An activation may carry a round brief of at most 200
characters of plain text, set when it is started by the room creator or by a delegated agent
([R30.37]) and immutable for that activation; starting the type that is already running changes
nothing and is reported as such."

Amend **[R30.26]** second sentence to: "The active-activation read and the activation-started
broadcast carry that same projection and the activation's round brief."

Amend **[R30.39]** first sentence to: "An `ActivitySession`'s subject is either one person (a
registered user or an anonymous guest session, [R5.04]) or one group, never both and never
neither; a group is a project Member Group ([R13.28]) or the room group ([R30.49])."

Amend **[R30.41]** second sentence to: "A member of a live Member Group of the room's project that
is bound to that room, or, in a room group ([R30.49]), any participant other than the room
creator, proposes a schema-valid payload while the type's activation is live." and replace
"recording the proposer as `producer_user_id`" with "recording the proposer, user or guest, as the
submission's producer".

Amend **[R30.44]** by inserting after "start time,": "the round brief,".

Add **[R30.49]** "**Room group.** When a group-mode activity type is live in a room where no live
Member Group of the project is bound, or the room does not admit Member Groups, the room itself is
one group, named after the room. Every participant who may send in the room, registered user or
guest, other than the room creator, may propose for it. Its voter set is pinned at creation as the
proposer and every human participant other than the room creator connected at that moment, a
participant whose last connection closed within the last 30 seconds counting as connected. Votes
and proposals record whether each person is a user or a guest. A room with a bound live Member
Group never uses a room group."

Amend **[R33.10]** by appending: "Each submission row carries a truncated round code of its
activation, the activation's start time and its round brief."

Amend **[R34.02]** second sentence to end: "…and when and how the creator first acted on it after
that view (release, delete, the creator's next message in the room, or the creator's next activity
start or end in the room); a release or delete of an alert with no recorded view records the view
first." and third sentence to: "A room action closes every open alert in that room raised before
it that the creator has viewed."

Amend **[R34.06]** by appending: "A modified text must not claim content it no longer contains;
the peer agent is told of its replies the teacher modified, with the posted text, and agents
reading the room see a modified reply marked as the teacher agent's revision."

## 14. Open Questions

- Resolved (D-1): the 11:58:36 re-check never showed the creator's message; see D-1.
- Resolved (D-7): the M6 demo 茶、酒、紙 → 杯 broke the item-bank isolation rule and was replaced
  by 日、法、英 → 文、語 (near-miss 子).

## 15. Deviation Log

- D-1 (AC-12, M5): The staging worker log for room a3b76882 shows the analyst-notice turn
  (job 11c02590) popped the parked trigger and took its re-check snapshot before the creator's
  11:58:34 message committed, so the re-check never showed that message and nothing marked it
  covered. The creator wake (job 4fe0987f) parked behind the turn at 11:58:34.57, and the drain's
  follow-up (job fea3877c) answered it at 11:58:42. The path §4.7 suspected (a re-check that showed
  the message, then its wake) already yields one reply through the covered skip. The user chose
  (2026-11-16) to tell the model rather than skip deterministically: when the message that woke a
  turn has a later reply from the same agent, the time note says so with both times and tells the
  agent to reply with nothing if that reply already answers it. AC-12's test reproduces the real
  sequence; a second test pins the covered path.
- D-2 (M3): Owners-only rooms are treated as room-group rooms alongside rooms that do not admit
  Member Groups, since neither can host a Member Group submission ([R30.49] names the second case
  only).
- D-3 (M3, security audit): The room-group proposal listing returns a proposal only to its
  pinned voters and the room creator, matching the single read; a participant who joined after
  the ballot was pinned sees the room group as eligible but no open proposal (FU-6).
- D-4 (M5, security audit): Message bodies lose the brackets and parentheses of the new
  `[message_id ...]` prefix and the teacher-revision mark, as they already did for the room notice
  marker and participant codes; §8 called both "platform-generated" but a body could spell them.
- D-5 (M5): `resolve_member_group_names` resolves any id no Member Group holds as a room, instead
  of taking the room id from each caller, so the dashboard watchlist names room-group rows too.
- D-6 (M6): Each puzzle runs as its own activation (B1 starts it with the clues as brief, E2 ends
  it), so D1 reads any valid submission of the current activation as this puzzle's and the
  earlier-round and late-submission rules are removed; four pack tests that pinned the
  single-activation rules were rewritten to pin the new ones. The TA prompt grew from 15,577 to
  about 17,300 characters, mostly the review section and the fixed demo.
- D-7 (M6, user decision 2026-11-16): Students who linked a character to the first clue often
  found nothing for the second, so the user chose multi-answer rounds over a single answer. The
  C1 to C8 bank was replaced by eight rounds with two or three targets each, every one listed;
  any listed target counts as correct, and E1 reveals the others. The demo became
  日、法、英 → 文、語 with near-miss 子: the user had approved 茶、酒、紙 → 杯 earlier the same
  day, but 杯 is a T1 test target and 茶杯 a T1 component word, which the research item bank's
  isolation rule (training items share no target or component word with test items) forbids.
  Every round was checked against the MOE concise and revised dictionaries: no answer outside the
  listed set is a T1 target, no listed word is a T1 component word, no two clues form a word, and
  no round shares two clues with a CCRAP or NTNU 2009 template item. Words the other agents'
  prompts already use (同學, 好奇, 意見, 決定) were excluded, because the pack test keeps bank
  words out of every non-teacher prompt. The DA's "exactly one target" rule now asks for every
  target. The isolation check must be rerun against the research team's T1 bank whenever either
  changes.

## 16. Follow-ups

- FU-1: Export proposals and votes (who proposed, pinned set, outcome) for research on group
  consent.
- FU-2: A pinned voter who leaves the room stays undecided; consider dropping departed guests from
  the undecided count.
- FU-3: Rooms with bound Member Groups where some participants are in no group still give those
  participants nothing; consider an individual fallback line in the state block.
- FU-4 (security MEDIUM): `submission_received` is a platform trigger, so it bypasses autostop and
  can fire every 20 seconds for a whole untimed round; a participant scripting valid submissions
  drives about 180 teacher turns an hour on the teacher's key. Cap submission wakes per activation
  or count them toward autostop.
- FU-5 (security MEDIUM): A room-group ballot is everyone connected, and the guest link mints a
  new guest session per fresh browser id up to the room's guest cap, so one person with several
  guest sessions can carry a room-group vote. Decide whether to accept this as a documented limit
  of anonymous voting or bound guest weight.
- FU-6: A participant not pinned on the open room proposal (late joiner) sees the propose form,
  and proposing fails because one proposal is already open; the panel should say a vote is in
  progress among the people present when it started.
- FU-7 (quality Warning): The submission wake's window is anchored at the first submission's
  `created_at` with no upper bound, so a submission between the Redis claim's expiry and the
  deferred job, or an asynchronously validated one, can be counted in two windows' "N new". Count a
  bounded window or keep a per-activation watermark of the reported total.
- FU-8 (quality Info): `ActivitySession.group_kind` is persisted but not read; the human start
  route drops a brief sent to an already running type without saying so; `untold_rejections`
  now serves modifications too under its old name and partial index; the brief limits are
  restated in the tool description and `ActivityPanel.vue` instead of derived from
  `MAX_BRIEF_CHARS`.
- FU-9 (pre-existing): The "all-submitted, else submission wake" decision is written in both the
  submit route and the validator worker; one `platform_wakes` entry point would keep them from
  diverging.
- FU-10 (pre-existing, tests): `test_activities_services.py::TestRegistry::test_list_registered_returns_id_and_title_sorted`
  fails when run after tests that register validators (a `-k` selection), and
  `AgentToolsView.test.ts` failed twice under full-suite load and passed alone; both pass in
  their normal runs.
