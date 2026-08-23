---
type: feature
status: draft
created: 2026-08-24
requirements: [R27.01]
depends_on: []
---

# Traceability extraction script and repo gate

## 1. Summary

`docs/traceability.csv` is the index from every `[Rxx.yy]` requirement to the SRS section
that defines it. §27 of `REQUIREMENTS.md` instructs "Re-run the extraction whenever new
`[Rxx.yy]` IDs are added", but no extraction tool has ever existed: the file was produced
by a one-off author pass on 2026-04-25 and has been maintained by hand since, per-chapter,
whenever a task dossier happened to remember. This task builds the extraction as a script,
wires it into the existing `repo-gates` CI job as a consistency check, regenerates the file
from `REQUIREMENTS.md`, and rewrites §27 so the instruction names a tool that exists.

This is a pre-existing gap, found on 2026-08-24 while adding `[R13.33]`, `[R13.34]` and
`[R30.38]` for the activity-context work (commit `18ef810`). It is not a regression from
that change — those three requirements are simply the most recent additions to a backlog of
83.

## 2. Goals and Non-goals

**Goals**

- One script that derives `docs/traceability.csv` from `REQUIREMENTS.md`, deterministically,
  so the file can be regenerated rather than edited.
- A CI check that fails when the committed file does not match what the script would
  produce, so the two cannot silently diverge again.
- The same check verifies that every `[Rxx.yy]` cited outside the SRS names a requirement
  that exists, so a renumbered or deleted requirement cannot leave dangling citations in
  code, tests, or the construction plan.
- The 83 missing rows are present and the file is green when the gate first runs.
- §27 describes the mechanism that exists, not one that does not.

**Non-goals**

- **No new columns.** A `tests` or `implemented_in` column would need a second, larger
  extraction (from 441 code and test citations) with its own accuracy problem. Out of scope;
  see FU-1.
- **No SRS restructuring.** The three ID shapes and three definition forms found in §4 are
  taken as given and the extractor accommodates them; normalising the SRS to one form is a
  separate, higher-risk edit (FU-2).
- **No enforcement that a new requirement has a test.** The gate checks that the index is
  complete and that citations resolve; it makes no claim about coverage.
- **Not a `docs/implement/` sync.** The three dangling citations this task's gate will find
  live there and are fixed as part of making the gate green, but the wider question of
  whether the phase files still describe the built system is untouched (FU-3).

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Is this a feature, a bugfix, or a refactor? | **feature** | The deliverable is a capability the repo has never had (an extraction script plus a gate). §27 documents an intent that was never implemented, which reads bugfix-shaped, but a bugfix dossier carries an empty SRS Delta and this work must rewrite §27's text. The feature template's Design and SRS Delta sections are the ones this task actually needs. |
| Q-2 | Is `summary` derived from the SRS text, or an authored abstract? | **Derived, and the existing 306 rows are regenerated** | Evidence says it was already meant to be derived and drifted: 44 rows end in `...` at two different lengths (220 and 243 chars), and the longest untruncated summary is 386 — three different rules in one file. Regenerating makes the gate able to verify the whole row rather than just the ID set, which is what closes the drift permanently. Accepted cost: a large one-time diff touching text in all 306 existing rows. |
| Q-3 | Script and gate only, backfill only, or all three? | **All three** | A gate that lands red needs a suppression mechanism, and a suppression mechanism that outlives the sprint is how a gate stops meaning anything (`check_no_lazy_prompt.py:8-11` records the same failure mode for a different gate). Backfilling in the same change means the gate is green from its first run and the file cannot regress. |
| Q-4 | Should the gate also verify `[Rxx.yy]` cited outside the SRS? | **Yes** | Measured before deciding: 174 distinct IDs are cited across `backend/`, `frontend/`, and `docs/` (excluding dossiers and migrations), and **3 already dangle** — `docs/implement/E-agents-knowledge.md` cites `[R9.04]`, `[R9.05]` and `[R9.08]`, which §31's chapter header (`REQUIREMENTS.md:2211`) records as removed on 2026-07-16. The check finds real drift on the day it lands, which is the strongest argument that it is not ceremony. |
| Q-5 | Does this depend on `2026-07-07-graphrag-two-axis-redesign` (approved), which drafts `[R11.05]`, `[R11.07]`, `[R11.08]`? | **No** | That dossier's §13 records "Applied to `REQUIREMENTS.md` on approval (done)" (`spec.md:917`), so its IDs are already in the SRS — they are part of the 19 missing R11 rows this task backfills, not future additions. No file overlap: it touches neither `scripts/`, `ci.yml`, nor `traceability.csv`. |
| Q-6 | Does this depend on `2026-07-19-large-artifacts-silently-dropped` (in-progress)? | **No** | Its two matches on `REQUIREMENTS.md` are prose citations (`spec.md:240`, `:422`), not edits. It adds no requirement IDs and touches none of this task's files. |

## 4. Current State

### The file and its instruction

- `docs/traceability.csv` — 306 rows, header `requirement_id,section,summary`.
- `REQUIREMENTS.md:2097` (§27) — "The mapping is maintained in `docs/traceability.csv`,
  generated from this document by an author pass on 2026-04-25 (304 entries; columns:
  `requirement_id, section, summary`). Re-run the extraction whenever new `[Rxx.yy]` IDs are
  added."

The file now holds 306 rows against the stated 304, so two were hand-added after the author
pass. No extraction script exists: `scripts/` contains `check_lock_consistency.py`,
`check_no_lazy_prompt.py`, and `ci-retry.sh`, and nothing anywhere reads or writes
`traceability.csv`.

### The gap, measured

`REQUIREMENTS.md` defines **389 distinct requirement IDs**, counting every `**[Rxx.yy]**`
occurrence. That pattern is a reliable definition marker: 389 occurrences yield 389 unique
IDs, so no ID is bolded twice, and every one of the 306 CSV rows resolves to one of them.

- **83 SRS requirements have no CSV row**, by chapter: R30 38, R11 19, R13 10, R6 5, R15 4,
  R12 2, R24 2, R5 1, R9 1, R14 1.
- **0 CSV rows are stale.** Every `requirement_id` in the file exists in the SRS.

The distribution is per-chapter rather than uniform, and the reason is visible in the
history: §31 Agent Skills is fully covered because its dossier added the rows explicitly
(`docs/tasks/2026-07-16-agent-skills/spec.md:1710` specifies the row's exact shape,
`section = "31. Agent Skills"`), while §30 Structured Activities — the largest chapter added
since the author pass — has none of its 38. The mechanism is "whoever writes the dossier
remembers", and it holds exactly where someone did.

### What the extractor has to parse

Three ID shapes are in use:

| Shape | Count | Example |
|---|---|---|
| `Rn.nn` | 373 | `[R30.15]` |
| `Rn.nn` + letter suffix | 9 | `[R7.09a]` (`REQUIREMENTS.md:321`), `[R9.10a]` (`:454`) |
| `Rn.nn.nn` | 7 | `[R22.15.01]` (`:1690`) through `[R22.15.07]` (`:1708`) |

A naive `\[R(\d+\.\d+)\]` misses the last two families — 16 IDs, 4 % of the corpus. This is
not hypothetical: the first pass of this very analysis used that pattern and reported 9
non-existent "stale" rows that were simply formatted differently.

Three definition forms are in use, all sharing the `**[...]**` bold marker:

- line-initial bullet — `- **[R30.15]** ...`, the majority
- bare paragraph — `**[R3.01]** ...` (`REQUIREMENTS.md:131`), `**[R5.05]**` (`:210`),
  `**[R13.04]**` (`:686`)
- numbered list item — `5. **[R7.03]** ...` (`:300`)

Eight CSV rows correspond to the latter two forms, so an extractor keyed on the bullet alone
would drop them.

`section` is derivable: the nearest preceding `## N. Title` heading, stored without the `##`
(`3. High-level Architecture`). `summary` is the requirement's text with the ID prefix
removed and markdown stripped — `R31.01`'s row renders "A Skill is (name, description, body,
files[])." against the SRS's ``A Skill is `(name, description, body, files[])`.`` — but the
truncation rule is not consistent, which is the evidence behind Q-2.

### Where the gate goes

`.github/workflows/ci.yml` already has a `repo-gates` job (ubuntu, 5-minute timeout, Python
set up) running two consistency checks of exactly this shape. Adding a third step is the
whole CI change.

### Citations outside the SRS

174 distinct IDs are cited across `backend/`, `frontend/src`, `frontend/e2e` and `docs/`
(excluding `docs/tasks/`, `docs/audits/` and `backend/alembic/versions/`, which are historical
records). Raw citation counts: 266 in `backend/contexts` + `backend/app`, 72 in
`backend/tests`, 103 in `frontend/src`. Three of the 174 resolve to nothing:
`[R9.04]`, `[R9.05]`, `[R9.08]`, all in `docs/implement/E-agents-knowledge.md:62,72`, removed
by §31 on 2026-07-16 (`REQUIREMENTS.md:2211`).

## 5. Design

### Options considered

**Option A — generator + `--check` mode, single script.** `scripts/traceability.py` writes
the CSV when run bare and exits non-zero with a diff when run with `--check`. CI runs
`--check`; a human who adds a requirement runs it bare and commits the result. One file, one
parser, no way for the writer and the checker to disagree about what a requirement is.

**Option B — checker only, rows added by hand.** A gate that verifies the ID set and nothing
else. Smallest diff (the 306 existing summaries stay untouched), but the checker cannot
verify `summary` or `section` without reimplementing the derivation it refuses to own, so
those two columns keep drifting silently — which is the failure this task exists to end.

**Option C — drop the CSV, generate an HTML/Markdown index on demand.** Removes the
synchronisation problem by removing the artifact. Rejected: the CSV is referenced by §27 as
the traceability mechanism for a research project whose SRS is a deliverable, and a file in
git is reviewable in a PR diff while a generated page is not.

### Decision

**Option A.** The generator and the checker must share one definition of "a requirement", and
the cheapest way to guarantee that is for them to be the same code path. §4's regex finding
is the argument: an ID-shape family that one implementation knows and the other does not is
precisely how this analysis went wrong on its first pass, and two separate implementations
would institutionalise that.

Consciously given up: the 306 existing `summary` values are rewritten, so the backfill commit
touches every row rather than appending 83. Reviewing that diff means spot-checking a
mechanical transform, not reading 306 sentences — and the alternative is a column no gate can
defend.

### Extraction rules (normative)

1. A requirement is defined by an occurrence of `**[<id>]**` in `REQUIREMENTS.md`, where
   `<id>` matches `R\d+\.\d+(\.\d+)?[a-z]?`. Exactly one occurrence per ID is expected; the
   script fails on a duplicate rather than picking one.
2. `section` is the text of the nearest preceding `^## ` heading, with the marker stripped.
3. `summary` is the definition's text from the end of the ID marker to the end of that
   Markdown block, with inline markdown removed (backticks, bold, links reduced to their
   text), whitespace collapsed to single spaces, and truncated to a single documented length
   with a trailing `...`.
4. Rows are emitted in SRS document order, which is also chapter order, so a diff on the file
   reads as a diff on the SRS.
5. The check compares the committed file to the generated one byte for byte and prints the
   differing rows.

### Citation check (normative)

6. Scan tracked files under `backend/`, `frontend/src`, `frontend/e2e`, `docs/` for
   `\[R\d+\.\d+(\.\d+)?[a-z]?\]`. Every match must be a defined ID.
7. Excluded, each for a stated reason: `REQUIREMENTS.md` itself (it is the definition source),
   `docs/tasks/` and `docs/audits/` (dossiers are a historical record — the same exclusion and
   the same reason as `check_no_lazy_prompt.py:36`), `backend/alembic/versions/` (a landed
   migration is immutable history).

## 6. Detailed Changes

- **Backend** — none. No context, service, repository, table, or migration is touched.
- **API contract** — none. No endpoint, no model, no `gen:api` rerun.
- **Frontend** — none.
- **Scripts** — new `scripts/traceability.py`. Follows `check_no_lazy_prompt.py`: Python
  rather than shell so it runs on a Windows dev box, `git ls-files` rather than a filesystem
  walk for the citation scan, exclusions declared as data with a reason string each, and a
  summary line printed on success so a passing run still says what it covered.
- **CI** — one step added to the `repo-gates` job in `.github/workflows/ci.yml`, beside the
  two existing checks.
- **Docs** — `docs/traceability.csv` regenerated (306 rewritten + 83 new = 389 rows);
  `REQUIREMENTS.md` §27 rewritten (SRS Delta, §13); `docs/implement/E-agents-knowledge.md`
  corrected at `:62` and `:72` to stop citing three removed requirements.
- **Deploy/config** — none.

## 7. NFR Checklist

- [x] i18n — N/A. No user-facing string; the script's output is developer-facing English,
  consistent with the other two repo gates.
- [x] Audit log — N/A. No domain event; this touches no runtime code path.
- [x] Tenant isolation — N/A. No endpoint.
- [x] Error handling UX — the script must distinguish its three failure modes in its output
  (rows differ / ID defined twice / citation does not resolve) and print the offending
  `path:line`, because a gate whose failure message does not say what to do gets suppressed.
- [x] Performance — `REQUIREMENTS.md` is a single ~2200-line file and the citation scan reads
  the tracked files under four roots; `check_no_lazy_prompt.py` does the same walk inside the
  job's 5-minute budget.

## 8. Security Considerations

None — no sensitive surface touched. The script reads repository files and writes one CSV; it
performs no network I/O, handles no credential, and runs only in CI and on a developer
machine.

## 9. Quality Notes

**Existing debt in the touched area**

- `REQUIREMENTS.md` mixes three definition forms and three ID shapes (§4). The extractor
  accommodates all of them rather than normalising, per the Non-goals; the accommodation is
  debt made explicit rather than paid down (FU-2).
- `REQUIREMENTS.md:2097` states a row count (304) inline, which is a second number to keep in
  sync with the file. The §13 rewrite removes it rather than updating it.
- `docs/tasks/2026-07-13-activities-activation-ux/spec.md` carries `status: done`, which is not
  one of the six values the contract defines (`docs/tasks/README.md:41`). Not this task's file
  to fix — recorded as FU-4.

**Patterns to follow**

- `scripts/check_no_lazy_prompt.py` is the exemplar for the whole shape: module docstring
  stating what the gate defends and why it is Python, `REPO` resolved from `__file__`,
  `EXCLUSIONS` as a tuple of `(path, reason)` pairs printed on every run, `git ls-files -z`
  for scope, `main() -> int` returning the exit code, and a failure message naming the
  supported alternative.
- `scripts/check_lock_consistency.py:14-20` is the exemplar for scoping a check honestly —
  it states what it deliberately does not verify and why. The citation check needs the same
  paragraph about the exclusions.

**Reuse inventory**

- `csv` and `re` from the standard library. No new dependency: `repo-gates` sets up Python
  and installs nothing, and adding a requirement to that job for a docs check would be a
  poor trade.
- The `git ls-files -z` subprocess pattern at `check_no_lazy_prompt.py:52-68`, including its
  docstring's reasoning about why a filesystem walk is wrong here — copy the approach, not
  the file.
- `.github/workflows/ci.yml`'s `repo-gates` job already provides checkout and Python; no new
  job, no new runner setup.

## 10. Risks and Rollback

- **The extractor's `summary` rule disagrees with a hand-edited row in a way that loses
  meaning.** Mitigated by Q-2 being an explicit decision rather than a side effect, and by
  AC-6: the regeneration diff is reviewed for rows whose derived text is materially worse
  than the authored one, and any such case is fixed by editing the SRS sentence, not by
  special-casing the script.
- **A future SRS edit introduces a fourth definition form and the script silently drops it.**
  This is the failure the gate exists to prevent, so it must not be silent: rule 1 fails on a
  duplicate ID, and AC-4 requires the count assertion (`389`, updated as the SRS grows) so a
  dropped definition shows up as a count mismatch rather than as a missing row nobody reads.
- **Rollback** — no migration and no runtime code. Reverting the commit restores the previous
  CSV and removes the CI step; nothing depends on the script existing.

## 11. Acceptance Criteria

- [ ] AC-1: `python scripts/traceability.py` regenerates `docs/traceability.csv` and running
  it twice in a row produces no diff.
- [ ] AC-2: `python scripts/traceability.py --check` exits 0 against the committed file and
  exits non-zero, naming the offending row, when a row is edited or deleted.
- [ ] AC-3: The regenerated file has one row per defined requirement — 389 today — with every
  one of the 83 previously-missing IDs present, including all 38 `R30.*`.
- [ ] AC-4: The script fails, with a message naming the ID, when `REQUIREMENTS.md` defines the
  same ID twice; and its success output states how many definitions it found, so a dropped
  one is visible as a count change.
- [ ] AC-5: All three ID shapes round-trip: `[R30.15]`, `[R7.09a]` and `[R22.15.01]` each
  produce a row, and each of the three definition forms in §4 is represented in the output.
- [ ] AC-6: The regeneration diff is reviewed row by row for the 306 rewritten summaries, and
  every case where the derived text reads materially worse than the authored one is resolved
  by editing the SRS sentence rather than by an exception in the script.
- [ ] AC-7: The citation check fails against `docs/implement/E-agents-knowledge.md` as it
  stands today, naming `[R9.04]`, `[R9.05]` and `[R9.08]` with their `path:line`.
- [ ] AC-8: Those three citations are corrected — §31 supersedes them — and the check then
  passes across all four scan roots.
- [ ] AC-9: The check is mutation-probed in both directions before landing: it goes red on a
  deleted CSV row, red on an invented `[R99.99]` citation added to a scanned file, and green
  once both are reverted.
- [ ] AC-10: `repo-gates` runs the check in CI and the job is green on the branch.
- [ ] AC-11: §27 of `REQUIREMENTS.md` names the script and the gate, and states no row count.
- [ ] AC-12: Adding a requirement to `REQUIREMENTS.md` without regenerating the CSV makes CI
  red — demonstrated on a scratch commit, not argued.

## 12. Test Plan

The script is the kind of code whose tests are cheap and whose absence is expensive, so it
gets a unit file alongside the repo gates it joins.

| AC | Level | Where |
|---|---|---|
| AC-1, AC-2 | manual + CI | run locally, then `repo-gates` |
| AC-3, AC-5 | unit | `backend/tests/unit/test_traceability_extraction.py` — parse a fixture SRS covering all three ID shapes and all three definition forms, assert the emitted rows |
| AC-4 | unit | same file — a fixture with a duplicated ID must raise, not silently pick one |
| AC-6 | manual | review of the regeneration diff; the outcome is recorded in the Deviation Log |
| AC-7, AC-8 | manual + CI | the check's own output before and after the `E-agents-knowledge.md` fix |
| AC-9 | manual | two mutations, each reverted, recorded with what the check printed |
| AC-10, AC-12 | CI | a scratch commit adding a throwaway requirement, confirmed red, then dropped |

The unit tests parse a fixture, never `REQUIREMENTS.md` itself — a test that reads the live
SRS would change its own expectations every time a requirement is added, which is a test that
asserts nothing.

## 13. SRS Delta

Replace §27's first paragraph (`REQUIREMENTS.md:2097`) with:

> Every requirement `[Rxx.yy]` corresponds to a Q&A decision or a design recommendation. The
> mapping is maintained in `docs/traceability.csv` (columns: `requirement_id, section,
> summary`), which is **generated from this document** by `scripts/traceability.py` and is not
> edited by hand. Adding or renumbering a requirement means regenerating the file in the same
> change; `scripts/traceability.py --check` runs in the `repo-gates` CI job and fails when the
> committed file does not match this document. The same check verifies that every `[Rxx.yy]`
> cited outside this document — in backend or frontend source, in tests, or under `docs/` —
> names a requirement that exists here, so a removed or renumbered requirement cannot leave a
> citation pointing at nothing. Task dossiers under `docs/tasks/` and `docs/audits/` are
> excluded from that check: they are a historical record of what was true when they were
> written, not live documentation.

And add, at the end of §27:

> - **[R27.01]** `docs/traceability.csv` is a generated artifact, complete by construction: it
>   carries exactly one row per `[Rxx.yy]` defined in this document, and CI rejects a commit in
>   which the two disagree. Traceability is therefore a property the repository enforces rather
>   than a convention a contributor is asked to remember — the arrangement it replaces held for
>   the chapters whose author happened to update the file and had drifted by 83 requirements
>   across nine chapters by 2026-08-24.

Note: §27 currently defines no `[Rxx.yy]` of its own, so `[R27.01]` is the first. The
extractor must produce a row for it, which is a small end-to-end check of the whole mechanism
on the day it lands.

## 14. Open Questions

- The truncation length for `summary` is unset. The existing file uses 220 and 243
  inconsistently, and the longest untruncated row is 386. This is a formatting choice with no
  downstream consumer; the implementer picks one, states it in the script's docstring, and
  the gate makes it stable from then on. Not approval-blocking.

## 15. Deviation Log

Appended by /build. Empty means the implementation matches this spec exactly.

## 16. Follow-ups

- **FU-1** — A `tests` or `implemented_in` column mapping each requirement to the code and
  tests that realise it. 441 citations already exist across backend source, backend tests and
  frontend source, so the data is there; the accuracy problem (a citation in a comment is not
  proof of coverage) makes it a separate task with its own design.
- **FU-2** — Normalise `REQUIREMENTS.md` to one definition form and one ID shape. Would
  simplify the extractor and every future reader, at the cost of renumbering 16 IDs that are
  cited from code, which is exactly the change the new citation check exists to make safe.
- **FU-3** — `docs/implement/` phase files are derived from the SRS and nothing verifies they
  still describe the built system. This task fixes three dangling citations there because the
  gate demands it; whether the surrounding prose is still true is unexamined.
- **FU-4** — `docs/tasks/2026-07-13-activities-activation-ux/spec.md` carries `status: done`,
  which the contract does not define (`docs/tasks/README.md:41`). `/build` gates on
  `status`, and a value outside the enum is one no gate can reason about; the dossier is
  almost certainly `implemented`. Worth a sweep of every `spec.md` frontmatter for other
  out-of-enum values while fixing it.
