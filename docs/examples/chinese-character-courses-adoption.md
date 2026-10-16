# Adopting the three Chinese-character course packs

This guide is for a teacher or project editor who wants to run 字詞拼圖 (CRAT word puzzle),
拆字蓋樓 (character building) or 萬物新解 (object reinterpretation) with the shipped agent
packs. It explains what an install does and does not give a room, how a project that installed
an earlier revision adopts the current one, and which behaviors are guidance the agents follow
rather than rules the platform enforces. The design behind these packs is recorded in
`docs/tasks/2026-10-16-crat-facilitation-phase2-classroom-context-and-cadence/spec.md`.

## What ships

Each course has two packs in `backend/contexts/agents/infrastructure/examples/packs/`. The room
pack (`*-room.json`) holds the three classroom agents; the design pack (`*-design.json`) holds a
design agent (DA) for the teacher's own preparation room, kept separate so that installing the
classroom pack cannot place a design agent in a student discussion.

| Course | Room pack | Activity type the room agents are written for |
|---|---|---|
| 字詞拼圖 | `crat-word-puzzle-room` | `crat-word-puzzle` |
| 拆字蓋樓 | `character-building-room` | `character-building` |
| 萬物新解 | `object-reinterpretation-room` | `object-reinterpretation` |

The teacher agent (TA) leads the lesson, the peer agent (SA) takes part as a classmate who
speaks rarely, and the analyst agent (AA) is bound as an observer and writes notes only the room
creator sees. The activity types themselves belong to the `chinese-character-creativity` course
and are installed and enabled separately, as described for every course in
[the creative-thinking worked example](creative-thinking-course.md#installing).

Each TA carries a numbered bank of ready-to-use rounds in its own system prompt and uses them in
order: eight clue sets for 字詞拼圖, six component sets for 拆字蓋樓, six everyday objects for
萬物新解. The TA does not ask the human teacher for content and does not adopt a round that
anyone posts in the room, including the room creator. A teacher who wants different rounds edits
the bank in the installed TA's prompt. Every shipped item was approved by the requester; the
items and their verification tables are in section 17 of the design record.

## Shipped wake-up defaults

These are the designer defaults each installed agent starts with. All three teachers count only
messages from people, so an agent's own messages do not advance its counter.

| Agent | Message trigger | Silence trigger | Self-adjustment allowed |
|---|---|---|---|
| TA (all three courses) | every 2 human messages | 2 minutes, stops after 2 rounds without a person | silence on or off; message count within 2 to 3 (字詞拼圖: 2 to 4) |
| SA 字詞拼圖, SA 萬物新解 | off | 3 minutes, stops after 2 rounds without a person | none |
| SA 拆字蓋樓 | off | 2 minutes, stops after 2 rounds without a person | none |
| AA (all three courses) | every 20 human messages | 10 minutes, observer stops after 6 rounds | none |

The teachers adjust themselves at two moments only. When individual work starts, the 拆字蓋樓
and 萬物新解 teachers turn their silence trigger off and raise the message count to 3, so a
quiet room of students writing is not treated as a lull; when the human teacher ends the work
phase they restore both. The 字詞拼圖 teacher turns silence off while a group's answer is being
confirmed and back on when the next round starts, and may raise the message count to 3 or 4
during a lively comparison, returning it to 2 at the next round. These numbers are release defaults, not response-time guarantees.

## What installing does not do

Installing a pack creates the agents and one agent group in the project. It creates no
chatroom, binds no agent to any room, and grants nothing. The pack's
`may_control_activities` and `binds_activity_types` fields describe how the prompts are written;
they are not applied grants ([R30.35], [R30.37]). After binding the agents to a room, the room
creator decides each grant from the room's Settings page, on the agent's row.

**Activity control for TA.** The teacher prompts say "if you have the start_activity tool", so a
TA without the grant still runs the lesson: it announces the work phase and the human teacher
starts and ends the activity from the Activity tab. With the grant, scoped to the course's one
activity type, TA starts the round itself when it reaches the work step and ends it when the
room creator says time is up. Granting it to SA or AA is possible but not what the prompts are
written for.

**Draft reading.** TA and SA can use `read_drafts` ([R32.03]) to notice that someone has been
typing for a long time, and their prompts forbid repeating anything they read. SA also uses it
to stay quiet while someone is mid-sentence. The packs grant neither. If the grant is used,
keep `disclose_drafts` on so the class is told, and read
[the draft-reading section of the worked example](creative-thinking-course.md#letting-an-agent-read-unsent-text)
first. Leaving AA without it is deliberate: an observer's notes are built on submissions, and
its prompt forbids counting drafts.

**Missing grants are not errors.** A room where no agent holds any grant works; the human
teacher simply starts and ends each activity. Binding the same agents to a second room grants
nothing there, and unbinding removes the grants with the binding.

## Wake-up settings belong to each room

Since migration `0104_room_wakeup_layers`, an agent's wake-up behavior in a room is resolved
from three layers, in order ([R15.09]): the designer defaults above, the room's own choices,
and the agent's temporary adjustments for that room. The room creator sees and edits the room
layer under "本討論室的喚醒設定" on the agent's row in the room's Settings page ([R15.25]).
Each field shows its source: 代理預設 (designer default), 本討論室 (this room) or 代理自行調整
(agent adjusted).

A change made there applies to that room only and never edits the agent's own configuration
or any other room. A room choice also replaces the agent's adjustment of the same field, and
the teacher prompts tell the agent that a trigger it did not turn off was turned off by the
teacher and must stay off. The agent's adjustments undo themselves at the time the panel shows,
24 hours after the first adjustment by default; the room's own choices survive that. Two
buttons undo the agent's adjustments only (復原代理的自行調整) or return the room to the designer
defaults entirely (重設為代理預設值).

## Upgrading a project that installed an earlier revision

Re-installing a pack is idempotent by agent name and never rewrites an agent that already
exists. A project that installed any of these packs before this revision still holds the old
prompts and wake-up defaults after re-installing, and the install result reports those agents
as already present. This is intentional: the platform does not overwrite customized agents.
There are two ways to adopt the new revision.

**Apply the changes by hand (keeps everything else).** A project editor opens each agent's
settings page and replaces two fields with the values from the pack file: the system prompt and
the wake-up defaults. Leave the model, key group, temperature, tools and knowledge as they are;
nothing in this revision needs them changed, and the pack never carries a key. Room bindings,
grants and room wake-up choices are untouched by editing an agent, so nothing has to be
re-granted. Review the new teacher bank before saving if your course already used its own
rounds, since saving the shipped prompt replaces them.

**Delete and re-install (discards local edits).** Deleting an agent is a soft delete, and the
re-installed copy is a different agent. Its bindings, grants and room wake-up choices start from
nothing: bind it to each room again, have each room creator re-grant it, and unbind the deleted
agent there, since the room settings of a deleted agent can no longer be changed. Use this only
for an agent nobody has customized.

Either way, a note AA wrote before the upgrade is not revised, and the old prompts' timing
promises (fractional waits, automatic phase changes, whole-class completion estimates) remain in
any transcript they produced.

## What is guidance, not enforcement

Several behaviors in these packs are instructions to the model. The platform does not enforce
them, and no test of them is a guarantee. A teacher should observe them in a dry run with the
model and key group the class will use.

- **Invitations.** A teacher may gently invite one connected, clearly identifiable participant
  to speak when the lesson step allows discussion, never during individual work, never the room
  creator, and never the same person twice in a row ([R13.75]). No server checks the timing or
  the target, and declining or staying silent is always acceptable. The participant facts the
  teacher reads ([R13.74]) show who is connected and who spoke in the recent window; they are
  not attendance and do not show attention.
- **Phase changes.** Every transition that the lesson ties to time or to the whole class
  finishing is confirmed by the room creator, because a silence timer and a window of recent
  submissions can establish neither. Saying "時間到" as room creator is what ends a work phase.
  The platform does not time phases.
- **Answers before submission.** The 字詞拼圖 teacher holds each round's target character and
  words in its prompt and is told never to reveal them before the group submits; the other two
  teachers hold reference characters or uses they cite only after students share. A student can
  try to extract them with a prompt-injection message, and nothing in the platform prevents it.
  The cost is a spoiled example round. Anyone who can edit agents in the project can read these
  answers in the prompt, which is a wider audience than the Project-Owner-only
  `validator_config` ([R30.25]); keep keys there if that matters for your course.
- **Different but valid answers.** When a 字詞拼圖 group submits a target that differs from the
  bank, the teacher does not mark it wrong; it shows the words the group formed and asks the
  room creator to judge.
- **Quoting.** Each agent may quote submissions only for its own course's activity type, and
  never volunteers a student's answer. AA's notes use participant codes, not names, and do not
  claim to score flexibility, originality or convergence, which have no validated rubric.

## Before the first class

Run one short session in a test room with two or three people: confirm the agents are bound
with the intended roles, check which grants are on, watch the teacher adjust its own wake-up
settings at the start of individual work and restore them when you say time is up, and confirm
that a second room bound to the same agents keeps its own settings. Record the pack revision,
model and settings you tested with, so that a later difference in behavior can be traced to a
change rather than guessed at.
