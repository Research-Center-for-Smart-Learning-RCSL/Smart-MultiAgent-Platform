---
type: feature
status: implemented
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

Configuration changes do not reset autostop. Enabling the message trigger or changing its `n` or
`count` restarts that binding's message count and discards its deferred-fire flag (D-16); other
changes keep the accumulated count. Disabling a message trigger discards its deferred-fire flag. Enabling silence or changing its interval re-arms
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

- [x] AC-1: A chat-only account, registered guest and anonymous guest each have consistent codes
  across transcript, applicable activity and authorized draft context; same-name people are
  distinguishable and a missing/deleted name does not remove the stored sender code.
  `test_participant_facts.py::TestCodes` (the prefix code is the activity/draft `subject_code`,
  draft tools import the same function); `test_turn_participant_note.py::TestRoomGuestNames`,
  `::TestGuestMarker` (registered and anonymous guests, purged sessions keep code and marker).
- [x] AC-2: A connected non-speaker appears with window-scoped facts. Historical disconnected
  speakers are not labeled connected, and unrelated project members/enrolled guests are not
  imported as the live class.
  `test_participant_facts.py::TestRows` and `::TestTheTurnEngineSeams::test_the_facts_come_from_the_included_rows_and_presence`.
- [x] AC-3: Empty successful presence, lookup failure and truncation are distinguishable in
  model input. Cohort/code collisions are explicitly ambiguous, not silently merged.
  `test_participant_facts.py::TestUnknownsAreNotZeros`, `::TestThePresenceRead`;
  `test_presence.py::TestBoundedRoster`.
- [x] AC-4: Supplemental context meets the 100-row/2,000-token bounds; no new email fallback,
  forged label/control line, cross-room identity or hidden observation data is introduced.
  `test_participant_facts.py::TestBounds` (row and token caps, no email, a name cannot spell a code).
- [x] AC-5: Compaction, final request budgeting and recheck include added identity overhead;
  newly arrived speakers keep correct codes. Normal and observer room turns receive the
  facts, while unrelated headless turns do not.
  `test_participant_facts.py::TestTheTurnEngineSeams` (prefix and code budgeting, room and observer
  requests carry the block, headless builds have no slot); `test_turn_context_budget.py` (the
  compaction projection counts every human row's code); `test_reply_recheck.py::TestTheRecheckKeepsParticipantCodes`.

Cadence:

- [x] AC-6: One teacher bound to A/B can have different human and runtime cadence layers.
  Tool/edit/reset in A changes neither B nor Agent defaults/version.
  `test_wakeup_self_modification.py::test_tuning_in_one_room_leaves_the_other_room_alone`,
  `::test_self_modification_preserves_designer_config_and_bounds`; `test_room_wakeup_service.py::TestPatch`;
  db tier `test_room_wakeup_persistence.py::TestBindingLayers::test_cas_writes_one_room_and_refuses_a_stale_version`.
- [x] AC-7: Message, silence and worker execution all resolve identical effective settings;
  latest bounds, designer call-only and toggle revocations apply even to in-flight retries.
  `test_room_wakeup_resolution.py` (one pure resolver; revoked toggles, call-only and narrowed
  bounds); message, silence and worker gates read it (`test_wakeup_service.py`,
  `test_agent_trigger_wiring.py::test_wakeup_agent_skips_a_trigger_the_room_has_since_paused`); D-3.
- [x] AC-8: Human room patches affect only supplied fields; unrelated designer saves do not
  promote runtime drift. Runtime refresh/reset preserves human room overrides; room reset
  returns to current designer defaults.
  `test_room_wakeup_service.py::TestPatch`, `::TestReset`; `test_wakeup_refresh_interval.py::test_refresh_keeps_the_human_room_overrides`;
  designer saves cannot promote drift because the tool no longer writes `Agent.wakeup_config`.
- [x] AC-9: First override, subsequent tuning, shortened refresh interval, no-op, disabling
  deferred fires and silence rearming follow §6.3 under a deterministic clock. No config
  action resets autostop; only a change to the message trigger's own cadence restarts its count (D-16).
  `test_room_wakeup_service.py::TestTimers`; `test_wakeup_refresh_interval.py` (fake clock);
  `test_wakeup_self_modification.py::test_an_unchanged_toggle_is_audited_but_writes_nothing`.
- [x] AC-10: Stale revisions return 409; concurrent room changes do not cross-contaminate.
  Unbind/rebind invalidates old tools, queued workers and Redis state by binding generation.
  `test_room_wakeup_service.py::TestPatch::test_a_stale_version_is_a_conflict`, `::test_a_rebound_binding_is_a_new_incarnation`;
  `test_agent_trigger_wiring.py::test_wakeup_agent_drops_a_job_from_an_earlier_binding`;
  `test_wakeup_state_ttl.py` (binding-scoped keys); db tier `::test_unbind_and_rebind_is_a_new_incarnation`.
- [x] AC-11: A headless/no-binding invocation has no tuning tool and cannot mutate through
  a direct call. Cross-project/guest/non-creator observer setting access is denied without
  exposing unrelated agent configuration; a current room creator can manage permitted settings.
  `test_agent_runtime_tools.py::test_registry_dispatch_and_unknown` (no tool headless),
  `test_wakeup_self_modification.py::test_a_rebound_agent_cannot_use_the_old_binding`;
  `test_room_wakeup_routes.py` (role matrix, hidden observer as 404, no config fields).
- [x] AC-12: UI shows inherited/human/runtime values and reset meaning, handles rapid edits
  and conflicts, and never PATCHes Agent from room cadence controls. OpenAPI generation is clean.
  `ChatroomSettingsView.test.ts` (room panel patches the binding with its version, never
  `/api/agents/:id`; a 409 reloads; a 403 hides the panel). Writes are serialised by
  disabling the controls while one is in flight. `openapi.json` and the client regenerated.
- [x] AC-13: Populated migration/backfill and rollback rehearsal preserve defaults/room data;
  all runtime replicas use the new resolver before tuning-enabled pack adoption.
  Rehearsed on staging (smap.rcsl.online) on 2026-10-16 as runbook §3.3's stopped cutover, from
  `main` at `a8aa37ba` to this branch at `58b190d3`, with a `pg_dump` taken first: web and worker
  stopped, `0103 -> 0104` applied, then started. All 31 bindings kept their rows, each with its own
  `binding_id` and empty layers at version 1; no agent had drifted, so the backup table holds 0
  rows and the restore branch was exercised only by the db-tier test. In the browser: a room
  override saved, survived a reload and left the same teacher agent's other room and its
  `agents.wakeup_config` unchanged; a role change cleared the layers, issued a new `binding_id`
  and the panel re-read without a 409; reset to defaults worked; Redis keys carry the binding id.
  Rollback: web, worker and frontend restarted on the `a8aa37ba` images with `--no-deps` (the old
  code's alembic does not know `0104`), healthy with no error-level log lines; forward again, the
  room override set before the rollback (`silence_minutes_enabled: true`, version 4) was intact.
  Not covered: no turn or message was exercised on the old images during the rollback window.
  Staging runs one web and one worker replica, both stopped across the cutover.

Invitations and courses:

- [x] AC-14: Every teacher prompt carries the §6.2 invitation rules. Representative live
  transcripts show invitations that are optional, aimed at one identifiable connected
  participant, absent during individual-work stages, rotated away from the last person
  invited, and never based on unknown, truncated or ambiguous identity. Pack text and the
  adoption guide disclaim server-enforced timing or targeting.
- [x] AC-15: Sibling prompts never promise automatic timed transitions or whole-class
  completion from recent events, and ask the human teacher to confirm each stage change.
- [x] AC-16: All six course packs load and each cadence parses through the orchestration
  domain. Human counts and allowed toggles are explicit; all stage-table settings are legal,
  including preservation of a live message trigger during agent-disabled silence.
- [x] AC-17: No course promises unavailable `instruct`, fractional wake times, elapsed deadlines
  from silence, or individual contribution from a group submission. Analysts use supplied codes
  and return empty output when there is no new observation.
- [x] AC-18: Character/object instructions match actual worksheet fields and validation minimums;
  CRAT retains its required fields. Questionable example content is removed or explicitly
  approved by the educator, not silently presented as verified.
- [x] AC-19: Upgrading/reinstalling examples leaves existing customized prompts, settings and
  grants unchanged. The adoption guide describes manual application and missing room grants.
  `test_agent_example_service.py::test_a_reinstall_never_rewrites_a_customised_agent` (all three
  room packs) and `::test_a_second_install_reports_already_present_and_creates_nothing`; install
  has no binding or grant path. Guide: `docs/examples/chinese-character-courses-adoption.md`
  (manual application, delete-and-reinstall cost, missing grants, guidance-not-enforcement).
- [x] AC-20: Four-person CRAT live validation includes simultaneous candidates, a connected
  non-speaker, same-name/guest identities, drafting, optional invitations and observer release.
  The 30-minute steady-discussion run records reply/wake counts and any rate-limit failures.
  D-22 to D-24 (rooms `97f917ca`, `b43f9794`, `82f59a53`); the SA item from phase 3's room `22078b95`.
- [x] AC-21: Character and object live runs traverse every stage; two rooms sharing one teacher
  stay in different phases without changing each other's cadence. Confirm quiet-work behavior,
  teacher-confirmed transitions, missing grants and teacher-ended activities.
- [x] AC-23: Each room teacher ships its bank (CRAT 8, character building 6, object
  reinterpretation 6). The dossier holds each item's verification table, and the PR
  records the requester's or an educator's approval of every shipped item. No teacher
  prompt asks the human teacher for round content or adopts a creator-posted round.
  Verification tables in §17; the requester's item-by-item approval is recorded on PR #261
  (issuecomment-6218027017), cross-checked against the banks in the three shipped teacher prompts.
- [x] AC-24: Answers and reference items appear only in teacher prompts: no peer, analyst or
  designer prompt carries them. The CRAT teacher confirms a matching submission itself,
  routes a differing target to the human teacher, and in a live run does not reveal the
  target or a full word before the group submits, including when a student asks for it.
- [x] AC-22: Required backend unit/DB/integration, lint/type checks, frontend tests/lint/typecheck/
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

The previous CRAT no-self-tuning rule is intentionally replaced only after room-local tuning
exists, rather than treated as a mistake in that earlier release. Its live classroom checks
are carried forward as release gates.

- **D-1. The participant code helpers stay in `activities.domain.subject_code`.** §6.1 offered a
  public seam or a move to `shared_kernel`. The module is pure and already imported that way by
  `agents/application/runtime/draft_tools.py:46`; a re-export from `activities.interfaces` would
  have pulled its realtime broadcast module into the turn engine's import graph. Both surfaces
  call the same function (`test_participant_facts.py::TestCodes`).
- **D-2. The prefix is `[u:xxxxxxxx] Name`.** §6.1 fixed "code plus display label" but not a
  format. The code is bracketed because `one_line_label` strips brackets from every label, so a
  participant cannot name themselves into another person's code; an unnamed sender shows the code
  alone and an unnamed guest the bare marker. The owner note names the creator by the same prefix.
- **D-3. A concurrent designer permission change is enforced at resolution, not by a second CAS.**
  §6.3 asked to lock or CAS against both the Agent version and the binding version. Writes CAS the
  binding version only; every read resolves the stored layers against the designer's current
  defaults (`resolve_room_wakeup`), so a toggle written just before the designer revokes it never
  takes effect (`test_wakeup_self_modification.py::test_a_toggle_revoked_while_stored_stops_applying`).
  This avoids a cross-context row lock on `agents` from the turn's own session.
- **D-4. No-op handling differs by writer.** The agent tool skips the write when the resolved
  config would not change, as §6.3 requires, and still audits accepted toggles. A human save of a
  value the room layer already stores writes nothing, but a human value equal to the inherited one
  is stored as the room's own choice, so it survives a later designer change.
- **D-5. Explicit triggers carry no binding id.** §6.3 binding-scoped worker payloads. Every
  cadence enqueue (message count, silence, agent-reply counts) carries it and the worker drops a
  job whose binding is gone. `mention` and `release` are explicit calls by the room's people and,
  like the coalesced re-enqueue, resolve the room's current binding at run time.
- **D-6. The worker also skips an autonomous job whose trigger was turned off after it was
  queued** (`skipped:trigger_off`). Not in §6.3; it is what AC-7's "worker execution resolves
  identical effective settings" means for a job already in the queue.
- **D-7. Two reads became dead and were removed.** `IdentityFacade.get_chat_labels` (the email
  fallback, now used nowhere) and `AgentsFacade.list_agents_with_authored_snapshot` (the old
  refresh's scan). The snapshot columns stay (FU-5).
- **D-8. Migration `0104` restores only JSON-object snapshots.** Found by the db-tier test:
  `AgentRepository.create` writes a Python `None` snapshot into `pg.JSONB`, which stores the JSON
  value `null`, not SQL `NULL`. A plain `IS NOT NULL` would have replaced those agents' configs
  with `null` and switched every trigger off. The predicate is `jsonb_typeof(...) = 'object'`
  (`test_room_wakeup_persistence.py::TestMigration0104DataStep`).
- **D-9. The cutover is a stopped deployment.** §10 required it; the procedure is runbook §3.3.
  Redis counters restart from zero because their keys now carry the binding id.
- **D-10. Two security-audit fixes (MEDIUM, both Introduced).**
  - A message body could carry `\n[u:xxxxxxxx] Name: ...` and read as another person's turn,
    while the participant note says codes cannot be imitated. Every model-facing body now loses
    the brackets of a code-like token, the way the notice marker already did
    (`_defang_notice_marker`; `test_participant_facts.py::TestBodiesCannotForgeAPrefix`).
  - The room wake-up routes loaded the agent before refusing a hidden observer, so a non-creator
    could tell an observer binding from an unbound agent by response time. The 404/403 decision
    is now made from the binding row before anything else is read
    (`test_room_wakeup_routes.py::test_a_hidden_observer_reads_as_unbound`).
- **D-11. Quality-audit fixes.** The bounded presence read reports truncation when its scan
  stopped early, even if ghosts left fewer live members than the limit
  (`test_presence.py::test_an_early_stop_is_truncation_even_when_ghosts_shrink_the_result`). A role
  change clears both wake-up layers and bumps the version, because a stored `autostop_rounds`
  names the other role's cap afterwards and an editor's choices must not survive onto an observer
  binding (`test_room_wakeup_persistence.py::test_a_role_change_clears_the_layers_and_bumps_the_version`).
  The facts footer reserve is computed from its two lines at their longest. The room panel
  re-seeds a number draft only when that field's server value changes and skips a cleared field.
  The tool's conflict errors say what to do.
- **D-12. The refresh sweep reads every binding holding a runtime layer, not only due ones.**
  §6.3 asked for due bindings only. Due-ness depends on each agent's `refresh_every_hours`, which
  lives in `agents.wakeup_config`; filtering in SQL would join the agents table into the
  conversation repository. The sweep keyset-pages bindings with a runtime layer (indexed) and
  decides due-ness per binding, so its cost is bounded by bindings an agent actually tuned.
- **D-13. M3 static half recorded; the live and document halves stay open.** The six packs carry
  the §6.2 blocks, the §17 banks and the §6.4 cadences; `TestPhase2CoursePacks` in
  `test_agent_example_packs.py` checks them, and the stage-change table resolves every
  `update_wakeup` instruction a teacher prompt gives through `resolve_room_wakeup` (applied as
  written, none ignored or clamped, the message trigger live while silence is paused). Seven
  single-property mutations of the packs (an answer in a peer prompt, a revoked toggle, a narrowed
  bound, a dropped `count`, a dropped invitation rule, a broken bank word, a timed transition) each
  fail the suite. `test_agent_example_service.py` covers fresh install and reinstallation of the
  three room packs. AC-14 and AC-24 remain open for their live transcripts (AC-20/AC-21), AC-19 for
  the M4 adoption guide, and AC-23 for the approval record on the PR; the item-by-item approval
  itself is in §17. The retired assertions (the shared-cadence prohibition, creator-confirmed CRAT
  answers, `本課題組`, the designer's settings-visibility sentence) were replaced, not deleted, as
  the Test Plan row for AC-23..24 directs.
- **D-14. FU-9 fixed in this dossier rather than deferred (requester's decision, 2026-10-16).** The
  defect was introduced by this dossier's own unmerged diff, so it is fixed before the PR merges
  instead of shipping a known quality Warning. `RoomWakeupService` no longer touches Redis: each
  write returns its `TimerEffects` (`RoomWakeupChange` for human writes and refresh,
  `RuntimeTuning` for the tool), and each of the three callers applies them only after the
  transaction holding the write commits. The PATCH and reset routes apply them after `db.commit()`
  and log a Redis failure without failing the saved change. The refresh sweep collects the effects
  of bindings whose savepoint succeeded and applies them once its single commit lands, none if that
  commit fails. The `update_wakeup` tool appends its effects to `WakeupBinding.effect_sink`, which
  the turn drains inside `_post_commit` on its three committing branches (reply, empty reply,
  observer) beside the activation events; the failure path rolls back with the sink undrained. The
  choice was made over a SQLAlchemy `after_commit` hook, whose synchronous callback would have to
  schedule the async Redis calls with no place to report a failure. Regression tests:
  `TestTimersWaitForTheCommit` (failed against the previous code with the timers already touched),
  the route ordering tests in `test_room_wakeup_routes.py`, the sweep assertions in
  `test_workers.py`, and `test_turn_wakeup_effects.py` through the real `_run_locked`. Five
  mutations (a missing drain, a drain before the commit, the route or the sweep applying early, the
  service applying inline) each fail the suite.
- **D-15. `/code-review` of the branch, ten findings, decided with the requester (2026-10-16).**
  #1 (the tool's CAS holds the binding row lock for the rest of the turn) is FU-10 and #10 (the
  panel repeats backend bounds) is FU-11; both stay deferred. #4 (a runtime enabled flag outranks
  a room one, so an agent can reopen a trigger the teacher paused) keeps the §6.3 resolution
  order at the requester's choice: the three teacher prompts no longer claim the teacher's
  choice wins, they say the last write wins and tell the agent not to reopen a trigger it did not
  close, and the three designer prompts say the platform does not lock the teacher's value. The
  rest were fixed. #3: a role change now issues a fresh `binding_id`, so the old role's autostop
  and message counters, deferred firing, silence clock and queued jobs are left behind (a missing
  silence timestamp only arms the clock, `evaluate_silence_trigger`), which also retires the
  premise "binding id is immutable" in `tables.py`; the integration test that pinned the old id
  now pins a new one. #2/#5: `wakeup_agent` passes the binding it gated (or, for an explicit
  trigger, the live one) into `run_turn`, whose queued-trigger drain names it on the follow-up
  job, and counts the autostop round on it instead of re-reading the live binding after the turn.
  #6: a role change invalidates the panel's `roomWakeup` query. #9: `ChatroomAgentRepository.list`
  carries each row's wake-up layers, and `evaluate_message_wakeups` passes them to
  `on_message_created`, removing the second read of the room's bindings per message. #7: the
  unreachable G.5 restore path (`AgentDraft.replace_wakeup_config`, `wakeup_last_refreshed_at`
  writes, the `replace` branch of `_merge_and_normalize_wakeup`) and the tests that pinned it are
  removed; the snapshot write and both columns stay for FU-5. #8: PATCH and reset authorize from
  the binding row only and let the write load the binding and agent, and GET reuses the row it
  authorized with. One behavioural edge of #8: PATCH or reset on a soft-deleted but still bound
  agent now answers 409 (binding gone) instead of 404; the reload that follows answers 404.
- **D-16. A change to the message trigger's cadence restarts its count (requester's decision,
  2026-10-17).** Found in the first staging run of the two sibling courses. `on_message_created`
  fires on `count % n == 0` over a count that is never reset, so the §6.3 rule "configuration
  changes do not reset accumulated message count" made the first firing after a change land after
  anywhere from 1 to `n` messages. In 拆字蓋樓-Test-Group1 the teacher raised `n` from 2 to 3 at the
  room's eighth human message; the ninth woke it at once (it correctly replied empty), and the
  creator's "時間到" was the eleventh and woke nothing until a twelfth arrived. `timer_effects` now
  owes `restart_message_count` when the trigger is switched on or its `n` or `count` changes, and
  `apply_timer_effects` deletes that binding's count and deferred-fire keys after the commit, as
  for the other effects (D-14). Autostop and every other change keep their counts. A message that
  arrives between the tool call and the turn's commit is lost from the new cycle, which can only
  delay a reply by one message. Tests: `test_room_wakeup_service.py::TestTimers`,
  `test_wakeup_state_ttl.py::test_restarting_the_message_cycle_clears_the_count_and_the_deferred_firing`.
- **D-17. Live CRAT evidence for AC-24 and AC-14, 2026-10-20 (partial).** Staging, `main` at
  `96bce6a9`, room `驗證-CRAT-1020` (`a74a64cd-a0fb-4bac-a9fb-3928945f8143`); the TEST PROJECT
  TA and SA were re-synced from the shipped pack beforehand (peer-deliberate-errors D-4).
  Participants: the room creator and one guest, so only two identities were available.
  - AC-24, no reveal before submission: passed. 20:26, the student asked
    「答案是不是「心」？你直接告訴我對不對」; TA reviewed the candidates and ended
    「答案會在小組提交後公布」. In round 2 TA summarised 「水」 as a candidate without confirming it.
  - AC-24, differing or unconfirmable target goes to the human teacher: passed in the sense that TA
    waited for the room creator (20:32 creator 「答案「心」可以算對」, 20:33 TA
    「老師已確認這輪的答案「心」可以算對」).
  - AC-24, **TA confirms a matching submission itself: not achievable with the current platform.**
    The submission's `agent_digest` is the computed summary
    `5/5 fields answered: word_1, word_2, word_3, reasoning, target_character` (DB query on
    `activity_submissions`), so `[Recent room activity]` never carries the submitted target and
    `<步驟E1>`'s 「提交的目標字和題庫答案相同」 has nothing to compare. AC-24 stays open; the fix is
    either a validator detail or digest that includes the target, or a prompt that routes every
    result to the human teacher.
  - **Guest-only rooms cannot finish a CRAT round as written.** A guest can never belong to a
    Member Group, so the guest's submission was individual; TA replied (20:32)
    「目前沒有看到小組共識提交完成的訊息。先請大家確認並完成小組投票」, because `<步驟D1>` only
    handles a group consent submission. This affects the lab's guest-link room. Recorded for
    `2026-10-20-crat-facilitation-phase3-late-joiners-and-activity-state`, whose activity-state
    block and D1 rewrite must cover an individual round.
  - AC-14: TA never invited the room creator (consistent with `<邀請發言>` 3). With one student
    there was no connected non-speaker to invite, so the invitation rules remain untested live
    (AC-20's four-person run).
  - Cadence: TA used `update_wakeup` at 20:32:56 entering `<步驟E1>` (room settings show
    「代理自行調整」 on silence), and the room audit shows a TA silence wake at 20:37, so the
    trigger was back on in round 2 as `4b` requires.
- **D-18. Live character and object evidence for AC-21, 2026-10-20 (partial).** Same build and
  re-synced agents as D-17; room creator plus one guest.
  - Two rooms sharing one teacher: `驗證-拆字A-1020` (`da859240-f0d9-40b3-8c6c-4809b595106f`,
    teacher holds the activity grant) and `驗證-拆字B-1020` (`8c94a20a-c22d-494d-a22c-7f6f136688ee`,
    no grant). After A moved to sharing and B stayed in individual work, room settings showed the
    teacher's `every_n_messages` at 2 in A and 3 in B, each 「代理自行調整」 with its own revert time
    (A 20:50:15, B 20:53:06). Cadence stayed per room: passed.
  - Character stages A1 to D1 traversed in room A; the teacher started the activity at B2 with its
    grant (20:50), and neither teacher nor peer spoke during 3.5 minutes of individual work: passed.
  - Teacher-confirmed transition: passed with a delay. The creator's single 「時間到」 (20:55)
    woke nothing because the teacher had raised `n` to 3 and turned silence off for individual work
    (the risk already in FU of the peer-deliberate-errors review); the third creator message woke
    it, and it ended the activity with `end_activity` in the same turn (21:01), then led sharing.
  - **Missing grant: failed.** In room B, with no grant and no activation, the teacher still said
    「開始個人作答！請看畫面右邊的表單」 (20:53). The `<步驟B2>` wording is unconditional.
  - **Teacher-ended activity: failed.** `驗證-萬物-1020` (`679c7bb1-d281-485c-b095-2426b807a319`):
    the creator ended the activity from the panel (21:08). The student wrote
    「咦，表單不見了？」 and the teacher answered 「等老師宣布發想時間結束」; after the creator's
    「時間到」 the teacher moved to `<步驟C2>` and asked students to 「寫下理由後提交」 to a form that
    no longer existed. The teacher cannot observe activation state.
  - Both failures and the CRAT findings in D-17 are the subject of
    `2026-10-20-crat-facilitation-phase3-late-joiners-and-activity-state` (activity-state block,
    activation notices, state-conditional pack wording).
  - Bank reading: for K1 (「木、口」, no 「可重複使用」 marking) room A's teacher announced
    「可以重複使用」 while room B's announced 「每個部件不重複使用」. Marking only the reusable items
    is easy to misread; marking every item explicitly would remove the ambiguity (FU-15).
  - AC-21 stays open: the missing-grant and teacher-ended cases fail as written.
- **D-19. The remaining live runs use phase 3's prompts (requester's decision, 2026-10-24).**
  `2026-10-20-crat-facilitation-phase3-late-joiners-and-activity-state` was merged to `main` and
  deployed to staging before AC-14, AC-20, AC-21 and AC-24 closed (its D-8), so their evidence is
  observed on the phase 3 pack revision with its `[Activity state]` block and arrival notices. That
  revision keeps every §6.2 invitation rule and the AC-24 no-reveal rule this dossier pins.
- **D-20. AC-24's matching half observed live, 2026-10-24.** In the phase 3 staging run recorded
  in that dossier's D-10 (room `22078b95-5011-441c-93fc-9b320bac69f1`, four registered students in
  one member group), the group submitted 「心」 and TA confirmed it against the bank and announced
  the three words without the room creator. TA also withheld the answer before submission and,
  in round 2, did not name the peer's correct candidate. The differing half (a group submitting a
  different target) could not be submitted in that run because the panel offered no second
  proposal after an accepted one (phase 3 D-11), so AC-24 stays open for phase 3 AC-25's
  differing run.
- **D-21. AC-24 closed, 2026-10-25.** Phase 3's second run (its D-12, room
  `345abfe0-560c-496f-b9e4-74686b55efd5`) observed both halves: TA confirmed the matching 「心」
  itself and handed the differing 「油」 to the room creator, naming the reference answer only
  after the group had submitted. With D-17's no-reveal evidence and the pack tests, AC-24 is
  ticked.
- **D-22. First four-person AC-20 run, 2026-10-25.** The simulator ran
  `frontend/sim/scenarios/crat-ac20.ts` in room `97f917ca-7d21-4100-829d-a65ac85ad572` against TA
  v49. Three registered students sat in a new three-member group, so consent needed two approvals,
  and a guest took the name 小安, which P1 also uses.

  Six of the protocol's §4 items passed on the transcript.
  - Near-simultaneous candidates: TA's turn after 「心」 and 「火」 named both and asked the group to
    check them.
  - Same-name guest: TA used 小安 only when restating P1's own messages. It never named the guest's
    two messages and never called on either person.
  - No reveal: nothing was revealed before submission, including when a student asked for a hint.
  - Proposal outcomes: the rejection drew 「這一題的提案沒有通過。先回到討論」, and TA confirmed the
    accepted 「心」 itself.
  - Rate limits: the window had no `rate_limited` skip. The worker log showed no rate-limit line and
    no 429; the grep matched only timestamp digits.
  - Wake counts: TA's `every_n_messages` fired 11 times and skipped 2 as empty replies, and its
    `silence_minutes` fired 3 times. AA's `every_n_messages` fired once.

  Two items failed.
  - Disclosure: TA's first message skipped step A1 and opened with the demonstration, so the AI-peer
    disclosure was never said. The three phase 3 runs on the same script all said it.
  - AA release: the scripted release at minute 20 found no observation to release. AA wakes every 20
    human messages and first woke at about minute 25.

  The SA item was not exercised. SA wakes only after three quiet minutes, and the script never left
  that gap. Phase 3's first run (room `22078b95-5011-441c-93fc-9b320bac69f1`) supplies it: TA
  answered SA's 「水」 with 「請大家一起驗證這個新想法」 without judging it.

  For AC-14, TA invited no one in round 1, which the rules allow. In round 2 it invited 小晴, and
  after she answered asked her again whether she wanted to add more, which breaks the rotation rule.

  The requester chose to fix the prompts and run again.
  - Step A1 now states that it is TA's first message even when a student has already said they are
    ready.
  - Every teacher's rotation rule now applies whether or not the invited person answered.
  - The scenario's release moves to minute 27:30.

  AC-20 and AC-14 stay open for that run.
- **D-23. Second four-person AC-20 run, 2026-10-25.** The run used room
  `b43f9794-4a8c-4868-996f-5e200d11073d`, with TA v50 carrying D-22's fixes.

  D-22's two failures passed.
  - Disclosure: TA's first message, at 08:00:42, gave the step A1 opening including
    「討論室裡的 AI 同學也會分享它的想法，但它的想法可能有錯」.
  - AA release: the 27:30 release posted the observation to the room. It named participants only by
    code and did not show which agent wrote it.

  The items that passed in D-22 passed again: both candidates were named and checked, no name was
  used at all, the hint request drew 「先不用我給答案提示」, the rejection returned the group to
  discussion and TA confirmed the accepted 「心」, and no wake was skipped for a rate limit. TA's
  `every_n_messages` fired 10 times and skipped 3, its `silence_minutes` fired 3 times, and AA's
  `every_n_messages` fired once.

  One item failed: TA's next turn after the release ignored it. The acknowledgement clause exists
  only in `ac20-live-run-protocol.md` §4. AC-20 itself names only the release, and no teacher prompt
  covered released analyses. A release to the room also does not wake anyone: it counts as a
  non-human message, and the explicit `release` wake applies only to a release aimed at agents. TA
  therefore sees it only as `[Room notice] An analysis was released to the room: …` in the history of
  its next student-triggered turn. The requester chose to add a rule and run again. The CRAT TA's new
  `<放出的分析>` section tells it to pick up one discussion process from the analysis in a sentence at
  the start of its next message, once. TA must not read out codes, say who or which agent wrote it,
  or treat its suggested next step as a stage change, and must not use the submission table as a
  ranking.

  TA invited no one in this run. The requester chose to keep AC-14 open and gather its invitation
  evidence, including rotation after the D-22 fix, from the AC-21 runs, whose teachers share the
  same invitation block.
- **D-24. AC-20 closed on the third four-person run, 2026-10-25.** The run used room
  `82f59a53-0c43-4a63-8244-6c74b3832359` with TA v51, which carries D-23's rule. The script was
  unchanged, and all 38 steps ran; the only timeout was the facilitator's fallback confirmation,
  which is meant to stay unused when TA confirms a match itself.

  Every protocol §4 item that a run can show passed.
  - Disclosure: TA's opening at 08:42:07 included the AI-peer sentence.
  - Candidates: TA's turn at 08:46:43 named both 「心」 and 「火」 and asked the group to check them.
  - Same-name guest: TA called P1 小安 only when restating P1's own message. It referred to the guest
    as 「另一位同學」, so the two were not merged and neither was called on.
  - No reveal: no target or word was revealed before submission, including after the hint request.
  - Proposal outcomes: the rejection drew 「這一題的提案沒有通過…我們回到討論看看」, and TA confirmed
    the accepted 「心」.
  - Release: the 27:30 release reached the room. TA's next message, at 09:08:02, opened with
    「剛才分享到討論室的整理提到，大家有把候選字放回線索裡檢查，也比較了不同的聯想路徑」. It read
    out no code and did not say who or which agent wrote the analysis.
  - Wake counts: TA's `every_n_messages` fired 12 times and skipped 1 as an empty reply, its
    `silence_minutes` fired 3 times, and AA's `every_n_messages` fired once.
  - Rate limits: no wake was skipped for a rate limit. The worker log for the two hours covering
    D-23's run and this one returned no line for `rate.?limit| 429 |status.?429|too many requests`.

  The SA item is taken from phase 3's room `22078b95`, as in D-22. With this, AC-20 is ticked.

  For AC-14, the same run shows the rotation fix live. At 09:08:02 TA invited 小晴 optionally
  (「如果願意，可以說說」). After she answered, TA's next message at 09:09:39 said 「不用勉強補充」
  instead of inviting her again. AC-14 stays open, as the requester decided in D-23, until the
  AC-21 runs add the character and object teachers' invitations.
- **D-25. AC-21 simulator runs of 2026-10-26.** Character run 4 (room `5360c3b1`) passed every
  stage: TA opened D1 at 17:55 and posted K2 at 29:24, but invited nobody while 小遠 stayed silent.
  Object run 2 (room `46855e0f`) carried two compliant invitations (小晴 at 21:14, 小遠 at 29:44).
  Its D3 question came only after the creator's 進行下一輪, which the requester chose to record
  without a prompt change. The teacher-ended case went unobserved because the scripted reactions
  answered round 1's end notice; `61fdc664` added a `count` to the simulator's message condition so
  they wait for the second `ended` notice. At the requester's choice the character teacher's D1
  invitation wording was changed in `ed50c130` and synced to staging agent `5b2e0645` (v10).
- **D-26. Character run 5, 2026-10-27, failed on pace (room `5a043b0a`).** TA split A2 over two
  messages (04:27 and 07:04), posted B1 at 09:33 on a silence wake and asked the creator to start
  the activity at 12:02. The ask was compliant: TA neither started the work nor announced it. The
  simulator's start step waited only until 10:00, so the activity never started and every later
  form step failed; D1 was never reached and `ed50c130` is still unobserved. Off-script, TA
  repeated 「好，時間到！大家辛苦了。」 on three student messages after the creator's time-up with no
  activity running. At the requester's choice the scenario now waits up to ten minutes for the ask
  and opens the form only once the activation is active; the prompt is unchanged.
- **D-27. Object run 3, 2026-10-27, passed all 33 steps (room `3d5b9bca`).** Times are on the
  run clock. The work stayed quiet from the form announcement to the creator's 13:00 end of
  ideation, and the C2 reminder followed it while the activity ran. TA asked the creator about
  ending the round at 21:04, before the creator's scripted confirmation, and called `end_activity`
  in the confirming turn (ended 21:09), so D-25's late D3 did not recur. Its one invitation went to
  小遠 at 21:13, after the activity ended, and was not repeated. A reference use (臨時掛勾) was
  cited only after a student had shared it. The teacher-ended case was observed for the first time:
  the creator ended round 2 from the panel at 32:31, five seconds after TA started it, and TA
  replied at 32:44 that the round had ended and the form could no longer be submitted, without
  asking anyone to submit. It did not go on to E2 (asking about the next round) and stayed silent
  until the class closed. No silence wake fired in those nine minutes, which is consistent with the
  B2 announcement at 32:30 having switched silence off and no wake-up restore after the panel end;
  the run's evidence does not show the tool calls, so this is not confirmed. The
  requester accepted this as a pass with the missing E2 recorded; phase 4 replaces the creator
  question with the teacher's own transition.
- **D-28. Character run 6 passed all 32 steps; AC-14 and AC-21 closed, 2026-10-27 (room
  `a82020af`).** The run used D-26's scenario and the unchanged v10 teacher. Times are on the run
  clock. TA covered A1 with the AI-peer disclosure, A2, and B1 with K1 and a demo on 人、木 at 09:31,
  revealing no reference character. Without a grant it asked the creator to start at 12:01 and
  announced the work once, at 12:14, after the activation. No TA or SA message fell between that
  announcement and the creator's 15:13 time-up. TA then announced time up without claiming to
  close the form and opened D1 in the same message; the creator ended the activity from the panel.
  In D1 TA invited 小遠, a connected participant who had submitted but not spoken, optionally
  (「想分享再說就好」) at 21:50 and did not invite again, so `ed50c130`'s wording is observed. After
  the creator's 進行下一輪 TA summarised (D2/D3) and posted K2 marked repeatable at 30:07. A2 again
  described the form before any activation (FU-17).
  With D-18's two-room cadence check, D-27's teacher-ended case and this run's missing-grant case,
  every AC-21 clause is observed, and AC-21 is ticked. AC-14 is ticked on the invitations of D-24
  (CRAT), D-25 and D-27 (object) and this run (character): each optional, aimed at one named
  connected participant, outside individual work, never the creator, and not repeated to the
  person last invited. AC-22 remains for the PR.
- **D-29. AC-22 closed on PR #283, 2026-10-27.** Scope: the remaining branch
  `test/sim-ac21-scenarios` against `main`, which holds the AC-21 simulator scenarios and their
  condition, observer and participant fixes, the character and object teacher prompt changes
  (`f84e4d7f`, `ed50c130`), this dossier's run records, and the phase 4 dossier merged ahead of its
  code at the requester's choice. CI was green on every job at `8e2cdee8` (backend unit, db,
  integration, wiring, lint and typecheck; frontend unit, lint, typecheck, e2e, OpenAPI drift and
  every gate; dependency audit); only `compose-boot-prod` was skipped by its path filter. The
  self-review read the prompt changes line by line and the simulator diff in full. The prompt
  changes move the character teacher's cadence restore to the time-up turn, let both teachers
  follow a creator's 進行下一輪 and bound follow-up questions; they add no tool, grant or answer
  text, and every §6.2 invitation rule is unchanged. The simulator changes are operator-side only
  and read no secret. No quality or security finding. Merge decision: ready, merged by the
  requester. One gap is noted rather than fixed: the object teacher's panel-ended branches
  (`<步驟C1>` and `<步驟D3>`) jump to `<步驟E2>` without the wake-up restore b), unlike the
  character teacher's, which fits D-27's silent nine minutes. Phase 4's prompt rewrite replaces
  these branches.

## 16. Follow-ups

- **FU-1:** Global pseudonym/collision redesign across activities, drafts, dashboards and exports
  is outside this dossier. Local ambiguity detection does not establish global anonymity.
- **FU-2:** General per-tool audit/rate hardening remains separate. This change avoids no-op
  version/timer churn but does not remove the accepted-toggle audit requirement.
- **FU-3:** General facilitator memory, private teacher steering and a platform answer-key
  channel remain separate product decisions; the example banks live in prompt text only. Existing code-prefix/label qualification is not authentication.
- **FU-4:** Authoritative autonomous phase timing/completion (rejected Q-4B) remains future
  work. Do not reintroduce it as a promise in a design pack without that platform contract.
- **FU-5:** `agents.wakeup_authored_snapshot` and `wakeup_last_refreshed_at` are no longer read
  after migration 0104, and `AgentService.patch` still writes the snapshot on human edits. Retire
  both columns, the snapshot write and `AgentDraft.replace_wakeup_config` /
  `wakeup_last_refreshed_at` in a later release, once no rollback to the previous code is wanted.
- **FU-6:** `pg.JSONB` without `none_as_null=True` stores a Python `None` as JSON `null` in
  `agents.wakeup_authored_snapshot` (D-8). Any other nullable JSONB column written from Python
  `None` has the same split between SQL `NULL` and JSON `null`; an audit of those columns is
  outside this task.
- **FU-7 (security Hardening):** `one_line_label` strips ASCII brackets only, so a name such as
  `［u:1a2b3c4d］ Teacher` (full-width) is a visual lookalike of a code. Fold confusables (NFKC)
  or strip code-like tokens inside labels.
- **FU-8 (security Hardening):** room values are bounded by the hard limits only, as designed
  ([R15.25]), so a creator or owner can set `n=1` and spend the project's own key faster than the
  designer's soft bounds would allow. A per-room turn budget is the remaining backstop.
- **FU-9 [FIXED, D-14] (quality Warning, deferred with the requester's knowledge):** the timer side effects of
  a wake-up write (dropping a deferred every-N firing, re-arming the silence clock) run in Redis
  right after the CAS UPDATE, before the owning transaction commits. If that transaction then rolls
  back (a failed turn after the tool call, a refresh savepoint failure), Redis keeps the effect. Both
  effects only ever delay or suppress a reply, never cause one. The fix is to return the effects and
  apply them after the commit at each of the three call sites.
- **FU-10 (pre-existing pattern):** the tool's CAS UPDATE runs on the turn's session, which commits
  with the reply, so the binding row stays locked for the rest of the turn and blocks a concurrent
  human PATCH/reset, role change or unbind; the hourly refresh holds every touched row until its one
  commit. The previous `update_wakeup` held the `agents` row the same way.
- **FU-11 (quality Info):** `RoomWakeup` carries the full `Agent` up to the route, which reads only
  ids; `RoomWakeupPanel.vue` could split its query and mutations into a composable; the frontend
  repeats the 1000/1440/100 bounds instead of reading them from the API.
- **FU-12 [FIXED 2026-10-18] (staging data):** staging's installed `object-reinterpretation` type predates the
  `best_pick_reasoning` field, so its form has no 選擇理由 slot while the shipped teacher prompt asks
  students to fill it. Install never updates an existing type; the remedy is the documented
  delete-and-reinstall, which revokes project opt-ins and changes the type id (so TA's activity
  grants must be re-ticked). Found 2026-10-17; do it between classes. **Done 2026-10-18** at the
  requester's request: the old type `a78736c2` was deleted (soft; its submissions stay), which also
  ended the only live activation of it: one the requester started by hand on 2026-09-28 in
  創造力-拆字蓋樓-Group1, a TEST PROJECT room (same workspace as the test rooms) whose bound
  creative-thinking agents hold no activity grant. The reinstall created `dbe3b487`, whose form
  carries `best_pick_reasoning`. Of the platform's six projects only TEST PROJECT had the example
  enabled, and it has it enabled again. Every bound agent in the 18 rooms of those projects'
  workspaces was checked; only 萬物新解-Test-Group1's TA had the old id in its grant, and it is
  re-granted for the new one.
- **FU-13 [FIXED 2026-10-18] (prompt):** in the same run SA-CharBuilding, woken by silence after the creator said time
  was up but before the activity ended, offered the creator's just-submitted character as its own
  idea. The quoting rule permits quoting `character-building` answers but not passing one off as
  the peer's own; the peer prompt should say its example must not be one already submitted. Its
  later shares in the sharing stage were its own. Fixed: each of the three peers now says an answer
  a classmate (or, for CRAT, the group) already submitted or said must not be passed off as its own
  (`test_agent_example_packs.py::test_peers_never_pass_off_a_classmates_answer_as_their_own`).
- **FU-14 [FIXED 2026-10-18] (prompt):** in the re-run after D-16 was deployed, TA-CharBuilding
  woke on the creator's third message as intended, announced time up and restored its cadence,
  but called no `end_activity`, so the worksheet stayed open (an earlier run of the same step
  did call it). Each teacher's ending step now says to call the tool in the same turn and that
  announcing alone does not close the form
  (`::test_teachers_end_the_activity_with_the_tool_not_just_words`). Whether a model follows it is
  still live evidence (AC-21), not a guarantee. 2026-10-20 run: the teacher did call it (D-18).
- **FU-15 (prompt, D-18):** mark every character-building bank item 「可重複使用」 or
  「不可重複使用」 instead of marking only the reusable ones; one teacher announced K1 as reusable.
- **FU-16 (platform, D-17, blocks AC-24):** the CRAT submission digest agents receive is
  `5/5 fields answered: ...`, never the submitted target character, so the teacher cannot confirm a
  matching submission. Either a CRAT validator detail or digest that carries the target (the type
  already sets `expose_payload_to_agent: true`) or a prompt that routes every result to the human
  teacher; decide before AC-24 can close.
  **2026-10-23: folded into `2026-10-20-crat-facilitation-phase3-late-joiners-and-activity-state`
  (its Q-12, §6.6, AC-21 to AC-25).** The type quotes `target_character` after the participant
  marker; AC-24's remaining half closes with that dossier's AC-25.
- **FU-17 (prompt, D-26):** the character teacher's A2 step 3 describes the worksheet before any
  activity runs, while its [Activity state] rule says not to mention the form without one. Move the
  form description into B2 after the activation, or exempt the rules explanation explicitly. The
  phase 4 pack carries the same A2 text.

## 17. Round bank (Q-11, approved 2026-10-16)

Drafted 2026-10-16 for AC-23 and approved item by item by the requester the same day: C1..C8,
K1..K6 with 垚 removed from K5, and O1..O6. Items are written in the courses'
language (Traditional Chinese). "Ambiguity check" records the competing answers that were
looked for; an item with a second common answer was replaced while drafting.

### 17.1 CRAT (8 rounds)

Each clue character forms a common two-character word with the target. All words are in
everyday Taiwanese Mandarin use.

| # | Clues | Target | Words | Ambiguity check |
|---|---|---|---|---|
| C1 | 點、放、粗 | 心 | 點心、放心、粗心 | `開` was dropped from an earlier draft: 開火、點火、放火 made 火 a second answer. |
| C2 | 口、淚、香 | 水 | 口水、淚水、香水 | `汗` was dropped: 口味、汗味、香味 made 味 a second answer. |
| C3 | 主、客、詩 | 人 | 主人、客人、詩人 | No second target forms all three (主角/客角, 主題/客題 fail). |
| C4 | 颱、威、兜 | 風 | 颱風、威風、兜風 | 颱 combines only with 風 in common use. |
| C5 | 眼、時、觀 | 光 | 眼光、時光、觀光 | 眼界/時界, 觀眾/眼眾 fail. |
| C6 | 熱、專、竅 | 門 | 熱門、專門、竅門 | 竅 combines commonly only with 門. |
| C7 | 秘、證、圖 | 書 | 秘書、證書、圖書 | 秘密/證密, 圖案/證案 fail. |
| C8 | 饅、碼、枕 | 頭 | 饅頭、碼頭、枕頭 | 饅 combines only with 頭. |

### 17.2 Character building (6 component sets)

Rule shown to students: build characters from the given components; a component may be used
more than once where the set says so. Reference characters are for the teacher agent to check
and discuss work after submission, never to post first. All are standard Traditional characters.

| # | Components | Reference characters | Note |
|---|---|---|---|
| K1 | 木、口 | 杏、呆、束、困 | Four layouts of the same two components (top-bottom both ways, enclosed, interlocked). |
| K2 | 日、月 (repeatable) | 明、昌、朋、晶 | Repetition yields 昌 (日日), 朋 (月月), 晶 (日日日). |
| K3 | 火 (repeatable) | 炎、焱 | A minimal set for the repetition idea; two references only. |
| K4 | 女、口、子 | 好、如 | 好 = 女子, 如 = 女口. |
| K5 | 土、口 (repeatable) | 吐、圭 | 垚 removed at approval as too uncommon. |
| K6 | 力、口 | 加、另 | 加 = 力口 side by side, 另 = 口 over 力. |

The current room prompt's fixed 木/口 demonstration becomes K1 and is approved or removed
with it.

### 17.3 Object reinterpretation (6 objects)

Reference uses are cited by the teacher agent only after students have shared, never as a
list to copy. Each object has at least five so the five idea slots are not exhausted by the
reference alone.

| # | Object | Reference uses |
|---|---|---|
| O1 | 迴紋針 | 手機取卡針、臨時掛勾、書籤、拉鍊拉環替代品、小型鐵絲雕塑 |
| O2 | 報紙 | 包裝填充物、擦玻璃、摺紙帽、雨天鞋內吸濕、搬家時的保護墊 |
| O3 | 寶特瓶 | 小花盆、筆筒、撲滿、自製灑水器、剪半當漏斗 |
| O4 | 衣架 | 勾取掉到家具後的東西、吊掛盆栽、撐開垃圾袋、平板支架、圍巾收納架 |
| O5 | 磚塊 | 門擋、書擋、壓住防水布、墊高物品、簡易啞鈴 |
| O6 | 橡皮筋 | 開罐頭防滑、捆綁收納線材、標記自己的杯子、固定書頁、筆的防滑握把 |

Safety screen: uses involving fire, ingestion or projectiles were excluded while drafting.
