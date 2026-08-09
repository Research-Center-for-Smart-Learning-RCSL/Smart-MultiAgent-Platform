# Worked example: a creative-thinking course on the activities platform

A two-unit example showing how a published curriculum maps onto SMAP's structured
activities. It exists to answer one question — *can the custom-activity feature carry a
real lesson plan end to end?* — not to serve as a research instrument. Read
[Limitations](#limitations) before using it for anything else.

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
would not resolve for another reader.

## Why this course fits the platform as-is

The entire course is text-based — worksheets, discussion sheets, grid fill-ins. It needs
no drag, rotate, or component-manipulation canvas, which is the capability gap flagged as
the major engineering risk for the *other* creativity paper in
`docs/assessments/nstc-meeting-learning-activities.md`. That is why these units could be
built on today's platform without new rendering infrastructure.

## The eight units, and the two modelled here

| Week | Unit | Technique | Modelled here |
|---|---|---|---|
| 1 | 為什麼要學習？ | Six Thinking Hats | no |
| 2 | 時空旅人 | Mandala | **yes** — `mandala-9grid` |
| 3 | 我的本色 | Green hat + Mandala | no |
| 4 | 情緒播報台 | Six Thinking Hats | **yes** — `six-hats-emotion-desk` |
| 5 | 好家在有你 | Mandala | no |
| 6 | 超能拼圖 I | Mandala | no |
| 7 | 超能拼圖 II | Six Thinking Hats | no |
| 8 | 我的超能力 | Six Thinking Hats | no |

Two units cover both techniques and both rendering paths. Unit 2 has a custom plugin;
unit 4 deliberately has none, demonstrating that an activity type ships with zero frontend
code.

## Unit 2 — 時空旅人 (Mandala)

| Field | Value |
|---|---|
| `key` | `mandala-9grid` |
| `name` | 單元二 時空旅人 |
| `validator_kind` | `in_process` |
| `validator_config` | `{"validator_id": "filled_count", "min_filled": 4}` |
| `retention_days` | `null` |
| `expose_payload_to_agent` | `true` |
| `echo_includes_content` | `false` |
| Renderer | the bundled `mandala-9grid` plugin (3x3 grid) |

```json
{
  "type": "object",
  "properties": {
    "center": {
      "type": "string",
      "title": "中心主題：30 歲的我",
      "description": "用一句話寫下你想像中 30 歲的自己。"
    },
    "cell_1": { "type": "string", "title": "格 1" },
    "cell_2": { "type": "string", "title": "格 2" },
    "cell_3": { "type": "string", "title": "格 3" },
    "cell_4": { "type": "string", "title": "格 4" },
    "cell_5": { "type": "string", "title": "格 5" },
    "cell_6": { "type": "string", "title": "格 6" },
    "cell_7": { "type": "string", "title": "格 7" },
    "cell_8": { "type": "string", "title": "格 8" }
  },
  "required": ["center"]
}
```

`min_filled: 4` means the centre plus at least three associations.

**The eight ring cells are unlabelled on purpose.** The thesis's radial-mandala figures
(放射型曼陀羅) are free-association layouts; pre-theming the cells would constrain exactly
the divergent thinking the unit sets out to elicit. If the collaborating educator prefers
themed cells, it is a one-file data edit in
`backend/smap/examples/courses/creative-thinking.json` — no code change, no Python.

## Unit 4 — 情緒播報台 (Six Thinking Hats)

| Field | Value |
|---|---|
| `key` | `six-hats-emotion-desk` |
| `name` | 單元四 情緒播報台 |
| `validator_kind` | `in_process` |
| `validator_config` | `{"validator_id": "filled_count", "min_filled": 3}` |
| `retention_days` | `null` |
| `expose_payload_to_agent` | `true` |
| `echo_includes_content` | `false` |
| Renderer | the generic schema form (no plugin) |

```json
{
  "type": "object",
  "properties": {
    "event": {
      "type": "string",
      "title": "困擾我的事件",
      "description": "最近或曾經讓自己困擾的一件事。"
    },
    "hat_white": { "type": "string", "title": "白帽：事實", "description": "只寫客觀發生了什麼，不加評價。" },
    "hat_red": { "type": "string", "title": "紅帽：感受", "description": "當下的情緒，不需要說明理由。" },
    "hat_yellow": { "type": "string", "title": "黃帽：好處", "description": "這件事有沒有任何好的一面？" },
    "hat_black": { "type": "string", "title": "黑帽：風險", "description": "可能的壞處或風險是什麼？" },
    "hat_blue": { "type": "string", "title": "藍帽：總結", "description": "整理以上，你現在的想法是什麼？" }
  },
  "required": ["event"]
}
```

Property order drives render order in the generic form. The thesis names five hats
(黃、黑、白、紅、藍) without fixing a sequence, so this uses de Bono's standard review
order — facts, feelings, upside, risk, summary. Adapting it is a data edit.

## Seeding

```bash
cd backend
python -m smap.examples creative-thinking-course \
  --project-id <project-uuid> \
  --owner-user-id <owner-uuid>
```

Idempotent: a type whose `key` already exists in the project is reported as
already-present and left untouched, so re-running after a partial failure is safe.

`--course` selects which file under `backend/smap/examples/courses/` to seed. It
defaults to `creative-thinking`, so the command above needs no extra flag.

Two things to be clear about:

- The seeder registers types into a project that **already exists**. It does not create
  orgs, projects, rooms, or users.
- Like every `smap` CLI it trusts its operator. It calls the activities facade directly,
  bypassing the HTTP route's Project Owner check — anyone who can run it already holds DB
  credentials. `--owner-user-id` is recorded as the audit actor and **authorizes nothing**.

Types can equally be created by hand through the owner-only management page at
`/projects/:projectId/activity-types`, using the schema JSON above.

## Running a session

1. **Owner** seeds (or hand-authors) the two types once per project.
2. **Facilitator** — the room creator — opens the Activity tab in a chatroom and starts
   one type. A room holds **at most one active activity at a time**; starting a second
   while one is live is rejected until the first is ended.
3. **Participants** join the active activity, which opens a per-subject session, and
   submit. Each participant gets their own monotonic attempt counter, so repeated attempts
   are individually recorded rather than overwriting.
4. **Each submission** is validated against the payload schema, then scored server-side by
   `filled_count`. The verdict (`is_valid`, `sub_scores.filled`) is authoritative and
   computed on the server; nothing the client sends can influence it.
5. **The room transcript** gets a system-stamped echo that a submission happened. With
   `echo_includes_content: false` the echo carries no answer text, so one student's writing
   is not pasted in front of the whole room.
6. **Room agents** (teacher / peer / observer personas) read a digest of recent structured
   activity because `expose_payload_to_agent` is `true`. That is what lets an agent respond
   to what a student actually wrote.
7. **Facilitator** ends the activity. Ending blocks further submissions; it does not
   force-close participants' open sessions.

To run week 2 and then week 4 in the same room, end the first activity before starting the
second.

## What `filled_count` does and does not measure

`filled_count` counts how many of the type's **declared** schema properties carry an answer,
and marks the submission valid once that count reaches `min_filled`. It reports the count as
`sub_scores.filled`.

Only declared properties count. JSON Schema permits extra properties unless a schema
forbids them, so a participant calling the API directly could otherwise pad a submission
with keys the activity never asked for and clear the threshold — and inflate the recorded
fluency count — without answering.

That count is a direct operational definition of **fluency (流暢力)** — one of the four
creativity dimensions the source study measured. It says nothing whatsoever about
**flexibility (變通力)**, **originality (獨創力)**, or **elaboration (精進力)**, which are
judgements about answer *quality*.

A threshold of `0` is legal and turns the activity collect-only: every schema-valid
submission is valid. That is the supported way to run an open-ended activity that has no
answer key.

One caveat: `filled_count` is meant for text-response schemas. Booleans always count as
filled, because the generic form submits a value for every declared boolean property
whether or not the participant touched it. Both units here are all-string, so this does not
affect them.

## Limitations

Stated plainly, because an example that oversells the platform is worse than no example.

- **One active activity per room.** An eight-week course means switching the active type
  week by week, or running separate rooms. There is no notion of a course schedule.
- **Only fluency is scored automatically.** The other three creativity dimensions need a
  rubric, and that rubric is an open domain-expert deliverable — see item C-1 in
  `docs/assessments/nstc-meeting-learning-activities.md`. Nothing here substitutes for it.
- **This is not the study's assessment battery.** The source used external paper
  instruments (新編創造思考測驗, 威廉斯創造性傾向量表, 兒童自我概念量表) for pre- and
  post-tests. None of them are modelled here.
- **Six of the eight units are not modelled.**
- **Cell labels and hat ordering are adaptations**, not verbatim thesis content.
- **One plugin per activity-type key.** The plugin registry is a single global map keyed by
  type key, while type keys are unique only per project. Two consequences: any project that
  names a type `mandala-9grid` inherits the grid renderer, and a single project cannot give
  the grid to more than one of its Mandala units. This affects presentation only — storage
  and scoring are server-side.
- **Retention is unset.** Submissions follow the room's normal purge. A real study should
  set `retention_days` on the type to pin the record for its data-retention period.

## Privacy and ethics

With `expose_payload_to_agent: true`, student-written text enters the agent context block
and is therefore **sent to whichever LLM provider the project's API key targets**.

The source study's participants were 13-year-olds, and unit 4 collects negative-affect
narratives about things that troubled them. Anyone deploying this with real students needs
informed consent, an IRB position, and a decision about provider data handling — the open
items under section G of `docs/assessments/nstc-meeting-learning-activities.md`.

If a study needs answers kept out of agent prompts entirely, set
`expose_payload_to_agent: false` on the type. Submissions are still recorded
authoritatively for later analysis; agents simply cannot read them.

## Where the pieces live

| Piece | Path |
|---|---|
| `filled_count` validator | `backend/app/plugins/activity_validators.py` |
| Seeder CLI | `backend/smap/examples/__main__.py` |
| **This course's content** | `backend/smap/examples/courses/creative-thinking.json` |
| Course loader + validation | `backend/smap/examples/_catalogue.py` |
| Seeding engine (course-agnostic) | `backend/smap/examples/_seeding.py` |
| Mandala grid plugin | `frontend/src/slices/activities/plugins/mandala9grid/` |
| Generic schema form | `frontend/src/slices/activities/components/SchemaForm.vue` |
| Type management page | `frontend/src/slices/activities/views/ActivityTypesView.vue` |
| Task dossier | `docs/tasks/2026-08-08-creative-thinking-course-example/spec.md` |
| Catalogue refactor dossier | `docs/tasks/2026-08-08-activity-example-catalogue/spec.md` |

## Adding another course

A course is a data file. Drop a JSON document into
`backend/smap/examples/courses/`, named for its `course_key`, and seed it with
`--course <key>`. No Python changes, and the loader validates it on read: every
field is required (including the visibility flags — defaulting
`expose_payload_to_agent` would be the wrong way to decide whether student text
reaches an LLM provider), the `payload_schema` must be a valid JSON Schema
declaring at least one property, and a `filled_count` `min_filled` may not exceed
the number of declared properties, since the scorer counts declared properties
and a higher threshold would ship an activity nobody can pass.

Use `creative-thinking.json` as the template.
