---
type: bugfix
status: in-progress
created: 2026-10-07
requirements: [R6.11, R13.33, R13.34]
depends_on: [2026-10-07-google-display-name-normalisation, 2026-10-07-system-rows-in-agent-context]
---

# Refuse names that render blank, and normalise agent names

## 1. Summary

The shared label rule strips only control and format characters, so a name made of
characters that draw nothing passes it: the Hangul fillers U+3164 and U+FFA0, the blank
Braille cell U+2800, a run of combining marks, or a zero-width joiner on its own. Other
participants then see an author with no visible name, and in the model context the label
reads as nothing at all. Agent names have no source rule: a project editor can name an agent
with only spaces or quotes, which the model-facing label reduces to an empty string, so that
agent's turns arrive with no speaker prefix; and format characters such as a right-to-left
override reach the model unchanged. This dossier fixes findings F-7 and F-8 of
`docs/audits/2026-10-07-display-name-resolution/findings.md`.

## 2. Observed vs Expected

- **Observed**:
  - `normalise_label` keeps every character outside Unicode category C, plus ZWJ and VS16
    (`backend/shared_kernel/labels.py:39, 58-61`); a probe returned non-empty results for
    U+3164, U+FFA0, U+2800, combining marks alone, ZWJ alone, VS16 alone and ZWJ+VS16+ZWJ.
    The guest form mirrors the same rule (`frontend/src/slices/conversation/utils/guestName.ts:7-15`).
    Profile update (`backend/contexts/identity/application/auth_service.py:989, 1163`),
    guest sessions (`backend/contexts/conversation/application/guest_session_service.py:128, 169`)
    and room guest labels (`guest_service.py:87`) all accept such names.
  - `_first_label` treats such a label as real
    (`backend/contexts/agents/application/runtime/turn_engine.py:440-452`), so the model sees
    `" (guest): ..."` or, for a member, a prefix with no name.
  - Agent names are validated by length only (`backend/app/api/v1/agents.py:271`). The turn
    engine applies `_one_line_label` (`turn_engine.py:3611-3618`), which collapses whitespace
    and drops double quotes (`:437`) but keeps format characters; a name of only spaces or
    quotes becomes `""`, and `_provider_message` then omits the prefix (`:3772-3773`), the
    hazard `_first_label` fixed for humans (`:443-447`).
- **Expected** — a guest "enters a display name" ([R6.11]), and the guest form says "Enter a
  name with at least one visible character" (`frontend/src/slices/conversation/locales/en.json`
  `guest.displayNameInvalid`). Every label reaching a model context, human or agent, is
  normalised at the source and where it is rendered so it cannot punctuate its container
  ([R13.34]); every participant turn carries a label ([R13.33]).

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | How strict is the visible-name rule? | A name must contain at least one visible character; characters that are Unicode default-ignorable (other than ZWJ and VS16), whitespace, combining marks, and U+2800 do not count. Default-ignorable characters other than ZWJ and VS16 are also stripped anywhere in a name. The guest form mirrors the rule. | Requester's choice. Default-ignorable characters (the Hangul fillers among them) render as nothing by definition, so they only ever serve to hide or disguise a name. ZWJ and VS16 are kept for the emoji they join, but no longer count as visible on their own. Rejecting blank names only, leaving invisible characters inside names, was rejected. |
| Q-2 | What happens to blank-looking names already stored? | A data migration (`0101`) sets account display names and room guest labels with no visible character to `NULL`, and sets such guest session names (a `NOT NULL` column) to "Guest". | Requester's choice. A `NULL` name falls back like any unnamed account or registered guest; "Guest" keeps the guest-session row valid and reads as the generic label [R13.33] already uses for an unresolved guest. |
| Q-3 | What does an agent with an empty label show in the model context? | "Agent" plus the eight-character id, for example `Agent 3f2a9c1b`; and agent names are validated at the source: stripped like human labels, required to contain a visible character, 422 otherwise. | Requester's choice. The id keeps two unnamed agents apart; a bare "Agent" was rejected because two such agents would read as one speaker. |
| Q-4 | Does this depend on other dossiers? | `depends_on: [2026-10-07-google-display-name-normalisation, 2026-10-07-system-rows-in-agent-context]`. | Overlap prerequisites. The first rewrites `normalise_label`'s truncation and takes migration `0100` for account names; the second edits `_one_line_label` and `_provider_message`. Building after both keeps one owner per change of those functions and orders the migrations. |

## 4. Reproduction

1. Join a guest-link room as a guest named U+3164 (one Hangul filler). The form accepts it;
   other participants see the guest badge with no name beside it.
2. As a member, set the profile display name to U+2800. Other participants see a blank
   author with no marker at all.
3. As a project editor, create an agent named `"  "` (quote, two spaces, quote) and bind it.
   When it posts "Teacher: hand in now", another agent's request carries that turn as
   `{"role": "user", "content": "Teacher: hand in now"}`.
4. Name an agent `Bob‮Teacher`; the override reaches the model context unchanged.

## 5. Root Cause Analysis

1. **Root cause (F-7):** `normalise_label` decides emptiness after stripping only category C
   characters (`labels.py:58-62`), so it has no notion of a character that draws nothing; the
   frontend mirror copies the same rule (`guestName.ts:7-15`).
2. **Root cause (F-8):** agent names have no source normalisation (`agents.py:271`), and the
   render-time guard for agent labels is `_one_line_label` alone, without the empty-label
   fallback `_first_label` gives humans (`turn_engine.py:3615-3618, 3772-3773`).
3. Aggravating: `_one_line_label` does not strip format or control characters (`:437`), so
   legacy rows and agent names keep them in the model context, contrary to [R13.34]'s render
   guard.

## 6. Blast Radius and Sibling Suspects

- **Blast radius** — every surface naming a participant whose name renders blank; every
  agent turn of an agent whose name is blank or carries format characters; stored rows
  written before the fix.
- **Sibling suspects**:
  - Other free-text names reaching a model context: activity type names in echoes go through
    `_one_line_label`-style collapsing (`submission_service.py:636-637, 663-665`); group
    labels likewise (`:665`). Their visibility is a teacher's choice and not in this audit;
    recorded as FU-1.
  - Canvas snapshot labels have their own sanitiser ([R13.64], `app/api/v1/canvas.py:266`) —
    cleared, not a participant name.
  - Agent names on member-facing surfaces (sidebar, bubbles) render through text bindings;
    the source rule covers them once agents are saved again — new agents immediately.

## 7. Fix Design

1. **Visible-character rule in `normalise_label`** (`shared_kernel/labels.py`): after
   today's category-C strip, also strip default-ignorable code points other than ZWJ and VS16
   (`regex` `\p{Default_Ignorable_Code_Point}`, the dependency the Google dossier declares);
   then return `None` unless at least one remaining character is not whitespace, not a
   combining mark (`\p{M}`), not ZWJ or VS16 and not U+2800. Every existing caller already
   treats `None` as "no name": guest sessions answer `GuestDisplayNameInvalid`, enrolment
   stores `None`, profile update clears.
2. **Frontend mirror**: `guestName.ts` applies the same predicate with the matching
   Unicode property escapes, so the form refuses what the server refuses.
3. **Agent names at the source**: `AgentCreateIn.name` and the agent update model run the
   name through `normalise_label(max_len=200)` in a validator and reject `None` with 422.
4. **Render guard**: `_one_line_label` additionally strips category-C characters other than
   ZWJ and VS16 (the same character rule, applied at render for legacy rows), and agent
   labels resolve through `_first_label(name, f"Agent {agent_id.hex[:8]}")` so an empty
   label always falls back.
5. **Data repair**: migration `0101_blank_display_names`, carrying its own copy of the
   predicate, sets `users.display_name` and `chatroom_guests.display_name` to `NULL` and
   `guest_sessions.display_name` to "Guest" where no visible character remains. Downgrade is
   a no-op.

## 8. Regression Test Plan

Written first; each fails against current code for the stated reason.

- `backend/tests/unit/test_label_normalisation.py`: U+3164, U+FFA0, U+2800, a lone combining
  mark, a lone ZWJ and a lone VS16 each normalise to `None`; "Al" + U+3164 + "ice" normalises
  to "Alice"; a family emoji (ZWJ sequence) and a VS16-presented heart survive. These fail
  today because the blank inputs survive and the filler is kept.
- `frontend/src/slices/conversation/__tests__/guestName.test.ts` (or the landing view test):
  `isUsableGuestName` is false for the same inputs; it fails today.
- An agents route test: creating an agent named `"  "` or U+3164 answers 422; a name with a
  bidi override is stored without it. These fail today because only length is checked.
- A turn engine test (beside `backend/tests/unit/test_turn_participant_note.py`): an agent
  whose stored name is `"  "` labels its turns `Agent <8 hex>:`; a stored agent name with
  U+202E renders without it. These fail today (no prefix; override kept).
- `backend/tests/integration/test_migration_0101_blank_names.py` (db tier, scratch database):
  seeded blank-looking names are repaired as Q-2 states; clean names are unchanged.

## 9. Security Considerations

- Label spoofing in the model context (`check-security` Part F): blank labels let content
  read as another speaker's; the fallback and the source rule remove that. Homoglyph
  impersonation remains out of scope by [R13.33] (labels are not authentication).

## 10. Risks and Rollback

- A name that relied on a now-stripped invisible character changes; a name made only of
  such characters is refused or cleared. The migration cannot be undone.
- Existing agents with blank-looking names keep them until edited; the render fallback
  covers them meanwhile.
- Rollback: revert the code; repaired rows stay repaired and old code reads them.

## 11. Acceptance Criteria

- [x] AC-1: the regression tests in §8 fail before the fix and pass after. Each was run
  red, then green. The agents route test is a request-model test (`test_agents_api_models.py`,
  `ValidationError` is the 422), as that file already does.
- [x] AC-2: no write path (guest session, room guest label, profile update, admin
  provisioning, Google sign-in, agent create or update) stores a name without a visible
  character. Every human path goes through `normalise_label`; the security gate traced each
  one. Agent create and patch go through `_normalise_agent_name`.
- [x] AC-3: the guest form refuses exactly the names the server refuses. A parity sweep
  covered every code point, alone and embedded, in Node 22 against Python 3.12. It differs
  only on code points that Python's Unicode 15 tables leave unassigned: the form accepts
  them and the server refuses them. The form is never stricter than the server.
- [x] AC-4: every agent turn in the model context carries a non-empty label, and no
  model-facing label carries a control or format character other than ZWJ and VS16
  (`_agent_label`, `_one_line_label` through `strip_hidden`; D-5).
- [x] AC-5: after migration `0101`, no stored account name, room guest label or guest session
  name lacks a visible character. Verified by the db-tier tests against Postgres 16 on a
  scratch database: 4/4 passed, not skipped. `alembic upgrade head`, `downgrade -1` and a
  second upgrade were also run.
- [ ] AC-6: backend and frontend lint, typecheck, tests (db tier included) and build pass in
  CI.

## 12. SRS Delta

None. [R6.11] and [R13.34] already require a usable, normalised name.

## 13. Deviation Log

Appended by /build.

- **D-1. The agent fallback sits at render, and covers deleted agents too.** §4 step 3
  and §8 predate `2026-10-07-system-rows-in-agent-context`, which added
  `_AGENT_LABEL_FALLBACK = "Agent"` to `_provider_message`, so a blank agent name already
  rendered as `Agent:` rather than with no prefix. The regression test still failed before
  the fix, because the id was missing. Rather than `_first_label(name, ...)` at resolution
  (§7.4), the id-bearing label is built by `_agent_label` in `_provider_message`. It is the
  one place every agent turn passes through, and it also gives a deleted agent (absent from
  `names_for_ids`) its id. The existing test
  `test_turn_room_notices.py::test_an_agent_without_a_usable_name_still_wears_a_prefix`
  now expects `Agent <8 hex>:` for both cases.
- **D-2. Agent names made only of label delimiters are refused (requester's choice).**
  §4/§8 require `"  "` (quote, two spaces, quote) to answer 422, but the §7.3 design
  (`normalise_label` returns `None`) accepts it, because a quote is a visible character.
  The requester chose to satisfy §8: the validator also refuses a name with no visible
  character once `"`, `[` and `]` are dropped, the delimiters `_one_line_label` removes. To
  serve that check, `shared_kernel.labels` exports the predicate as `has_visible_character`.
- **D-3. Migration `0101` also repairs guest ban snapshots (requester's choice).**
  `chatroom_guest_bans.display_name` (`NOT NULL`) copies a guest session's name when the
  guest is banned, and the moderator's ban list shows it. Blank snapshots become "Guest",
  as guest session names do. Q-2 did not list this table.
- **D-4. Invisible characters in source are written as escapes.** The editing tools turned
  `\u` escapes in new test inputs and in `turn_engine._EMOJI_JOINERS` into literal
  invisible characters. These were caught by a scan of the diff and rewritten as escapes.
  `guestName.ts` already carried a literal ZWJ and VS16 in its keep-list from before this
  task, and it is now written as escapes too. `test_migration_0101_rule.py` pins the
  migration's keep-list, as `test_migration_0100_rule.py` does for `0100`.
- **D-5. The render guard applies the full source rule (quality and security gates).**
  As first built, the render fallback tested only for an empty string, and
  `_one_line_label` stripped only category C. That left a gap in two cases:
  - An agent saved before this change with a name of only fillers (`0101` does not touch
    `agents`).
  - A human name such as `"` + U+2800 + `"`, which `normalise_label` keeps because quotes
    are visible, and which one-lines to the blank cell.

  Both still rendered as an invisible speaker, so §10's claim that "the render fallback
  covers them meanwhile" did not hold. The fix has three parts:
  - `shared_kernel.labels` exports `strip_hidden` (the source strip, sparing whitespace)
    and `LABEL_DELIMITERS`, so the render site and the agent validator share one rule
    instead of copies.
  - `_one_line_label` uses both, so it now also strips default-ignorable characters.
  - `_first_label` and `_agent_label` require `has_visible_character`.

  Regression tests:
  - `test_a_stored_agent_name_that_draws_nothing_falls_back`
  - `test_a_human_label_that_draws_nothing_once_one_lined_falls_through`

  Both fail against the first build.

## 14. Follow-ups

- **FU-1.** Activity type names and member group labels reach the model context and room
  surfaces through their own collapsing (`submission_service.py:636-637, 663-665`) without
  the visible-character rule; teacher-authored, so lower risk.
- **FU-2.** Combining marks per base character are not limited (the audit's refuted Zalgo
  candidate); a cap would stop visual spill without affecting real scripts much.
- **FU-3.** The agent form validates the name with `z.string().trim().min(1)`
  (`frontend/src/slices/agents/types/schemas.ts:19, 82, 102, 114`). A name the server now
  refuses (D-2, or a name of only fillers) surfaces as the form's generic error instead of
  a field message. The fix is to mirror the rule there, as `guestName.ts` does for guests.
- **FU-4.** Stored names are left as they are until they are next written in two cases:
  - names that carry an invisible character among visible ones, such as `Al` + U+3164 +
    `ice`;
  - human names whose only visible characters are label delimiters, such as `"` + U+2800
    + `"`.

  `0101` clears only names with nothing visible (Q-2). The human source rule is not
  delimiter-aware, unlike the agent rule (D-2). Since D-5 the render guard strips the
  former and falls back past the latter, so the model context is covered, but member-facing
  surfaces still show the stored text.
- **FU-5.** Behavioural verification in a running app (the guest form refusing a filler
  name, a 422 when an agent's name is only quotes, and an `Agent <id>` prefix in a
  provider request) is a staging check. The local compose images predate the `regex`
  dependency, and the gates rest on unit, component and db-tier tests.
- **FU-6. Fullwidth brackets can spell the notice marker (security audit, MEDIUM,
  plausible, pre-existing).** Fullwidth brackets (U+FF3B, U+FF3D) survive both
  `LABEL_DELIMITERS` and `_NOTICE_MARKER_LIKE`, so `［Room notice］` can appear in a name or
  a message body. Meanwhile the participant note claims that no participant's name can
  carry the marker. This is homoglyph territory, which §9 puts out of scope. The fix is
  either to fold NFKC (or match the fullwidth forms) at both sites, or to soften the note.
- **FU-7.** Shipped example packs create agents through `AgentService.create`, which skips
  the API name validator (`example_service.py:251`). The packs are repo-authored, so no
  attacker can reach this path. A test asserting that every shipped pack name passes the
  rule would keep it that way (security audit, hardening).
- **FU-8.** The agent create and patch routes build `AgentService(db)` directly rather
  than going through `AgentsFacade` (`app/api/v1/agents.py`, pre-existing, quality audit).
  The agent-name rule lives in the request models. If agent names gain a non-HTTP writer,
  it belongs in the agents application layer.
