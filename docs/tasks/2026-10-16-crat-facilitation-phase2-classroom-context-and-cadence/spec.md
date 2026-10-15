---
type: feature
status: in-progress
created: 2026-10-16
requirements: [R13.33, R13.34, R13.72, R13.73, R15.01, R15.02, R15.03, R15.06, R15.06a, R15.07, R15.08, R15.09, R28.02, R28.05, R28.10, R28.12, R28.18, R30.15, R30.37, R30.38, R30.43, R32.04]
depends_on: []
---

# Classroom facilitation phase 2: participant context, room cadence, and three course packs

## 1. Summary

Give teacher and observer agents a bounded, accurately scoped view of the people in their
room; use consistent participant codes across chat, activity and authorized draft context;
isolate human-authored room cadence and agent self-adjustments from other rooms; and align
the CRAT, character-building and object-reinterpretation packs with those capabilities.
Include participation-invitation rules: the user explicitly requested calling on participants,
not merely displaying facts to the agent. Q-3 settles them as pedagogical prompt rules, not
server-enforced eligibility.

This is one feature dossier with sequenced milestones, not three unrelated implementations.
It continues the October 12 CRAT facilitation work; the phase-2 name does not rename or reopen
the completed predecessor dossiers. Analysis was verified against `a37e8875` on 2026-10-16.
No application code is part of this dossier's preparation; its SRS Delta was applied at approval.

Sources:

- `docs/audits/2026-10-12-crat-ta-multi-speaker-facilitation/findings.md:131` (missing model
  roster), `:174` (agent-wide tuning), `:278` (chat-only participants lack codes),
  `:330` and `:344` (participant-context follow-ups).
- `docs/tasks/2026-10-12-wakeup-human-count-and-trigger-toggle/spec.md:347` (room isolation)
  and `:352` (runtime values accidentally becoming designer defaults).
- `docs/tasks/2026-10-12-crat-pack-prompt-platform-alignment/spec.md:343` (sibling packs)
  and `:349` (adopt the newly implemented counting/toggle capabilities).

**Approval state:** approved 2026-10-16 as a whole (Q-1 to Q-13, goals, non-goals, design,
ACs). The SRS Delta in §13 was applied to `REQUIREMENTS.md` at approval.

## 2. Goals and Non-goals

**Goals**

- A teacher agent can distinguish a currently connected participant who has not spoken in the
  observed window from someone who spoke earlier, without treating either as a measure of attention.
- Chat-only participants and anonymous guests have server-derived codes that agree with their
  activity/draft codes. Observers can attribute an observation without inventing identities.
- Teachers may invite participation under a clearly specified policy; observers remain private
  and cannot use this feature to post into the room.
- A teacher or agent changing cadence in room A does not change room B, the designer's defaults,
  or the shared Agent version. Human room preferences survive the agent's periodic reset.
- All three courses retain their distinct teaching progression while using supported tools,
  actual worksheet fields, meaningful silence behavior and the new participant facts.
- Each example teacher can run a lesson without asking the human teacher for content: it ships
  a reviewed bank of ready-to-use rounds (Q-11..Q-13), and the CRAT teacher judges answers
  against that bank.
- Demonstrate the outcome on a running stack, including parallel rooms and a 30-minute class.

**Non-goals**

- Attendance, psychological inference, engagement scoring, ranking students, or an all-project
  roster. Online presence and a recent message window are not a class denominator.
- General agent working memory, private answer-key channels, a new A2A `instruct` tool,
  automatic observation publication, or changes to observer confidentiality.
- Changing worksheet schemas, validators, validator answer keys ([R30.25]) or curricular
  learning objectives.
- A platform content field or tool for rounds (`ActivityType` has none,
  `backend/contexts/activities/domain/models.py:100-127`), runtime substitution of
  creator-posted rounds (Q-12), or generating rounds at runtime.
- Automatic overwrites of installed/customized agents, an example-pack upgrade service, or
  an automatic migration of old global runtime tuning into every room.
- A global pseudonym redesign, cross-course identity linkage, or claiming codes are anonymous.
- Changes to GraphRAG, sandbox artifact handling, provider models or billing behavior.
- An autonomous phase scheduler and whole-class completion calculation (Q-4).
- Server-enforced invitation eligibility, cooldowns, an invitation tool or an invitation-policy
  table, and any new private recommendation channel (Q-3).

## 3. Clarifications

| ID | Question | Decision / recommendation | Rationale |
|---|---|---|---|
| Q-1 | Who receives participant facts, and are invitation rules included? | **Confirmed 2026-10-16:** teacher and observer agents; include calling/invitation rules. | User selected both roles and added that calling rules are also required. This supersedes the earlier facts-only suggestion. |
| Q-2 | Which course packs are in scope? | **Confirmed 2026-10-16:** CRAT, character building and object reinterpretation, validated in stages. | User selected all three, not CRAT alone. |
| Q-3 | How are participation invitations decided? | **Confirmed 2026-10-16: B.** The teacher agent judges from the classroom facts using the prompt rules in §6.2. | Smallest contract that satisfies the request for calling rules. Rejected: A, platform-enforced eligibility and transactional cooldowns (5-minute quiet window, 10-minute repeat, an `invite_participant` tool, a policy table and creator API), which still could not stop targeted prose outside the tool; C, observer recommends privately and a human decides, which needed a private delivery contract that does not exist. |
| Q-4 | Who decides the timed/completion-based transitions in the two sibling courses? | **Confirmed 2026-10-16: A.** The human teacher confirms each transition; the AI summarizes and reminds. | Current silence timers and recent activity windows cannot establish deadline or whole-class completion claims. Rejected: B, authoritative phase timing and completion tracking, a separate product capability (FU-4). |
| Q-5 | Does this depend on either unfinished dossier? | **Approved 2026-10-16: `depends_on: []`.** See the complete active-dossier scan below. | Neither unfinished dossier supplies this feature or requires concurrent changes to the same implementation region. |
| Q-6 | What persists when an agent's self-tuning refreshes? | **Approved 2026-10-16:** three layers: designer defaults, durable human room overrides, temporary agent runtime overrides. Refresh clears only runtime overrides. | Preserves the teacher's intentional room settings and prevents runtime values becoming designer defaults. |
| Q-7 | Who can manage room cadence? | **Approved 2026-10-16:** current room creator/admin under the existing creator rule; a project editor may manage normal bindings when they can read the room. Hidden observer settings remain creator-only. | A Project Member who created a teaching room needs a narrow room setting, not permission to read the agent's prompt or other configuration. This is a deliberate new authorization contract. |
| Q-8 | Does approving all three packs remove the earlier CRAT validation gate? | **Approved 2026-10-16:** analysis and implementation are in one dossier; retain the live CRAT pilot before enabling the rewritten sibling packs for classroom use. | Integrates the work without treating the earlier unchecked classroom ACs as passed. |
| Q-9 | Identity and bounded context defaults? | **Approved 2026-10-16:** shared existing codes, no new email fallback; up to 100 supplemental participant rows and 2,000 estimated tokens; explicit unavailable/truncated states. | Keeps labels usable and bounds context without changing every existing code consumer. |
| Q-11 | Where do the example teachers' rounds come from? | **Confirmed 2026-10-16:** the implementer drafts a built-in bank per course (CRAT 8 rounds of three clue characters with target character and the three words; character building 6 component sets; object reinterpretation 6 objects) with a verification table; the requester or an educator approves every item in the PR before release. | Requester's choice, raised during review: an example should be usable as installed instead of asking the teacher for content. Rejected: requester-supplied items (blocks M3 on content delivery) and runtime generation (CRAT word combinations need human verification, the reason the current prompt forbids it). |
| Q-12 | What if the room creator posts their own round? | **Confirmed 2026-10-16:** the teacher always uses its bank, in order, and does not adopt creator-posted rounds. | Requester's choice. Rejected: creator rounds take precedence with the bank as fallback. Supersedes the CRAT dossier's D-4 creator-only clue rule (`docs/tasks/2026-10-12-crat-pack-prompt-platform-alignment/spec.md:328-333`). A teacher who wants other rounds edits the installed copy. |
| Q-13 | Where do the answers live? | **Confirmed 2026-10-16:** only in the teacher agent's `system_prompt`; peers and analysts receive none. | Requester's choice. CRAT Q-7 kept answers out of every agent because any project member could read `system_prompt` (`crat-pack-prompt-platform-alignment/spec.md:66`); `2026-10-13-agent-config-visibility-and-project-reads` (implemented) now limits full agent configuration to `RESOURCE_CREATE_EDIT` holders (its Q-1, `:61`; AC-3, `:267`). Rejected: answers for peers too (another leak path, and a peer's role is to think alongside students), and no answers (the teacher would still wait on the human to judge). |
| Q-10 | One dossier or three? | **Confirmed 2026-10-16:** one dossier with milestones M1 to M4, each committed and gated on its own. | Requester's choice. Rejected: three dossiers chained by `depends_on`. |

**Active-dossier scan at `a37e8875`.** Frontmatter, rather than historical BOARD prose, is authoritative.

| Unfinished dossier | State and remaining scope | Relationship |
|---|---|---|
| `2026-07-19-large-artifacts-silently-dropped` | `in-progress`; remaining AC-2 is Linux/gVisor live delivery validation (`spec.md:485`). | Its artifact acquisition/persistence code is already implemented. This dossier touches participant assembly in `turn_engine.py`, not artifact handling; no outstanding overlapping edit or prerequisite. If that validation discovers a new runtime change, reassess overlap before building. |
| `2026-10-12-graphrag-phase5a2-evidence-refs-contract` | `in-progress` since 2026-10-16 (milestone B on PR #260, deploy-gated); Neo4j read/write/restore and deploy cleanup (`spec.md:47`). | No logical or file-region overlap. |

Implemented prerequisites already available: human counting/toggle capability, timestamped
transcripts, stale-reply recheck, agent-config visibility/project reads, UI roster completeness,
group submissions and live drafts. Their presence does not prove classroom effectiveness.
The previous CRAT AC-10/AC-10a are still unchecked (`crat-pack-prompt-platform-alignment/spec.md:297`).

## 4. Current State

### Participant data flow

- `_SystemBlocks.build` has activity, canvas, memory and other blocks but no participant roster;
  room assembly does not supply one (`backend/contexts/agents/application/runtime/turn_engine.py:1281`,
  `:3104`). Human provider messages use `Name: body` (`:4203`). Rechecks share that renderer (`:4520`).
- Activity codes are made only for subjects in the recent activity rows
  (`backend/contexts/activities/application/activity_context_provider.py:125`, `:265`).
  A code legend is therefore not a full participant list or evidence that everyone has submitted.
- `subject_code` uses `u:` plus eight UUID characters; group codes use `g:`
  (`backend/contexts/activities/domain/subject_code.py:30`, `:34`). Authorized drafts use the
  same helper (`backend/contexts/agents/application/runtime/draft_tools.py:46`). Prefixes can
  collide; the current function does not promise uniqueness or room-local anonymity.
- Registered guests use account identities; anonymous guests use session identities
  (`backend/contexts/conversation/application/room_guests.py:22`). Preserve this mapping.
- Presence reconciles live connections and deduplicates multiple tabs
  (`backend/contexts/conversation/infrastructure/presence.py:268`). The facade currently returns
  an empty list on Redis failure (`backend/contexts/conversation/interfaces/facade.py:467`).
  That behavior cannot mean “nobody is online” in the new model context.
- Existing composing recency is room-wide, while current typing sets are per participant
  (`backend/contexts/conversation/infrastructure/presence.py:185`, `:212`). The former cannot
  enforce a participant-specific invitation exclusion without additional state.

### Wake-up data flow

- The tool accepts only `agent_id` and writes through `AgentsFacade.patch_agent`
  (`backend/contexts/agents/application/runtime/tool_registry.py:402`, `:415`;
  `backend/contexts/orchestration/application/wakeup_service.py:418`).
- Message evaluation, silence evaluation and the final worker gate read the global agent config
  (`wakeup_service.py:115`, `:256`; `backend/app/workers/tasks/orchestration.py:107`).
- Room settings also patch the shared agent and use Agent.version
  (`frontend/src/slices/conversation/composables/useChatroomBindings.ts:350`, `:364`).
- Human agent patches update the authored snapshot (`backend/contexts/agents/application/agent_service.py:880`,
  `:892`); the editor can therefore promote runtime drift into the baseline.
- Refresh scans agents, not bindings (`backend/app/workers/tasks/orchestration.py:321`).
  Bindings have a composite room/agent key but no incarnation/version
  (`backend/contexts/conversation/infrastructure/tables.py:67`;
  `backend/contexts/conversation/domain/models.py:102`). Unbind deletes the row and rebind can
  reuse the pair (`backend/contexts/conversation/infrastructure/repositories/chatroom_repo.py:385`, `:425`).
- Redis cadence state is room/agent scoped but lacks binding generation
  (`backend/contexts/orchestration/infrastructure/wakeup_state.py:23`). A stale turn or worker
  can therefore refer to the same pair after a new binding is created.
- Headless turns also receive `update_wakeup` today (`tool_registry.py:854`;
  `turn_engine.py:1664`). Global `call_only` also participates in A2A policy
  (`backend/contexts/agents/application/a2a_scope.py:58`); do not turn this into a room permission.

### Course packs

Room teacher, peer and analyst prompts are in lines 24, 46 and 68 respectively of
`backend/contexts/agents/infrastructure/examples/packs/crat-word-puzzle-room.json`,
`character-building-room.json`, and `object-reinterpretation-room.json`.

- The room packs omit message `count`, whose default is `all`; sibling teachers also omit
  `self_toggle`, whose default is empty (`backend/contexts/orchestration/domain/models.py:211`, `:259`).
- Sibling prompts use unavailable `instruct`, fractional waits and cadence changes their
  declarations do not authorize. Character building treats silence firings as elapsed time;
  object reinterpretation estimates start time and whole-class completion from recent submissions.
- Character building says one character per submission, but its form has `char_01` through
  `char_10` and requires at least two filled fields
  (`backend/contexts/activities/infrastructure/examples/courses/chinese-character-creativity.json:64`, `:69`, `:77`).
  The object form has `use_1` through `use_5`, `best_pick` and `best_pick_reasoning` (`:132`, `:145`, `:171`, `:177`).
  Submission attempts cannot stand for number of ideas.
- No pack ships ready-to-use rounds. The CRAT teacher has the creator post each round's clues,
  forbids generating them because word combinations need human verification, holds no answer,
  and asks the teacher to confirm every submission (`crat-word-puzzle-room.json:24`, sections
  A1, A2, B1, E1, E2). The CRAT designer keeps answers out of every agent because "project
  members can read agent settings" (`crat-word-puzzle-design.json:24`), which is no longer true.
  The sibling teachers say components or the object come "from the teacher or the design agent"
  with no step to obtain them (`character-building-room.json:24`, `object-reinterpretation-room.json:24`, B1).
  The course file holds only payload schemas, and agents see submissions, not round content
  (`activity_context_provider.py:106`). Tests pin `請老師確認答案`, forbid `本課題組` in CRAT
  agent prompts and pin the designer's settings-visibility sentence
  (`backend/tests/unit/test_agent_example_packs.py:773`, `:774-775`, `:806`).
- Installed examples are independent copies; name-matched agents are skipped on reinstall
  (`backend/contexts/agents/application/example_service.py:188`, `:236`, `:245`).
  Existing installations will not adopt new prompts automatically.

## 5. Design

### Options considered

**Prompt-only rewrite:** smallest change, but cannot supply missing participant identities or
stop global cadence mutations. Rejected as the integrated solution.

**Duplicate the agent for every room:** isolates current settings but duplicates editable
configuration and does not fix participant context or the install-by-name behavior. Rejected.

**Room context plus binding-owned cadence, then pack adoption:** recommended. Uses current
domain boundaries, keeps designer defaults shared, and gives each room explicit local state.
Costs a migration and a coordinated worker rollout.

**A full classroom state machine:** would give deadlines, cohort completion and durable stage
memory, but adds a separate product capability. Rejected at Q-4 (FU-4).

### Decision

Recommend the third option, in four milestones (§6.5). Shared infrastructure is generic;
course-specific teaching decisions remain in the six example packs. Do not add branches in
runtime code keyed on CRAT or any course name. Invitations are prompt rules (Q-3) and phase
transitions are confirmed by the human teacher (Q-4); neither adds platform state.

## 6. Detailed Changes

### 6.1 Participant context and consistent codes

**Backend contract.** Add a conversation facade query returning a typed snapshot with an
observation timestamp, presence availability, verified live participant IDs and truncation
information. Bound the infrastructure read itself; do not fetch an unlimited roster and
truncate only after resolving names. Keep the old `present_user_ids()` contract for existing
callers. Runtime uses the new result and never turns lookup failure into an empty class.

The supplemental cohort is the union of human sender IDs in the raw messages actually included
in this model request and verified current presence. Do not enumerate all project members,
all enrolled guests or the full historical sender set. Summaries are not authoritative sender
facts. Prefer transcript speakers, then other connected people in deterministic ID order.
Cap the block at 100 rows and 2,000 estimated tokens; include explicit truncation and unknown
markers. A participant omitted from this block retains their inline transcript code.

Each row contains the server-derived code, a normalized display label when available, guest
marker, connection state (`connected`, `not_connected`, or `unknown`) and whether a message
from that person occurs in the included raw-history window. Include window boundaries and
snapshot time. Say “no message observed in this window,” never “has never spoken.” Presence
means a live connection, not attention, attendance or permission to demand a response.

Change human transcript labels to code plus display label. Use the same identity code in
normal turns, observer turns and recheck rendering; system notices remain system notices and
agent identities are not human subjects. Resolve human display labels without login-email
fallback for this model-facing path; code-only is sufficient when a name is missing. Keep
existing UI labels, guest markers and owner-authority qualifications. Deleted guest identities
can retain the stored sender's code but gain no reconstructed name.

Expose the pure participant/group code helpers through a public seam (or move just those
helpers to `shared_kernel` with compatibility re-exports). Do not move activity-specific
`outcome_word` into shared kernel or add runtime-to-activity-infrastructure imports. Preserve
the existing `u:`/`g:` values across activity, draft, aggregate and export consumers.

Detect duplicate code prefixes within the observed identity set. Mark such codes ambiguous,
do not merge two rows silently, and exclude ambiguous identities from targeted invitations.
This is local detection, not a global uniqueness guarantee. Analysts must qualify attribution
instead of inventing suffixes. Group submissions remain group facts, not individual contributions.

Add a measured-and-rendered participant block to `_SystemBlocks`. Budget both its tokens and
the new inline code/label overhead before compaction and knowledge allocation. Recompute
transcript-derived rows after compaction; refresh neither names nor the presence timestamp by
accident during rendering. Rechecks include codes for newly arrived speakers and clearly retain
or explicitly replace the earlier presence snapshot. Headless turns get no room roster.

### 6.2 Participation invitations (Q-3B: pedagogical prompt rules)

No invitation-policy table, new scheduler, invitation tool or enforceable cooldown is added.
Teacher prompts may invite one identifiable, currently connected participant when the course
step permits discussion and the provided window gives a reason to broaden participation.
They skip the creator, avoid interrupting an individual-work phase, use a gentle optional
question, rotate away from a person just invited in visible history, and accept no response.
Unknown/truncated presence or ambiguous identities never justify a targeted invitation.
Recent silence is not evidence of nonparticipation; visible activity and permitted draft
signals are considered without quoting private text.

Invitations preserve observer privacy, permit declining or remaining silent, and prohibit
public rankings, claims of laziness/inattention, fabricated personal traits, or exposing
private observation contents. A teacher prompt must not treat an invitation as a prerequisite
for submitting work or remaining in the activity. Names in ordinary explanatory prose do not
become platform authorization or evidence of a completed invitation.

These are model guidance, verified with representative live transcripts, not server-enforced
timing guarantees; the adoption guide says so. Observers use codes, report evidence limits,
and never post invitations. Human moderation and normal room controls remain unchanged.

### 6.3 Room-owned wake-up configuration

**Three layers.** Effective configuration is a pure, shared domain resolution of:

1. Current designer defaults (`Agent.wakeup_config`, no room runtime state).
2. Durable human room overrides.
3. Temporary self-tuning overrides for that exact live binding.

Designer-only policy remains authoritative: `self_toggle`, `call_only`, `allow_self_open`,
hard/soft bounds and refresh interval cannot be changed through the agent tool or a narrow
room editor. The room editor may change the two numeric thresholds, their enabled flags,
message `count` and the role-appropriate autostop limit within existing bounds. It may pause
both autonomous triggers; the agent tool still may not disable its last trigger. A designer
`call_only` setting suppresses autonomous triggers in all rooms and keeps its A2A meaning.

Agent overrides are limited to the existing four tool arguments. Room human changes replace
only explicitly supplied fields and clear runtime overrides for those same fields, so saving
an unrelated setting cannot adopt another field's agent-made value. Separate explicit actions
reset runtime only or return all room fields to designer defaults; neither changes other rooms.

**Persistence.** Add an immutable fresh `binding_id`, a `wakeup_version`, typed sparse human
and runtime override documents, and nullable `runtime_started_at` to `chatroom_agents`.
Keep uniqueness of room/agent. Default layers are empty; rebind creates a new incarnation.
Application repositories expose typed read/CAS operations; routes never access tables directly.

Each effective read uses current defaults, validates the human layer against current bounds,
then validates runtime against current bounds and `self_toggle`. Ignore/reconcile revoked
runtime fields; do not keep a previously allowed toggle effective after revocation. Numeric
values are clamped consistently, and designer call-only wins. Return source/provenance per
editable field without returning unrelated agent configuration.

Define a shared orchestration resolver used by message evaluation, silence scanning, final
worker execution and the setting read/tool result. Stale cached configs cannot survive a
defaults revision. Capture the relevant Agent version and binding version for writes; lock or
CAS against both so a concurrent designer permission edit cannot be bypassed by a stale tool.
The resolver performs no divergent ad hoc validation in individual callers. Reconciliation
writes use CAS and audit changed fields once; pure evaluation can suppress invalid overrides
immediately even before durable cleanup completes.

**Tool and lifecycle.** Build `update_wakeup` with server-owned room and binding incarnation.
Recheck the live binding on every invocation; compare binding ID/version in the mutation.
Headless calls with no live binding do not receive the tool and the facade rejects forged
direct calls. Merely supplying a room ID is not sufficient authority. A bounded retry starts
again from the original requested patch and the latest policy; no cross-room fallback.

Generation-scope Redis counters, deferred every-N firing, silence clocks and worker payloads
to the binding ID. Before starting a queued turn, resolve that exact incarnation. Unbind/rebind
cannot revive old overrides, counters or pending wakeups. Cleanup can expire abandoned keys;
correctness must not depend on winning a race to delete them.

**Refresh/reset.** The first actual runtime override starts its refresh interval; later tuning
does not postpone it. Refresh clears only that binding's runtime layer and clock, preserving
human room overrides. A changed interval is evaluated against the existing start time, so
shortening it can make the binding immediately due. A new override after refresh starts a
new interval. Refresh scans only due bindings in bounded batches, retaining per-item savepoint
failure isolation. It never patches Agent.version.

Configuration changes do not reset autostop or accumulated message count. Disabling a message
trigger discards its deferred-fire flag. Enabling silence or changing its interval re-arms
that binding's silence baseline at the change time to avoid an immediate surprise reply.
An effective no-op changes neither timers nor version. Reset actions follow the same rules;
they cannot bypass the requirement that a human message resets autostop.

**API and frontend.** Add GET/PATCH `/chatrooms/{room_id}/agents/{agent_id}/wakeup` and POST
`/chatrooms/{room_id}/agents/{agent_id}/wakeup/reset`, with typed allowlisted models. PATCH/reset
require `binding_id` and `expected_version` in the body, matching existing versioned mutation
style; stale versions return the standard 409 problem. Reset scope is `runtime` or `room`.
GET returns only safe wake-up fields, effective values, their sources, revision and next
refresh time. It never returns prompts, tools, skills, keys or hidden observer existence.

Authorize room access first, then Q-7's narrow management rule. Observer binding reads/writes
always require the existing creator rule. Do not widen `GET /agents` or add full config to the
general binding list. All paths recheck project/room liveness and membership.

The conversation slice uses new query/mutation wrappers, not `agentsApi.patch`. Preserve its
sequential save behavior but key it by binding incarnation/revision. Show inherited value,
human room choice, agent adjustment, and refresh/reset meaning. Handle loading, 403, 404,
409 reload, unavailable and reset confirmation. The designer editor displays only defaults.
Regenerate OpenAPI/client types. No frontend cross-slice internal imports.

### 6.4 Course pack and authoring alignment

Update the three `*-room.json` packs and their corresponding `*-design.json` prompts. Preserve
pack keys, agent names and the existing task sequence. Do not grant room activity, draft or
canvas authority on installation; use the existing grant mechanism and explain missing grants.

Every teacher ships explicit `count: human`, integer intervals and declared `self_toggle`
permissions matching its prompt. An analyst uses human counting too; a peer remains
mention/silence driven as configured, without fabricated teacher-to-peer `instruct` calls.
Keep at least one autonomous teacher trigger enabled when the agent tunes; human pause is
different. A no-op phase instruction must not generate repeated writes every turn.

**Built-in round banks (Q-11..Q-13).** Each room teacher's `system_prompt` carries a numbered
bank and uses it in order, one round per cycle. It never asks the human teacher for content
and does not adopt rounds posted in the room by anyone, including the creator (Q-12).

- **CRAT:** 8 rounds. Each has three clue characters, the target character and the three words
  formed. The teacher posts the clues and never reveals the target or the words before the
  group's submission. A submission matching the bank answer is confirmed correct. A different
  target character is not declared wrong: the teacher says it differs from the reference
  answer, shows the words the group formed, and asks the human teacher to judge whether it
  is valid. Hints may use the answer only to point at a clue character or a word structure,
  never to state the target or a full word. After the last round the teacher says the bank
  is finished and leaves ending the activity to the human teacher.
- **Character building:** 6 component sets, each with reference characters the teacher may
  use to check and discuss work but not post before submissions, plus a one-line rule for
  how the components combine.
- **Object reinterpretation:** 6 everyday objects, each with reference uses the teacher may
  cite after students have shared, never as a list to copy.

Peers and analysts carry no bank or answers (Q-13). A peer that is asked for the answer says
it does not know it. The teacher's refusal to reveal answers before submission is prompt
guidance: a student can try to extract it by prompt injection, and nothing in the platform
prevents that (§8).

Every bank item is drafted by the implementer with a verification table in the dossier (for
CRAT, each of the three words and why it is a common word; for the others, why each reference
character or use is valid) and approved item by item by the requester or an educator in the
PR. An unapproved item is removed, not shipped. This replaces the CRAT room prompt's
creator-posted clues and creator confirmation (sections A1, A2, B1, E1, E2) and its "no
answer" boundary. The CRAT designer drops the obsolete "project members can read agent
settings" rationale. It now explains that answers in a teacher prompt are readable by anyone
who can edit agents. That scope is wider than [R30.25]'s Project-Owner scope for
`validator_config`, so a teacher who needs owner-only keys keeps them in the validator. The
sibling designers describe the same bank structure.

The following is the proposed baseline. Numeric values are release defaults,
not guaranteed provider response times. Teacher human settings can override them.

| Course/stage | Teacher cadence | Teaching and transition rule |
|---|---|---|
| CRAT A/B: setup and candidate collection | human `n=2`; silence enabled, `t=2`, normal autostop 2 | Post the next bank round's clues; acknowledge distinct candidate contributors using platform identities. Never reveal the target or words before submission. |
| CRAT C/D: comparison and consensus | same baseline; room-local tuning may adjust `n` within declared bounds | Check the lesson's word-verification conditions, encourage a new voice under §6.2, then the group submits the existing required fields. `g:` submissions remain group work. |
| CRAT E: confirmation/reflection | human `n=2`; silence disabled temporarily, human trigger remains enabled | Teacher checks the submission against the bank answer; a different target goes to the human teacher to judge. Moving to the next round is confirmed by the human teacher (Q-4). Do not use a named person's text as authenticated authority. |
| Character A/B: rules/components/demo | human `n=2`; silence enabled at 2 minutes, autostop 2 | Present the next bank component set. Remove the current prompt's fixed 木/口 decomposition unless the bank review approves it. |
| Character C: individual work | human `n=3`; silence disabled temporarily | Preserve quiet work; no automatic targeted invitation during this stage. Explain the actual multi-field worksheet and minimum two filled characters. Only the human teacher confirms its end (Q-4). |
| Character D: voluntary sharing/reflection | human `n=2`; silence enabled at 2 minutes | Invite optional sharing under §6.2, then summarize strategies, not student ranks. Peer examples follow its own prompt and supported wakes. |
| Object A/B: concepts/item/demo | human `n=2`; silence enabled at 2 minutes, autostop 2 | Preserve functional-fixedness discussion; present the next bank object. |
| Object C: divergent individual ideas | human `n=3`; silence disabled temporarily | Explain five idea slots; count filled fields only when that information is actually available. Never equate three submission attempts with three ideas. |
| Object D/E: choose/reason/share | human `n=2`; silence enabled at 2 minutes | Explain `best_pick` and reasoning. Ask for human phase confirmation (Q-4) instead of claiming most students finished; optional invitations follow §6.2. |

All three analysts use explicit human `n=20`, keep their existing valid silence/autostop
settings, use only supplied codes, preserve group attribution, and return an empty response
when no new observation is warranted. Supplemental identity facts never change aggregate
denominators or make recent activity a complete record. Peers must state exactly when to return
an empty reply and cannot claim to receive a non-existent instruction tool. Character peer
silence remains disabled throughout individual work unless a teacher explicitly enables it;
do not infer that “half of five minutes elapsed” from a silence firing.

For all prompts: no fractional/subminute wake promises, no `instruct`, no guaranteed deadline
from silence, no invisible whole-class completion inference, no names in codes-only analyst
notes, no invention of codes, no request to the human teacher for round content. Authoring prompts must
describe manual application, room scope, actual tool permissions, the §6.2 invitation rules and human-confirmed transitions.

Existing installs remain untouched. Release/adoption instructions explain that reinstall is
idempotent and does not update old copies; show how an editor reviews and manually applies
the prompt and default changes without copying credentials or granting permissions. Record
the tested model/configuration and pack revision in classroom evidence.

### 6.5 Milestones and release sequence

| Milestone | Deliverable | Gate |
|---|---|---|
| M1 | Participant snapshot, shared codes, request budgeting and observer attribution. | Identity/unknown/truncation/security tests and provider-request inspection pass. |
| M2 | Three-layer room cadence, lifecycle migration, APIs/UI and generation-scoped workers. | Cross-room, CAS, reset/refresh, permissions and real DB/Redis tests pass; coordinated runtime cutover documented. |
| M3 | §6.2 invitation rules, built-in round banks and six aligned packs. | M1/M2 deployed; every bank item approved in the PR; prompt/schema/domain tests and CRAT live pilot pass. |
| M4 | Sibling-course classroom runs and adoption guide. | All three stage paths and two parallel groups observed, including the 30-minute sustained-discussion run. |

Commit coherent milestones independently on the feature PR branch. Do not mark this dossier
implemented after M1/M2 or unit tests alone. No user authorization to push or deploy is implied
by approving this dossier; implementation follows the repository PR/self-review workflow.

## 7. NFR Checklist

- [ ] i18n: room settings, errors and reset explanations use the existing localization
  mechanism; frontend strings use `$t()`. Pedagogical pack text retains
  the existing course language rather than masquerading as translated product UI.
- [ ] Audit: room-setting changes/resets and agent tuning/clamps include actor and
  room/binding identity. No names, prompts, observation bodies or draft contents in audit
  metadata.
- [ ] Tenant isolation: current room ACL and narrow creator/editor rules on all new endpoints;
  live binding and creator grants are checked again at tool/action execution.
- [ ] Error handling UX: explicit loading/error/unknown/empty states; standard problem types;
  stale revision recovery cannot silently overwrite the teacher's next change.
- [ ] Performance: bounded snapshot/query/token counts, batched label lookup, paginated due
  bindings and no per-participant SQL/Redis round trips.
- [ ] Compatibility: additive schema first, controlled worker cutover, no mixed old/global
  and new/room-local cadence writers; existing installs and code values remain stable.

## 8. Security Considerations

- Names, chat bodies, imported prompts and draft text are untrusted. Use the existing label
  normalization and delimiters; explicit server-derived codes/facts cannot be supplied by a
  participant's chosen name. Labels, including the creator's, are not authentication.
- The new roster exposes currently connected silent participants to the room's teacher and
  observer models; this is an intentional scope decision Q-1, bounded to the room. It does
  not authorize cross-room lookup, login-email disclosure or private observer publication.
- Codes are pseudonyms and can collide; never advertise anonymization. No broad code-to-user
  lookup API is added. Existing activity/draft visibility and payload-consent checks still apply.
- Self-tuning cannot modify another room, authored settings, permissions, call-only policy,
  bounds or refresh rules, even if a prompt is injected. Revalidate binding incarnation and
  current policy at writes, including retries and revoked grants.
- Room creator authority does not expose agent configuration. Hidden observer settings must
  remain indistinguishable from inaccessible resources to a non-creator. Guest room access
  alone grants no configuration/policy control.
- No-op tuning avoids state/version/event churn, but existing R15.06a still requires an audit
  record for accepted toggle requests, including `changed: false`. Do not silently remove
  that audit contract. General tool/rate limits remain in force; broader audit-rate hardening
  is FU-2 rather than an unbounded new subsystem in this feature.
- Bank answers live only in the teacher's `system_prompt`, which full-configuration reads
  restrict to `RESOURCE_CREATE_EDIT` holders (`2026-10-13-agent-config-visibility-and-project-reads`
  AC-3). No room-scoped surface, peer or analyst prompt, activity context or audit metadata
  carries them. The teacher's refusal to reveal an answer before submission is model guidance:
  a student can attempt extraction by prompt injection in the room. The impact is limited to
  spoiling an example round; the adoption guide says so, and a teacher who needs stronger
  protection keeps keys in `validator_config` ([R30.25]).
- Invitation rules are prompt guidance (Q-3B). Pack text, the adoption guide and R13.75 state
  that no server enforces their timing or targeting, and no test is described as a guarantee.
- No new dependency or network destination is proposed. Existing Markdown sanitization and
  observer output validation remain mandatory.

## 9. Quality Notes

**Existing debt to avoid.** `turn_engine.py` already owns many context responsibilities; add a
small participant-context assembler, not inline SQL/Redis or another giant helper argument list.
The current facade's empty-on-presence-error behavior is unsuitable for factual claims, but
changing every existing caller is unnecessary. Room settings currently cross into full-agent
mutation; replace that seam instead of duplicating its global-state assumptions.

**Patterns and reuse.** Reuse `_SystemBlocks` for measurement/render consistency
(`turn_engine.py:1281`), the shared provider renderer for rechecks (`:4520`), `RoomGuests`
for identity/guest marking (`room_guests.py:22`), `shared_kernel.labels` for normalization,
the canonical subject code helper, `WakeupConfig`/existing bounds (`orchestration/domain/models.py:254`),
existing creator/room-access checks (`backend/app/api/v1/chatrooms.py:948`), the sequential
frontend save queue (`useChatroomBindings.ts:341`), and per-item refresh savepoints
(`backend/app/workers/tasks/orchestration.py:347`). New application services receive repository
ports; cross-context access goes through interfaces, never route handlers or concrete tables.

**Quality lens, all 12 dimensions.** Layer direction and circular imports: conversation owns
binding data, orchestration owns cadence policy, agents consumes both through interfaces.
Abstraction boundaries: typed snapshots/patches, no DB rows or Redis clients in runtime APIs.
Separation and single responsibility: context, cadence and course authoring stay separate.
Open/closed: no course-name branches. Dependency inversion: repository/provider ports.
Interface segregation: narrow room settings, not full Agent DTOs. Mutability: immutable
snapshots and patches. Resource consistency: transaction/CAS and binding generations.
Errors: unknown differs from zero and 409 differs from silent replacement. Hygiene: reuse
code/label/render helpers and remove the superseded global room-editor path.

## 10. Risks and Rollback

- **Scope risk:** deterministic invitations and automatic phases are distinct expansions,
  excluded at Q-3 and Q-4. An implementer must not reintroduce them through pack prompts.
- **Teaching risk:** a correct roster does not guarantee useful intervention. Preserve quiet
  work, voluntary participation, and human moderation; require live transcript review.
- **Legacy defaults:** restore `Agent.wakeup_config` from `wakeup_authored_snapshot` only when
  the latter is not `None`; explicit `{}` is meaningful. When no snapshot exists, retain
  the stored config as the baseline and record that provenance. Do not propagate unassignable
  old runtime drift into each binding. Retain old values in a restricted rollback backup,
  not logs; migration is idempotent and tested on populated data.
- **Schema:** add binding identity/version/layers/clock. Determine the actual Alembic head graph at implementation, not the next
  numeric filename (`backend/alembic/versions/0103_agent_group_member_read_filter.py:28`).
- **Deployment:** additive schema alone does not make old workers safe. Pause/drain runtime
  workers and mutations, migrate/backfill, deploy all API/worker replicas, retire old global
  refresh/writers, initialize binding-generation state, then resume. Pack adoption follows.
  Record the cutover and do not claim uninterrupted mixed-version behavior.
- **Rollback:** pause/drain before returning to a global-writer build; disable new pack tuning,
  export room settings for recovery, restore the backed-up legacy config if reverting the
  data cutover, and explicitly warn that room overrides are unavailable in the old build.
  Do not drop populated room-layer tables/columns as an automatic rollback step. M1 can be
  reverted separately without rewriting stored messages or existing subject codes.

## 11. Acceptance Criteria

Participant facts:

- [ ] AC-1: A chat-only account, registered guest and anonymous guest each have consistent codes
  across transcript, applicable activity and authorized draft context; same-name people are
  distinguishable and a missing/deleted name does not remove the stored sender code.
- [ ] AC-2: A connected non-speaker appears with window-scoped facts. Historical disconnected
  speakers are not labeled connected, and unrelated project members/enrolled guests are not
  imported as the live class.
- [ ] AC-3: Empty successful presence, lookup failure and truncation are distinguishable in
  model input. Cohort/code collisions are explicitly ambiguous, not silently merged.
- [ ] AC-4: Supplemental context meets the 100-row/2,000-token bounds; no new email fallback,
  forged label/control line, cross-room identity or hidden observation data is introduced.
- [ ] AC-5: Compaction, final request budgeting and recheck include added identity overhead;
  newly arrived speakers keep correct codes. Normal and observer room turns receive the
  facts, while unrelated headless turns do not.

Cadence:

- [ ] AC-6: One teacher bound to A/B can have different human and runtime cadence layers.
  Tool/edit/reset in A changes neither B nor Agent defaults/version.
- [ ] AC-7: Message, silence and worker execution all resolve identical effective settings;
  latest bounds, designer call-only and toggle revocations apply even to in-flight retries.
- [ ] AC-8: Human room patches affect only supplied fields; unrelated designer saves do not
  promote runtime drift. Runtime refresh/reset preserves human room overrides; room reset
  returns to current designer defaults.
- [ ] AC-9: First override, subsequent tuning, shortened refresh interval, no-op, disabling
  deferred fires and silence rearming follow §6.3 under a deterministic clock. No config
  action resets autostop or message count.
- [ ] AC-10: Stale revisions return 409; concurrent room changes do not cross-contaminate.
  Unbind/rebind invalidates old tools, queued workers and Redis state by binding generation.
- [ ] AC-11: A headless/no-binding invocation has no tuning tool and cannot mutate through
  a direct call. Cross-project/guest/non-creator observer setting access is denied without
  exposing unrelated agent configuration; a current room creator can manage permitted settings.
- [ ] AC-12: UI shows inherited/human/runtime values and reset meaning, handles rapid edits
  and conflicts, and never PATCHes Agent from room cadence controls. OpenAPI generation is clean.
- [ ] AC-13: Populated migration/backfill and rollback rehearsal preserve defaults/room data;
  all runtime replicas use the new resolver before tuning-enabled pack adoption.

Invitations and courses:

- [ ] AC-14: Every teacher prompt carries the §6.2 invitation rules. Representative live
  transcripts show invitations that are optional, aimed at one identifiable connected
  participant, absent during individual-work stages, rotated away from the last person
  invited, and never based on unknown, truncated or ambiguous identity. Pack text and the
  adoption guide disclaim server-enforced timing or targeting.
- [ ] AC-15: Sibling prompts never promise automatic timed transitions or whole-class
  completion from recent events, and ask the human teacher to confirm each stage change.
- [ ] AC-16: All six course packs load and each cadence parses through the orchestration
  domain. Human counts and allowed toggles are explicit; all stage-table settings are legal,
  including preservation of a live message trigger during agent-disabled silence.
- [ ] AC-17: No course promises unavailable `instruct`, fractional wake times, elapsed deadlines
  from silence, or individual contribution from a group submission. Analysts use supplied codes
  and return empty output when there is no new observation.
- [ ] AC-18: Character/object instructions match actual worksheet fields and validation minimums;
  CRAT retains its required fields. Questionable example content is removed or explicitly
  approved by the educator, not silently presented as verified.
- [ ] AC-19: Upgrading/reinstalling examples leaves existing customized prompts, settings and
  grants unchanged. The adoption guide describes manual application and missing room grants.
- [ ] AC-20: Four-person CRAT live validation includes simultaneous candidates, a connected
  non-speaker, same-name/guest identities, drafting, optional invitations and observer release.
  The 30-minute steady-discussion run records reply/wake counts and any rate-limit failures.
- [ ] AC-21: Character and object live runs traverse every stage; two rooms sharing one teacher
  stay in different phases without changing each other's cadence. Confirm quiet-work behavior,
  teacher-confirmed transitions, missing grants and teacher-ended activities.
- [ ] AC-23: Each room teacher ships its bank (CRAT 8, character building 6, object
  reinterpretation 6). The dossier holds each item's verification table, and the PR
  records the requester's or an educator's approval of every shipped item. No teacher
  prompt asks the human teacher for round content or adopts a creator-posted round.
- [ ] AC-24: Answers and reference items appear only in teacher prompts: no peer, analyst or
  designer prompt carries them. The CRAT teacher confirms a matching submission itself,
  routes a differing target to the human teacher, and in a live run does not reveal the
  target or a full word before the group submits, including when a student asks for it.
- [ ] AC-22: Required backend unit/DB/integration, lint/type checks, frontend tests/lint/typecheck/
  build, OpenAPI drift, quality and conditional security gates pass for the final diff. The
  complete PR self-review records scope, evidence and merge decision. Live ACs remain open
  until actual observations are attached; `implemented` requires all selected ACs.

## 12. Test Plan

| Coverage | Level and expected evidence |
|---|---|
| AC-1..5 | Extend `backend/tests/unit/test_turn_engine_observer_activity.py`, `test_reply_recheck.py`, `test_turn_time_context.py`, `test_activity_context_provider.py`, label tests and `test_presence.py`; inspect complete provider requests, not only helper strings. DB fixtures include account/session identities and colliding code prefixes. |
| AC-6..11 | Domain/service tables plus real PostgreSQL CAS/unbind-rebind/revocation and Redis generation/clock tests; extend `backend/tests/unit/test_wakeup_service.py` and worker tests. Test all three effective-config consumers and both role-specific autostop rules. |
| AC-11..13 | Route role matrix, cross-org/project/room and hidden-observer cases; frontend binding/settings view tests for queued saves, 409, resets and lost permissions; populated migration rehearsal. |
| AC-14 | Table-driven pack checks that each teacher prompt carries the rule set, plus the live transcripts of AC-20/AC-21. No test may be described as a hard invitation guarantee. |
| AC-15..19 | Extend `backend/tests/unit/test_agent_example_packs.py` and `test_agent_example_service.py`; table-driven stage/config/worksheet-contract checks plus fresh-install and reinstallation fixtures. Do not limit coverage to presence/absence of a few prompt substrings. |
| AC-20..21 | Running-stack classroom evidence: pack revision, grants, tested model/settings, anonymized scenario transcript, observed timing and turn counts; creator-reviewed pedagogical assessment. |
| AC-23..24 | `test_agent_example_packs.py`: per-pack bank size and shape, no bank or answer in peer/analyst/designer prompts, no request-for-content wording in teachers. Replaces the assertions at `:773` (teacher confirms answers), `:774-775` (no `本課題組` in CRAT prompts) and `:806` (designer settings-visibility sentence), whose premises Q-12/Q-13 retire. AC-24's live half is part of AC-20. |
| AC-22 | Prefer the PR's clean remote CI for full suites; use fast representative local checks and document local limitations. Independent approvals and protected-branch policy still apply. |

## 13. SRS Delta

Applied to `REQUIREMENTS.md` on 2026-10-16 at approval; R13.74, R13.75 and R15.25 were free.

**Replace R13.33** with:

- **[R13.33]** In room and observer model context, each human message with a stored sender identity carries the same server-derived participant code used by room activity and draft context, followed by a normalized display label when available. Human display labels resolve from the anonymous session label or registered guest's room label, then account display name; login email is not a fallback on this path. A missing or purged name leaves the stored sender's code and a neutral identity label. The system context explains the format, identifies the room creator by code and available label, and states that labels are user-chosen, can coincide, and are not authentication or permission. No creator identity is invented when it is unavailable. Guest identity retains the platform-added guest marker; UI guest badges and label normalization follow the existing rules. Code collisions detected in the observed cohort are marked ambiguous and must not silently merge participants or support a targeted invitation.

**Add R13.74**:

- **[R13.74]** Normal and observer agents running in a room receive a bounded participant-facts block derived only from verified current presence and human identities in the raw transcript included in that request. The block states its observation time, transcript window, truncation and presence availability; it distinguishes connected state from speaking observed in that window and makes no attendance, attention or whole-class completion claim. It contains at most 100 supplemental rows and 2,000 estimated tokens, is included in context budgeting, and introduces no login-email fallback. Headless turns receive no room roster. Participant codes agree with activity and authorized draft context; group codes remain distinct. Unknown or omitted facts must not be represented as a zero count or proof of nonparticipation.

**Amend R28.05** to:

- **[R28.05]** Observer turns read the room transcript, the bounded participant facts in [R13.74], and a bounded window of the observer's own prior observations folded into system context. Participant attribution uses the same supplied codes as the transcript, activity and authorized draft surfaces; the participant block does not alter observation confidentiality or turn recent activity into a complete population report.

**Replace R15.06** with:

- **[R15.06]** During a turn with a verified live room binding, an agent may call `update_wakeup({every_n_messages?, silence_minutes?, every_n_messages_enabled?, silence_minutes_enabled?})` to change only that binding's temporary runtime thresholds and the enabled flags allowed by the designer's `self_toggle` policy. Runtime identifies the room and binding incarnation; model arguments cannot select them. The tool cannot change another room, designer defaults or durable human room overrides. A call without a live authorized binding is refused and has no global fallback.

**Replace R15.06a** with:

- **[R15.06a]** A runtime toggle is refused when the designer has not allowed the trigger, designer `call_only` is enabled, or the resulting effective configuration would disable every trigger. The agent cannot modify `self_toggle`, `count`, `call_only`, bounds, refresh policy or binding identity. Every accepted toggle is audited with acting agent, room and binding, including an unchanged result; an effective no-op does not change version, timers or mutation events. Current designer policy is enforced again at mutation, retry and effective-config resolution.

**Replace R15.09** with:

- **[R15.09]** Each live room binding resolves wake-up behavior from current designer defaults, durable human room overrides and temporary agent runtime overrides, in that order and subject to designer-only policy and server bounds. The designer's `refresh_every_hours` starts at the binding's first actual runtime override; later runtime tuning does not postpone it. When due, refresh clears only that binding's runtime layer and clock, preserving human room overrides and other rooms. A changed interval is evaluated against the existing start time. Explicit runtime reset has the same scope; explicit room reset also removes human room overrides. Configuration changes never reset autostop or message count. Disabling the message trigger clears its deferred firing; enabling silence or changing its interval re-arms only that binding's silence baseline. Rebinding creates a new incarnation with no inherited local settings or cadence state.

**Add R15.25**:

- **[R15.25]** A current room creator or platform admin may read and change the narrow room wake-up settings without access to the agent's prompt or other configuration. An editor with room access may manage a normal binding; observer binding settings remain restricted by the creator rule in [R28.02]. The room API exposes only permitted wake-up fields, effective values, field sources, binding identity/version and refresh time, and uses optimistic concurrency. Human room changes alter only supplied local fields and remove runtime overrides for those fields; they do not patch the shared Agent or copy runtime drift into designer defaults. Designer-only policies, bounds and call-only authority cannot be relaxed through this surface.

R15.07/R15.08 bounds, R9.17 A2A authority, R28.18 aggregate limits, R30.37 activity grants,
R30.38/R30.43 activity/group identity, and R32.04 draft permissions remain in force.
Update §15.1's “Per-agent JSON” caption to “Designer wake-up defaults; room layers follow R15.09.”
Add §5.2 permission-matrix row 29 for room wake-up settings with the exact R15.25 conditions
and a row note, and the three room wake-up endpoints to the §22.10 chat room API table; do not
grant general configuration capability through that row. (The draft named "§22.6's permission
matrix"; §22.6 is the agents API table and the matrix is §5.2, corrected at approval.)

**Add R13.75** (Q-3B):

- **[R13.75]** Teaching example agents include explicit, optional participation-invitation rules using the current room facts in [R13.74]. Invitations are phase-aware, avoid quiet individual-work periods, address at most one identifiable connected participant at a time, rotate away from recently invited people in visible history, and accept silence or declining without penalty. They do not infer attention from presence or participation from a truncated feed. These are pedagogical model instructions, not server-enforced eligibility or cooldown guarantees; observers remain private and use supplied codes.

Q-4A changes example behavior and documentation only; no normative phase-state requirement.

## 14. Open Questions

None blocking; approved 2026-10-16.

Non-blocking implementation choices: concrete repository/provider class names, the Alembic
revision identifier after graph inspection, and the exact localized UI wording. Educator
review must approve any replacement concrete teaching example before it ships; retaining a
generic demonstration pattern avoids blocking the platform milestones on new curriculum.

## 15. Deviation Log

None; no implementation has begun. The previous CRAT no-self-tuning rule is
intentionally replaced only after room-local tuning exists, rather than treated as a mistake
in that earlier release. Its live classroom checks are carried forward as release gates.

## 16. Follow-ups

- **FU-1:** Global pseudonym/collision redesign across activities, drafts, dashboards and exports
  is outside this dossier. Local ambiguity detection does not establish global anonymity.
- **FU-2:** General per-tool audit/rate hardening remains separate. This change avoids no-op
  version/timer churn but does not remove the accepted-toggle audit requirement.
- **FU-3:** General facilitator memory, private teacher steering and a platform answer-key
  channel remain separate product decisions; the example banks live in prompt text only. Existing code-prefix/label qualification is not authentication.
- **FU-4:** Authoritative autonomous phase timing/completion (rejected Q-4B) remains future
  work. Do not reintroduce it as a promise in a design pack without that platform contract.
