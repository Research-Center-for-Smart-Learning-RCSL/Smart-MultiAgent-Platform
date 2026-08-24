---
type: feature
status: draft
created: 2026-08-24
requirements: [R5.06, R13.04, R13.28, R13.29, R13.30, R13.31, R30.01, R30.03, R30.08, R30.10, R30.21, R30.22, R30.23, R30.26, R30.27, R30.38]
depends_on: [2026-08-24-traceability-extraction-gate, 2026-08-24-observer-presentation-blocks, 2026-08-24-example-agents-quote-unit-two]
---

# An activity may be submitted by a Member Group, with configurable consent

## 1. Summary

Every activity submission today belongs to exactly one person: [R30.01] binds a session to
one subject, and `_ensure_subject_is_caller` refuses a submission made as anyone else
(`session_service.py:43-50`). This feature lets a **project Member Group** ([R13.28]) be the
subject of a session. One member proposes a payload, the group's other members vote, and the
submission is recorded once the type's configured consent threshold is met — the platform
does not hard-code unanimity, it enforces whatever fraction the activity type declares.

It also ships a new example activity type written for group work. **None of the four existing
example types is changed**, because none of them is a group activity and forcing them to be
one would damage the units they belong to (§4.4).

## 2. Goals and Non-goals

**Goals**

- An `ActivitySession` may have a Member Group as its subject instead of a user.
- An activity type declares whether it is group-submittable and what fraction of the group
  must approve. The platform enforces the fraction; it does not choose it.
- A proposal pins its voter set at creation, so a membership change mid-flight cannot move the
  goalposts in either direction.
- A group submission runs the unchanged validation, scoring, echo and audit path.
- Agent-visible surfaces distinguish a group subject from a person, by code, without naming
  anyone.
- The course ships one activity type that is genuinely a group task.

**Non-goals**

- **The four existing example types are not made group-submittable.** §4.4 is the analysis;
  Q-6 is the decision.
- **No ad-hoc groups.** The group is a Member Group of the room's project, bound to the room.
  There is no "pick some people" flow.
- **No new grouping entity.** Member Groups exist ([R13.28]-[R13.32], migration 0079,
  `status: implemented`) and this feature consumes them rather than adding a parallel concept.
- **Member Groups keep their meaning.** They remain an access tier ([R5.06]); this feature adds
  a *use* of the same membership, and confers no capability through it.
- **No mixed submission.** A submission is a person's or a group's, never both. A group
  member's own individual session for the same activation is unaffected and separate.
- **Agents do not propose or vote.** Agents are not users and hold no group membership.
- **No change to individual submission.** Every existing path behaves identically.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | What is a "group"? | A project Member Group bound to the room, which the proposer belongs to. | The user's direction. It reuses `chatroom_member_groups` and `TenancyFacade.member_group_ids_for_user` (`tenancy/interfaces/facade.py:54-62`) rather than inventing a grouping. The rejected alternative — "every non-agent member of the room" — cannot express several groups working in one room, which is the ordinary classroom shape. |
| Q-2 | How strict is consent? | The platform does not hard-code it. The activity type declares a fraction; the example uses 2/3. | The user's direction. Expressed as an integer `numerator`/`denominator` rather than a float, so the required count is exact integer arithmetic (`ceil(n * size / d)`) rather than a rounding argument about `0.667`. Unanimity is `1/1` and remains expressible. |
| Q-3 | Where does the threshold live? | A new `ActivityType.group_config` JSONB, not `validator_config`. | `validator_config` is owner-confidential ([R30.25]) and validator-scoped, while participants must see the threshold they are voting against. `group_config` is a behavioural definition field, so editing it re-runs the type checks, bumps `version`, and is refused while an activation is live ([R30.23]) — which is the right handling for a rule that governs a vote in progress. |
| Q-4 | Does one rejection kill a proposal? | No. A proposal fails when the remaining undecided votes can no longer reach the threshold. | With a 2/3 threshold, treating one rejection as fatal would silently implement unanimity. Stating the rule as reachability makes the configured fraction actually mean what it says. |
| Q-5 | Who may see the votes? | The pinned voters and the room creator. Not the room, and **not any agent**. | The vote record names people and records dissent. It is an accountability record for the group and the teacher; putting it where an agent can read it would make a disagreement into class material. |
| Q-6 | Do the four existing example types become group-submittable? | No. A new type is added instead. | §4.4. Units 2 and 4 are first-person by construction, and unit 2's teaching point is explicitly that answers differ per person — TA's prompt says "不需要被統一成一種答案" and AA's says the spread "是這個單元最值得回報的觀察" (`creative-thinking-room.json:27`, `:77`). A group submission erases exactly the signal those two agents exist to surface. |
| Q-7 | What is the new type? | `six-hats-shared-case` — the five hats applied to a scenario the group shares rather than a personal difficulty. | It returns the technique to its original use: de Bono's hats are a *parallel-thinking group method*, which this course deliberately turned inward for the self-development theme axis. It is therefore faithful to the source, complementary to `six-hats-emotion-desk`, and carries no personal disclosure at all — which is what makes it safe to submit collectively. |
| Q-8 | Does this depend on `2026-08-24-observer-presentation-blocks`? | Yes. Overlap plus reuse. | That dossier's `attempt_table` and `field_coverage` aggregates key on a participant code; a group subject makes that code polymorphic (§5.5). It also lands `filled_count_coverage`, which the new type uses. Built after it, this task extends one aggregate rather than two dossiers racing on the same shape. |
| Q-9 | Does this depend on `2026-08-24-example-agents-quote-unit-two`? | Yes, logically. | That task splits the agents' quoting rule by activity type key. The new type needs a column in that split from the moment it exists, and writing it into a flat rule that is about to be replaced would produce a prompt nobody can review. |
| Q-10 | What about `2026-08-24-agent-readable-live-drafts`, which also edits the pack prompts? | **Not** a dependency; a file overlap. Whoever builds second rebases. | Different clauses of the same prompts: that task adds a draft rule to the 界線 sections, this one adds the new unit and its type key. Sequencing them would serialise two unrelated reviews. |

## 4. Current State

### 4.1 One subject per session, enforced in three places

- **[R30.01]**: "an `ActivitySession` groups **one subject's** submissions within one
  `ActivityActivation`... A subject has at most one session per activation."
- `activity_sessions.subject_user_id` is `NOT NULL` with an FK to `users`
  (`activities/infrastructure/tables.py:69-74`).
- `_ensure_subject_is_caller` (`session_service.py:43-50`) refuses any submission whose
  subject is not the caller, collapsing the mismatch into `SessionNotFound` so a non-subject
  cannot even confirm the session exists. It is called from four sites
  (`submission_service.py:98`, `session_service.py:99`, `:128`, `:186`).

So this is a domain change, not a configuration change.

### 4.2 What already exists and can be reused wholesale

- **`ActivitySubmission.producer_user_id`** (`activities/domain/models.py:239`) is already
  distinct from the session's subject. The producer/subject split this feature needs is
  already in the schema; nothing uses it to mean anything different yet.
- **Member Groups are implemented** (`docs/tasks/2026-08-20-member-groups-and-room-visibility-isolation/`,
  `status: implemented`, migration 0079).
- **`TenancyFacade`** exposes exactly the three reads this needs, and its docstrings say why
  each is shaped that way: `get_member_group` (`:32-39`), `live_member_group_ids`
  (`:41-52`), `member_group_ids_for_user` (`:54-62`).
- **Room bindings exist**: `chatroom_member_groups` (`conversation/infrastructure/tables.py:153`)
  with `ChatroomMemberGroupRepository.list_for_room`, surfaced through
  `chatroom_service.list_member_groups` (`:56`) and audited on change (`:79`).
- **Attempt numbering is per session** (`submission_service.py:122-126`), so a group session
  gets a correct attempt sequence with no change at all.

### 4.3 The constraint that shapes the room setup

`allow_member_groups` and `allow_project_members` are **mutually exclusive**, refused
server-side (`chatroom_service.py:39-43`, [R13.04]). A room that uses Member Groups as its
access tier therefore admits project members *only* through a bound group.

Two consequences the guide must state rather than let a teacher discover:

- **Every student must be in a bound group**, or they cannot reach the room at all. The
  grouping is not just for submitting; in such a room it is the door.
- **A guest can never join a group submission.** [R13.28] restricts group membership to
  current Project Members, while [R30.26] makes a guest a full activity participant. So a
  guest in such a room submits individually and is invisible to every group flow. This is a
  real gap, recorded in §14, not designed around.

Project Owners and Org Owners reach every room regardless ([R13.30]), so the teacher needs no
group membership — which is correct, since a teacher is not part of a student group.

### 4.4 Why no existing example type fits

| Type | Prompt/field evidence | Why a group submission damages it |
|---|---|---|
| `mandala-9grid` | Cells are 家 / 工作 / 具備能力 / 外貌 / 休閒娛樂 / 人際關係 / 想對 30 歲的自己說 (`creative-thinking.json:17-67`) | First-person by construction. The unit's stated teaching point is that value orderings differ per person, and both TA and AA are written to surface that spread. A consensus answer erases the unit's own signal. |
| `time-traveler-next-steps` | "現在的我需要學習的可能有" (`:82-95`) | A personal action plan; a group's shared next step is a different exercise. |
| `emotion-desk-three-emotions` | "生活中最常出現的三種情緒" plus each one's most recent cause (`:108-146`) | First-person and personal. |
| `six-hats-emotion-desk` | "一件最近或曾經讓自己困擾的事" (`:160-166`) | The worst fit. Group consent over one member's distressing event either publishes that member's difficulty to the group or forces them to supply a fictional one. Every safety clause the room-facing prompts carry for unit 4 exists because of this field. |

This is the analysis the user asked for, and its answer is that the capability is worth
building and the current examples are not where to demonstrate it.

## 5. Design

### Options considered

**Option A — a submission linked to several individual sessions.** No schema loosening;
attribution is a join table. Rejected: attempt numbering, "I am finished" ([R30.22]), the
aggregation read model ([R30.10]) and the agent context block are all per session, so every
one of them would need a special case for a submission that belongs to N of them.

**Option B — a session whose subject is a Member Group.** Chosen. One nullable column and a
CHECK; everything downstream that reads a session keeps working because a session still has
exactly one subject — it is simply not always a person.

**Option C — a group-mode flag on the activation instead of the type.** Rejected: whether an
activity is a group task is a property of the task, and putting it on the activation would
let the same type be individual in one room and collective in the next, with the payload
schema and the agents' prompts written for only one of those.

### Decision

A session's subject becomes polymorphic: exactly one of `subject_user_id` or
`subject_member_group_id`. A group session is created only by an accepted **proposal**, never
by a direct submit, so the consent check is structurally unavoidable rather than a rule the
submit path must remember.

What was consciously given up: `subject_user_id` stops being `NOT NULL`, which weakens a
constraint several readers rely on implicitly. §6 pins the replacement with a DB CHECK and
§12 tests the polymorphism at the `db` tier, because a unit-tier `literal_binds` compile
cannot see a CHECK violation.

### 5.1 Type configuration

`activity_types.group_config JSONB NULL`. `NULL` means the type is individual-only, which is
every existing type, so nothing changes by default.

```
{"consent": {"numerator": 2, "denominator": 3}}
```

Validated at registration and edit ([R30.02], [R30.23]) alongside the existing schema and
validator checks: both integers, `0 < numerator <= denominator`, `denominator <= 100`.
Required approvals for a pinned group of size `N` is `ceil(numerator * N / denominator)`,
clamped to at least 1 and at most `N`. Unanimity is `1/1`.

It is a behavioural definition field, so an edit bumps `version` and is refused while any
activation of the type is live — a threshold must not change under a vote in progress.

### 5.2 The proposal

New table `activity_group_proposals`: `id`, `chatroom_id`, `activation_id`,
`activity_type_id`, `member_group_id`, `proposer_user_id`, `payload JSONB`,
`voter_user_ids JSONB` (the pinned set), `required_approvals INT`, `status`, `created_at`,
`expires_at`, `resolved_at`, `submission_id` (nullable). Plus
`activity_group_proposal_votes` keyed `(proposal_id, user_id)` carrying `approve | reject`
and a timestamp.

**Creating one.** The proposer must be a member of a live group of the room's project that is
bound to the room, the activation must be live for the type, the type must carry
`group_config`, and the payload must pass `payload_errors` immediately — a proposal nobody
can accept should fail at proposal time, not after three people have voted for it.

**Pinning.** `voter_user_ids` is the group's members at creation, intersected with those who
can read the room. `required_approvals` is computed from that set and stored. A later
membership change does not alter either: a person added mid-vote cannot be bound by a
proposal they never saw, and a person removed mid-vote does not lower a bar the group already
agreed to clear.

**One at a time.** At most one `open` proposal per `(activation_id, member_group_id)`,
enforced by a partial unique index. Two competing proposals would split the votes and neither
would pass.

**Resolution.** The proposer's approval is implicit and recorded as a vote row.

- `accepted` when approvals reach `required_approvals`.
- `rejected` when `approvals + undecided < required_approvals` — the threshold has become
  unreachable (Q-4).
- `withdrawn` by the proposer while open.
- `expired` at `expires_at`, and unconditionally when the activation ends ([R30.22] closes
  sessions; an open proposal for a finished round must not later become a submission).

**Acceptance is the submission.** In one transaction: resolve or create the group's session
for this activation, take the session lock, allocate the attempt number, run the validator,
insert the submission with `producer_user_id = proposer_user_id`, stamp `submission_id` on
the proposal, and emit the room echo. Every one of those steps is the existing code path
(`submission_service.py:100-190`), reached with a group session instead of a personal one.

### 5.3 Voting surface

`POST /api/chatrooms/{id}/activity-proposals`, `POST .../{proposal_id}/votes`,
`POST .../{proposal_id}/withdraw`, and a read scoped per Q-5. All room-scoped, gated through
the room-access chain ([R30.09]); the read additionally requires the caller to be a pinned
voter or the room creator.

Realtime: `activity.proposal.opened / voted / resolved` on the room channel, carrying ids,
the group id, and counts — **never the payload and never a per-person vote**. The room learns
that a group is deciding; only the group sees what and who.

### 5.4 What the room sees

[R30.08]'s system echo fires on acceptance, exactly as for an individual submission, and
carries the **group's name** rather than any member's. `echo_includes_content` governs answer
text on this path unchanged.

### 5.5 What agents see

`RecentActivityRow.subject_user_id` becomes a subject reference plus a kind.
`_subject_code` (`activity_context_provider.py:146-147`) gains a group form, `g:1a2b3c4d`, so
a group row is visually distinct from a person's `u:` row and no code space collides.

The legend ([R30.38]) resolves a group code to the **group's name** — which is a
teacher-authored label, not self-chosen personal text, so it does not carry the
injection-surface concern that made `_one_line` and the one-pair-per-line format necessary
for display names (`:150-199`). It is passed through `_one_line` anyway, because a rule that
holds only for the values someone remembered to sanitise is not a rule.

The block's preamble gains one sentence: a row may belong to a group, and a group row is one
submission by several people, not several submissions.

**No agent sees the votes** (Q-5). The proposal is invisible to the context block entirely;
only the resulting submission appears, exactly like any other.

### 5.6 The new example type

`six-hats-shared-case` — 共同情境六頂思考帽:

- `case` — the shared scenario the group is thinking about (teacher-set or group-chosen).
- `hat_white` / `hat_red` / `hat_black` / `hat_yellow` / `hat_blue` — the five hats, in the
  order the course already uses, with the same plain descriptors
  (`creative-thinking.json:168-197`) rather than the copyrighted character scaffold.
- `x-order` 1 to 6 ([R30.36]).
- `validator_id: filled_count_coverage` with `min_filled: 4`, `expose_payload_to_agent: true`,
  `echo_includes_content: false`, `group_config: {"consent": {"numerator": 2, "denominator": 3}}`.

It has no plugin, so it renders through `SchemaForm` — the registry keys on
`manifest.key == ActivityType.key` (`plugins/registry.ts:1-15`) and no plugin claims this
key.

The room pack's three agents add it to `binds_activity_types` and gain a short section on the
unit: it is a group task about a shared case, it contains no personal disclosure, and its
answers are quotable (the unit 2 column of the rule
`2026-08-24-example-agents-quote-unit-two` introduces). TA additionally must not treat a
group's answer as one student's.

## 6. Detailed Changes

**Backend — `contexts/activities`** (migration: **take the number from `alembic heads` at
build start**, not from this dossier — this task and `2026-08-24-agent-readable-live-drafts`
share all three predecessors with no ordering between them, so a hard-coded number collides
for whichever builds second)

- `tables.py`: `activity_sessions.subject_user_id` becomes nullable; new
  `subject_member_group_id` (no FK — the groups table belongs to tenancy, and [R30.09]
  forbids the cross-context join a constraint would invite); DB CHECK that exactly one is
  set; the existing per-activation uniqueness extended to cover the group subject. New
  `activity_types.group_config JSONB NULL`. Two new tables per §5.2, with a partial unique
  index on `(activation_id, member_group_id) WHERE status = 'open'`.
- `domain/models.py`: `ActivitySession` subject fields; `ActivityType.group_config`;
  `RecentActivityRow` subject kind; new `GroupProposal`, `ProposalVote`, `ProposalStatus`.
- `application/group_proposal_service.py`: create, vote, withdraw, expire, and the accept
  path that calls into the existing submission flow.
- `application/session_service.py`: `_ensure_subject_is_caller` keeps its exact meaning for
  personal sessions; a group session is refused to it outright and reached only through the
  proposal service, so the guard is never weakened, only bypassed by a path that has its own.
- `application/type_service.py`: `group_config` validation at registration and edit.
- `application/activity_context_provider.py`: group codes and the legend/preamble change.
- `interfaces/facade.py`: the proposal operations, plus the group-name resolution the legend
  needs, read through `TenancyFacade`.
- `infrastructure/repositories/`: proposal and vote repositories; session repository gains the
  group-subject resolution.

**Backend — workers**

- `app/workers/tasks/activities.py`: proposal expiry, alongside the existing retention and
  watchdog sweeps.

**API contract**

- Four new room-scoped endpoints (§5.3). `ActivityTypeOut` gains `group_config`, and so do
  **`ActivityTypeCreateIn` and `ActivityTypeUpdateIn`** (`app/api/v1/activities.py:87-117`,
  `:91-102`) — without them the field is settable only by hand-editing the shipped catalogue
  JSON, AC-3's edit half is unreachable, and no project could ever declare its own type
  group-submittable, which contradicts §5.1. `ActivitySessionOut` gains the subject kind.
  `pnpm run gen:api` rerun: **yes**.
- **`AdminPlatformActivityTypeIn` is deliberately not extended.** Its docstring
  (`admin_activities.py:350-356`) states the reason: it is a four-field install surface, and
  "the moment a schema is editable from here this stops being an install surface and becomes a
  course-authoring CMS". `group_config` is a behavioural definition field like
  `payload_schema`, so it belongs on the same side of that line. The consequence is real and
  goes in the guide: a platform-scoped example's consent fraction is whatever the shipped
  catalogue says, and a project wanting a different one installs a project-scoped copy via
  `python -m smap.examples` ([R30.28]) and edits that.

**Frontend — `slices/activities`**

- `ActivityPanel.vue`: a group mode when the active type carries `group_config` — pick the
  group, propose, and a live proposal card showing the threshold, the counts, and the
  caller's own vote.
- New `GroupProposalCard.vue` and `useGroupProposal.ts`.
- `types/schemas.ts`, `api/index.ts`, `stores/activities.ts`: the proposal state.
- i18n in both locales, including the threshold rendered as a sentence rather than a fraction
  glyph.
- **The slice still imports no conversation state**; the room id and the caller arrive as
  props, as they do today.

**Example and docs**

- `contexts/activities/infrastructure/examples/courses/creative-thinking.json`: the new type.
- `contexts/agents/infrastructure/examples/packs/creative-thinking-room.json`: bindings and
  the unit section for all three agents.
- `docs/examples/creative-thinking-course.md`: the new unit, the room setup constraint from
  §4.3 (both consequences), the guest gap, and the 2/3 threshold with what it means when a
  member disagrees.

## 7. NFR Checklist

- **i18n** — every new string through `$t()`. The threshold is rendered as a sentence ("需要
  3 人同意，目前 2 人"), never as a bare `2/3`, which reads as a score in an activity panel.
- **Audit log** — `activity.proposal_created`, `activity.proposal_voted`,
  `activity.proposal_resolved` (with the terminal status), plus the existing
  `activity.submission_created` on acceptance ([R30.11]). Payload content never enters audit
  metadata.
- **Tenant isolation** — every endpoint gates through the room-access chain ([R30.09]); the
  group must be a live group **of the room's project** (`live_member_group_ids`) and bound to
  the room; the proposer must belong to it. No cross-context SQL join — group identity and
  membership are read through `TenancyFacade`.
- **Error handling UX** — a vote on a resolved proposal returns 409 and the card refetches
  rather than showing a stale count; a proposal whose activation ended shows why; the payload
  validation error at proposal time names the offending fields, reusing the existing
  `SubmissionPayloadInvalid` shape.
- **Performance** — one proposal row plus at most N vote rows per group per round; the pinned
  voter list is stored, so resolution needs no membership re-read. The room broadcast carries
  counts, so a vote does not fan out a payload.

## 8. Security Considerations

Touches tenant boundaries, a new REST surface, WebSocket broadcast, and user-input
processing.

- **The group must be reachable from the room, not merely named.** Three independent checks —
  live group of the room's project, bound to the room, proposer is a member — and all three
  run server-side on every proposal and every vote. A group id from another project is
  indistinguishable from a missing one.
- **A vote is not a submission right.** Only pinned voters may vote, and the pin is stored,
  so adding someone to the group mid-vote does not hand them a ballot.
- **The threshold is below unanimity, and that has a cost.** With 2/3, a group's submission
  can carry an answer a member voted against. The mitigations: the submission is attributed to
  the **group**, never to individuals; the vote record is kept and is visible to the group and
  the teacher; and no agent can see the dissent (Q-5). What is not mitigated: a member of a
  group is associated with an answer they rejected. That is inherent in the user's chosen
  model and is stated in the guide so a teacher chooses the threshold knowingly.
- **Dissent must not become class material.** The room broadcast carries counts only, and the
  proposal never enters the agent context block. A per-person vote reaching an agent would
  turn a disagreement into something an agent can talk about in front of the class.
- **`allow_member_groups` is exclusive with `allow_project_members`** (§4.3). A teacher
  enabling group submission changes their room's access tier, and a student in no bound group
  loses access entirely. The settings UI must say this at the moment of the change, not in a
  document.
- **Legend injection.** A group name is teacher-authored rather than self-chosen, so it is a
  weaker injection surface than a display name — but it is still interpolated into the context
  block's legend, so it goes through `_one_line` like every other value ([R30.38],
  `activity_context_provider.py:150-170`).
- **Payload provenance.** The stored payload is the proposer's text; the other members
  approved it but did not write it. `producer_user_id` records who did, which is what makes
  the record honest.

## 9. Quality Notes

**Existing debt in touched files:**

- `_ensure_subject_is_caller` is imported by `submission_service` from `session_service`
  (`submission_service.py:26`) — a module-private helper crossing a module boundary. Do not
  imitate; the proposal service gets its own guard rather than a third importer. Not fixed
  here (FU-1).
- `activity_sessions` losing `NOT NULL` on `subject_user_id` weakens a constraint that several
  readers rely on implicitly. The DB CHECK is the replacement and must land in the same
  migration, not a later one.
- `ActivityPanel.vue` already carries the activation, submission and outcome state. Adding
  proposal state inline would make it the slice's largest component; the composable is not
  optional tidiness.

**Patterns to follow:**

- `submission_service.py:100-190` — the transaction discipline the accept path must reuse
  verbatim: hold the activation row for update, lock the session, allocate the attempt,
  validate, insert.
- `tenancy/interfaces/facade.py:32-62` — the three reads, and their docstrings' reasoning
  about which context owns which half of a question.
- `chatroom_service.py:39-43` — how a mutually exclusive flag pair is refused, for the
  settings-UI warning.

**Reuse inventory:**

- `payload_errors` (`validators/schema.py`), `build_agent_digest`, `InProcessValidator`,
  `next_attempt_no`, `get_active_for_update` — the accept path adds no validation or scoring
  code of its own.
- `filled_count_coverage` from `2026-08-24-observer-presentation-blocks`.
- `_subject_code` and `_legend` (`activity_context_provider.py:146-199`) — extend, do not
  restate.
- `SchemaForm.vue` — the new type needs no plugin.
- `ChatroomMemberGroupRepository.list_for_room` via the conversation facade; do not query
  `chatroom_member_groups` from the activities context.

## 10. Risks and Rollback

- **This migration is the riskiest in this series.** It relaxes a `NOT NULL` on a live table
  and adds a CHECK in its place. Forward compatible (old code writes `subject_user_id` and
  satisfies the CHECK); reversing needs every group session deleted before `subject_user_id`
  can go back to `NOT NULL`, which the down-revision must do explicitly rather than fail
  halfway.
- **A proposal that outlives its activation would create a submission for a finished round.**
  Expiry on activation end is a correctness requirement (AC-9), not housekeeping.
- **Vote-splitting** is prevented by the partial unique index, not by the application, so a
  concurrent double-propose fails at the database rather than producing two open proposals.
- **The room access change is the one most likely to surprise a teacher** (§4.3). It is a
  settings-time warning plus a guide section; nothing else can prevent it, because the
  exclusivity is deliberate ([R13.04]).
- **The example does not reach existing installs.** Same trap as every other example change:
  install is idempotent by key and never updates, so the new type appears only in projects
  that install after this lands — which for a *new* key is simply an install, not the
  delete-and-reinstall dance the modified types require
  (`docs/examples/creative-thinking-course.md:348-374`). Worth stating because it is the one
  example change in this series that is cheap to adopt.

## 11. Acceptance Criteria

- [ ] AC-1: A session may have exactly one subject, a user or a Member Group, enforced by a
      database CHECK and verified at the `db` tier.
- [ ] AC-2: An activity type with `group_config: NULL` behaves exactly as today; no existing
      path changes.
- [ ] AC-3: `group_config` is settable through the project-scoped create and edit routes and
      validated there; a malformed fraction is refused, and an edit is refused while an
      activation of the type is live. The admin platform-type surface still refuses it.
- [ ] AC-4: Required approvals is `ceil(numerator * N / denominator)` over the **pinned**
      voter set, at least 1 and at most N; `1/1` requires everyone.
- [ ] AC-5: A proposal may be created only by a member of a live group of the room's project
      that is bound to the room, only while the type's activation is live, and only with a
      payload that already passes the type schema.
- [ ] AC-6: The voter set and the required count are pinned at creation; adding or removing a
      group member afterwards changes neither, and a non-pinned user cannot vote.
- [ ] AC-7: At most one open proposal exists per (activation, group), enforced by a database
      constraint under concurrency.
- [ ] AC-8: A proposal is rejected when approvals plus undecided votes can no longer reach the
      threshold — not on the first rejection, unless that is the same thing.
- [ ] AC-9: Ending the activation resolves every open proposal for it, and no proposal can
      produce a submission afterwards.
- [ ] AC-10: Acceptance produces exactly one submission in the group's session, with
      `producer_user_id` set to the proposer, a correct per-session attempt number, the
      configured validator's verdict, and the standard room echo naming the group.
- [ ] AC-11: The room broadcast and the room echo carry no payload and no per-person vote.
- [ ] AC-12: Only pinned voters and the room creator may read a proposal's votes.
- [ ] AC-13: No agent-visible surface contains a proposal, a vote, or a dissenting member —
      the context block shows only the resulting submission.
- [ ] AC-14: A group row in the context block carries a `g:` code, the legend resolves it to
      the group's name through `_one_line`, and the preamble states that a group row is one
      submission by several people.
- [ ] AC-15: `six-hats-shared-case` installs, activates, accepts a 2/3 group submission, and
      is scored by `filled_count_coverage`. The four existing types are byte-identical apart
      from nothing — they are not edited.
- [ ] AC-16: All three room-pack agents bind the new type and state that a group answer is not
      one student's.
- [ ] AC-17: The guide documents the new unit, both consequences of the
      `allow_member_groups` exclusivity, the guest gap, and what a 2/3 threshold means for a
      member who disagrees.
- [ ] AC-18: The settings UI warns, at the moment of the change, that enabling
      `allow_member_groups` removes `allow_project_members` access.
- [ ] AC-19: The full Definition of Done passes — `pytest -q`, `ruff`, `mypy`, `pnpm test`,
      `pnpm lint`, `pnpm typecheck`, `pnpm build`, `check:openapi-drift`,
      `check:boundaries-enforced`.

## 12. Test Plan

- **AC-1, AC-7** — `pytest.mark.db`. A CHECK constraint and a partial unique index are
  invisible to the unit tier, which compiles with `literal_binds` and never executes
  (`backend/CLAUDE.md`). AC-7 additionally needs two concurrent inserts, which only a real
  database can arbitrate.
- **AC-2** — a regression sweep: the existing activities unit suite must pass unmodified. Any
  test that needed editing is evidence the change was not additive.
- **AC-3 to AC-6, AC-8** — unit, new `test_group_proposals.py`, with the fraction arithmetic
  table-driven across group sizes 1 to 10 for `1/1`, `2/3` and `1/2`.
- **AC-9** — unit: end the activation with an open proposal, assert it resolves and that a
  subsequent vote and a subsequent accept both refuse.
- **AC-10** — unit over the accept path, asserting it reaches the same repository calls the
  individual path does, plus an integration test for the attempt sequence across a mixed
  personal and group set.
- **AC-11 to AC-13** — unit assertions over the broadcast payload, the read authorisation, and
  the context block output, the last with a fixture that has an open proposal with recorded
  dissent and asserts neither appears.
- **AC-14** — unit over `activity_context_provider`, extending its existing legend tests.
- **AC-15, AC-16** — unit over the shipped catalogue and packs, extending
  `test_smap_examples_catalogue.py` and `test_agent_example_packs.py`. The "not edited" half
  is a git-diff review, recorded in the dossier rather than asserted.
- **AC-17** — doc-diff review.
- **AC-5, AC-18, and the panel** — frontend component specs, plus a browser pass with
  `frontend:verify`: three sessions in one room, a proposal reaching 2 of 3, and the same
  proposal failing when reachability is lost. The threshold sentence checked at 375px in both
  locales.

## 13. SRS Delta

To be applied to `REQUIREMENTS.md` §30 on approval, appended after [R30.38].

- **[R30.39]** An `ActivitySession`'s subject is either one user or one project Member Group
  ([R13.28]), never both and never neither. A group session is created only by an accepted
  group proposal ([R30.41]); the per-subject rules of [R30.01] apply unchanged with the group
  as the subject, so a group has at most one session per activation and its own monotonic
  attempt sequence. A member's own individual session for the same activation is separate and
  unaffected.
- **[R30.40]** An `ActivityType` may declare `group_config`, whose absence means the type is
  individual-only. It carries the consent fraction as two integers; the approvals required of
  a pinned group of size `N` is `ceil(numerator * N / denominator)`, at least 1 and at most
  `N`. The fraction is validated at registration and edit, and `group_config` is a behavioural
  definition field under [R30.23]: an edit bumps the type's version and is refused while any
  activation of it is live, so a threshold never changes under a vote in progress. The
  platform does not define the fraction; the type does.
- **[R30.41]** A group submission is made by proposal. A member of a live Member Group of the
  room's project that is bound to that room proposes a schema-valid payload while the type's
  activation is live. The proposal pins its voter set and its required-approval count at
  creation, so a later membership change alters neither. At most one proposal per (activation,
  group) is open at a time. It is accepted when approvals reach the requirement, rejected when
  the remaining undecided votes can no longer reach it, withdrawn by the proposer, or expired
  by time or by the activation ending — and once resolved it can never produce a submission.
  Acceptance creates exactly one submission in the group's session, recording the proposer as
  `producer_user_id`, and runs the unchanged validation, scoring, echo and audit path.
  Proposal creation, each vote, and resolution emit audit events carrying no payload content.
- **[R30.42]** A proposal's per-person votes are readable only by its pinned voters and the
  room creator. Neither the room broadcast nor the room echo carries the payload or any
  per-person vote, and no proposal, vote, or dissent appears on any agent-visible surface: an
  agent sees the resulting submission and nothing about how the group arrived at it.
- **[R30.43]** A group subject appears in the activity context block ([R30.15], [R30.38])
  under a truncated group code distinct from the participant code space, and the block's
  legend resolves it to the group's name under the same one-pair-per-line, single-line,
  delimiter-stripped rules every other legend value follows. The block states that a group row
  is one submission by several people rather than several submissions.

## 14. Open Questions

- **OQ-1.** A guest cannot belong to a Member Group ([R13.28]) but is a full activity
  participant ([R30.26]), so a guest in a group-submission room can never take part in one.
  Not designed around: the alternative is a guest-inclusive ad-hoc group, which Q-1 rejected.
  Documented in the guide.
- **OQ-2.** `allow_member_groups` excluding `allow_project_members` ([R13.04]) means adopting
  group submission changes who can enter the room, not merely who submits together. That
  exclusivity is deliberate and out of scope here, but it is the single most likely source of
  a confused first setup.
- **OQ-3.** Nothing stops a teacher configuring a group threshold on a type whose payload is
  personal. The platform cannot tell the difference, and §4.4 is guidance rather than a gate.

## 15. Deviation Log

Empty. Appended by `/build`.

## 16. Follow-ups

- **FU-1.** `submission_service.py:26` imports the module-private
  `_ensure_subject_is_caller` from `session_service`. With a third caller now in play, the
  guard wants a proper home in the domain layer.
- **FU-2.** A group's members cannot see each other's individual submissions, which is correct
  today but will be asked for the first time a teacher wants "compare your own answers, then
  agree on one". That is a read-model question, not a submission question.
- **FU-3.** `six-hats-shared-case` has no plugin and renders through `SchemaForm`. A parallel
  five-column layout would suit the technique better than a vertical form, and the plugin SDK
  already supports it.
- **FU-4.** The aggregation read model ([R30.10]) reports per subject. With group subjects it
  now mixes two populations in one count; no current consumer is wrong because of it, but a
  dashboard that adds them together would be.
