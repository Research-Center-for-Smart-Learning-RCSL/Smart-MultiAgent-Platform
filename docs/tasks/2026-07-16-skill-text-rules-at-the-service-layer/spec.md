---
type: bugfix
status: approved
created: 2026-07-16
revised: 2026-07-19
requirements: [R31.01]
---

# The skill charset rule guards the request, not the write — so `copy` launders text across scopes

## 1. Summary

Split from `2026-07-16-agent-skills`' FU-26, which the 2026-07-17 verification sweep confirmed on
both halves and identified as the only clean bugfix in that dossier's audit-debt set.

`text_rejection_reason` (`contexts/skills/domain/text_rules.py:111`) — the rule that keeps control
characters, bidi overrides, zero-width joiners, and the untrusted-content frame's own delimiters out
of every string a model reads — is called from **two writers, each of which remembered to call it on
its own**: `_validate_text` (`app/api/v1/skills.py:93-97`), wired into Pydantic field validators, and
`_reject` (`skill_md.py:533`), wired into the bundle frontmatter parser (`:439`, `:450`, `:454`,
`:459`, `:469-476`). It is called from **no shared layer**. `SkillService._insert` and `update`
accept any string, so the rule holds exactly where a writer's author happened to think of it.

`SkillService.copy` (`skill_service.py:355-370`) is the path where that distinction bites. Its
request model, `SkillCopyIn` (`app/api/v1/skills.py:148-161`), carries exactly three fields —
`target_scope`, `target_owner_id`, `name` — and validates `name` correctly. `description`,
`requires`, `allowed_tools`, and `extra_frontmatter` come from the **source row** (`:359-363`) and
are never revalidated. The Pydantic boundary is not failing: it validates its input faithfully.
`copy`'s real input is not the request.

No live attack today — every stored description was written under the current rule, by one validator
or the other, so there are no stale bytes to launder. It becomes a real laundering primitive the
moment the rule tightens or a writer forgets, and **the rule just tightened** (D-22 replaced an
enumeration of 16 codepoints with a category rule after the enumeration was found to admit the entire
Unicode Tag block).

**Revised 2026-07-19 — the second writer already landed.** The original draft argued from a bundle
importer that "does not exist yet". It exists and is live: `BundleService.import_bundle`
(`bundle_service.py:495`, writing at `:519`) behind four endpoints (`skills.py:958`, `:1151`,
`:1331`, `:1521`) and the arq task `skill_import_bundle` (`app/workers/tasks/skills.py:199`,
registered `app/workers/main.py:293`). This does not weaken the case; it sharpens it in two ways.

*The funnel assumption is now verified rather than assumed.* `bundle_service.py:492` binds
`self._skills` to a `SkillService`, not a repository, so the import write at `:519` is
`SkillService.create` → `_insert` (`skill_service.py:102` → `:118`). One gate at `_insert` covers
create, copy, and import.

*The gap is now demonstrable rather than predicted.* Two independent writers each carry their own
copy of the same rule, and they have **already diverged**: a tool name is capped at 200 characters
through the API (`skills.py:74`, `:104`) and at 1024 through the importer (`skill_md.py:450`). That
is §9's first risk, realised before the fix that was supposed to prevent it. The argument for a
shared layer is no longer "a stranger will write the next one" — it is "the two that exist already
disagree".

## 2. Observed vs Expected

**Observed.** Three services accept unvalidated text:

- `_insert` (`skill_service.py:118-158`) takes `description: str` (`:124`) and passes it to
  `self._skills.create(description=description, ...)` (`:149`). The only check is a name collision
  (`:138-139`).
- `update` (`:160-212`) assigns `values["description"] = draft.description` (`:177-178`) with only an
  index-budget check behind it (`:192-210`).
- `copy` (`:355-370`) hands `source_skill.description` (`:359`) straight to `_insert`, along with
  `body` (`:360`), `requires` (`:361`), `allowed_tools` (`:362`), and `extra_frontmatter` (`:363`).

**Expected — the code's own documentation, in two places.**

`text_rules.py:112-116` describes a reason-not-raise contract so "each caller renders it in its own
idiom — a 422 from a Pydantic validator at the API, a `BundleInvalid` naming the key at import".
Both idioms now exist and both are correct. What the module header claims on top of that
(`text_rules.py:3-5`) is stronger and is the part that is false: "the same three entry points for
each: create, update, and — when bundles land — import. **A rule enforced at only some entry points
is not a rule.**" Import enforces it; create and update enforce it only when the caller is an HTTP
request. `copy` is neither, and enforces nothing.

`index_builder.py:30-33` is the sharper contradiction, because it is a *security* claim the frame
depends on: the delimiters are "rejected inside author-controlled text at **every entry point**, so
a hostile description cannot close the frame early and have the rest of itself read as trusted
instruction". `copy` is an entry point and does not reject. Note `index_builder` imports
`contains_delimiter` (`:26`) and **re-exports it without ever calling it** (`:81`) — the re-export
exists so "the frame and the rule that protects it cannot drift apart", which means `render_index`
has no defence of its own and the entry-point claim is load-bearing rather than belt-and-braces.

`2026-07-16-agent-skills` §8 (`spec.md:861`) likewise promises "Q-31(b)'s charset rules at **both
entry points**".

`[R31.01]` (`REQUIREMENTS.md:1560`) states the rule for `description` without saying where it is
enforced — which is why this is a bugfix against documented intent rather than a feature.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Which fields? | **`name`, `description`, `requires[]`, `allowed_tools[]` — and explicitly not `body`.** | That is exactly the set the API validates (`skills.py:98-117`, `:137-145`) and the set `test_skill_sanitization.py`'s docstring names as the matrix. `body` is bounded only by `max_length=_MAX_BODY` (`:94`) **by design**: it is multi-line markdown and `text_rejection_reason` rejects newlines, so applying it would reject every real skill. `body` never enters the index; it is served one at a time through `read_skill`. Adding it here would be a different rule, not this one. |
| Q-2 | What error? | A new `SkillTextRejected(SkillError)` carrying `field` and `reason` → `("skills/text-rejected", 422, ...)` in `interfaces/error_mapping.py`, with `_extras` returning `{"field", "reason"}`. | Follows the context's established shape exactly (`error_mapping.py:15-46`, `_extras:49-77`). 422 matches what the Pydantic path already returns for the same rejection, so the two entry points agree. The reason string already names the offending codepoint (`text_rules.py`), which is the only actionable handle for a character invisible in the editor that produced it. |
| Q-3 | Keep the Pydantic validators, or delegate to the service? | **Keep both.** | They do different jobs. Pydantic produces a per-field 422 with a `loc`, which is what the form needs; the service is the backstop for every writer that is not an HTTP request — `copy` today, the importer tomorrow. For `create`/`update` the API rejects first and the service never sees bad text, so the double check costs one function call and no user-visible change. Deleting the API validators to avoid duplication would trade a good error for a generic one. |
| Q-4 | What should `copy` do with a source row that fails the *current* rule? | **422 naming the field and the reason.** Do not sanitise. | Silently rewriting a user's stored text is a worse failure than refusing: it mutates content the author never reviewed and hides that a rule changed. There are no such rows today (§1), so this is a rule for the future — and the future case is exactly the one where a human should look. |
| Q-5 | Does this need the index-budget check to move too? | **No.** | `assert_index_fits` (`binding_service.py:264-273`) already runs in the service for `update`. Only the charset rule is boundary-only. Scope stays at one rule. |
| Q-6 *(added 2026-07-19)* | The gate needs a `max_chars` per field, and the two existing writers disagree — tool names are capped at 200 through the API (`skills.py:74`) and 1024 through the importer (`skill_md.py:450`). Which cap does the service use, and what happens to the other two? | **Define `MAX_NAME_CHARS = 64` and `MAX_TOOL_NAME_CHARS = 200` in `domain/models.py` beside `MAX_DESCRIPTION_CHARS`. All three writers use them: the new gate, `app/api/v1/skills.py`, and `skill_md.py`.** One number, three call sites, no literals. | **The gate forces this decision; it cannot be deferred.** Whatever cap `_insert` takes becomes the *effective* cap for every writer, because every writer goes through it — so choosing 200 for the gate already tightens the live importer from 1024 to 200, whether or not `skill_md.py` is touched. The only thing left to choose is which error a bundle author sees. Leaving `skill_md.py` at 1024 ships a parser that accepts a 500-character tool name and a service that then refuses it, with a `SkillTextRejected` that cannot name the frontmatter key — which is the two-writers-disagree defect this task exists to remove, relocated one layer down and made worse. So `skill_md.py` moves to 200 too and keeps raising `BundleInvalid` naming the key (`skill_md.py:450`), and the parser stays the thing that produces the good error. **This is a real behaviour change to a shipped feature** and is called out as such in §9: a bundle carrying a tool name between 201 and 1024 characters imports today and will not after this task. No such bundle is known to exist, and 200 characters is already far past any real tool name (`Bash(git:*)` is 11), but the risk is recorded rather than assumed away. `MAX_NAME_CHARS` is a pure de-duplication with no behaviour change: `SKILL_NAME_RE` (`models.py:62`) already bounds `name` to 64, so the literal `64` at `skills.py:123` and `:177` is a second spelling of a constraint the regex owns. |

## 4. Reproduction

No production reproduction exists today, and the dossier does not claim one — §1 explains why (no
stored row can currently fail the rule). The defect is demonstrable by the test in §8.1 and by the
following. *Revised 2026-07-19:* the original draft called this "the shape the importer will make
live". The importer is live, and it closes its own half by hand (`skill_md.py:439-476`) — so this
sequence is not reachable through import today. It is reachable the moment any writer's hand-written
half is forgotten, which is the condition the fix removes rather than mitigates:

1. Write a skill through the API with a description that is legal under the rule as it stands.
2. Tighten the rule — which D-22 did on 2026-07-16, and which any future hardening will do again.
3. `POST /api/{scope}/skills/{id}/copy` with a fresh, legal `name`. The request passes validation:
   `SkillCopyIn` only carries `name`.
4. The copy is written into the target scope with the now-illegal description intact
   (`skill_service.py:359` → `_insert:149` → `create`), and is rendered into the bound-set index of
   every agent in that scope with no rule ever having run on it.

Step 3 is the whole defect: the request was clean, and the write was not.

## 5. Root Cause Analysis

**The rule is enforced at the boundary of the request, but not every write comes from a request.**

1. `text_rejection_reason` (`text_rules.py:104`) is pure and correct. It is not the defect.
2. Its production call sites are both *per-writer*, not shared: `_validate_text` (`skills.py:93-97`)
   wired into `SkillCreateIn`/`SkillPatchIn`'s field validators (`:117-136`, `:156-164`), and
   `_reject` (`skill_md.py:533`) wired into the frontmatter parser. Each covers its own writer
   completely and neither covers the other's. That two writers independently arrived at the same
   rule is evidence the rule is right; that they had to arrive at it independently is the defect.
3. `SkillCopyIn` (`:148-161`) validates `name` — the one field its caller supplies — and is
   structurally unable to validate the rest, because the rest is not in the request. **This is the
   root cause**: the enforcement point was chosen as "where user text enters the process", and
   `copy`'s text does not enter the process, it moves within it.
4. `SkillService._insert` (`:118-158`) is the one place every write converges — `create` and `copy`
   share it deliberately, per its own docstring (`:133-137`) — and it validates nothing.

So the rule's coverage is a property of *which writer was used*, not of the data. That is why
`text_rules.py:3-5` reads as though a shared enforcement layer exists: the design intent was always
the layer every entry point crosses, and what got built instead was the rule copied into each entry
point by hand. The 200-vs-1024 cap divergence is the first place that copying visibly failed.

**Not a defect, and worth saying so:** the Pydantic validators are not wrong and should not be
removed (Q-3). The API layer is doing exactly what an API layer should. The gap is that nothing else
does.

## 6. Blast Radius and Sibling Suspects

**Blast radius.** Today: none reachable (§1). The exposure is conditional on either a rule change or
a non-request writer, and both are expected — D-22 already performed one, and the bundle importer is
specified. The consequence when it does land: text that no rule has seen is rendered into
`<<<SMAP_SKILLS_UNTRUSTED>>>`-framed content in the system prompt of every agent bound to that skill,
and the specific characters the rule excludes are the ones that defeat the control §8 rests on — the
human bind decision. Tag-block smuggling is invisible in every renderer, so the approver reads
"Fills PDF forms." and the model reads that plus whatever was smuggled. Scope-crossing is what makes
`copy` the sharp case: `[R31.01]`'s containment means a project skill is bound by project agents, and
copying it to org scope widens the audience for the same bytes.

**Sibling suspects.**

| Site | Verdict |
|---|---|
| `SkillService.copy` (`:355-370`) | **CONFIRMED** — carries `description`, `requires`, `allowed_tools`, `extra_frontmatter` from the source row unvalidated. |
| `SkillService._insert` (`:118-158`) | **CONFIRMED** — the convergence point; validates nothing. |
| `SkillService.update` (`:160-212`) | **CONFIRMED** — reachable only via `SkillPatchIn` today, so clean in practice; still a hole by construction. |
| `restore` / any other writer in `skill_service.py` | **swept 2026-07-19 — CLEARED, no sibling.** `restore` (`skill_service.py:320`) is the one skill-row write that bypasses both `_insert` and `update`, but `SkillRepository.restore` sets only `deleted_at=None` (`repositories.py:195-208`) and touches no text field; it also re-runs name uniqueness (`:294`), bound-set conflict (`:301`), and the index budget (`:311-318`). `soft_delete` (`:256`) and `cascade_owner_deleted` (`:392`) likewise touch no text. Binding and file writers carry no skill text. `facade.py` holds no repository and funnels every write to the service. **Every skill-row insert in the context reaches `SkillRepository.create` through `_insert` alone**, which is what makes the one-gate design sound. |
| `index_builder.render_index` | **cleared as a defence, and that is the finding** — it imports `contains_delimiter` (`:26`) and re-exports it (`:81`) without calling it. Its comment (`:30-33`) states the entry-point claim this task makes true. Deliberately not changed: rejecting at render time would fail a turn for a stored row rather than the write that created it, which is later and less actionable. |
| The bundle importer | **exists, is live, and is CLEARED as a defect — but it is the evidence.** `BundleService.import_bundle` (`bundle_service.py:495`) writes through `SkillService.create` → `_insert` (`:519`, `:492`), and `parse_skill_md` runs the charset rule on `description` (`skill_md.py:439`), `allowed_tools` (`:450`), `requires` (`:454`), `license` (`:459`), and unknown keys and values (`:469-476`) before the write. `name` carries no `_reject` call (`:432`) but `SKILL_NAME_RE` is strictly narrower than the charset rule, so nothing can pass it — cleared. The importer is safe **because its author remembered**, not because anything made them. Its cap divergence from the API (200 vs 1024 on tool names) is what that costs. |
| `body` charset rules | **not this rule** (Q-1). |
| `name` on `copy` | **cleared** — `SkillCopyIn._check_name` (`:155-161`) runs `_validate_text` and `SKILL_NAME_RE`. The one field the caller supplies is the one field validated, which is the asymmetry that names the bug. |

## 7. Fix Design

**Enforce in `SkillService._insert` and `update` — the layer every write crosses.**

1. Add `SkillTextRejected(SkillError)` to `contexts/skills/domain/errors.py` carrying `field` and
   `reason` (Q-2), following `SkillContainmentFailed`'s shape (`errors.py`).
2. Add a small domain helper beside the rule — `assert_text_ok(field, value, max_chars)` in
   `text_rules.py` — that calls `text_rejection_reason` and raises. This keeps the raise in the
   domain, so the service does not re-implement the reason wording, and it is the third "idiom"
   `text_rules.py:107-109` already anticipates.
3. Call it in `_insert` for `name`, `description`, `requires[]`, `allowed_tools[]` (Q-1), and in
   `update` for whichever of those the draft carries. `_insert` is the funnel `create` and `copy`
   share (`:133-137`), so one call site covers both.
4. Register `SkillTextRejected: ("skills/text-rejected", 422, "Skill text contains rejected
   characters")` in `interfaces/error_mapping.py`'s `_MAP`, and return `{"field": exc.field,
   "reason": exc.reason}` from `_extras` — placed before any base-class arm, per that module's own
   MRO note (`error_mapping.py:35-41`).
5. Add `MAX_NAME_CHARS = 64` and `MAX_TOOL_NAME_CHARS = 200` to `domain/models.py` and route all
   three writers through them (Q-6): the new gate, `app/api/v1/skills.py` (replacing `_MAX_TOOL_NAME`
   and the two literal `64`s at `:123`/`:177`), and `skill_md.py:450`/`:454`.
6. Keep every Pydantic validator in `app/api/v1/skills.py` (Q-3). The file is touched **only** to
   swap literals for the shared constants — no validator is removed, added, or reordered.

**Why this does not merely mask the symptom.** The alternative — validating inside `copy` — would fix
the one path found and leave `_insert` open for the importer, which is the same mistake one layer
down. `_insert` is where the data becomes a row; that is the last point at which the rule can be
about the data rather than about the caller.

**Data repair: none.** No stored row can currently fail the rule (§1). Worth a line in the release
note: if a future rule change strands a row, `copy` and `update` will 422 on it and the fix is to
edit the source skill.

**Sequencing:** `2026-07-16-agent-skills` FU-28 (Q-31(b) specifies NFC normalisation; nothing
normalises) **depends on this task** and must land after it. Normalising means returning a
transformed string, which breaks `text_rejection_reason`'s reason-only contract (`:107-109`) and
needs a service-layer call site to apply the result — the one this task creates. FU-27 (§8 claims a
homoglyph mitigation that does not exist) is a documentation fix and is independent.

## 8. Regression Test Plan

1. **`tests/unit/test_skill_sanitization.py` — the headline.** `SkillService.copy` of a row whose
   stored `description` violates the rule raises `SkillTextRejected`. Build the row through the
   repository fake rather than the API so the Pydantic layer is bypassed — which is the point: the
   test must reach the service the way `copy` does. *Fails now*: the copy succeeds and the text lands
   in the target scope. This file already owns the charset matrix and its docstring frames it over
   fields, so the service-layer arm belongs here.
2. **Same file — `_insert` and `update` reject directly.** Call the service with hostile `name`,
   `description`, `requires`, `allowed_tools` in turn. *Fails now*: all four are accepted.
3. **Same file — `body` is not subject to the rule (Q-1).** A multi-line body with newlines is
   accepted by `_insert`. *Passes now and must keep passing* — this is the test that stops a future
   reader "fixing" the asymmetry and rejecting every real skill.
4. **`tests/unit/test_skill_error_mapping.py`** — `SkillTextRejected` maps to 422
   `skills/text-rejected` with `field` and `reason` in the body, and does not collide with the
   `SkillScopeMismatch`/`SkillContainmentFailed` MRO arms. That file exists for exactly this
   (15 tests) and already covers the MRO ordering hazard.
5. **The existing 52 tests in `test_skill_sanitization.py` stay green** — the rule itself is not
   being changed, only where it runs. Any red there means the fix altered the rule.
6. **`tests/unit/test_skill_sanitization.py` — the caps have one spelling (AC-9, Q-6).** Assert
   `MAX_TOOL_NAME_CHARS == 200` and that the API model and `parse_skill_md` reject a 201-character
   tool name **identically in cap, differently in error type** — `SkillCreateIn` raises a
   `ValidationError`, `parse_skill_md` raises `BundleInvalid` naming the key. *Fails now*: the
   importer accepts it. This is the test that would have caught the divergence that already
   happened.
7. **`tests/unit/test_skill_md.py` — the importer's good error survives (AC-10).** An over-length
   tool name is rejected by `parse_skill_md` with `BundleInvalid` carrying `key="allowed-tools"`,
   so the rejection never reaches the service as a keyless `SkillTextRejected`. *Fails now* at the
   new cap. Existing `test_skill_md.py` arms that assert 1024-character acceptance for
   `requires`/`allowed_tools` must be re-pointed at `MAX_TOOL_NAME_CHARS`; **`description` stays at
   `MAX_DESCRIPTION_CHARS` and its arms must not move** — the two caps are separate numbers and
   conflating them is the likeliest way to break this.
8. **Probe** (per this repo's practice, recorded in the commit rather than as a test): revert the
   `_insert` gate and confirm 1 and 2 redden.

## 9. Risks and Rollback

- **Double validation could diverge — and already has.** This risk was written as a prediction and
  was found realised on 2026-07-19: tool names are capped at 200 through the API (`skills.py:74`) and
  1024 through the importer (`skill_md.py:450`). Mitigation is now structural rather than advisory
  (Q-6): the caps move to `domain/models.py` and all three writers import them. **Do not introduce a
  second literal** — that instruction is what was violated, so treat a new numeric cap anywhere in
  this context as a defect.
- **Tightening the importer's tool-name cap is a live behaviour change.** A bundle carrying a tool
  name of 201-1024 characters imports today and will be rejected after this task (Q-6). This is
  unavoidable — the gate makes 200 effective for every writer regardless of what `skill_md.py` says —
  so the choice made is to have the parser reject it with a `BundleInvalid` naming the key rather
  than have the service reject it with an error that cannot. No such bundle is known to exist; 200 is
  far above any real tool name. If one turns up, the fix is to raise `MAX_TOOL_NAME_CHARS` in one
  place, which is the point of the refactor.
- **A 422 from a service layer is a new shape for this context's callers.** The error mapper handles
  it (`register_context_handler`), but any non-HTTP caller of `SkillsFacade.copy` — none today —
  would now see a raise where it saw a return.
- **`update`'s draft is partial.** `SkillPatchIn` allows every field to be absent (`:120-135`); the
  gate must validate only what the draft carries, or a patch touching `description` alone will
  re-validate a `requires` list it was never given and fail on `None`.
- **Low blast radius.** The feature has no frontend and no bound skills in production, so a mistake
  here is cheap to discover and cheap to revert.
- **Rollback:** additive and self-contained — remove the calls in `_insert`/`update`, the error
  class, and the mapper row. Nothing persists.

## 10. Acceptance Criteria

- [ ] AC-1: §8.1 fails before the fix and passes after — `copy` of a row with a rule-violating stored
      `description` raises `SkillTextRejected` and writes nothing.
- [ ] AC-2: §8.2 passes — `_insert` and `update` reject hostile `name`, `description`, `requires[]`,
      and `allowed_tools[]`.
- [ ] AC-3: §8.3 passes — a multi-line `body` is still accepted; the rule is not applied to it.
- [ ] AC-4: §8.4 passes — `SkillTextRejected` → 422 `skills/text-rejected` with `field` and `reason`,
      and the existing MRO arms are unaffected.
- [ ] AC-5: `text_rules.py:3-5`'s "the same three entry points for each: create, update, and import"
      is true of the code, and `index_builder.py:30-33`'s "every entry point" claim is true. Both
      docstrings updated to describe the shared gate rather than a per-writer convention.
- [ ] AC-6: `text_rejection_reason` (via its raising wrapper) is reached from `skill_service.py`, so
      that **no write path can reach `SkillRepository.create` or a `description=` update without
      crossing it** — verified by the §6 sweep staying true, not by grep alone.
- [ ] AC-7: the 52 existing tests in `test_skill_sanitization.py` are unchanged and green.
- [ ] AC-8: backend gates green — `pytest -q`, `ruff check . && ruff format --check .`, `mypy .`.
- [ ] AC-9 *(added 2026-07-19, Q-6)*: `MAX_NAME_CHARS` and `MAX_TOOL_NAME_CHARS` live in
      `domain/models.py` and are the only spelling of those caps — no numeric literal for a text cap
      remains in `app/api/v1/skills.py` or `skill_md.py`, and a test pins that the API and the
      importer now agree on the tool-name cap.
- [ ] AC-10 *(added 2026-07-19, Q-6)*: the live import path still works end to end — a bundle whose
      frontmatter is legal under the shared caps imports and writes its row, and one carrying an
      over-length tool name is rejected by `parse_skill_md` with a `BundleInvalid` naming the key
      (not by the service with an error that cannot).

## 11. SRS Delta

None. `[R31.01]` (`REQUIREMENTS.md:2140`) already states the rule for `description`; this task makes
the code match it on every write rather than on one. The SRS's silence about *where* the rule is
enforced is not an error to correct — enforcement layers are an implementation concern, and the
`text_rules.py` docstring is the right home for that intent.

*Checked 2026-07-19 for the Q-6 cap change:* `[R31.01]` says `description` is "length-capped" without
naming a number, and neither it nor `[R31.19]` (`:2168`) caps tool-name length at all. So both the
200 and the 64 are implementation constants with no SRS commitment behind them, and unifying them
needs no delta. Had the SRS named a number, Q-6 would have been an SRS question rather than a
refactor.

Note `2026-07-16-agent-skills` FU-27/FU-28 do carry SRS-facing work on the neighbouring claims
(§8's homoglyph overclaim, Q-31(b)'s unimplemented NFC). Both are out of scope here and FU-28
sequences behind this task (§7).

## 12. Deviation Log

Appended by `/build`.

## 13. Follow-ups

- **FU-1: `extra_frontmatter` crosses scopes unvalidated by anything but the parser.**
  *Revised 2026-07-19.* `copy` carries `dict(source_skill.extra_frontmatter)`
  (`skill_service.py:363`) and neither `SkillCreateIn` nor this task's gate touches it — it is a
  free-form `dict[str, Any]` reaching the row. The bundle path **does** validate it
  (`skill_md.py:469-476`, keys capped at 64 and values at 1024 with the full charset rule), which is
  the same per-writer pattern this task is removing everywhere else: the importer is safe, the
  service is not, and `copy` moves the dict between scopes with no rule running. It does not enter
  the index today, so there is no model-visible path and it stays out of scope — but it is now the
  *only* remaining field where that asymmetry survives, which makes it the obvious next task rather
  than a someday item.
- **FU-2: `restore` and any future writer must not re-open the hole.** *Revised 2026-07-19 — the
  sweep found no current sibling* (§6): `restore` (`skill_service.py:320`) bypasses `_insert` and
  `update` but writes only `deleted_at` (`repositories.py:195-208`). So this is a design gap, not a
  live one. This task gates `_insert` and `update` because they are today's funnel; nothing
  structurally prevents a new method calling `self._skills.create` directly. The durable fix is to
  make the repository's `create`/`update` signature refuse unvalidated text — a typed `ValidatedText`
  wrapper rather than `str` — which is a larger refactor and a real design decision. The importer's
  arrival is the argument for it: that writer was added *after* the rule was written and had to
  re-derive it by hand, which is exactly the failure a type would have prevented.
- **FU-3 *(added 2026-07-19)*: the `license` field is capped and charset-checked only by the
  parser.** `skill_md.py:459` validates it at 1024 characters; it is not a column this task's gate
  covers, because it is not one `_insert` takes (`skill_service.py:118-131`). When `license` becomes
  a first-class field rather than a parsed-and-discarded one, it needs the same treatment — and it is
  named in `text_rules.py:8-9` as being in the rule's scope, so the docstring is ahead of the schema
  here.
