# Worked example: a creative-thinking course on the activities platform

A two-unit example showing how a published curriculum maps onto SMAP's structured
activities, together with the agents that run alongside it. It exists to answer one
question: *can the custom-activity and agent features carry a real lesson plan end to
end?* It is not a research instrument. Read [Limitations](#limitations) before using it
for anything else.

## Source

Ke Pei-jung (2019). *Effect of Creative Thinking Skills Integrated into Guidance Activity
Curriculum Self-Development Theme Axis on Creativity and Self-Concept for the Junior High
School Students* (創造思考技法融入輔導活動課程自我發展主題軸對國中生創造力及自我概念之影響).
MA thesis, Graduate Institute of Creativity Development, National Taiwan Normal
University. Advisor: Chen Hsueh-chih.

The study ran an eight-week guidance course, 少年《I》的奇幻旅程, with 133 seventh-graders
(67 experimental, 66 control), one 45-minute session per week, integrating two
creative-thinking techniques: de Bono's Six Thinking Hats (六頂思考帽) and the Mandala
method (曼陀羅法).

The thesis PDF is not in this repository. It lives outside version control, so this
document restates everything needed to run the example rather than linking to a path that
would not resolve for another reader. **The activity types and agent prompts are
transcribed from the thesis's appendix 一 (the lesson plans and the student worksheets),
not from a summary of it.** Where this example adapts rather than reproduces, it says so.

## Why this course fits the platform

The course's classroom activities are worksheet-based, and worksheets are structured text
fields. It needs no drag, rotate, or component-manipulation canvas, which is the
capability gap flagged as the major engineering risk for the *other* creativity paper in
`docs/assessments/nstc-meeting-learning-activities.md`. That is why these units could be
built on today's platform without new rendering infrastructure.

**One adaptation to be clear about up front:** both worksheets ask the student to *draw*
("請將你腦海中的畫面畫下來" for unit 2, "請將…情緒角色畫出來" for unit 4). Text entry is a
substitution, not a property of the source.

## The eight units, and the two modelled here

| Week | Unit | Technique | Modelled here |
|---|---|---|---|
| 1 | 為什麼要學習？ | Six Thinking Hats | no |
| 2 | 時空旅人 | Mandala | **yes**, two activity types |
| 3 | 我的本色 | Green hat + Mandala | no |
| 4 | 情緒播報台 | Six Thinking Hats | **yes**, two activity types |
| 5 | 好家在有你 | Mandala | no |
| 6 | 超能拼圖 I | Mandala | no |
| 7 | 超能拼圖 II | Six Thinking Hats | no |
| 8 | 我的超能力 | Six Thinking Hats | no |

Two units cover both techniques and both rendering paths. Unit 2's grid has a custom
plugin; everything else deliberately has none, demonstrating that an activity type ships
with zero frontend code.

## The four activity types

One type per worksheet section. The split is forced for unit 2: the bundled plugin lays
out a 3x3 grid only for a schema of exactly nine fields, so the worksheet's second section
could not simply become a tenth property. Unit 4 follows the same shape because its two
sections are the lesson plan's 準備活動 and 總結活動 and are scored on different things.

| Key | Name | Renderer | Fields | `min_filled` |
|---|---|---|---|---|
| `mandala-9grid` | 單元二 時空旅人（曼陀羅九宮格） | bundled plugin | 9 | 4 |
| `time-traveler-next-steps` | 單元二 為了與你相遇 | generic form | 1 | 1 |
| `emotion-desk-three-emotions` | 單元四 情緒播報台（三種情緒） | generic form | 6 | 2 |
| `six-hats-emotion-desk` | 單元四 情緒列車（六頂思考帽） | generic form | 6 | 3 |

All four set `validator_kind: in_process` with `filled_count`, `retention_days: null`,
`expose_payload_to_agent: true`, and `echo_includes_content: false`.

### Field order is declared, not implied

Every property carries an `x-order` integer, and the renderer sorts by it ([R30.36]).

This is not decoration. `activity_types.payload_schema` is `jsonb`, and PostgreSQL does
not preserve a JSON object's key order: it stores keys sorted by length, then bytewise.
The order an author writes in the course file is therefore *not* the order a participant
sees. An earlier version of this document claimed "property order drives render order in
the generic form"; that was false as implemented, and the shipped six-hats form rendered
事件, 紅, 藍, 黑, 白, 黃 as a result. `backend/tests/integration/test_activity_schema_key_order.py`
pins the reordering against a real database so the claim cannot quietly become false again.

A schema that declares no `x-order` behaves exactly as before.

### Unit 2: 時空旅人 (Mandala)

The worksheet's grid prints seven themes and leaves exactly one cell blank. The lesson
plan states it directly: 紀錄13歲與30歲的一日生活差異（外貌、工作、家庭、人際關係、休閒
娛樂等），並說明未寫下主題的格子由同學自由發揮.

```
家              工作              具備能力
外貌       30歲的我會有什麼改變呢?    休閒娛樂
想對30歲的自己說…    (自由發揮)        人際關係
```

Property keys are `home`, `work`, `abilities`, `appearance`, `center`, `leisure`,
`message_to_self`, `free`, `relationships`, with `x-order` 1 to 9. The plugin removes
`center` and splices it back into the middle, so that declared order produces the layout
above.

Keys are semantic rather than positional (`cell_1`…`cell_8`) because the agent digest
carries **raw property keys and no titles**, so an agent reading a submission sees
`{"work": "…"}` and can tell 工作 from 人際關係 only if the key says so.

The centre cell is a printed question on the worksheet, not a blank, so `center` is
optional; `min_filled: 4` carries the completeness floor instead. `filled_count` counts
declared properties, and the second worksheet section is its own type, so a facilitator
runs 時空旅人 and then 為了與你相遇 in the same session.

### Unit 4: 情緒播報台 (Six Thinking Hats)

The 情緒列車 table fixes both the column order and the wording:

| Field | Title | Description |
|---|---|---|
| `event` | 事件 | 一件最近或曾經讓自己困擾的事情。 |
| `hat_white` | 白帽 | 中立、客觀、事實 |
| `hat_red` | 紅帽 | 情緒、直覺、預感 |
| `hat_black` | 黑帽 | 悲觀、負面、謹慎 |
| `hat_yellow` | 黃帽 | 樂觀、正面、積極 |
| `hat_blue` | 藍帽 | 指揮、控制、結論 |

An earlier version ordered these 白, 紅, **黃, 黑**, 藍 on the stated grounds that the
thesis fixes no sequence. The worksheet fixes one. (Table 3-5-2's prose lists
黃、黑、白、紅、藍, which agrees with neither; the worksheet is the instrument the students
actually filled in.)

The worksheet's first section (three recurring emotions, what each represents, and the
most recent occasion for it) is `emotion-desk-three-emotions`.

The unit's teaching frame, which the agents also carry: 情緒列車 is 事件 → 想法 → 情緒 →
行為. The event is the locomotive, but what determines the feeling is the thought in
between, which is why the same event produces different feelings in different people.

## The two agent packs

Shipped agents written to accompany the course, in
`backend/contexts/agents/infrastructure/examples/packs/`.

| Pack | Agents | Where they belong |
|---|---|---|
| `creative-thinking-room` | TA 教師代理, SA 學生代理, AA 分析代理 | the class chatroom |
| `creative-thinking-design` | DA 設計代理 | the teacher's own preparation room |

DA is a separate pack precisely so that installing the classroom pack cannot put a
design agent into a student discussion.

### The packs carry the orchestration, not just the prompts

Wake-up is per-agent configuration, not a room setting: `wakeup_config` decides whether an
agent answers every message, waits for a lull, or only speaks when named. That is the part
of this example that cannot be reproduced by copying four prompts.

| Agent | Room role | Wake-up | Effect |
|---|---|---|---|
| TA | `normal` | `every_n_messages` n=1 | leads; responds to every chat message |
| SA | `normal` | `silence_minutes`, `every_n` off | peer catalyst; speaks on a lull or when named |
| AA | `observer` | `silence_minutes` with a bounded observer autostop | silent; writes notes only the room creator sees |
| DA | not for a class room | both triggers off | speaks only when named |

The packs deliberately do **not** set `triggers.call_only`. It reads as "explicit
invocation only" and does suppress autonomous wake-ups, but it is also an A2A
authorization widener: `a2a_scope.evaluate` lets any a2a-enabled agent in the project call
a `call_only` agent with no shared context. Disabling both triggers suppresses wake-ups
identically and grants nothing.

### Three constraints every shipped prompt states

These are asserted by `backend/tests/unit/test_agent_example_packs.py` over the shipped
files, not left to review.

1. **No agent may quote or paraphrase a participant's submission.** Every type sets
   `echo_includes_content: false`, so the room transcript deliberately withholds answer
   text while agents still read a digest of it. An agent reading that aloud reverses the
   privacy decision for the whole class.
2. **AA may not claim to score creativity.** `filled_count` operationalizes **fluency
   (流暢力)** alone. **Flexibility (變通力), originality (獨創力), and elaboration (精進力)
   have no scorer and no delivered rubric.** What AA may reason from is the thesis's own
   five-level (A–E) per-unit competency rubric, which measures the self-development theme
   axis rather than the creativity dimensions, and the prompt requires it to say which it
   is using.
3. **Unit 4 collects negative-affect narratives from 13-year-olds.** The room-facing
   prompts forbid pressing for detail, eliciting further disclosure, and therapeutic
   responses, and require handing back to the teacher when a disclosure exceeds a classroom
   exercise.

### What DA cannot do

DA drafts lesson flows and TA/SA prompt text. **It has no path from its output into an
agent's configuration**; the teacher copies the draft by hand into the agent's settings
page. The prompt requires DA to say so every time it delivers one. A design agent that
appears to configure agents is the obvious misreading.

The lesson plan teaches the hats through Doraemon characters (白=小杉, 黑=大雄, 黃=靜香,
綠=哆啦A夢, 紅=胖虎, 藍=藤子·F·不二雄). Those are copyrighted characters, so the shipped
prompts use the worksheet's plain descriptors instead; a teacher can add the scaffold back
locally.

## Installing

**The activity types** are installed by a platform admin from `/admin/activities`, which
creates them as platform-scoped rows, and then enabled per project by a Project Owner from
`/projects/:projectId/activity-types` ([R30.32], [R30.33]).

The `smap.examples` CLI remains available for installing a **project-scoped copy** instead,
which is the path for an air-gapped operator:

```bash
cd backend
python -m smap.examples creative-thinking-course \
  --project-id <project-uuid> \
  --owner-user-id <owner-uuid>
```

Idempotent by key. Like every `smap` CLI it trusts its operator: it calls the activities
facade directly, bypassing the HTTP route's authority check, and `--owner-user-id` is
recorded as the audit actor and **authorizes nothing**. It registers types into a project
that already exists; it creates no orgs, projects, rooms, or users.

**The agent packs** are installed by anyone who can create agents in the project, from the
project's Agents page. Installing asks for a key group and creates the pack's agents plus
one agent group: **no chatroom, no room binding, no activity started**.

A pack names a preferred provider but never a key. If the chosen key group cannot serve
that provider, a provider it does carry is used instead and the result says which; if it
carries none at all, the install is refused rather than partially applied. That
substitution is not free: on a model that rejects sampling controls the packs' shipped
temperatures stop applying. See
[the temperature entry under Limitations](#limitations) before installing against a key
group that does not carry Claude.

Install is idempotent **by agent name within the project**, which is weaker than the
course installer's key idempotency: `agents` has no `key` column and no name uniqueness.
Renaming an installed agent and re-installing therefore produces a second copy.

### Upgrading an environment installed before this correction

The activity type corrections above reach only environments that have not installed the
course yet. `payload_schema` is outside the set of fields a platform admin may edit, and
re-syncing an installed example is deliberately not implemented (OQ-1 of
`docs/tasks/2026-08-09-platform-example-activity-types/spec.md`).

**Re-installing without deleting first is the trap, because it half-works.** Install is
idempotent by key and never updates an existing row, so on a deployment that already has
the course it creates the two new types (`time-traveler-next-steps`,
`emotion-desk-three-emotions`) and leaves the two old ones untouched. The result reports
success and looks installed, but `mandala-9grid` still renders 格 1 to 格 8 while the agent
packs' prompts describe 家 / 工作 / 具備能力, `six-hats-emotion-desk` still runs the
transposed 黃/黑 order, and neither old row carries `x-order` at all, so the ordering
mechanism is inert for exactly the two units it was introduced to fix.

To upgrade properly: **delete `mandala-9grid` and `six-hats-emotion-desk` from
`/admin/activities` first**, then install the course again. Deleting a platform type ends
its active activations across every tenant, so do it between classes.

## Running a session

1. **Platform admin** installs the course once; **Project Owner** enables the types for the
   project and installs the agent packs.
2. **Facilitator** (the room creator) creates a chatroom, binds TA and SA as normal
   agents and AA as an observer, then opens the Activity tab and starts one type. A room
   holds **at most one active activity at a time**.
3. **Participants** join the active activity, which opens a per-subject session, and
   submit. Each participant gets their own monotonic attempt counter.
4. **Each submission** is validated against the payload schema, then scored server-side by
   `filled_count`. The verdict is authoritative and computed on the server; nothing the
   client sends can influence it.
5. **The room transcript** gets a system-stamped echo that a submission happened, carrying
   no answer text.
6. **Room agents** read a digest of recent structured activity, which is what lets TA
   respond to what the class actually wrote and AA observe across submissions. Note what
   a submission does and does not do to the agents: it **re-arms the silence clock**, so
   SA does not mistake a class quietly filling in a worksheet for a lull, but it does
   **not** itself wake anyone. An agent sees the submissions on its next turn, whether
   that turn comes from a chat message or from a genuine lull. Submissions are
   deliberately not counted by `every_n_messages`: TA runs at `n=1`, so counting them
   would mean one TA turn per student per submission, on your own provider key.
7. **Facilitator** ends the activity, then starts the unit's second type.

## What `filled_count` does and does not measure

It counts how many of the type's **declared** schema properties carry an answer, and marks
the submission valid once that count reaches `min_filled`, reporting the count as
`sub_scores.filled`.

Only declared properties count. JSON Schema permits extra properties unless a schema
forbids them, so a participant calling the API directly could otherwise pad a submission
with keys the activity never asked for and clear the threshold.

That count is a direct operational definition of **fluency (流暢力)**, one of the four
creativity dimensions the source study measured. It says nothing whatsoever about
**flexibility (變通力)**, **originality (獨創力)**, or **elaboration (精進力)**, which are
judgements about answer *quality*.

A threshold of `0` is legal and turns the activity collect-only.

What counts as filled is decided by `_is_filled` in
`backend/app/plugins/activity_validators.py`, which distinguishes four cases:

- `null` and `false` do **not** count.
- A **string** counts only if it is non-empty after whitespace is stripped.
- An empty **array** or **object** does not count; a non-empty one does.
- Every other value counts, including the number `0`. A numeric field is only present in
  the payload when the participant typed something.

The `false` case is the one to know before authoring your own schema. `filled_count` is
meant for text-response schemas: the generic form submits a boolean for every declared
boolean property whether or not the participant touched it, so an unticked box cannot be
told apart from an untouched one, and counting it would make the metric report the
schema's size rather than the participant's effort. A threshold set on the assumption that
declaring a checkbox is enough will instead require that many boxes actually ticked. See
`_is_filled`'s docstring for the reasoning and the cost it accepts; it is the authority,
and this paragraph is a pointer to it rather than a second copy.

All four types here are all-string, so none of this affects them.

## Limitations

Stated plainly, because an example that oversells the platform is worse than no example.

- **One active activity per room.** Each modelled unit is two activity types, so a
  45-minute session dispatches two in sequence. There is no notion of a course schedule.
- **Only fluency is scored automatically.** The other three creativity dimensions need a
  rubric, and that rubric is an open domain-expert deliverable; see
  `docs/assessments/nstc-meeting-learning-activities.md`. Nothing here substitutes for it.
  The thesis's per-unit A–E rubric is a *competency* rubric for the self-development theme
  axis and is not a substitute either.
- **This is not the study's assessment battery.** The source used external paper
  instruments (新編創造思考測驗, 威廉斯創造性傾向量表, 兒童自我概念量表) for pre- and
  post-tests. None of them are modelled here.
- **Six of the eight units are not modelled.**
- **Text entry replaces drawing** in both worksheets.
- **A pack's `temperature` applies only where the resolved model accepts sampling
  controls.** The packs ship a deliberate spread (AA 0.2, TA 0.7, SA 0.9), and the install
  fallback described under [Installing](#installing) can put them on a provider that
  discards it. OpenAI is that case today: its default chat model is a reasoning model, and
  reasoning models accept only the default temperature, so the adapter drops `temperature`
  and `top_p` rather than send a value the provider would answer with a 400
  (`backend/contexts/keys/infrastructure/adapters/openai.py:155-160`). Overriding the model
  does not escape it, because every OpenAI preset in the catalog is a reasoning model. An
  OpenAI-only project therefore gets the packs' prompts, roles, tools and wake-up
  configuration, but the provider's default sampling instead of the spread. Gemini forwards
  temperature on every model, and so does Claude's current default `claude-sonnet-4-6`;
  note the rule is per resolved model rather than per provider, and the newer Claude
  families reject sampling controls too
  (`backend/contexts/keys/infrastructure/adapters/anthropic.py:35`).
- **AA's observations are not reproducible run to run.** The packs pin no `seed`.
- **DA cannot write its drafts into an agent.**
- **One plugin per activity-type key.** The plugin registry is a single global map keyed by
  type key. Any project whose type is named `mandala-9grid` inherits the grid renderer.
  A key does not name exactly one type: since platform scope exists, a project's usable set
  can hold both its own `mandala-9grid` and an opted-in platform `mandala-9grid` ([R30.02]).
  The plugin binds to the key, so it renders for both, and so does any workflow reactive
  rule matching on `activity_type_key` alone. Both actions that create the collision warn
  the Project Owner, and `scope` is what distinguishes the two rows everywhere they are
  listed together.
- **Retention is unset.** Submissions follow the room's normal purge. A real study should
  set `retention_days` on the type to pin the record for its data-retention period.

## Privacy and ethics

With `expose_payload_to_agent: true`, student-written text enters the agent context block
and is therefore **sent to whichever LLM provider the project's API key targets**.

The source study's participants were 13-year-olds, and unit 4 collects negative-affect
narratives about things that troubled them. Anyone deploying this with real students needs
informed consent, an IRB position, and a decision about provider data handling.

The three prompt constraints above are asserted as text in the shipped files. Whether an
agent *obeys* them at runtime is not something a test can establish. **A classroom dry-run
against a real provider is required before any use with students.**

If a study needs answers kept out of agent prompts entirely, set
`expose_payload_to_agent: false` on the type. Submissions are still recorded
authoritatively for later analysis; agents simply cannot read them.

## Where the pieces live

| Piece | Path |
|---|---|
| `filled_count` validator | `backend/app/plugins/activity_validators.py` |
| **This course's activity content** | `backend/contexts/activities/infrastructure/examples/courses/creative-thinking.json` |
| Course loader + validation | `backend/contexts/activities/infrastructure/examples/catalogue.py` |
| **This course's agent packs** | `backend/contexts/agents/infrastructure/examples/packs/` |
| Pack loader + validation | `backend/contexts/agents/infrastructure/examples/catalogue.py` |
| Pack install service | `backend/contexts/agents/application/example_service.py` |
| Seeder CLI (project-scoped copy) | `backend/smap/examples/__main__.py` |
| Mandala grid plugin | `frontend/src/slices/activities/plugins/mandala9grid/` |
| Generic schema form | `frontend/src/slices/activities/components/SchemaForm.vue` |
| Pack install dialog | `frontend/src/slices/agents/components/AgentPackInstallDialog.vue` |
| Task dossiers | `docs/tasks/2026-08-08-creative-thinking-course-example/`, `docs/tasks/2026-08-08-activity-example-catalogue/`, `docs/tasks/2026-08-09-platform-example-activity-types/`, `docs/tasks/2026-08-13-creative-thinking-example-agents/` |

## Adding another course, or another pack

Both are data files, and both loaders validate on read.

A **course** is a JSON document under `courses/`, named for its `course_key`. Every field
is required, including the visibility flags: defaulting `expose_payload_to_agent` would be
the wrong way to decide whether student text reaches an LLM provider. The `payload_schema`
must be a valid JSON Schema declaring at least one property, and a `filled_count`
`min_filled` may not exceed the number of declared properties.

A **pack** is a JSON document under `packs/`, named for its `pack_key`, declaring the
course it accompanies and, per agent, the activity type keys it is written against. Every
field is required, including `room_role`: defaulting it would quietly decide whether an
agent speaks in front of a class or watches in silence.

The pack loader does not resolve `for_course` or `binds_activity_types`, because doing so
would make the agents context reach into the activities context's infrastructure. That
cross-check is `backend/tests/unit/test_agent_example_packs.py`, which fails if a pack
names a course or an activity type that does not exist.
