---
type: feature
status: implemented
created: 2026-08-24
requirements: [R28.01, R28.03, R28.05, R28.06, R28.08, R28.12, R28.13, R30.15, R30.27, R30.37]
depends_on: [2026-08-24-traceability-extraction-gate]
---

# Observer agents present their analysis as system-defined blocks

## 1. Summary

An observer agent's output today is one blob of markdown rendered into a card in the
creator-only Observer tab (`ObservationCard.vue:16-20`). This feature gives the observer a
**closed set of platform-defined presentation blocks** it can assemble into an observation:
prose, key points, a timeline, and three blocks whose numbers the *server* computes
(per-field coverage, a mandala grid, an attempt table). The agent picks which blocks to
use, in what order, and fills the text fields; it never writes code, markup, or the
numbers behind a chart. The creator reads the result; nothing about the release,
disclosure or AuthZ model changes.

The motivating case is the `creative-thinking-room` example pack's `AA 分析代理`
(`backend/contexts/agents/infrastructure/examples/packs/creative-thinking-room.json:55-77`),
whose prompt already mandates a fixed shape — three to six points, each with the
observation it rests on, then one suggested next step — that the platform currently has no
way to render as anything but a bulleted list.

## 2. Goals and Non-goals

**Goals**

- An observer agent can deliver its turn as an ordered list of blocks drawn from a
  platform-defined set, via one structured tool call.
- The agent controls block choice, block order, titles and the free-text fields.
- Every quantitative block is populated by the **server**, from data the platform already
  computes. The model supplies no counts.
- Every non-prose block renders a platform-authored basis label saying what its content
  rests on and what it cannot mean.
- An observer that does not call the tool behaves exactly as today.
- Observations stored before this feature render exactly as today.

**Non-goals**

- **No agent-authored code, HTML, SVG, CSS or template strings.** No new `v-html` binding,
  no extension of the gate #4 allowlist beyond `ObservationCard.vue`, which is already on it
  (`frontend/eslint.config.js:311-324`).
- **No user-defined or project-defined block kinds.** The set is fixed in platform code
  this release; there is no authoring surface for a new block.
- **No structured rendering in the room.** Releasing to the room continues to produce a
  `sender_type=system` message carrying markdown ([R28.06]); the blocks degrade to their
  markdown serialisation. `ChatroomMessageBubble.vue` is not touched.
- **The creator does not edit block layout.** The release dialog's plain-text override
  (`ObservationReleaseDialog.vue:15-20`) keeps working on the serialised markdown and is
  the only editing surface.
- **Normal-role agents get nothing.** The tool is bound to observer-role turns only.
- **No change to `filled_count` or `RecentActivityRow`.** The new per-field fact is supplied
  by a *new* first-party validator that the example course opts into (Q-4). The
  recent-activity block is touched in exactly one place — `_CONTENT_NOTE` must stop calling a
  server-computed digest the participant's own words (§6) — and that is a correction the
  validator change forces, not a widening of scope.
- **No change to the observation retention, soft-delete, release-CAS or audit paths.**

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | What is the data model of one observation? | A `blocks[]` array; `prose` is one block kind. `content_md` becomes a server-side serialisation of the blocks. | "Change the arrangement" is then block order, and prose can interleave with figures. The cost, accepted: `content_md` is what release-to-room and the observer's own memory window ([R28.05]) read, so the serialisation must be deterministic and stable. |
| Q-2 | How does the model deliver the structure? | A structured tool call, `present_observation(blocks)`, offered only on observer turns. | Reuses the argument that already licenses `start_activity`/`end_activity` (`activity_tools.py:7-19`): the only path from model output to a rendered surface is a schema-validated tool call over a server-built enum. A fenced ```` ```smap-view ```` block would add a parser as a new trust boundary, which no existing agent-output path in this repo has. A forced response schema would collide with the turn engine's text-synthesis path, which persists `final_text` even when synthesis failed (`turn_engine.py:3012-3015`), and leaves no graceful degradation on a provider without it. Not calling the tool degrades to today's behaviour. |
| Q-3 | Which block kinds ship in v1? | Six: `prose`, `key_points`, `timeline`, `field_coverage`, `mandala_grid`, `attempt_table`. | The first three are agent-authored text. The last three are server-computed (§5). `timeline` is included at the user's explicit direction despite resting only on the transcript; its basis label states that. |
| Q-4 | `field_coverage` and `mandala_grid` need "which box was filled", and no such fact exists. Where does it come from? | A **new** first-party validator that sets `ValidationResult.detail` and records the filled field names in `sub_scores`. The example course's four types opt into it. `filled_count` is untouched. | The user's constraint was to change the example, not the platform. `build_agent_digest` prefers `detail` over the raw-payload dump (`agent_digest.py:21-27`), so this also **removes participant answer text from the digest** for these types, which is a privacy improvement over today. Registering an additional validator in `app/plugins/activity_validators.py` is the sanctioned site for a first-party validator (its docstring, `:1-8`) and changes no existing type's behaviour. |
| Q-5 | Who arranges the blocks? | The agent. The creator reads, releases or deletes, exactly as today. | Matches the request ("let the observer choose how to present, per situation"). An observation row is immutable except for the release CAS (`observation_repo.py:167-217`); a creator-side re-arrangement would need a mutable presentation layer, which is a separate feature. |
| Q-6 | Do the blocks reach the room on release? | No. Release serialises to markdown, unchanged. | Keeps `messages`, the WS fan-out, the release dialog and the disclosure rules ([R28.09]) entirely out of scope, and avoids a second public data surface over student submissions. |
| Q-7 | Does this depend on `2026-07-19-large-artifacts-silently-dropped`, which is `status: in-progress` and names the same two files? | No — it is complete. The user confirmed that on 2026-08-24; its frontmatter is stale, its Deviation Log is filled in, and its close-out reviews are committed (`4323256`, `f0733fc`). | Nothing sequences against finished work, so no region comparison is needed. **Do not reuse that dossier's line anchors**: they were written against an older `turn_engine.py` and no longer resolve — `_persist_artifacts` is at `:2008` today, not the `:1133`/`:1843` it cites. Its prose is still accurate; only its coordinates have moved. See FU-1. |
| Q-8 | Does this depend on `2026-08-24-traceability-extraction-gate`? | Yes — `depends_on: [2026-08-24-traceability-extraction-gate]`. | Overlap prerequisite. This task's SRS Delta adds `[R28.15]`-`[R28.19]`, each of which needs a `docs/traceability.csv` row; that dossier rewrites all 306 existing rows from a generator it builds. Sequencing means the new rows are produced by the script rather than hand-written into a file that is about to be regenerated. |

## 4. Current State

### 4.1 How an observer turn produces output

`run_turn` resolves the binding role and sets `is_observer = role is ChatroomAgentRole.OBSERVER`
(`turn_engine.py:2428`). On a completed turn the observer branch persists the model's
`final_text` verbatim and never touches `messages` (`turn_engine.py:3026-3036`):

```
obs = await ObservationService(self._db).record(..., content_md=final_text, metadata=reply_meta)
```

`reply_meta` (`:3015-3024`) is **engine-stamped** telemetry — `trigger`, `tool_rounds`,
`rag_sources`, `skill_reads`, synthesis flags. `observation.created` is emitted post-commit
with ids only (`:3052-3061`), matching `message.created`; the body comes back over REST
([R28.13]).

### 4.2 Storage and transport

`agent_observations` (`contexts/conversation/infrastructure/tables.py:107-134`) has
`content_md TEXT NOT NULL` and `metadata JSONB NOT NULL DEFAULT '{}'`. `ObservationOut`
already returns `metadata` wholesale to the client (`app/api/v1/observations.py:76-103`),
and the frontend types it as `Record<string, unknown>`
(`frontend/src/slices/conversation/types/index.ts:52-64`).

Note for the implementer: `metadata` being already-exposed makes it a tempting carrier for
the blocks. It is the wrong one — it holds engine internals, and mixing agent-chosen content
into it makes every future reader guess which keys are which. §6 adds a column.

### 4.3 How the creator reads it

`useObservations.ts` gates the query on creator resolution (`:63-71`), pages newest-first at
50 per page (`:94-112`), and patches release state immutably (`:193-213`). `ObserverPanel.vue`
renders the observer roster plus a list of `ObservationCard`. `ObservationCard.vue:79`
renders `content_md` through `renderMarkdown()` and binds it with `v-html`; that file is on
the gate #4 allowlist (`frontend/eslint.config.js:321-323`). Bodies over 600 characters clamp
to 14 lines (`:80-81`).

### 4.4 The only structured thing the observer can read

`ActivityContextProvider.query` builds the `[Recent room activity]` block
(`activity_context_provider.py:74-104`): a preamble, a code-to-label legend, then one line
per submission event — `- (ts) u:1a2b3c4d #3 type-key: valid [error_class] — digest`
(`:202-210`). Three properties bound what any block built from it can claim:

- **It is a window.** `DEFAULT_ACTIVITY_WINDOW = 30` (`:32`), newest first, one row per
  attempt.
- **There is no roster.** The legend resolves only the codes that appear (`:173-199`).
- **The digest is the raw payload.** `filled_count` sets no `detail`, so `build_agent_digest`
  falls back to `json.dumps(payload)` truncated at 480 characters (`agent_digest.py:17-27`).

### 4.5 The finding that shaped this design

**No server-computed fact says which fields a participant filled.**

- `filled_count_scorer` writes `sub_scores = {"filled": n}` — a count, not a field list
  (`app/plugins/activity_validators.py:109-116`).
- `RecentActivityRow` (`contexts/activities/domain/models.py:267-281`) carries no
  `sub_scores` at all, so even the count never reaches an agent.
- Therefore the only path to "which box has text" today is the agent eyeballing a truncated
  JSON dump of the participant's own words.

A bar chart built on that is fabrication rendered as measurement, in exactly the area
AA's prompt spends a whole section forbidding (`creative-thinking-room.json:77`, the
"你不評什麼" section). §5 removes the agent from that arithmetic entirely.

### 4.6 The precedent this design copies

`activity_tools.py` is the one place in the runtime where a room grant, not an `agent_tools`
row, produces a tool. Its docstring (`:7-19`) states the safety argument verbatim: the only
path from model output to a class-visible effect is a structured call whose argument is an
`enum` built at turn-assembly time, so no participant text can become an argument value.
`resolve_activity_control` fails closed on every error path (`:69-110`).

## 5. Design

### Options considered

**Option A — prose plus one optional figure.** Keep `content_md` authoritative; hang a
single optional view off `metadata`. Smallest diff; release, search and the existing UI are
untouched. Rejected: one observation gets one figure, always below the prose, so "change the
arrangement" degrades to "reorder the columns inside one widget".

**Option B — `blocks[]`, agent supplies every field including counts.** Full arrangement
control, small backend. Rejected on §4.5: the counts would be the model's arithmetic over a
truncated dump, and a bar renders that as a measurement.

**Option C — `blocks[]`, quantitative blocks server-populated.** Chosen.

### Decision

One observation is an **ordered array of blocks**. Block kinds split into two classes, and
the split is the design:

| Class | Kinds | Who writes the content |
|---|---|---|
| Narrative | `prose`, `key_points`, `timeline` | The agent, as text |
| Computed | `field_coverage`, `mandala_grid`, `attempt_table` | The **server**, at tool-invoke time |

For a computed block the agent supplies only its *selection and framing* — which activity
type, an optional title, an optional caveat sentence. The server runs the aggregate and
fills the numbers. The model cannot state a number it did not measure, because it is never
asked for one. This is what makes "the observer chooses the presentation" safe to grant.

What was given up: the agent cannot make a figure about something the platform does not
already compute. That is the intended bound, not a limitation to work around later — a
seventh block kind is a platform change with its own aggregate, which is where the review
attention belongs.

### 5.1 The block schema

Every block carries `kind` and, except for `prose`, a required `basis` enum plus an optional
`caveat` string (max 280 chars). `basis` selects a **platform-authored** sentence rendered
as a footnote on the block; the agent picks which one applies, it does not write it.

`basis` values and what each renders (i18n keys under `conversation.observers.basis.*`):

- `server_facts` — computed by the server over this room's submissions; counts submissions,
  never participants, and says nothing about who did not submit.
- `recent_window` — read off the recent-activity window, which holds only the most recent
  events and is not a complete record.
- `transcript` — read off what was said in the room; not a measurement.

Block payloads:

```
{ "kind": "prose",     "text": <string, <= 4000> }

{ "kind": "key_points", "title"?: <string <=120>, "basis": <enum>, "caveat"?: <string <=280>,
  "points": [ { "text": <string <=400>, "evidence"?: <string <=200> } ],   // 1..8
  "next_step"?: <string <=400> }

{ "kind": "timeline",   "title"?, "basis": <enum>, "caveat"?,
  "entries": [ { "label": <string <=120>, "detail"?: <string <=400> } ] }  // 1..12

{ "kind": "field_coverage",  "title"?, "caveat"?, "type_key": <enum of reachable types> }
{ "kind": "mandala_grid",    "title"?, "caveat"?, "type_key": <enum, 9-property types only> }
{ "kind": "attempt_table",   "title"?, "caveat"?, "type_key"?: <enum>, "limit"?: <1..30> }
```

The three computed kinds take **no** `basis` — the server stamps `server_facts` on them, so
a computed block cannot be mislabelled by its caller. `type_key` is an `enum` built at
turn-assembly time from the types reachable in the room's project, exactly as
`_resolve_allowed_types` builds its own (`activity_tools.py:113-118`); `mandala_grid`'s enum is filtered to types
declaring exactly nine properties, so a mismatched grid is unrepresentable rather than
handled.

At most **12 blocks**, at most **one** computed block per `(kind, type_key)` pair, and at
most 20 KB of serialised JSON. `present_observation` may be called more than once in a turn;
**the last call wins**, and the `ToolResult` says so.

### 5.2 What the server computes

Three new read-only aggregates on `ActivitiesFacade`, room-scoped, excluding soft-deleted
submissions:

- `field_coverage(chatroom_id, type_key)` → the type's declared properties in `x-order`,
  each with a filled count, plus the number of submissions counted. Reads
  `sub_scores.filled_fields` (Q-4's validator). A type whose submissions carry no
  `filled_fields` returns no coverage and the tool refuses the block with a message the
  model can act on.
- `mandala_grid(...)` — the same aggregate, returned in 3x3 positional order.
- `attempt_summary(chatroom_id, type_key?, limit)` → one row per participant code:
  `attempt_no` high-water mark, the latest outcome. Codes come from the same
  `_subject_code` helper the context block uses (`activity_context_provider.py:146-147`), so
  a code in a block matches a code in the agent's own context.

**Names never appear.** The aggregates return codes; there is no legend and no display-name
lookup on this path. The creator holds the roster.

**Denominators are submissions, not people.** Every computed block renders "N submissions
counted", never a percentage of a class. This is the load-bearing wording: a coverage-rate
reading of these blocks is precisely what AA's prompt has to decline
(`creative-thinking-room.json:77`).

### 5.3 Serialisation to `content_md`

A deterministic, platform-owned serialiser turns blocks into markdown, which is stored in
`content_md` on the same insert. Every consumer that exists today keeps working unchanged:
release-to-room ([R28.06]), the release dialog's override, the observer's own memory window
([R28.05]), and any future search.

- `prose` → the text.
- `key_points` → `### title`, then `- text` with ` — evidence` appended, then the next step
  as a final paragraph.
- `timeline` → `### title`, then `- label — detail`.
- Computed blocks → `### title`, a markdown table of the aggregate, then the basis sentence
  in **English** (the serialiser has no request locale; the rendered UI uses `$t()`).

The `caveat` and the basis sentence are always serialised, so a released observation carries
its limits into the room with it.

### 5.4 No tool call

`blocks` stays `[]` and `content_md` is `final_text`, as today. The renderer falls back to
the markdown path for any observation whose `blocks` is empty, which covers every row
written before this migration.

### 5.5 Blocks with no prose — the path that would otherwise lose them

`run_turn` guards on `if not final_text.strip():` (`turn_engine.py:2958`) and **returns
`skipped` before the observer branch at `:3026` is ever reached**, emitting
`observation.skipped` / `observation.failed` (`:2985-2993`) and calling
`ObservationService.record` not at all. A model told to deliver its analysis as structured
blocks — the entire point of this feature — that calls `present_observation` and then says
nothing in prose is the ordinary case, not an edge case, and under the guard as it stands
every block is silently discarded.

The guard therefore becomes: **the turn is empty only when it produced neither text nor
blocks.** On an observer turn holding validated blocks, `content_md` is their serialisation
(§5.3), which is non-empty by construction, so the existing "never persist an empty message"
invariant is preserved rather than weakened.

Two sub-cases, decided rather than left to the implementer:

- **Blocks, no prose** — record the observation. Not a skip.
- **Blocks, and synthesis failed** (`outcome.synthesis_failed`) — record the observation and
  keep `synthesis_meta` on it, exactly as the non-empty path already does at `:3012-3015`
  ("Persisted even when the synthesis failed... never unmarked"). The tool rounds behind
  those blocks are real work; the reason for the missing prose is a provider fault, and
  filing it as `empty_reply` would be the same misfiling the comment at `:2959-2965` was
  written to prevent.

Nothing changes for a normal-role turn: an agent with no blocks and no text is still a skip.

## 6. Detailed Changes

**Backend — `contexts/conversation`**

- `tables.py`: `agent_observations` gains `blocks JSONB NOT NULL DEFAULT '[]'::jsonb`.
- `domain/models.py`: `AgentObservation` gains `blocks: list[dict[str, Any]]`.
- `infrastructure/repositories/observation_repo.py`: `create(...)` accepts `blocks`;
  `_row_to_observation` maps it. No change to `get`/`list`/`mark_released`/`soft_delete`.
- `application/observation_service.py`: `record(...)` accepts `blocks` and passes it through.
  `release()` and `delete()` unchanged — release still reads `content_md` (`:139`).

**Backend — `contexts/activities`**

- `interfaces/facade.py`: three new read-only aggregate methods (§5.2). No domain change, no
  table change, no change to `RecentActivityRow`.
- `application/`: one new query module for the aggregates; repository reads only.

**Backend — `contexts/agents/application/runtime`**

- New `observation_blocks.py`: the block JSON Schema builder (turn-scoped enums), the
  validator, the aggregate calls, and the markdown serialiser.
- New `observer_tools.py`: `build_present_observation_tool(...)`, mirroring
  `activity_tools.py`'s shape — resolves fails-closed, writes to a turn-scoped
  `observation_block_sink`, commits nothing.
- `tool_registry.py`: `present_observation` added to `BUILTIN_TOOL_NAMES` (`:119-135`), which
  `test_builtin_tools_wiring` asserts is complete.
- `builtin_tools.py`: `build_agent_tools` (`:863-873`) gains
  `observation_presentation: ObservationPresentationContext | None = None` and
  `observation_block_sink: list[dict] | None = None`, following the `activity_control` /
  `activation_event_sink` pair exactly.
- `turn_engine.py`: `_builtin_tools` (`:1457-1528`) resolves the presentation context and
  gains an `is_observer` parameter — it takes no role argument today, and `run_turn` is where
  the role is known (`:2428`), so the flag is threaded from there rather than re-read. The
  empty-text guard at `:2958` gains the blocks arm from §5.5. The observer branch
  (`:3026-3036`) drains the sink, validates, serialises and passes both `blocks` and the
  derived `content_md` to `record`.
- `activity_context_provider.py`: `_CONTENT_NOTE` (`:64-67`) becomes conditional. It currently
  tells the model "Text following the first — on a row is what that participant wrote
  themselves: quoted from them, not computed", and it is appended whenever any row carries a
  digest (`:98-99`). Once the example types move to `filled_count_coverage`, their digest is a
  server-computed field list, and the note would vouch for computed text as the participant's
  own words — the exact confusion it exists to prevent. The note is split so a row whose
  digest came from a validator `detail` is described as computed, and only a payload-fallback
  digest is described as the participant's own text.

**Backend — `app/plugins/activity_validators.py`** (Q-4)

- New `filled_count_coverage` validator: same verdict logic as `filled_count`, plus
  `sub_scores["filled_fields"] = [<declared property names that are filled>]` and
  `detail = "<n>/<m> fields answered: a, b, c"` — **field names only, never values**.
  Reuses `_is_filled` (`:68-91`) and both existing config validators. `filled_count` itself
  is not modified.

**Backend — the example course**

- `contexts/activities/infrastructure/examples/courses/creative-thinking.json`: the four
  types move to `"validator_id": "filled_count_coverage"`, `min_filled` unchanged.
- `backend/contexts/agents/infrastructure/examples/packs/creative-thinking-room.json`: AA's
  prompt gains a short section naming the blocks it may use and restating that the computed
  ones are server facts it must not restate as scores. TA and SA are not touched.

**API contract**

- `ObservationOut` gains `blocks: list[dict[str, Any]]`. No new endpoint, no changed request
  model. `pnpm run gen:api` rerun required: **yes**.

**Frontend — `slices/conversation`**

- `types/index.ts`: `Observation.blocks: ObservationBlock[]`, plus a discriminated union of
  the six kinds.
- New `components/observation-blocks/`: one presentational component per kind plus an
  `ObservationBlocks.vue` switch. **None of them uses `v-html`.** A `prose` block can sit at
  any position in the array, so it cannot simply reuse the card's own binding: the card passes
  a **scoped slot** down through `ObservationBlocks`, and renders each prose block's
  `renderMarkdown()` output at `ObservationCard.vue:15-19` — the single allowlisted site
  (`eslint.config.js:322`) — wherever the slot lands. Without this the design contradicts
  itself: a prose block at position 3 would have no legal path to a sanitiser, and AC-14
  forbids widening the allowlist.
- `ObservationCard.vue`: renders `<ObservationBlocks>` when `blocks.length`, else the current
  markdown path. The clamp, the release/delete footer and the release chip are unchanged.
- i18n: new keys under `conversation.observers.blocks.*` and `conversation.observers.basis.*`
  in both `zh-TW.json` and `en.json` (existing group at `zh-TW.json:172-217`).

**Deploy/config** — none.

## 7. NFR Checklist

- **i18n** — every block label, column header, basis sentence and empty state goes through
  `$t()`. Agent-supplied strings (`title`, `caveat`, point text) are data, rendered as text
  nodes; they are never keys and never interpolated into a key. The markdown serialiser emits
  English because it runs with no request locale (§5.3) — recorded, not accidental.
- **Audit log** — nothing new. A block is part of an observation, and observations are
  audited on release and delete only ([R28.11]); the content still never enters audit
  metadata.
- **Tenant isolation** — no new endpoint. The tool resolves through `ConversationFacade` and
  `ActivitiesFacade` with the room id from the turn, and the aggregates are room-scoped in
  SQL. The reachability gate that `activity_tools.py` applies to a type id ([R30.33]) applies
  here too, via the same facade call.
- **Error handling UX** — a malformed block array is rejected at the tool boundary with the
  violation list `schema_violations` already produces (`tool_registry.py:223-227`), the model gets a
  chance to correct it, and a turn whose blocks never validate records prose only. On the
  client, an unknown `kind` renders the block's `title` plus a "cannot display" line rather
  than throwing — a stored row must survive a rollback of the frontend.
- **Performance** — three aggregates per computed block, capped at 12 blocks and at one
  computed block per `(kind, type_key)`, so at most a handful of indexed room-scoped queries
  per observer turn. Observer turns run on `silence_minutes` with a bounded
  `observer_autostop_rounds` ([R28.12]), not per message. `blocks` is capped at 20 KB, well
  under the 50-row page the panel already fetches.

## 8. Security Considerations

This touches an agent/LLM tool surface and renders agent-influenced content in a
creator-only UI.

- **Prompt injection into a block.** Every string in a block is model-authored and the model
  reads participant text. The blocks are rendered as **text nodes**, never as markup, and no
  new `v-html` binding is introduced. The one markdown path (`prose`) is the existing
  `renderMarkdown()` → DOMPurify pipeline in an already-allowlisted file.
- **The computed blocks are the mitigation, not a risk.** A participant can persuade an agent
  to *include* a coverage block; they cannot change a number in it, because the model never
  supplies one.
- **Answer text must not leak into a block.** The computed aggregates return field *names*
  and counts, never payload values. The new validator's `detail` likewise carries field names
  only — and by setting `detail` it stops the raw-payload dump from reaching the agent digest
  at all for these types, so the example's agents see *less* participant text after this
  change than before.
- **Names must not leak into a block.** The aggregates use `_subject_code` and never resolve
  a display name. The legend that exists for the context block ([R30.38]) is deliberately not
  reachable from this path.
- **Cross-room and cross-tenant.** The tool is constructed from the turn's own `chatroom_id`;
  `type_key` is an enum of that room's project's reachable types. There is no id argument a
  model could point at another room.
- **Fail closed.** Presentation-context resolution catches everything and returns `None`, so
  a resolution fault costs the tool, never authorises one — the posture
  `resolve_activity_control` already takes (`activity_tools.py:103-110`).
- **Observer invisibility.** Nothing here emits on the room channel or names the observer to
  a non-creator; `observation.created` still carries ids only ([R28.13]).

## 9. Quality Notes

**Existing debt in touched files** (do not imitate, do not silently fix):

- `ObservationOut` returns `metadata` wholesale (`observations.py:81`), exposing engine
  telemetry keys to the client with no contract. This feature adds a typed column instead of
  widening that. Not fixed here — FU-2.
- `turn_engine._builtin_tools` swallows every assembly exception into "no tools at all"
  (`:1526-1528`). Correct for its purpose, but it means a bug in the new resolver is silent.
  The resolver therefore logs on its own before returning `None`, as `activity_tools` does.
- `ObservationCard.vue:80` clamps on `content_md.length > 600`. With blocks, character count
  is the wrong measure of height. The clamp moves to a block-count/character hybrid — stated
  so the implementer does not carry the old predicate over unexamined.

**Patterns to follow:**

- `activity_tools.py` — the whole file, for tool shape, fail-closed resolution, the sink, and
  the docstring that states why the tool is safe to exist.
- `activity_context_provider.py:146-199` — for how this codebase talks about codes, legends
  and the difference between a window and a record.
- `observation_repo.py` — for room-scoped queries where the room id is part of the AuthZ
  boundary (`:75-77`).
- `useObservations.ts:191-213` — immutable cache patching; in-place mutation of a pushed
  object does not retrigger computeds.

**Reuse inventory:**

- `tool_registry.Tool` / `ToolResult` / `schema_violations` / `clip_tool_output` — do not
  write a second validator or a second output cap.
- `_is_filled` (`app/plugins/activity_validators.py:68-91`) — the new validator reuses it;
  its `False`-is-not-filled rule is load-bearing and documented there.
- `validate_filled_count_config` / `validate_filled_count_against_schema` (`:119-153`) — the
  new validator registers both unchanged.
- `_subject_code` (`activity_context_provider.py:146-147`) — export it rather than restating
  the `[:8]` truncation; two truncations that drift produce codes that do not match the
  agent's own context.
- `renderMarkdown()` (`slices/conversation/utils/renderMarkdown.ts`) — the only sanitised
  markdown path; `prose` uses it, nothing else needs it.
- Shared UI: `STable` for `attempt_table`, `SBadge`, `SDivider`, `SEmptyState`. Design tokens
  only, no raw px or hex — `shared/styles/__tests__/no-raw-style-literals.test.ts` enforces it.
- The activities plugin registry (`slices/activities/plugins/registry.ts`) is a **precedent,
  not a dependency**. Do not import it: gate #1 forbids `conversation` reaching into
  `activities` internals, and the block set is fixed in code, so a runtime registry buys
  nothing.

## 10. Risks and Rollback

- **Migration 0080** adds one nullable-by-default JSONB column with a server default. Forward
  compatible: old code ignores it. Reversible with a `DROP COLUMN`, which loses block data but
  leaves every `content_md` intact — every observation remains readable, because the
  serialisation is stored, not derived on read. This is the main reason §5.3 stores
  `content_md` rather than rendering it from blocks at read time.
- **The example course's `validator_id` change does not reach existing installs.** Install is
  idempotent by key and never updates an existing row; the documented upgrade is to delete the
  types from `/admin/activities` and re-install, which **revokes every project's opt-in and
  mints new type ids** (`docs/examples/creative-thinking-course.md:348-374`). Until a project
  re-enables, its facilitators get a bare 404. AC-12 requires this to be written into the
  example guide before the change lands, and it must be done between classes.
- **A room that upgrades mid-course gets mixed data.** Submissions made before the validator
  change carry no `filled_fields`, so a coverage block computed over a mixed set would
  silently undercount. The aggregate therefore counts only submissions that carry the key and
  reports that denominator; when no submission carries it, the tool refuses the block rather
  than rendering an empty chart.
- **A chart still reads as a score.** The basis label and the submissions-not-people
  denominator are the mitigation, and they are asserted (AC-8, AC-9), not left to review. The
  residual risk is a teacher reading a coverage bar as an achievement measure; that is why
  the block never renders a percentage of a class.
- **The model may ignore the tool.** Acceptable and by design — the turn degrades to prose.

## 11. Acceptance Criteria

- [x] AC-1: An observer turn that calls `present_observation` with a valid block array stores
      those blocks on `agent_observations.blocks` and a markdown serialisation on
      `content_md`; nothing is written to `messages`.
      *`test_observer_agents.py::test_an_observer_turn_records_its_blocks_and_their_serialisation`.*
- [x] AC-2: A normal-role agent's turn is never offered `present_observation`, in any room,
      including one where it holds an activity-control grant.
      *`test_observer_presentation_tools.py::TestResolution::test_a_normal_role_binding_resolves_nothing`
      and `TestAssembly::test_a_normal_role_turn_is_never_offered_the_tool`. The engine half
      asserts `is_observer` is threaded from the role `run_turn` already resolved.*
- [x] AC-3: An observer turn that does not call the tool stores `blocks = []` and
      `content_md = final_text`, byte-identical to today.
      *`test_an_observer_turn_that_never_calls_the_tool_is_byte_identical_to_before`, plus
      `test_create_without_blocks_writes_an_empty_array` at the repository.*
- [x] AC-4: A block array that violates the schema is rejected with a violation list, the
      model may retry within the turn's tool-round cap, and a turn whose blocks never validate
      still records the prose.
      *`test_observation_blocks.py::TestSchema` asserts through `schema_violations`, the same
      function `ToolRegistry.call` runs before `invoke`. Every refusal test asserts the sink is
      left empty, which is what leaves the prose to be recorded.*
- [x] AC-5: `type_key` outside the room's reachable types is unrepresentable — the tool's
      `input_schema` carries an enum, and the invoke path rejects a value not in it.
      *`test_the_type_enum_is_exactly_the_rooms_reachable_set` and
      `test_a_type_key_not_in_the_mapping_is_refused_rather_than_queried`.*
- [x] AC-6: A `field_coverage` / `mandala_grid` / `attempt_table` block's numbers come from
      the server aggregate. A tool call that supplies its own counts is rejected as an
      unknown property.
      *`test_a_computed_block_that_supplies_its_own_numbers_is_rejected`, four shapes. **The
      SQL half runs on CI** — see §17.*
- [x] AC-7: No aggregate output contains a participant display name, a login email, or any
      payload value — only `u:` codes, declared field names, and counts.
      *Unit: `test_no_payload_value_reaches_sub_scores_or_detail`,
      `test_rows_carry_codes_and_the_denominator_is_submissions`,
      `test_the_repository_maps_to_domain_rows_and_owns_the_off_by_one`. **The
      over-real-data half runs on CI** — see §17.*
- [x] AC-8: Every rendered non-prose block shows a basis sentence from the platform's i18n
      catalogue, and every computed block shows a submissions-counted denominator. Neither is
      suppressible by a tool argument.
      *`ObservationBlocks.test.ts`. Both live on the shared block frame, so no computed kind
      can ship without either, and the schema rejects a `basis` on a computed block outright.*
- [x] AC-9: No computed block renders a percentage of participants, a rate, or any label that
      reads as coverage of a class.
      *`test_renders_no_percentage_and_no_participant_denominator`, and the markdown side in
      `test_a_computed_block_renders_a_table_and_a_submissions_denominator`.*
- [x] AC-10: Releasing a block-carrying observation to the room produces the same
      `sender_type=system` message shape as today, with the serialised markdown as the body;
      the release dialog's override still edits plain text.
      *`test_releasing_a_block_carrying_observation_is_the_same_message` and
      `test_a_release_override_replaces_the_serialisation`.*
- [x] AC-11: `filled_count_coverage` produces the same valid/invalid verdict as `filled_count`
      for the same payload and config, adds `filled_fields`, and sets a `detail` containing
      field names and no field values. `filled_count`'s own behaviour is unchanged.
      *`TestFilledCountCoverageValidator`, including a six-case parity table run through both
      scorers.*
- [x] AC-12: `docs/examples/creative-thinking-course.md` documents the new blocks, the
      validator change, and the delete-then-reinstall upgrade path for an existing install.
- [x] AC-13: An observation whose `blocks` contains an unknown `kind` renders its title and a
      "cannot display" line; the panel does not throw and the other blocks still render.
      *`test_renders_an_unknown_kind_as_a_cannot_display_line_without_throwing`.*
- [x] AC-14: `pnpm lint` passes with no new file added to the gate #4 `v-html` allowlist.
      *`eslint.config.js` is not in the task diff; a prose block reaches the card's existing
      binding through a scoped slot.*
- [x] AC-16: An observer turn that calls `present_observation` with valid blocks and then
      produces **no prose** records the observation, with `content_md` set to the blocks'
      serialisation. It is not reported as `observation.skipped`, and no block is lost.
      *`test_blocks_with_no_prose_are_recorded_rather_than_skipped`, mutation-probed against
      the old text-only guard.*
- [x] AC-17: The same turn with `synthesis_failed` also records, keeps `synthesis_meta` on the
      observation, and is not filed as `empty_reply`. An observer turn with neither text nor
      blocks is still a skip, and a normal-role turn's empty-text behaviour is unchanged.
      *`test_blocks_survive_a_failed_synthesis_and_are_marked_not_filed_as_empty`,
      `test_neither_text_nor_blocks_is_still_a_skip`, and D-4's
      `test_blocks_that_render_to_nothing_are_still_an_empty_turn`.*
- [x] AC-18: A row whose digest came from a validator `detail` is not described by the context
      block as the participant's own words; a payload-fallback digest still is.
      *`TestDigestProvenance`, mutation-probed against a single shared marker.*
- [ ] AC-15: The full Definition of Done passes — `pytest -q`, `ruff`, `mypy`, `pnpm test`,
      `pnpm lint`, `pnpm typecheck`, `pnpm build`, `check:openapi-drift`.
      **Unticked pending CI**, and deliberately so. Everything runnable on this host is green:
      the `pytest` unit tier (7397 passed, 6 skipped), `ruff check`, `ruff format --check`,
      `mypy`, `lint-imports`, `pnpm test`, `pnpm lint`, `pnpm typecheck`, `pnpm build`. What
      is left needs infrastructure this host does not have — see §17.

## 12. Test Plan

- **AC-1, AC-3, AC-4, AC-16, AC-17** — unit, `backend/tests/unit/test_observer_agents.py`
  (extends the existing turn-engine observer tests at `:1181`, `:1304`, `:1704`) plus a new
  `test_observation_blocks.py` for the schema and serialiser. AC-16 and AC-17 drive the turn
  with an empty `final_text` in three combinations — blocks only, blocks plus
  `synthesis_failed`, and neither — and assert which of `record` / `observation.skipped` /
  `observation.failed` fires in each. The existing `:1704` failure test pins the normal-role
  half.
- **AC-18** — unit over `activity_context_provider`, with one row whose digest came from a
  validator `detail` and one payload-fallback row in the same block.
- **AC-2, AC-5** — unit, new `test_observer_presentation_tools.py`, mirroring
  `test_activity_control_tools.py`'s structure: assert the tool is absent for a normal-role
  binding, and that the built `input_schema` enum equals the room's reachable set.
- **AC-6, AC-7, AC-11** — unit over the aggregates and the validator, with a fixture whose
  payload values are distinctive strings, asserting none appears in any output. AC-11 is a
  parity test: the same payload through both validators must give the same verdict.
- **AC-6 (SQL)** — the aggregates read `sub_scores` JSONB with PostgreSQL operators, so per
  `backend/CLAUDE.md` they need at least one `pytest.mark.db` test that actually executes
  them. A unit-tier `literal_binds` compile cannot see a parameter-type error here.
- **AC-8, AC-9, AC-13** — component, `frontend/src/slices/conversation/__tests__/`, one spec
  per block component plus an `ObservationBlocks` switch spec. AC-9 asserts the absence of a
  `%` and of any participant-denominator string in the rendered output.
- **AC-10** — unit on `ObservationService.release` (existing coverage at
  `test_observer_agents.py:732-847`) confirming it still reads `content_md`.
- **AC-12** — review of the doc diff.
- **Browser pass** — `frontend:verify` against the compose stack: bind an observer, produce an
  observation carrying each block kind, read the panel at 375px and 1280px in both themes and
  both locales, then release it and confirm the room message. Recorded per surface, not as a
  tick. Note the standing constraint from `2026-08-19-chatroom-scroll-and-composer`'s D-5:
  `fake_provider.py` cannot produce a real agent turn, so the observation rows must be seeded
  directly for the panel half of this pass.

## 13. SRS Delta

To be applied to `REQUIREMENTS.md` §28 on approval. New subsection **28.6 Presentation
blocks**, placed after §28.5:

### 28.6 Presentation blocks

- **[R28.15]** An observation carries an ordered `blocks` array (`agent_observations.blocks`,
  JSONB, default `[]`) alongside `content_md`. The block kinds are fixed in platform code;
  there is no authoring surface for a new kind. An observation with an empty `blocks` array
  renders from `content_md`, which is how every observation recorded before this requirement
  behaves.
- **[R28.16]** An observer-role binding's turn is offered a `present_observation` tool whose
  single argument is a block array validated against a schema built at turn assembly. A
  normal-role binding is never offered it. The last successful call in a turn wins. A turn
  that does not call it records `blocks = []` and the model's text as `content_md`. An
  observer turn that delivers blocks and no prose is **not** an empty turn: the observation is
  recorded with the blocks' serialisation as `content_md`, including when the turn's text
  synthesis failed, and the failure is marked on the observation rather than filed as a benign
  skip. Only a turn producing neither text nor blocks is skipped.
- **[R28.17]** Block kinds split into narrative kinds (`prose`, `key_points`, `timeline`),
  whose text the agent supplies, and computed kinds (`field_coverage`, `mandala_grid`,
  `attempt_table`), whose values the **server** computes at tool-invoke time from
  room-scoped aggregates. A computed block accepts no agent-supplied value; a tool call
  carrying one is rejected. Any `type_key` argument is an enum of the types reachable from
  the room's project ([R30.33]).
- **[R28.18]** A computed block's aggregate returns truncated participant codes, declared
  schema field names and counts only — never a display name, a login email, or any
  submission value. Its denominator is the number of submissions counted, never a
  participant population, and no block renders a participation or coverage rate.
- **[R28.19]** Every non-prose block carries a basis label drawn from a platform-authored
  catalogue, stating what the block rests on and what it cannot mean. The label is not
  suppressible by a tool argument, and it is included in the markdown serialisation stored
  in `content_md`, so it travels with a released observation ([R28.06]).

## 14. Open Questions

- **OQ-1.** The per-field coverage fact is less sensitive than payload text, yet it rides the
  same `expose_payload_to_agent` / platform-policy gate
  (`activity_context_provider.py:124-143`). An admin locking that policy for consent reasons
  also removes the field names. Correct-by-default and deliberately not relaxed here;
  separating the two gates is a policy change of its own.
- **OQ-2.** `content_md` is stored rather than derived, so editing the serialiser does not
  retroactively change existing rows. Intended (§10), but it means two rows written by
  different serialiser versions can render differently in the room while rendering identically
  in the panel.

## 15. Deviation Log

- **D-1. AC-18 needed a fact §6 said would not exist, so `RecentActivityRow` gained one
  field.** §6 states "no change to `RecentActivityRow`", and AC-18 requires the context block
  to tell a validator-`detail` digest from a payload-fallback one per row. No such
  distinction is stored: `activity_submissions.agent_digest` is one `TEXT` column written
  either way (`submission_service.py:182`, `:284`). The row gains
  `digest_is_computed: bool`, **derived** in `list_recent_for_room` by rebuilding the
  deterministic fallback and comparing, not stored. Derivation was chosen over a column
  because it is also correct for every row written before the distinction existed, which a
  backfilled column could not be — and the wrong guess is the unsafe direction, since it
  would let the block vouch for computed text as the participant's own words. The payload is
  read inside the repository to answer the question and dropped; nothing about it reaches the
  read model, so the non-goal's actual intent (do not widen the agent-visible surface) holds.

- **D-2. The computed digest took a new row marker, `::`, and all four shipped prompts moved
  with it.** §6 scoped the prompt edit to AA and said "TA and SA are not touched". Splitting
  `_CONTENT_NOTE` in place would have left a note saying "some rows are computed" with nothing
  marking which, and — worse — TA's, SA's and DA's prompts all state the em-dash rule verbatim
  ("破折號後面的文字是學生自己寫的作答"), which becomes false for these four types on every row
  once they adopt `filled_count_coverage`. The em dash keeps its shipped meaning and the
  computed case takes `::`, so each prompt's existing sentence stays true and gains one more.
  DA is included because it drafts TA and SA prompt text: leaving it out would ship a designer
  that writes the stale rule into every new unit's prompt.

- **D-3. Two read models and two pure helpers landed in `domain/`, not `application/`.** §6
  says the activities change is "three new read-only aggregate methods… no domain change".
  The read models (`FieldCoverage`, `MandalaGrid`, `AttemptSummary` and their rows) sit beside
  `RecentActivityRow` and `ActivityAggregate`, which is where this context already keeps read
  models. `subject_code` and `agent_digest` moved there too, from `application/`: the
  repository needs both — one to build an attempt row, one to answer D-1's question — and an
  infrastructure module importing an application one is an upward dependency. Both are pure
  and framework-free, so `domain/` is where they belong. `lint-imports` passes; it could not
  have caught the original direction, since its contracts enforce domain purity only.

- **D-4. §5.5's "non-empty by construction" was not true, and is now true by checking.** The
  spec argues the blocks serialise to a non-empty `content_md` by construction, so the widened
  empty-turn guard cannot persist an empty observation. It can: `minLength: 1` accepts a
  single space and `schema_violations` strips `pattern` outright (`tool_registry.py:178`), so
  a whitespace-only `prose` block is schema-valid and serialises to `""`. With no prose in the
  turn either, the observer branch recorded an empty observation. The tool now refuses such an
  array with a reason the model can act on, and the engine's guard tests the **serialisation**
  rather than the sink, so the invariant holds either way.

- **D-5. `_observer_memory_block` had to learn that a body is multi-line.** Q-1 names the
  observer's own memory window ([R28.05]) as a consumer of the serialisation, and that window
  renders one observation per `- (timestamp)` line. A serialised block array is a whole
  markdown document, so its own `- ` lines read as new memory entries and its headings land at
  the top level of the system prompt. Continuation lines are indented under their entry now.
  The defect predates this task — a model could always write a multi-line reply — but this
  change makes multi-line the normal case rather than the occasional one.

- **D-7. Only three of the four example types adopted the coverage validator, and the two
  unit-2 prompts split.** §6 moves all four; a post-build `/code-review` found that doing so
  silently reverses `2026-08-24-example-agents-quote-unit-two`, which is `implemented` and
  whose entire purpose was making unit-2 answers quotable. `filled_count_coverage` always
  sets a `detail`, and `build_agent_digest` prefers `detail` over the payload dump, so every
  adopting type stops putting any student writing in front of any agent. Q-4 called that "a
  privacy improvement" without noticing the collision. The user chose the split:

  | Key | Validator | Why |
  |---|---|---|
  | `mandala-9grid` | `filled_count_coverage` | The course's only nine-field type, so the only possible subject of a `mandala_grid` block. Without it that kind ships dead. |
  | `time-traveler-next-steps` | `filled_count` | One declared field: coverage could only ever read `1/1 fields answered`, so adopting it would cost the answer text for nothing. |
  | `emotion-desk-three-emotions` | `filled_count_coverage` | Never quotable, so replacing the dump with field names removes text no agent was allowed to use. |
  | `six-hats-emotion-desk` | `filled_count_coverage` | As above. |

  The residual cost is real and is stated rather than hidden: TA and SA can no longer quote
  or build on a mandala cell. All three room prompts now say they cannot see that type's
  content, know only which cells were filled, and should hand the question back to the
  student — a *third* case beside quotable and unquotable, because an agent that treats "I
  may not quote this" and "I cannot see this" as the same rule answers with a fabrication
  rather than a refusal. The guide's dry-run checklist gains an item for exactly that.

- **D-6. The authoring form's `min_filled` sub-form covers both validator ids.** Not in §6 at
  all. `GET /api/activity-validators` lists every registered validator, so
  `filled_count_coverage` appears in the picker the moment it registers; the form's sub-form
  was gated on the literal `filled_count`, so selecting the new one produced a config with no
  `min_filled` and the backend refused every save. Shipping a picker entry whose only legal
  config the form cannot produce is a defect this change would have introduced.

## 16. Follow-ups

- **FU-1.** `docs/tasks/2026-07-19-large-artifacts-silently-dropped/spec.md` is
  `status: in-progress` while its Deviation Log is filled in and its close-out review is
  committed (`4323256`, `f0733fc`). The user confirmed on 2026-08-24 that it is complete.
  Its frontmatter and its `BOARD.md` row want updating by whoever owns it.
- **FU-2.** `ObservationOut.metadata` is returned to the client wholesale
  (`observations.py:81`), publishing engine telemetry with no contract. Worth a typed
  projection.
- **FU-3.** `mandala_grid` is a 3x3 special case of `field_coverage` distinguished only by
  layout. If a second positional layout ever ships, the two want a shared "coverage with a
  layout hint" shape rather than a third kind.
- **FU-4.** `filled_count` and `filled_count_coverage` will differ only in their extra
  outputs. If the platform ever decides the coverage fact is universally wanted, they should
  merge — but that is the platform change this task was explicitly scoped away from (Q-4).

- **FU-5.** `observation_blocks.py` is ~680 lines carrying the schema builder, the
  materialiser and the serialiser. That is past the file-size calibration and it was a
  deliberate call: adding a seventh block kind means editing all three, and keeping them
  adjacent is what makes an omission obvious. Worth revisiting if a second layout hint
  arrives (see FU-3), which is the change that would split them along a real seam.

- **FU-6.** `ObservationAggregateService` instantiates `ActivitySubmissionRepository`
  directly, as `AggregationService` does two files over. Consistent with the context and
  wrong by dimension 7; it belongs to whoever inverts that dependency for the context as a
  whole, not to one new service.

- **FU-7.** The intermittent `AgentToolsView.test.ts` CodeMirror failure reproduced once
  during a full `pnpm test` on this host and passed in isolation immediately after. It is
  the same failure `2026-08-22-visual-refinement-phase3-verification-and-debt` recorded as
  host-wide thin headroom rather than a file-specific timeout, and it is untouched by this
  task.

- **FU-8.** `resolve_observation_presentation` offers every type reachable from the room's
  **project**, not only those ever activated in this room. No data crosses: the aggregates
  are room-scoped, so an unused type yields nothing and the tool refuses the block. But the
  enum names worksheets the room has never run, which is noise in the tool description. A
  room-scoped type list would be a second query per turn; worth it only if the enum gets long.

## 17. What has not been verified on this host

Recorded rather than closed by reasoning. Each item below has a written test that runs, or a
command that exists; none of them has been *executed*, and the reason is the same in every
case: this host has no Docker daemon, so the compose network the `db`, `integration` and
`wiring` tiers bind to does not exist (`get_settings().database.dsn` resolves to the compose
hostname `postgres`, which does not answer here).

- **The `db` tier.** `backend/tests/integration/test_observation_aggregates_db.py` — 10 tests
  covering exactly what the unit tier structurally cannot see (`backend/CLAUDE.md`): that
  `@>` is **false** rather than an error for a submission carrying no `filled_fields`, which
  is the mid-course upgrade case the whole design turns on; that window functions are
  evaluated before `DISTINCT ON`, so each subject's `attempts` covers their whole set; and
  that the `jsonb` cast of a bound text parameter resolves at all. It collects cleanly. AC-6
  and AC-7 are ticked on their unit halves; this is the other half.
- **`alembic upgrade head` and its downgrade.** Migration 0080 adds one JSONB column with a
  server default and drops it on the way down. The chain is linear (only 0080 revises 0079)
  and `test_migration_autocommit_ordering.py` passes, but neither direction has been run
  against a real database.
- **`pnpm check:openapi-drift`.** A bash script, and this host is PowerShell — the same
  blocker three earlier dossiers recorded for `check:bundle-size`, `check:type-coverage` and
  `check:boundaries-enforced`. Its two steps *were* run by hand (`python -m
  scripts.export_openapi` into `backend/openapi.json`, then `pnpm run gen:api`), and the only
  drift was the intended `ObservationOut.blocks`; both outputs are committed. That is not the
  same as the gate going green.
- **The browser pass** (§12's last item). Not performed. Note the standing constraint from
  `2026-08-19-chatroom-scroll-and-composer`'s D-5: `fake_provider.py` cannot produce a real
  agent turn, so the panel half needs observation rows seeded directly.
