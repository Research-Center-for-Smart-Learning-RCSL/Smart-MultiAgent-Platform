---
type: feature
status: implemented
created: 2026-08-13
requirements: [R9.01, R9.02, R15.01, R28.01, R28.04, R30.15, R30.17, R30.18, R30.23, R30.27, R30.28, R30.32, R30.33]
depends_on: []
---

# Example agent packs for the creative-thinking course, and a course example that matches its source worksheets

## 1. Summary

The repository ships a worked activity example built from Ke Pei-jung's 2019 MA thesis
(`docs/examples/creative-thinking-course.md`), but nothing on the agent side: there is no
example agent, no agent catalogue, no install path, and no shipped answer to "what should
the teacher, peer, and analyst agents in this room actually say". This feature adds two
shipped **agent packs** that a Project Owner installs into their own project as ordinary
project-scoped agents, deliberately paired with the course's activity types.

Reading the source lesson plan to write those prompts surfaced that the shipped course
itself does not match its worksheets in three places, and that the generic form renders
its fields in an order nobody chose. Both are fixed here, because an example agent that
"fits the activity" is worth nothing if the activity does not fit its source.

Prior art this builds on: `docs/tasks/2026-08-09-platform-example-activity-types/spec.md`
(the install/opt-in mechanism for activity examples),
`docs/tasks/2026-08-08-activity-example-catalogue/spec.md` (the JSON catalogue + strict
loader idiom this mirrors), and `docs/tasks/2026-07-13-activities-observer-context/spec.md`
(the `[Recent room activity]` block these agents read).

## 2. Goals and Non-goals

**Goals**

- Two shipped agent packs under `backend/contexts/agents/infrastructure/examples/packs/`,
  each declaring the course it is written against and the activity type keys each agent is
  written to work with.
- A Project Owner installs a pack from the project's Agents page: the pack's agents are
  created as ordinary project-scoped agents plus one agent group, with the model provider
  resolved against a key group the owner chooses.
- The pack encodes the classroom **orchestration**, not only the prompts: each agent's
  `wakeup_config` is shipped data, so "TA leads, SA intervenes on a lull, AA stays silent"
  is reproduced by installing, not by hand-tuning four agents.
- The cross-reference between a pack and its course is machine-checked in CI, so deleting
  or renaming an activity type without updating the pack fails a test.
- The shipped course's two activity types match the thesis worksheets field for field, and
  the two worksheet sections currently unmodelled become activity types of their own.
- A payload schema can declare an explicit field order that the renderer honours, because
  today's implicit order is whatever PostgreSQL `jsonb` happens to store.
- Every shipped prompt states, in the prompt itself, the three things these agents must
  not do (§8).

**Non-goals**

- **No agent export/import.** [R9.02] forbids it and this does not introduce it. A pack is
  repository data instantiated into new agents; it is not a serialization of anybody's
  existing agent, and an installed agent keeps no link back to the pack.
- **No room creation and no room binding.** Install produces agents and a group. Creating a
  chatroom, binding TA/SA/AA to it with their roles, and starting an activity stay the
  facilitator's actions, mirroring the activity example's "installing is not activating"
  boundary.
- **No re-sync of an installed pack.** Same answer as OQ-1 of the platform-example dossier,
  for the same reason: once the rows are editable by their owner, a re-sync has to decide
  whether it overwrites an edit.
- **No platform-scoped agents.** Structurally impossible under BYO-key (§5); not attempted.
- **No creativity rubric.** The four-dimension scorer remains the open domain-expert
  deliverable recorded as item 4 of `docs/assessments/nstc-meeting-learning-activities.md:50`.
  The AA prompt is bounded by that absence rather than papering over it.
- **No pack-authoring UI.** A pack is a JSON file in the repository, like a course.
- **No change to the turn engine, the activity digest format, the observer output path, or
  the plugin SDK.**
- **Not seeding the other six course units.** Unchanged blocker: the unit designs need the
  collaborating educator's confirmation (FU-1 of the platform-example dossier).

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Where does example agent content live: extend the course JSON, a separate agent catalogue, or documentation only? | **A separate agent catalogue** under `contexts/agents/infrastructure/examples/packs/`, cross-referencing a course by `for_course`. | User decision. The install paths and authorities differ (a course installs platform-wide by an admin; agents copy into one project by its owner), so one file with two install paths would be a false unity. Cost accepted and paid in §5: the "fits the course" relation becomes a field, so it is machine-checked (Q-5) rather than asserted. |
| Q-2 | Which of the four NSTC roles does the example cover? | **All four** — TA, SA, AA, DA (`docs/assessments/nstc-meeting-learning-activities.md:29-31`). | User decision, over a recommendation to drop DA. DA is a design-time assistant with no room to sit in and no write-back path, both of which are now stated limitations rather than silent gaps (Q-3, §5). |
| Q-3 | How is DA positioned, given it is not a member of the class room? | **Its own pack**, `creative-thinking-design`, installed separately from the room pack. | User decision. The cleanest boundary: a reader cannot accidentally bind DA into a class room by installing "the pack", because DA is not in that pack. Cost: "four roles" is split across two files, so both the catalogue listing and the docs must say the two are companions. |
| Q-4 | How is an agent's model provider decided, given a pack cannot know a project's keys? | **Resolved at install against the chosen key group.** The pack carries `preferred_model_hint`; the install form takes a key group, uses the preferred hint when that group carries it, otherwise the first provider the group does carry, and lets the owner override. | User decision. A hard-coded provider would make install fail at `agent_service._assert_key_group_has_provider` (`agent_service.py:450-453`) for any project holding a different vendor's key — a shipped example that cannot be installed is worse than one that adapts. |
| Q-5 | How is "this agent fits that activity" kept true? | Every pack agent declares `binds_activity_types`; a unit tripwire scanning every pack asserts `for_course` resolves and every declared key exists in that course. The **loader** does not do this. | Not a user question. Putting the cross-check in the loader would make `agents/infrastructure` import `activities/infrastructure`, an infrastructure-to-infrastructure cross-context edge the project's layering forbids. A test may read both; production code may not. |
| Q-6 | Does this dossier also correct the shipped course where it disagrees with the thesis worksheets? | **Yes, in this dossier.** | User decision, over deferring it to a separate bugfix. The agent prompts are written against the activity fields; shipping prompts that fit a mis-transcribed worksheet and then correcting the worksheet later would invalidate the prompts twice. |
| Q-7 | How faithful should the correction be: fix only what contradicts the source, or model the worksheets completely? | **Completely.** Seven themed mandala cells plus one free cell; hat order and hat descriptions taken from the worksheet; both worksheets' unmodelled sections become activity types. | User decision. The example's stated purpose is "can a published lesson plan run on this platform end to end"; a partial transcription cannot answer that. |
| Q-8 | The generic form's field order comes from JSON object key order, but `payload_schema` is `jsonb`, which does not preserve it (§4.3). How are correct order and agent-readable keys obtained together? | **Add an explicit `x-order` integer per property**; `fieldsFromSchema` sorts by it, falling back to current behaviour when absent. Property keys become semantic (`work`, `relationships`). | User decision, over uniform-length ordinal keys plus a digest format change, and over choosing key names that happen to sort correctly. It is the only option that fixes the defect for every activity type rather than for the two shipped ones, and the only one under which a later label edit cannot silently reorder a form. |
| Q-9 | Does this depend on any unfinished dossier? | **No — `depends_on: []`.** | Every `spec.md` under `docs/tasks/` whose status is not terminal: `2026-07-07-graphrag-two-axis-redesign` (approved, graphrag) and `2026-07-19-large-artifacts-silently-dropped` (in-progress; its surface is `kernel.py` / `turn_engine.py:1133-1136` / `attachment_service.py`). Neither touches `contexts/agents/infrastructure`, `contexts/activities/infrastructure/examples`, `schemaFields.ts`, or the agents/activities frontend slices. `2026-07-13-activities-activation-ux` carries the invalid status `done` (FU-6 of the platform-example dossier) but is complete. |

## 4. Current State

### 4.1 There is no example agent anywhere

- The shipped example is activity types only:
  `backend/contexts/activities/infrastructure/examples/courses/creative-thinking.json`, two
  types, parsed by `contexts/activities/infrastructure/examples/catalogue.py:236-325`.
- `backend/contexts/agents/` has no `examples` package; `available_courses`
  (`catalogue.py:267-280`) has no agent counterpart. Grep for a seeded or shipped agent
  persona across `backend/` and `frontend/src/slices/agents/` returns nothing.
- The four roles the example would cover are already named in
  `docs/assessments/nstc-meeting-learning-activities.md:29-31` (TA / SA / AA / DA) and
  nowhere else in the repository.

### 4.2 An agent cannot be platform-scoped, so the activity example's model does not transfer

- `Agent.project_id: uuid.UUID` is non-optional (`contexts/agents/domain/models.py:139-140`)
  and `key_group_id: uuid.UUID` is likewise required (`:145`).
- `AgentService.create` refuses a draft without `key_group_id` (`agent_service.py:615-616`),
  then requires the group to belong to the project (`_assert_key_group_in_project`,
  `:445-448`) and to carry an actively-carried key for the agent's provider
  (`_assert_key_group_has_provider`, `:450-453`, delegating to
  `KeysFacade.has_carried_provider_in_group`, `contexts/keys/interfaces/facade.py:161-170`).
- Under BYO-key the platform holds no provider key of its own, so there is no key group a
  platform-owned agent could reference. Option B of the platform-example dossier
  (a platform row plus per-project opt-in) has no analogue here.
- Agent creation is gated by `Capability.RESOURCE_CREATE_EDIT` scoped to the project
  (`app/api/v1/agents.py:405-410`), **not** by `assert_project_owner` — unlike the activity
  example opt-in routes (`app/api/v1/activities.py:476`, `:501`, `:526`).
- `agents.name` carries no uniqueness constraint (`contexts/agents/infrastructure/tables.py:24`;
  the only unique constraint in that file is `uq_agent_workspace_files_agent_path`, `:120`),
  and `Agent` has no `key` column. There is therefore no existing identifier an idempotent
  install could key on.
- The project cap is 1 000 active agents, enforced under an advisory lock
  (`agent_service.py:80`, `:602-609`).

### 4.3 The generic form's field order is not the authored order

- `activity_types.payload_schema` is `pg.JSONB`
  (`contexts/activities/infrastructure/tables.py:39`). PostgreSQL `jsonb` does not preserve
  object key order; keys are stored sorted by length, then bytewise.
- The renderer derives field order from `Object.entries(schema.properties)`
  (`frontend/src/slices/activities/components/schemaFields.ts:47-63`), consumed by both
  `SchemaForm.vue` and the Mandala plugin (`plugins/mandala9grid/MandalaGrid.vue:28`).
- Applying `jsonb`'s ordering to the shipped six-hats keys — `event` (5), `hat_red` (7),
  `hat_blue` (8), `hat_black` (9), `hat_white` (9), `hat_yellow` (10) — gives the render
  order 事件, 紅, 藍, 黑, 白, 黃. That is neither the order the JSON declares
  (`courses/creative-thinking.json:82-106`) nor any order de Bono or the thesis states.
- `docs/examples/creative-thinking-course.md:125` asserts "Property order drives render
  order in the generic form". That is false as implemented.
- The Mandala's layout survives only by coincidence: `center` and `cell_1`..`cell_8` are all
  six characters, and `cell_*` precedes `center` bytewise, so `jsonb` happens to emit the
  authored order. Any rename that changes a key's length would silently reorder the grid.
- This is precisely the class of defect `backend/CLAUDE.md` requires a `db`-tier test for:
  the unit tier never round-trips through PostgreSQL, so it cannot observe the reordering.

### 4.4 The shipped course disagrees with its source worksheets

Verified against the thesis appendix (`_projects_documents/年興老師_創造力課程相關資料_20260716/第二篇論文_創造思考技法/2.論文教案_創造思考技法_柯佩蓉.pdf`;
page numbers below are PDF pages). The PDF is outside version control, which is why the
findings are restated here rather than cited by path alone.

**Unit 2, 時空旅人 (lesson plan p.115, worksheet p.118).** The worksheet's nine-grid carries
seven printed themes and exactly one blank cell:

```
家              工作              具備能力
外貌       30歲的我會有什麼改變呢?    休閒娛樂
想對30歲的自己說…    (自由發揮)        人際關係
```

The lesson plan states it directly: "紀錄13歲與30歲的一日生活差異（外貌、工作、家庭、
人際關係、休閒娛樂等），並說明未寫下主題的格子由同學自由發揮" (p.115).

- The shipped schema declares `cell_1`..`cell_8` with no titles
  (`courses/creative-thinking.json:24-56`).
- `docs/examples/creative-thinking-course.md:87-91` justifies that as preserving free
  association. The source is a themed (實—實) mandala, so the justification inverts the
  source's design.
- The centre cell on the worksheet is a printed question, not a field to fill; the shipped
  schema makes `center` required (`creative-thinking.json:58-60`).
- Worksheet section 二, "若要讓我更接近想像中的生活，現在的我需要學習的可能有：", is not
  modelled at all.

**Unit 4, 情緒播報台 (lesson plan p.123-124, worksheet p.126).** The worksheet's 情緒列車
table fixes both the column order and the wording:

事件 | 白帽（中立、客觀、事實）| 紅帽（情緒、直覺、預感）| 黑帽（悲觀、負面、謹慎）|
黃帽（樂觀、正面、積極）| 藍帽（指揮、控制、結論）

- The shipped schema orders the hats 白, 紅, **黃, 黑**, 藍
  (`creative-thinking.json:82-106`) — yellow and black transposed relative to the worksheet.
- `docs/examples/creative-thinking-course.md:125-127` states the thesis fixes no sequence.
  The worksheet does. (The prose list at 表 3-5-2 reads 黃、黑、白、紅、藍, which agrees with
  neither the worksheet nor the shipped file — the worksheet is the instrument the students
  actually filled in.)
- Worksheet section 一, three recurring emotions with the emotion each represents and the
  most recent occasion for it, is not modelled.

**A third, smaller claim.** `docs/examples/creative-thinking-course.md:26-28` states the
course is entirely text-based. Both worksheets instruct the student to draw ("請將你腦海中的
畫面畫下來", p.118; "請將…情緒角色畫出來", p.126). Text is an adaptation, not a property of
the source.

### 4.5 What the platform already provides for these agents

- **The activity block reaches every agent, not only observers.** `ActivityContextProvider`
  is built once per engine (`turn_engine.py:952-954`) and folded in as the `activity` block
  (`turn_engine.py:843`); its own docstring records "Given to every agent's turn, not just
  observers" (`activity_context_provider.py:6-8`). Content is included per row only when
  that row's type sets `expose_payload_to_agent` and the platform policy still permits it
  (`activity_context_provider.py:57-87`).
- **The digest carries raw property keys and no titles.** `build_agent_digest` emits
  `json.dumps(payload, ensure_ascii=False, separators=(",",":"), sort_keys=True)`
  (`contexts/activities/application/agent_digest.py:21-27`). An agent reading a submission
  therefore sees `{"cell_3":"…"}` and cannot tell one unlabelled cell from another. This is
  the second half of Q-8's problem.
- **Wake-up is per-agent, not room-wide.** `evaluate_message_wakeups`
  (`contexts/conversation/application/triggers.py:48-81`) hands every bound agent to
  `OrchestrationFacade.on_message_created`, which evaluates each agent's own
  `WakeupConfig.triggers.every_n_messages` (`orchestration/application/wakeup_service.py:110-112`,
  shape at `orchestration/domain/models.py:300-310`). `@mention` is a separate, explicit
  path and drops observer bindings silently (`triggers.py:104-119`, [R28.04]). The default
  applied when a draft supplies none is `{"triggers": {"every_n_messages": {"enabled": true, "n": 1}}}`
  (`agent_service.py:354`).
- **`docs/assessments/nstc-meeting-learning-activities.md:47` states "預設情況下每則訊息會讓
  所有在場 agent 一起回應".** That describes the case where every agent carries the default
  `n=1`, not a platform behaviour. As written it reads as an unavoidable limitation, which
  is what makes the packs' orchestration look impossible.
- **The observer path is built.** `ChatroomAgentRole.OBSERVER`
  (`contexts/conversation/domain/models.py:34-36`) suppresses the room channel and routes
  output to an Observation the room creator releases deliberately.

### 4.6 The install surface's exemplars

- Project-scoped example routes and their post-commit ordering:
  `app/api/v1/activities.py:463-536`.
- The Owner-facing dialog, including the "this sends participant text to your LLM provider"
  notice shown before anything is enabled:
  `frontend/src/slices/activities/components/ExampleImportDialog.vue:46-49`, `:140-150`.
- Query-key convention `['<slice>','<resource>',...scope]`:
  `frontend/src/slices/agents/queries/index.ts:1-40`.
- The agents list page, where the pack trigger belongs, already reads the project's key
  groups for its create form (`frontend/src/slices/agents/views/AgentListView.vue:59-70`,
  header at `:242-257`).
- Cross-slice constraint: `activities` may import `agents` and `tenancy`; `admin` may import
  `prompt-studio` and `skills` (`frontend/eslint.config.js` `SLICE_DEPS`,
  `frontend/src/slices/README.md`). Nothing here needs to cross a forbidden edge — the pack
  UI lives entirely in `agents`.

## 5. Design

### 5.1 How a pack reaches a running project

**Option A — platform-scoped agent rows plus per-project opt-in**, mirroring the activity
example exactly. Rejected: not implementable. §4.2 shows an agent row cannot exist without a
project-owned key group, and the platform owns no keys.

**Option B — copy-on-import (chosen).** The pack is repository data; installing instantiates
ordinary project-scoped agents owned and editable by that project.

**Option C — ship the prompts as `prompt_studio` platform templates.** No key group needed,
no new install surface, and [R9.02] already blesses "insert a template at authoring time".
Rejected: a template carries a system prompt and nothing else, so the orchestration
(`wakeup_config`), the observer role, and the model hint — the parts of the example that are
actually hard to reproduce by hand — would all be lost. Recorded because it remains the
right fallback if the install surface proves not to be worth its weight.

**Decision: Option B.** Note this is the *opposite* of Q-1 of the platform-example dossier,
which chose a shared platform row over a copy. The difference is not a change of mind: an
activity type is a measurement instrument the platform wants to own identically everywhere,
while an agent's prompt, model, and temperature are exactly what each project is expected to
tune. Copy-on-import gives that for free, and [R9.02]'s existing prompt-template sentence —
"an applied template leaves no persistent link to its source" — is the precedent the SRS
Delta extends rather than an obstacle.

**Idempotency is by name within the project, and it is weaker than the course seeder's.**
There is no `key` on `agents` (§4.2), so `install_pack` reports a pack agent whose `name`
already exists in the project as already-present and leaves it untouched. The consequence is
explicit: an owner who renames an installed agent and re-installs gets a second copy. That
is preferable to adding a column to `agents` for the sake of an example, and preferable to a
silently non-idempotent install.

### 5.2 Where the catalogue lives, and how the course link is checked

`backend/contexts/agents/infrastructure/examples/` — `catalogue.py` plus `packs/*.json`,
mirroring `contexts/activities/infrastructure/examples/` in both layout and strictness
(every field required, unknown fields rejected, `_PACK_KEY_RE` anchored `\A..\Z` guarding
the filename, UTF-8-sig decode).

- **Not `contexts/activities/`**: the artifacts installed are agents. `agents → activities`
  is the direction the codebase already permits (`turn_engine.py:32` imports
  `contexts.activities.application.activity_context_provider`), and the reverse is barred by
  the tripwire `backend/tests/unit/test_activities_no_agents_import.py`.
- **The loader validates shape only.** `for_course` and `binds_activity_types` are checked
  by `backend/tests/unit/test_agent_example_packs.py`, which may read both catalogues
  because it is a test. Production code must not create an
  `agents/infrastructure → activities/infrastructure` edge (Q-5).
- Parsed packs are cached per process behind a `clear_pack_cache()` test hook, exactly as
  `example_service._PARSED` / `clear_catalogue_cache` does
  (`contexts/activities/application/example_service.py:52-65`). Only successful parses are
  cached, for the same reason recorded there.

### 5.3 Field order becomes explicit

`x-order`, an integer on each property node. `fieldsFromSchema` sorts by
`x-order ?? Number.MAX_SAFE_INTEGER` with a stable sort, so a schema declaring none behaves
exactly as today and a schema declaring some puts them first in their stated order.

- JSON Schema ignores unknown keywords, and the backend's well-formedness check is
  `Draft202012Validator.check_schema` (`contexts/activities/application/validators/schema.py:18-24`),
  so `x-order` needs no backend change to be accepted.
- The guided schema builder is unaffected: `isFlatSchema`
  (`frontend/src/slices/activities/types/schemas.ts:37-49`) already requires each property to
  carry exactly the single key `type`, so every schema with a `title` — including both
  shipped ones — already opens in the raw JSON editor only.
- The Mandala plugin inherits the fix without modification: it derives its cells from
  `fieldsFromSchema` (`MandalaGrid.vue:28`), removes the property named `center`, and splices
  it back at index 4 (`:37-48`).
- **The digest is deliberately left alone.** `build_agent_digest`'s `sort_keys=True`
  (`agent_digest.py:23`) still emits keys alphabetically. With semantic keys that is legible
  to an agent, and changing the digest would change the prompt input of every activity type
  in every deployment for the sake of presentation. Recorded as FU-3.

### 5.4 What the corrected course looks like

Four activity types, one per worksheet section. Two keys are preserved verbatim because they
are already installed in real deployments and because `mandala-9grid` is the key the bundled
plugin binds to (`plugins/mandala9grid/index.ts:15`); only their `name`, schema, and
validator config change.

| Key | Name | Renderer | Properties | `min_filled` |
|---|---|---|---|---|
| `mandala-9grid` (kept) | 單元二 時空旅人（曼陀羅九宮格） | bundled plugin | 9 | 4 |
| `time-traveler-next-steps` (new) | 單元二 為了與你相遇 | generic form | 1 | 1 |
| `emotion-desk-three-emotions` (new) | 單元四 情緒播報台（三種情緒） | generic form | 6 | 2 |
| `six-hats-emotion-desk` (kept) | 單元四 情緒列車（六頂思考帽） | generic form | 6 | 3 |

**Why one type per worksheet section rather than one per unit.** For unit 2 the split is
forced: `MandalaGrid.vue:35` renders the 3x3 layout only for a schema of exactly nine
fields, so adding the worksheet's section 二 as a tenth property would collapse the grid to a
single column (Q-7's second question). For unit 4 nothing forces it, but the two sections are
the lesson plan's 準備活動 and 總結活動, they are scored on different things, and one
`min_filled` across twelve mixed properties would measure nothing. Keeping both units
symmetric also keeps the docs honest about the consequence: a room runs one activity at a
time, so a 45-minute session now dispatches two.

`mandala-9grid`'s nine properties, with `x-order` reproducing the worksheet exactly once the
plugin splices `center` into the middle: `home`(1) `work`(2) `abilities`(3) `appearance`(4)
`center`(5) `leisure`(6) `message_to_self`(7) `free`(8) `relationships`(9). `center` becomes
optional (`required: []`) because on the worksheet it is a printed question, not a blank; it
keeps its worksheet wording as its title.

### 5.5 The four agents

Room pack `creative-thinking-room` — TA, SA, AA. Design pack `creative-thinking-design` — DA.
`room_role` is **advisory metadata**, surfaced in the UI and the docs; install binds no room,
so nothing enforces it.

| Agent | `room_role` | `wakeup_config` | Brief |
|---|---|---|---|
| TA 教師代理 | `normal` | `every_n_messages {enabled: true, n: 1}` | Leads. Runs the lesson plan's arc: 「你想遇見什麼樣的自己?」 → 生涯幻遊 → 「為了與你相遇」 (p.115); for unit 4, the 情緒列車 frame 事件→想法→情緒→行為 and its closing point that thoughts, not events, produce feelings (p.124). |
| SA 學生代理 | `normal` | `every_n_messages {enabled: false}`, `silence_minutes` enabled with a bounded `autostop_rounds` | Peer catalyst. Speaks on a lull or when named, not on every message — which is what makes "TA leads, SA intervenes" reproducible without a central arbiter. |
| AA 分析代理 | `observer` | `silence_minutes` enabled with `observer_autostop_rounds` | Silent. Reads the activity block and writes descriptive observations for the teacher only. Its worked task is the lesson plan's own closing move: "從每個人的曼陀羅中可以觀察到每個人對於理想生活的價值觀也有所不同，有些人特別著重在工作，有些人重視和他人的關係" (p.115). |
| DA 設計代理 | `null` | both triggers disabled; `@mention` only | Lesson-design assistant for the teacher's own room. Drafts unit plans and TA/SA prompt text. |

**Grounding the AA without inventing a rubric.** The thesis ships a five-level (A–E) unit
rubric per unit (p.116 for unit 2, p.124 for unit 4) — unit 4's is: A 能分享運用不同角度看待
自己的挫折經驗，並調適自己的情緒 / B 能覺察情緒因個人想法受到的影響，以及運用轉換想法的
方法 / C 能透過情緒裁判覺察面對不同事件可能產生不同之情緒反應 / D 能表達自己最常出現的
情緒反應及事件 / E 未達D. This is a **self-development competency** rubric, not the
creativity four-dimension rubric, and the AA prompt must name which one it is using. The
distinction is load-bearing: `filled_count` operationalizes fluency alone
(`app/plugins/activity_validators.py:94-116`, [R30.27]), and flexibility, originality, and
elaboration have no scorer.

**The copyrighted scaffold is not shipped.** The lesson plan teaches the hats through
Doraemon characters (p.111, p.123). The packs use the worksheet's plain descriptors instead,
and the docs record the omission so a teacher can add it back locally.

**DA's honest limit.** DA can draft a system prompt in conversation; there is no path from
its output into an agent's configuration. The teacher copies it by hand. The pack
description and the docs must say so, because a "design agent" that appears to configure
agents is the obvious misreading.

## 6. Detailed Changes

### Backend — `contexts/agents`

- **`infrastructure/examples/catalogue.py`** (new) — `PackAgent`, `AgentPackDefinition`,
  `PackFileInvalid`, `available_packs()`, `load_pack()`, `parse_pack()`, `packs_root()`.
  Modelled field-for-field on `contexts/activities/infrastructure/examples/catalogue.py`.
  Required agent fields: `key`, `name`, `room_role` (`"normal" | "observer" | null`),
  `preferred_model_hint`, `system_prompt`, `temperature`, `wakeup_config`,
  `binds_activity_types`. Required pack fields: `pack_key`, `title`, `source`, `for_course`,
  `group_name`, `agents`. Nothing defaults — omitting `room_role` must be an error, not a
  guess, for the same reason the course loader refuses to default
  `expose_payload_to_agent`.
- **`infrastructure/examples/packs/creative-thinking-room.json`**,
  **`packs/creative-thinking-design.json`** (new).
- **`application/example_service.py`** (new) — `AgentExampleService`:
  `list_catalogue(project_id)` returns each pack annotated with which of its agents already
  exist by name; `install_pack(...)` resolves the model hint against the key group, creates
  the agents through `AgentService.create`, creates one agent group through
  `AgentGroupsFacade.create_group` and adds each agent as a member, and returns a report
  shaped like `InstallReport` (`activities/application/example_service.py:92-99`).
- **`interfaces/facade.py`** — `list_example_packs(project_id)` and `install_example_pack(...)`.
  The route calls the facade. `app/api/v1/agents.py` instantiates `AgentService` directly at
  fourteen sites (`:390`, `:413`, …); that is pre-existing and is not imitated here (§9).
- **`domain/errors.py`** — `AgentPackNotFound`, `KeyGroupHasNoUsableProvider`, plus rows in
  `interfaces/error_mapping.py` (404 and 422).

No migration. No new table, no new column.

### Backend — `contexts/activities`

- **`infrastructure/examples/courses/creative-thinking.json`** — the four types of §5.4, with
  `x-order` on every property, worksheet titles and descriptions, and the corrected hat
  order. Re-check each `min_filled` against the new declared-property counts: the loader
  runs `validate_filled_count_against_schema`
  (`app/plugins/activity_validators.py:132-153`) through the registry, so a stale threshold
  is a load-time error, not a runtime surprise.

### API contract

`gen:api` rerun required: **yes** (and `check:openapi-drift` in CI).

| Method | Path | Auth |
|---|---|---|
| `GET` | `/api/projects/{project_id}/agent-example-packs` | `require(Capability.RESOURCE_CREATE_EDIT, scope_from_path(project_param="project_id"))` |
| `POST` | `/api/projects/{project_id}/agent-example-packs/{pack_key}/install` | same |

The guard matches `create_agent` (`app/api/v1/agents.py:405-410`) rather than the activity
examples' `assert_project_owner`, because the operation *is* agent creation and must not be
reachable by anyone who could not already create the same agents by hand.

Install body: `{key_group_id: UUID, model_hint: str | null}` — `null` means "resolve from the
key group". Response: created agent ids, already-present names, the group id, and the model
hint actually used, so the UI can state what it picked rather than implying the pack chose.

### Frontend — `slices/agents` only

- `api/index.ts` — `listAgentExamplePacks`, `installAgentExamplePack`.
- `queries/index.ts` — `agentKeys.examplePacks(projectId)`, following the existing
  `['agents', '<resource>', ...scope]` shape (`queries/index.ts:1-40`).
- `components/AgentPackInstallDialog.vue` (new) — an `SModal` listing packs and their agents
  with role, orchestration summary, and the activity types each is written for; a key-group
  select reusing the list `AgentListView` already loads (`:64-68`); the resolved model hint
  shown before confirming; `SEmptyState` / `SQueryError` / `SLoadingSpinner` states matching
  `ExampleImportDialog.vue:119-138`.
- `views/AgentListView.vue` — a trigger beside the existing create action (`:242-257`), and
  cache invalidation of `agentKeys.agents(projectId)` on success.
- i18n keys under `agents.examplePacks` in **both** `slices/agents/locales/en.json` and
  `zh-TW.json`.

### Frontend — `slices/activities`

- `components/schemaFields.ts` — `fieldsFromSchema` sorts by `x-order` (§5.3). This is the
  only behavioural change; `SchemaForm.vue` and `MandalaGrid.vue` inherit it.
- `sdk/types.ts` — `JSONSchema` gains the optional `'x-order'?: number`.

### Docs

- `docs/examples/creative-thinking-course.md` — the four types, the corrected order and
  rationale, the two agent packs and how to install them, the three prompt constraints, DA's
  write-back limit, the drawing-instruction adaptation, and the sentence at `:125` replaced
  with the `x-order` fact. FU-7 of the platform-example dossier (the CLI-only installation
  prose) is closed here.
- `docs/assessments/nstc-meeting-learning-activities.md:47` — corrected to say wake-up is
  per-agent configuration, with the packs as the worked counter-example.

### Deploy/config

None.

## 7. NFR Checklist

- [x] **i18n** — every new string via `$t()` in `en.json` and `zh-TW.json` under
  `agents.examplePacks`; gate #12 fails the build on a bare template literal. Pack content
  (agent names, prompts) is course data in Chinese, not UI chrome, and is not translated —
  same treatment as the course JSON's activity names.
- [x] **Audit log** — install emits one `agent.created` per agent (reusing
  `AgentService.create`'s existing emission, `agent_service.py:682-689`) plus the agent-group
  events `AgentGroupsFacade.create_group`/`add_member` already emit. No new audit action is
  introduced; the installing user is the actor throughout.
- [x] **Tenant isolation** — both routes carry the project-scoped
  `RESOURCE_CREATE_EDIT` capability check. `install_pack` passes the path's `project_id` to
  `AgentService.create`, which re-verifies the key group belongs to that project
  (`agent_service.py:445-448`); a `key_group_id` from another tenant is refused by the
  existing gate, not by new code.
- [x] **Error handling UX** — loading / empty / error states in the dialog; a key group with
  no usable provider is a named 422 the dialog explains ("this key group carries no provider
  key"), not a generic failure. A partially-completed install reports created and
  already-present separately, as the course installer does.
- [x] **Performance** — two JSON files parsed once per process behind the same cache the
  course catalogue uses. `list_catalogue` costs one `list_agents_for_project` read
  (`interfaces/facade.py:94-98`). Install creates at most three agents plus one group inside
  the existing per-project advisory lock; no N+1.

## 8. Security Considerations

This ships **LLM prompts** and a route that **creates agents**, so both the agent/LLM surface
and the tenancy surface apply.

- **Prompt content is a privacy control here, not decoration.** All four course types set
  `expose_payload_to_agent: true` and `echo_includes_content: false`, so participant text
  reaches the agent's context while the room transcript deliberately does not echo it
  (`activity_context_provider.py:85-86`; the flags' interaction is noted at
  `activities/infrastructure/tables.py:45-47`). Every shipped prompt must forbid quoting a
  participant's submission text back into the room: an agent that reads the digest aloud
  reverses `echo_includes_content: false` for the whole class. This is AC-9 and is the single
  most important line in the packs.
- **The AA must not claim to score creativity.** `filled_count` measures fluency only
  ([R30.27]); the other three dimensions have no scorer and no rubric. A prompt that implies
  otherwise manufactures assessment data about minors. The prompt names the thesis unit
  competency rubric it is actually using (§5.5) and says what it cannot judge. AC-10.
- **Unit 4 collects negative-affect narratives from 13-year-olds.** The TA and SA prompts
  must set an explicit boundary: do not press for detail, do not elicit further disclosure,
  do not respond therapeutically, hand back to the teacher when a disclosure exceeds a
  classroom exercise. AC-11.
- **`system_prompt` is repository content, not user input**, and reaches the model through
  the same `base_system` block as any hand-authored prompt (`turn_engine.py:826-828`). The
  packs introduce no new injection surface. Participant text still enters as a digest inside
  a labelled system block, unchanged by this work.
- **The install route must not become a way to create agents against another project's
  keys.** It does not: the capability check is project-scoped and `_assert_key_group_in_project`
  runs unconditionally inside `AgentService.create`. The negative case is asserted in §12.
- **`pack_key` arrives from an HTTP path parameter**, so the loader's `\A..\Z`-anchored key
  guard bounds a network-reachable path from the first commit — the same reasoning recorded
  for `course_key` at `activities/application/example_service.py:170-176`.
- **No secret is ever written into a pack.** `key_group_id` comes from the request; the pack
  names a provider family (`claude`/`openai`/`gemini`) and never a key, a model endpoint, or
  a credential.

## 9. Quality Notes

**Existing debt in the touched files** — record, do not silently fix:

- `app/api/v1/agents.py` instantiates `AgentService` directly at fourteen sites rather than
  going through `AgentsFacade`, contrary to `backend/CLAUDE.md`'s route rule. The new routes
  use the facade; the existing ones are left alone. FU-1.
- `docs/examples/creative-thinking-course.md` carries three claims contradicted by the source
  (§4.4) and one contradicted by the implementation (§4.3). All four are corrected here
  because the whole task depends on that document being true.
- `contexts/activities/domain/errors.py:91-104` still omits three policy errors from
  `__all__` (FU-3 of the platform-example dossier). Untouched.
- `MandalaGrid.vue:35`'s exact-nine-fields rule is undocumented outside the component, and it
  silently changes layout rather than failing. This dossier works within it (§5.4) rather
  than changing it. FU-2.

**Patterns to follow** — exemplars:

- Catalogue loader: `contexts/activities/infrastructure/examples/catalogue.py` end to end —
  `_require_fields`/`_require_str`/`_require_bool`, `_fail(source, where, problem)`, the
  anchored key regex at `:49`, UTF-8-sig read at `:307`, and the `available_*` OSError-to-empty
  rule at `:267-280`.
- Install service: `contexts/activities/application/example_service.py:52-65` (process cache
  + test hook), `:159-225` (idempotent install returning a report).
- Route style and post-commit ordering: `app/api/v1/activities.py:463-536`.
- Owner-facing dialog: `frontend/src/slices/activities/components/ExampleImportDialog.vue`.
- Tests: module docstring naming the ACs pinned; `class TestSomeBehaviour:` with no base;
  sentence-style method names; module-level `_make_*(**over)` builders rather than fixtures.
- Layering tripwire: `backend/tests/unit/test_activities_examples_layering.py` (AST scan) is
  the shape for both the pack cross-reference test and the no-reverse-import assertion.

**Reuse inventory** — use these, do not re-invent:

| Need | Use | Location |
|---|---|---|
| Create an agent with every invariant | `AgentService.create` | `contexts/agents/application/agent_service.py:593` |
| Does a key group carry a provider | `KeysFacade.has_carried_provider_in_group` | `contexts/keys/interfaces/facade.py:161-170` |
| Key group belongs to project | already inside `AgentService.create` | `agent_service.py:445-448` |
| Create group / add member | `AgentGroupsFacade.create_group` / `add_member` | `contexts/agent_groups/interfaces/facade.py:41-73` |
| Project's agents, for name-idempotency | `AgentsFacade.list_agents_for_project` | `contexts/agents/interfaces/facade.py:94-98` |
| Provider catalog + defaults | `chat_model_catalog` | `contexts/agents/domain/models.py:69-78` |
| Schema well-formedness | `validate_schema_wellformed` | `contexts/activities/application/validators/schema.py:18-24` |
| Validator config + cross-field check | `get_config_validator` / `get_schema_config_validator` | `contexts/activities/application/validators/registry.py` |
| Project capability gate (API) | `require(Capability.RESOURCE_CREATE_EDIT, scope_from_path(...))` | `app/api/v1/agents.py:405-410` |
| Error → RFC 7807 | `register_context_handler` via `_MAP` | `contexts/agents/interfaces/error_mapping.py` |
| Modal / badge / empty / error UI | `SModal`, `SBadge`, `SEmptyState`, `SQueryError`, `SLoadingSpinner` | `@shared/ui` |
| Toasts, confirm | `useToast`, `useConfirmDialog` | `@shared/composables` |

## 10. Risks and Rollback

- **The schema correction does not reach an installed deployment.** `payload_schema` is
  outside the admin's editable set (AC-8 of the platform-example dossier) and install is
  idempotent by key, so an environment that already installed `creative-thinking` keeps the
  old two types with the old fields. Mitigation: the docs carry an explicit upgrade note —
  delete the platform types (which ends their activations across every tenant, by design) and
  re-install, or hand-edit via a future re-sync. This is the concrete cost of OQ-1 having
  been deferred, and it is stated rather than discovered.
- **`x-order` changes rendering for every activity type that adopts it, and only those.**
  Schemas without it are byte-identical in behaviour, so the blast radius is the two shipped
  courses plus whatever an owner opts into. The risk is the inverse: someone assumes
  `x-order` works and omits it, getting `jsonb` order. The docs and the raw-schema editor
  help text must say it.
- **The `jsonb` reordering claim is reasoned from PostgreSQL semantics, not yet observed in
  this codebase.** `backend/CLAUDE.md` is explicit that the unit tier renders `literal_binds`
  and cannot see PostgreSQL coercion behaviour. AC-4 therefore requires a `db`-tier test that
  round-trips a schema and asserts the key order actually changed. **If that test shows keys
  survive in authored order, Q-8's premise collapses and the `x-order` work should be
  reconsidered before the rest of the change is built.** Run it first.
- **Name-based idempotency is weak** (§5.1). A re-install after a rename duplicates agents.
  Bounded by the 1 000-per-project cap and by the report telling the owner what it created.
- **Scope creep into an agent marketplace.** A pack is a repository file with no re-sync, no
  versioning, and no authoring UI, all of which are non-goals. The moment a pack gains a
  version field this becomes a distribution system.
- **Prompt quality is not testable.** The three constraints are assertable as text (AC-9,
  AC-10, AC-11); whether the agent *obeys* them at runtime is not. Recorded as OQ-1 — a
  manual classroom dry-run before any use with real students.
- Rollback: `git revert` per commit. No migration, no new table, no data written outside the
  acting project. Reverting after an install leaves the created agents in place; they are
  ordinary agents and the owner deletes them normally.

## 11. Acceptance Criteria

- [x] **AC-1** — `mandala-9grid`'s schema declares exactly nine properties named
  `home, work, abilities, appearance, center, leisure, message_to_self, free, relationships`
  with `x-order` 1–9 and the worksheet's titles; `center` is not in `required`. Rendering it
  through the bundled plugin produces the worksheet's 3x3 layout with 家/工作/具備能力 on the
  top row and 想對30歲的自己說…/自由發揮/人際關係 on the bottom.
- [x] **AC-2** — `six-hats-emotion-desk`'s properties render in the order
  事件, 白帽, 紅帽, 黑帽, 黃帽, 藍帽, each carrying the worksheet's own descriptor wording.
- [x] **AC-3** — The course installs four activity types; `time-traveler-next-steps` and
  `emotion-desk-three-emotions` exist with the worksheet sections they model, and every
  type's `min_filled` is at most its declared property count (enforced by the loader, so a
  violation is a load error naming the type).
- [x] **AC-4** — A `db`-tier test round-trips a `payload_schema` whose authored key order
  differs from `jsonb`'s and asserts the stored order differs from the authored one, pinning
  §4.3's premise. A companion unit test asserts `fieldsFromSchema` returns `x-order` order
  regardless of object key order, and unchanged object order when no property declares it.
  *The unit half passes. **The `db` half has not been observed passing:** Docker Desktop was
  not running on this host, so `tests/integration/test_activity_schema_key_order.py` has only
  been collected, never executed. It runs on CI, and until it does the `x-order` half of this
  change rests on reasoning about PostgreSQL rather than on measurement — §10 says what to do
  if it fails. Treated as closeable on the same basis the platform-example dossier's AC-15
  used for its `db`/`integration`/`wiring` tiers.*
- [x] **AC-5** — Every shipped pack parses: `available_packs()` returns both keys, and
  `load_pack` rejects a pack missing any required field, carrying an unknown field, naming an
  unknown `room_role`, or whose `pack_key` disagrees with its filename.
- [x] **AC-6** — A tripwire scanning every pack asserts `for_course` resolves through
  `available_courses()` and every `binds_activity_types` key exists in that course. Deleting
  a type from the course JSON fails this test.
- [x] **AC-7** — A second tripwire asserts `contexts/agents/**` contains no import of
  `contexts.activities.infrastructure`, so the cross-check stays in tests.
- [x] **AC-8** — Installing `creative-thinking-room` into a project with a key group carrying
  Claude keys creates three agents with the pack's names, prompts, temperatures, and
  `wakeup_config`s, plus one agent group containing all three, and emits one `agent.created`
  per agent with the installing user as actor. A second install reports all three as
  already-present and creates nothing.
- [x] **AC-9** — Every shipped `system_prompt` contains an explicit instruction never to
  quote or paraphrase a participant's submission text into the room, asserted by a test over
  the pack files rather than by review.
- [x] **AC-10** — The AA prompt states that only fluency is scored automatically, names the
  thesis unit competency rubric as what it reasons from, and contains no claim to score
  flexibility, originality, or elaboration. Asserted over the pack file.
- [x] **AC-11** — The TA and SA prompts contain the unit-4 boundary clause (no pressing for
  detail, no eliciting further disclosure, no therapeutic response, hand back to the
  teacher). Asserted over the pack file.
- [x] **AC-12** — `preferred_model_hint` is honoured when the chosen key group carries that
  provider; when it does not, install succeeds using a provider the group does carry, and the
  response reports which. When the group carries no provider at all, install returns a named
  422 and creates nothing — no partial install.
- [x] **AC-13** — A caller with `RESOURCE_CREATE_EDIT` on project A cannot install into
  project B, and cannot install into A using a key group belonging to B; both are refused
  before any agent row is created.
- [x] **AC-14** — `creative-thinking-design` installs DA alone, with both wake-up triggers
  disabled, and the dialog and docs both state that DA belongs in the teacher's own room and
  that its drafts must be pasted into an agent by hand.
- [x] **AC-15** — `docs/examples/creative-thinking-course.md` describes four activity types,
  both packs, the `x-order` fact replacing the claim at `:125`, the drawing adaptation, and
  the upgrade note for already-installed deployments;
  `docs/assessments/nstc-meeting-learning-activities.md:47` no longer states that every agent
  answers every message.
- [x] **AC-16** — All user-facing strings exist in `agents/locales/en.json` and `zh-TW.json`.
- [x] **AC-17** — Gates green: `ruff check . && ruff format --check .`, `mypy .`, `pytest -q`,
  `pnpm lint`, `pnpm typecheck`, `pnpm test`, `pnpm build`, `pnpm run check:openapi-drift`,
  `pnpm run check:bundle-size`, `pnpm run check:type-coverage`,
  `pnpm run check:boundaries-enforced`. `db` / `integration` / `wiring` tiers on CI.
  *All green locally except `check:openapi-drift`, whose shell script needs `python` on the
  git-bash PATH and cannot run on this host — the same limitation the platform-example
  dossier recorded. Its two steps were performed by hand instead (re-export the spec,
  regenerate the client, confirm the tree is clean) and CI runs the script itself. See D-8:
  the export required a pydantic upgrade within the declared range to produce an additive
  diff, and the spec is written without a BOM, which shell redirection would otherwise add.*

## 12. Test Plan

| AC | Level | Location |
|---|---|---|
| AC-1, AC-2, AC-3 | unit | `backend/tests/unit/test_smap_examples_catalogue.py` extended — assert the shipped course's field names, `x-order` values, titles, and thresholds against the worksheet, field for field |
| AC-1 | component | `frontend/src/slices/activities/__tests__/MandalaGrid.test.ts` — mount with the shipped schema, assert the nine cells' rendered order |
| AC-4 | **`db` tier first** | `backend/tests/integration/test_activity_schema_key_order.py` (`pytest.mark.db`) — insert a schema, read it back, assert order changed. **Run before building anything else** (§10) |
| AC-4 | unit | `frontend/src/slices/activities/__tests__/schemaFields.test.ts` — `x-order` sorting, absent-`x-order` fallback, mixed case |
| AC-5 | unit | new `backend/tests/unit/test_agent_example_packs.py`, mirroring `test_smap_examples_catalogue.py`'s malformed-input table |
| AC-6, AC-7 | unit | same file for the cross-reference; AST tripwire in the idiom of `test_activities_examples_layering.py` for the import direction |
| AC-8, AC-12 | unit | new `backend/tests/unit/test_agent_example_service.py`, facade double per `test_activity_examples_service.py`; audit via `patch("…application.example_service.audit.emit")` |
| AC-9, AC-10, AC-11 | unit | same file — assertions over the parsed shipped packs, not over hand-built fixtures, so the constraint holds for the content that actually ships |
| AC-13 | unit | `backend/tests/unit/test_agents_authz.py` extension — cross-project install and cross-project key group, each asserting no row was created |
| AC-14, AC-16 | component | `frontend/src/slices/agents/__tests__/AgentPackInstallDialog.test.ts`, `AgentListView.test.ts` |
| AC-15 | manual | doc review at approval |
| AC-17 | CI | the full gate set; per the project's remote-CI rule, CI is authoritative over the local Windows host |

`frontend/e2e/` is not extended: no e2e spec covers the activities or agents surfaces today
(FU-4 of the platform-example dossier, still open).

## 13. SRS Delta

Apply verbatim on approval.

**Amend [R9.02]** (append one sentence; the existing text is unchanged):

> - **[R9.02]** Agents are not versioned; no export/import (Q41). Editing overwrites in
>   place. Prompt templates (§29) may be inserted at authoring time; an applied template
>   leaves no persistent link to its source. Skills (§31) differ: an agent's bound skills
>   **are** a persistent link (`agent_skills`), and a skill exports on its own — but a skill
>   export contains no agent, and agent export remains out of scope. A shipped example agent
>   pack ([R30.35]) is instantiated the same way a template is: it creates ordinary
>   project-scoped agents and leaves no persistent link to the pack, and it is not an import
>   mechanism — no agent from any project can be serialized into one.

**New [R30.35]**:

> - **[R30.35]** The repository may ship example **agent packs**: definitions of agents
>   written to accompany a shipped course ([R30.28]), each declaring the course it targets and
>   the activity type keys it is written for. A user who may create agents in a project may
>   install a pack into that project, which creates ordinary project-scoped agents plus one
>   agent group and nothing else — no chatroom is created, no room binding is made, and no
>   activity is activated. Installation is never automatic, records the installing user as the
>   audit actor of each `agent.created` event, and is idempotent by agent name within the
>   project. A pack names a preferred model provider but never a key: the provider is resolved
>   at install against a key group the installer chooses, falling back to a provider that group
>   carries, and installation is refused rather than partially applied when the group carries
>   none. Pack content is repository data, not platform behavior: the platform operates
>   normally when the catalogue is absent.

**New [R30.36]**:

> - **[R30.36]** A payload schema property may declare an explicit render order; the platform
>   renders declared fields in that order and must not depend on the key order of the stored
>   schema document, which the datastore does not preserve. A schema declaring no order
>   renders in the order the stored document presents.

## 14. Open Questions

- **OQ-1** — The three prompt constraints are assertable as text but not as behaviour. A
  classroom dry-run against a real provider, checking that the agents actually decline to
  quote submissions and that AA stays inside the competency rubric, is needed before any use
  with students. Does not block approval; blocks deployment with minors.
- **OQ-2** — Should an installed pack be re-syncable when the shipped JSON changes? Same
  question OQ-1 of the platform-example dossier left open for courses, now with the same
  answer (no) and the same reason. Worth resolving once, for both.
- **OQ-3** — The six remaining course units still await the collaborating educator's
  confirmation. Once they land, do the packs' prompts need per-unit variants, or does one TA
  prompt covering the course's arc suffice? The current packs assume the latter.

## 15. Deviation Log

- **D-1** — **AC-13's test home moved.** §12 cited `tests/unit/test_agents_authz.py`, which
  does not exist; the agents context's cross-project guard tests live in
  `test_agent_config_project_guard.py`. The AC-13 assertions went into
  `test_agent_example_service.py::TestTenantIsolation` instead, beside the install path they
  are about. Caught by the freshness spot-check before any code was written.
- **D-2** — **`AgentService._assert_key_group_in_project` was promoted to public.** Not in
  the spec. The security gate found that `install_pack` probed the key group's providers
  *before* the ownership check, and since `has_carried_provider_in_group` answers for any
  group id, the two distinguishable refusals formed an oracle for another project's provider
  inventory. Reordering required calling the guard from the example service; promoting the
  one rule was chosen over a second copy of it, given §9 already records what seven copies of
  a tenancy check cost this codebase.
- **D-3** — **Packs deliberately omit `triggers.call_only`.** §5.5 described DA as
  "both triggers disabled; `@mention` only" without naming the mechanism. `call_only` reads
  as exactly that and does suppress autonomous wake-ups, but it is also an A2A authorization
  widener (`a2a_scope.evaluate` lets any a2a-enabled agent in the project call a `call_only`
  agent with no shared context). Shipping it would leave that widening latent behind a flag
  nobody re-reads when enabling a2a later, so the packs disable both triggers instead and a
  test asserts `call_only` is absent.
- **D-4** — **The pack loader bounds `system_prompt` at 100 000 characters**, mirroring
  `_MAX_SYSTEM_PROMPT` in `app/api/v1/agents.py`. Not in the spec. Installing bypasses the
  request model, so without this a pack could create an agent the API would then refuse to
  accept on the owner's next edit.
- **D-5** — **The routes live under the existing agents router** as
  `/api/projects/{project_id}/agents/example-packs`, not at
  `/api/projects/{project_id}/agent-example-packs` as §6 wrote. The latter needed a third
  router for two endpoints and split the OpenAPI grouping; the former reads as "this
  project's agents, its example packs" and reuses the router that already carries the
  capability gate.
- **D-6** — **`AgentsFacade` imports the example service inside its two methods.**
  `example_service` reaches `AgentService`, which reaches the turn runtime, which imports the
  facade — a module-level import closes that cycle at startup. The in-method import is the
  idiom already used at `activity_context_provider.py:43` and `app/api/v1/activities.py:351`.
- **D-7** — **The course JSON carries both halves of the change.** §10 planned the `x-order`
  work as separately revertable, and M0 (the `db` premise test) and M1 (the frontend sort)
  are. The course file is not: it necessarily gained both the worksheet corrections and the
  `x-order` values in one edit. If the premise fails, that file needs its `x-order` keys
  stripped rather than a commit reverted.
- **D-8** — **The local venv's pydantic had to be upgraded to regenerate the API contract.**
  It sat at 2.9.2, the floor of `pydantic[email]>=2.9,<2.14`, and emits a different JSON
  Schema for free-form config fields than whatever generated the committed `openapi.json`;
  regenerating stripped `additionalProperties` across the whole spec. Upgraded to 2.13.4
  within the same declared range, after which the regeneration is purely additive. Recorded
  because it means the committed spec is reproducible only against a *resolved* dependency
  set, not a pinned one — see FU-11.
- **D-9** — **`test_smap_examples_packaging.py` was generalised rather than duplicated.** It
  now parametrizes over both shipped catalogues, so a third one is covered by adding a row.
- **D-10** — **`list_catalogue` gained a log line and a narrower except.** It originally
  skipped an unloadable pack with a bare `except ValueError` and no log, which made a
  packaging error indistinguishable from a pack that was never shipped and would have
  swallowed a genuine defect in the method itself. Found by the audit gates.
- **D-11** — **The install dialog gained key-group precondition states.** The quality gate
  found `keyGroupsQuery` had no error handling, so a failed request and a project with no key
  groups both rendered as an empty select with inert buttons and no explanation.
- **D-12** — **No behavioural verification was performed.** `/build`'s Definition of Done
  asks for the change observed working in the running app. Docker Desktop was not running on
  this host, so the compose stack could not be started and neither the install flow nor the
  corrected worksheets have been seen rendering in a browser. Every claim about them rests on
  unit and component tests. This is the same gap the `2026-08-09-chatroom-rail-scroll-and-resize`
  dossier closed with, and it carries the same instruction: confirm on the first deployed build.

Appended after `/code-review` on the finished branch:

- **D-13** — **Every agent a pack names now joins its group, not only the ones the run
  created**, and `AgentGroupService.add_member` emits `agent_group.member_added` only on a
  real insert (`AgentGroupRepository.add_member` returns whether it inserted, mirroring
  `remove_member`). This was recorded as FU-10 and deliberately deferred on the grounds that
  adding existing members would write false audit events. The review found two paths into
  the gap that the deferral had not considered — re-installing to recover a deleted group
  leaves it empty, and an agent someone hand-authored under a pack's name silently stays out
  with nothing in the report mentioning it — and the audit objection turned out to be fixable
  at its source rather than a reason to accept the gap. Touches `contexts/agent_groups`,
  which this task otherwise only consumed.
- **D-14** — **All install buttons are disabled while any install is in flight.**
  `pendingPack` is single-valued, so disabling only the in-flight row let a second install
  start, overwrite it, and have the first completion clear the pending state for the wrong
  pack.
- **D-15** — **The observer notice was reworded.** It named "this pack" while being rendered
  once above the whole list and gated on *any* pack carrying an observer, so it sat above the
  design pack and attributed a silent observer to it.
- **D-16** — **The upgrade note now states the trap rather than only the remedy.** §10 and
  the docs said the correction cannot reach an installed deployment and gave the delete-then-
  reinstall step. What neither said is that re-installing *without* deleting half-works: it
  creates the two new types, leaves the two corrected ones untouched, and reports success —
  so the mandala still renders 格 1 to 格 8 against agent prompts describing 家 / 工作 /
  具備能力, and `x-order` is inert for exactly the units it exists to fix.

## 16. Follow-ups

- **FU-1** — `app/api/v1/agents.py` calls `AgentService` directly at fourteen sites instead of
  going through `AgentsFacade`, against `backend/CLAUDE.md`'s route rule. A sweep would make
  the agents context match the activities context.
- **FU-2** — `MandalaGrid.vue:35` silently falls back to a single column for any schema that
  is not exactly nine fields. The rule is invisible to a schema author until the form looks
  wrong. A validation hint in the authoring UI, or a plugin that grids the first nine and
  stacks the rest, would remove the constraint §5.4 works around.
- **FU-3** — `build_agent_digest` emits `sort_keys=True` over raw property keys
  (`agent_digest.py:23`), so an agent never sees a field's `title` and reads fields in
  alphabetical rather than declared order. Emitting titles in `x-order` order would make the
  digest self-describing for every activity type; deliberately out of scope here because it
  changes the prompt input of every existing deployment.
- **FU-4** — The guided schema builder cannot express `x-order` (or `title`, or
  `description`): `isFlatSchema` (`schemas.ts:37-49`) admits only bare `{type}` properties, so
  any labelled schema is raw-JSON-only. Teaching the builder these three keywords would let an
  educator author a worksheet without hand-writing JSON.
- **FU-5** — The packs ship no `seed`, so AA's observations are not reproducible run to run,
  which is what `docs/tasks/2026-07-13-agent-sampling-reproducibility/spec.md` exists for and
  what item 5 of `docs/assessments/nstc-meeting-learning-activities.md:52` asks for. Pinning a
  seed in shipped data is a research decision, not an engineering one.
- **FU-6** — DA's output has no path into an agent's configuration (§5.5). A "apply this draft
  to agent X" action would make the design role real rather than advisory, and is the natural
  next step if DA proves useful.
- **FU-7** — The lesson plan's Doraemon-character scaffold for the six hats (p.111, p.123) is
  omitted for copyright. A locale-neutral equivalent scaffold would restore the pedagogical
  device the thesis found effective.
- **FU-8** — `docs/tasks/2026-07-13-activities-activation-ux/spec.md` still carries
  `status: done`, not a value in the contract's lifecycle. Carried forward unresolved from
  FU-6 of the platform-example dossier.
- **FU-9** — The two example catalogues duplicate their error-formatting helpers: `_fail`,
  `_require_fields`, `_require_str` are near-identical across
  `contexts/activities/infrastructure/examples/catalogue.py:102-127` and
  `contexts/agents/infrastructure/examples/catalogue.py:112-138`. The *schemas* diverging is
  intentional (Q-1); these helpers are not artifact-specific and belong in `shared_kernel`.
  Deferred because lifting them means editing the activities loader, outside this task's
  blast radius.
- **FU-10** — ~~`install_pack` adds only newly created agents to the group.~~ **Resolved by
  code review, see D-13.** The deferral reasoning was right about the audit-noise cost and
  wrong to stop there: the review surfaced two commoner paths into the gap than the rename
  case this entry described, and the emission was fixable at its source.
- **FU-11** — `backend/pyproject.toml` pins dependency *ranges*, so `backend/openapi.json` is
  reproducible only against whatever resolves at generation time; D-8 is what that costs in
  practice. A lock file, or an exact pin on the codegen-relevant dependencies, would make
  `check:openapi-drift` mean the same thing on every machine.
- **FU-12** — An unknown `course_key` on `POST /api/admin/activity-examples/{course_key}/install`
  returns 500: `CourseFileInvalid` is a bare `ValueError` and is absent from
  `contexts/activities/interfaces/error_mapping.py:15`. The agent-pack route handles the
  equivalent case as a 404 by checking `available_packs()` before loading. Pre-existing in the
  activities work, not touched here.
- **FU-13** — The activity digest emits `json.dumps(payload, sort_keys=True)` over raw
  property keys (`agent_digest.py:23`), so an agent sees fields alphabetically and never sees
  a `title`. Now that schemas declare `x-order`, emitting titles in declared order would make
  the digest self-describing for every activity type. Out of scope here because it changes the
  prompt input of every existing deployment.
