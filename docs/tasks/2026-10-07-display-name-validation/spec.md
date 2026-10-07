---
type: bugfix
status: approved
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

- [ ] AC-1: the regression tests in §8 fail before the fix and pass after.
- [ ] AC-2: no write path (guest session, room guest label, profile update, admin
  provisioning, Google sign-in, agent create or update) stores a name without a visible
  character.
- [ ] AC-3: the guest form refuses exactly the names the server refuses.
- [ ] AC-4: every agent turn in the model context carries a non-empty label, and no
  model-facing label carries a control or format character other than ZWJ and VS16.
- [ ] AC-5: after migration `0101`, no stored account name, room guest label or guest session
  name lacks a visible character.
- [ ] AC-6: backend and frontend lint, typecheck, tests (db tier included) and build pass in
  CI.

## 12. SRS Delta

None. [R6.11] and [R13.34] already require a usable, normalised name.

## 13. Deviation Log

Appended by /build.

## 14. Follow-ups

- **FU-1.** Activity type names and member group labels reach the model context and room
  surfaces through their own collapsing (`submission_service.py:636-637, 663-665`) without
  the visible-character rule; teacher-authored, so lower risk.
- **FU-2.** Combining marks per base character are not limited (the audit's refuted Zalgo
  candidate); a cap would stop visual spill without affecting real scripts much.
