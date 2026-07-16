---
type: bugfix
status: draft
created: 2026-07-16
requirements: [R12.03]
---

# `agent-files/` paths reported to `code_exec` do not resolve

## 1. Summary

An agent with the Code Interpreter tool enabled and persisted workspace files is told, in a
system note every turn, that its files are available at `agent-files/{name}`. That path does
not resolve. The kernel executes with its working directory set to the room's session
directory, while the files are materialised at the volume root, so the model's
`open('agent-files/data.csv')` raises `FileNotFoundError` — the exact call
`docs/agent-tools/D-code-interpreter-files.md:137-139` names as the feature's exit criterion.
The feature is, on its documented happy path, non-functional. The `file` tool is unaffected.

Found during the review of the Agent Skills dossier (`docs/tasks/2026-07-16-agent-skills/`,
FU-15), which is why it is written up separately: the fix is a behavior change on a documented,
model-facing path and does not belong inside a feature change.

## 2. Observed vs Expected

- **Observed** — `_stage_persisted_files` (`turn_engine.py:647-690`) stages files and folds the
  returned paths into the note at `:642`:
  `"[Files available in the code_exec workspace: " + ", ".join(all_paths) + "]"`. Those paths
  read `agent-files/data.csv` (`docker_runsc.py:1026-1027`, `:1032`/`:1058`). The kernel
  `chdir`s to `/workspace/sessions/{room}` (`kernel.py:37-41`, `:119-123`), so that string
  resolves to `/workspace/sessions/{room}/agent-files/data.csv`. The bytes are at
  `/workspace/agent-files/data.csv` — `put_archive` targets `/workspace` (`:1053`) with the tar
  rooted at `agent-files` (`:1037`). Result: `FileNotFoundError`.
- **Expected** — the path the model is told **is** the path that opens.
  `docs/agent-tools/D-code-interpreter-files.md:137-139` states the exit criterion: *"Designer
  uploads `data.csv`; a chat turn runs `code_exec` doing `open('agent-files/data.csv')` and
  reads it."* `[R12.03]`'s Lifetime bullet (`REQUIREMENTS.md:582`) establishes the per-agent
  persistent volume the feature rests on — `smap-agent-fs-{agent_id}`, mounted read-write at
  `/workspace`. (That same line is itself stale about *which* containers mount it; see §11.)

Note the intent source specifies the **relative** form, and Q-1 below deliberately departs from
it. That is a documentation change, made explicitly rather than silently.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Report absolute `/workspace/agent-files/x`, or make the relative form resolve (per-session symlink, or stage under the session dir)? | **Absolute path in the note**, and update `D-code-interpreter-files.md` to match. | A symlink makes the correct path depend on invisible filesystem state, and must be recreated per room, on a volume that already persists across turns — new lifecycle, new failure mode. Staging under the session dir defeats `[R12.03]`'s per-agent persistence: files would be re-copied per room and the `manifest_sha` cache (`docker_runsc.py:1029-1032`) would thrash. The absolute path is stable, cwd-independent, needs no new state, and is what `[R31.22]` already chose for skill scripts — one rule for both staged trees. |
| Q-2 | Also fix `_tar_staged_inputs`' hardcoded `"inputs"` return (`docker_runsc.py:138`)? | **No.** Out of scope, and it is not a bug. | For its only other caller, `stage_kernel_inputs` (`rel_dir="sessions/{room}/inputs"`, `:986`), `inputs/x` is **correct** under the kernel's cwd, is documented as the return contract (`:979`), and is pinned by `test_code_exec_kernel.py:174`. The Skills dossier's first draft proposed "fixing" it and would have broken every attachment upload. Only `stage_agent_workspace_files`' use of it is wrong. |
| Q-3 | Fix the path flattening at `turn_engine.py:683`? | **Yes** — same defect class, same note, same turn. | See §6; a workspace file at `reports/q1.csv` is staged as `q1.csv`, so the note names a path that is wrong in a second, independent way. Fixing the prefix while leaving the basename wrong would still fail the exit criterion for any nested file. |
| Q-4 | Repair already-persisted bad data? | **None needed.** | The defect is in a per-turn computed string, not in stored state. No `agent_workspace_files` row, MinIO object, or message row holds the bad path. Messages that *quote* the note in history are historical text and are not rewritten. |

## 4. Reproduction

Deterministic. Preconditions: an agent in a project the actor owns, Code Interpreter enabled
(`AgentToolType.HOSTED_CODE_INTERPRETER`, gated at `turn_engine.py:601-604`), a live sandbox
(the staging path is room-only; `run_input_turn` stages nothing).

1. Upload `data.csv` to the agent's workspace files (`purpose=agent_workspace`).
2. In a chat room with that agent, send a message that makes it run `code_exec`.
3. Observe the system note: `[Files available in the code_exec workspace: agent-files/data.csv]`.
4. Have it run `open('agent-files/data.csv').read()`.

**Actual:** `FileNotFoundError: [Errno 2] No such file or directory: 'agent-files/data.csv'`.
**Also observable:** `os.getcwd()` returns `/workspace/sessions/{room}`, and
`open('/workspace/agent-files/data.csv')` succeeds — which is the whole bug in two calls.

Not reproducible in CI: there is no Docker/gVisor test tier (the `wiring` marker is
Postgres+Redis+MailHog only, `backend/pyproject.toml`). §8 tests the path *computation*; the
end-to-end proof is `/verify` against a live sandbox.

## 5. Root Cause Analysis

1. `_tar_staged_inputs` (`docker_runsc.py:106-139`) does two jobs: it tars into `rel_dir`
   (`:133`, correctly parameterised) and it **reports** staged paths (`:138`,
   `posixpath.join("inputs", name)` — a hardcoded literal). The report is correct for the caller
   it was written for and is unparameterised for any other.
2. `stage_agent_workspace_files` (`:1008`) reuses it with `rel_dir="agent-files"` (`:1037`),
   inheriting a return value that says `inputs/`.
3. It compensates with `_fix_paths` (`:1024-1027`), `p.replace("inputs/", "agent-files/", 1)`,
   applied at `:1032` and `:1058`. This produces a *plausible* string and hides the design flaw:
   the function's caller is patching its callee's output rather than the callee taking a
   parameter.
4. Nobody checked the result against the consumer. The kernel `chdir`s to `_SESSION_DIR`
   (`kernel.py:123`) so relative paths mean *session-relative*; `agent-files/` is *volume-root*
   relative. The two staging trees live at different depths and only one of them is under the
   cwd.

**Root cause: step 3** — `_fix_paths`. The earliest link whose correction prevents the symptom.
It exists solely to launder a return value that was never parameterised, and by making the
output look right it removed the pressure to ask what the path was relative to. Steps 1-2 are
*aggravating*: reusing a two-job function is what created the need for laundering, and is why
the correct fix is a parameter, not a better `replace()`.

The absence of a test tier is an aggravating factor, not the cause: a unit test asserting the
returned string would have passed just as happily against the wrong string. What was missing is
a stated contract for what the returned paths are relative to — which is exactly what
`kernel.py:119-121` and `docker_runsc.py:979` state for `stage_kernel_inputs` and what nothing
states for `stage_agent_workspace_files`.

## 6. Blast Radius and Sibling Suspects

**Blast radius.** Every `code_exec` turn for every agent with persisted workspace files, on
every tenant, since the D.4 feature landed. Bounded by: the paths' **only** consumer is the note
at `turn_engine.py:642` (verified by search — no other reader of `stage_agent_workspace_files`'
or `_stage_persisted_files`' return), so nothing else is corrupted; no persisted state holds the
bad path (Q-4); and staging is wrapped in `try/except` (`:610-613`, `:643-645`), so this never
aborted a turn — it degraded silently, which is why it survived. Cost is model confusion and
wasted tool rounds, not data loss.

**Sibling suspects.**

- `_tar_staged_inputs:138` → **cleared.** Correct for `stage_kernel_inputs` (§3 Q-2).
- `turn_engine.py:683` → **CONFIRMED, second defect, same note.**
  `StagedFile(filename=wf.path.rsplit("/", 1)[-1], ...)` discards every directory component, so
  `reports/q1.csv` stages as `agent-files/q1.csv` while the note (built from the *returned*
  paths) also says `q1.csv` — self-consistent, so the file opens, but the agent's own layout is
  silently flattened and two files named `q1.csv` in different folders collide into
  `q1.csv` / `q1-1.csv` via the disambiguator at `:129-131`. Fixed here (Q-3).
- The `file` tool → **cleared.** `_safe_relpath` (`file_tool.py:30-41`) roots every path at
  `/workspace` by normpath-then-prefix, so `agent-files/x` resolves correctly there. The two
  tools disagree about what a relative path means, which is the deeper hazard — recorded as
  FU-1.
- `stage_kernel_inputs` attachments → **cleared.** `inputs/x` is session-relative and the cwd is
  the session dir. Correct today.
- `_persist_artifacts` (`turn_engine.py:692`) / `outputs/` → **cleared.** `kernel.py:119-121`
  names `outputs/<file>` as session-relative and `_OUTPUTS` is under `_SESSION_DIR`. Consistent.

## 7. Fix Design

1. **Parameterise the report.** Add a keyword-only `report_prefix: str | None = None` to
   `_tar_staged_inputs`; `:138` becomes
   `staged.append(posixpath.join(report_prefix or "inputs", name))`. Omitting it is
   byte-identical to today, so `stage_kernel_inputs` and `test_code_exec_kernel.py:164-185` are
   untouched — a signature change with an unchanged default, not a behavior change.
2. **Delete `_fix_paths`** (`docker_runsc.py:1024-1027`) and its two call sites (`:1032`,
   `:1058`). `stage_agent_workspace_files` passes `report_prefix="/workspace/agent-files"`.
   This is what corrects the root cause rather than masking it: the callee now states the
   contract, and the caller stops rewriting the callee's output.
3. **Preserve the tree.** `turn_engine.py:683` passes `wf.path` instead of
   `wf.path.rsplit("/", 1)[-1]`. `_safe_input_name` (`docker_runsc.py:100-103`) currently
   sanitises a *filename*; staging a relative path needs a path-aware sanitiser that still
   rejects traversal, absolute paths, and control characters. Reuse `_safe_relpath`'s
   normpath-then-prefix reasoning (`file_tool.py:30-41`) rather than inventing a third rule, and
   keep the collision disambiguator for the case where two sanitised paths still collide.
4. **Correct the documentation.** `D-code-interpreter-files.md:137-139`'s exit criterion becomes
   `open('/workspace/agent-files/data.csv')`. The doc is the intent source (§2); leaving it
   stating the broken form would make the next reader "fix" the code back.

Why not merely change `_fix_paths`' replacement string to the absolute prefix: it leaves a
caller patching its callee's output by substring match — a `replace("inputs/", ...)` that is a
silent no-op the moment the callee's literal changes, and that would corrupt any real file named
`inputs/…`. The bug is the laundering, not the string.

**Data repair:** none (Q-4).

## 8. Regression Test Plan

The failing test comes first. New `backend/tests/unit/test_workspace_staging.py`:

- **T-1 (fails now).** Call `stage_agent_workspace_files` with a runner whose Docker client is a
  spy; assert the returned paths are exactly `["/workspace/agent-files/data.csv"]` and that the
  tar member written via `put_archive` is `agent-files/data.csv` under target `/workspace`.
  Against current code the return is `agent-files/data.csv` → red. **The assertion that makes it
  a real test is the pairing**: the reported path, resolved from `/workspace/sessions/{room}`,
  must equal the volume location the tar member lands at. Asserting the string alone is what let
  the bug through.
- **T-2 (fails now).** A workspace file at `reports/q1.csv` returns
  `/workspace/agent-files/reports/q1.csv` and tars to `agent-files/reports/q1.csv` (Q-3).
- **T-3 (passes now, must keep passing).** `_tar_staged_inputs("sessions/room-1/inputs", files)`
  with no `report_prefix` returns `inputs/a.csv` — characterization of the contract at
  `docker_runsc.py:979` / `kernel.py:119-121`. Extends, does not replace,
  `test_code_exec_kernel.py:164-185`.
- **T-4.** Traversal (`../../etc/passwd`), absolute (`/etc/passwd`), and control-character paths
  are rejected, not sanitised into something plausible.
- **T-5.** The note text from `_stage_workspace_inputs` contains the absolute agent-files path
  and the relative `inputs/` attachment path **in the same string** — the mixed form is
  intentional (each is correct for its own tree) and a future reader must not "unify" them.

End-to-end (`/verify`, needs a live sandbox): the §4 reproduction, expecting a successful read.

## 9. Risks and Rollback

- **The model is told a new path shape.** Absolute paths are unambiguous and need no cwd
  knowledge; the risk is agents with hardcoded `agent-files/...` in their system prompts, which
  are broken today anyway. No behavior regresses that currently works.
- **T-3 is the guard rail.** The single highest risk is a future change to `_tar_staged_inputs`'
  default that silently breaks attachments. T-3 exists to make that red.
- **Path-preserving staging widens the sanitiser's input** from filenames to relative paths — a
  security-relevant surface, covered by T-4. If the sanitiser cannot be made airtight, ship
  steps 1-2 and 4 alone; they fix the reported defect, and Q-3 can fall back to FU-2.
- **Rollback:** revert. No migration, no persisted state, no API contract change.

## 10. Acceptance Criteria

- [ ] AC-1: T-1 fails against current code and passes after the fix.
- [ ] AC-2: `code_exec` running `open('/workspace/agent-files/data.csv')` reads a file the
      designer uploaded, and the note names that exact path (`/verify`, live sandbox).
- [ ] AC-3: A workspace file at `reports/q1.csv` is readable at
      `/workspace/agent-files/reports/q1.csv`; its directory is preserved.
- [ ] AC-4: `stage_kernel_inputs` still returns `inputs/x` and
      `test_code_exec_kernel.py:164-185` passes **unmodified**; attachment upload into
      `code_exec` still works end-to-end.
- [ ] AC-5: `_fix_paths` no longer exists; `rg '_fix_paths' backend/` returns no matches.
- [ ] AC-6: `D-code-interpreter-files.md`'s exit criterion states the absolute path, and
      `rg "open\('agent-files/" docs/` returns no matches.
- [ ] AC-7: Traversal, absolute, and control-character workspace paths are rejected at staging.
- [ ] AC-8: Two workspace files whose sanitised paths collide remain distinguishable, and
      neither silently overwrites the other.

## 11. SRS Delta

The reported path shape needs no requirement: `[R31.22]` (approved 2026-07-16) already requires
skill scripts to be "reported to the model as absolute paths", so this fix brings `agent-files`
into line with a rule the SRS states for the sibling tree, and step 4 corrects the document that
is actually the intent source here (`D-code-interpreter-files.md`).

**But the analysis found `[R12.03]` factually wrong, and it must be corrected.**

`REQUIREMENTS.md:582` (the Lifetime bullet of `[R12.03]`) reads, in part:

> User-provided MCP containers do NOT receive this mount; only the built-in `file` tool container does.

The second clause is false. **Four** containers mount `smap-agent-fs-{agent_id}` at `/workspace`:
the `file` tool (`docker_runsc.py:604`), the persistent `code_exec` kernel (`:917`), the
attachment-staging container (`:990`), and the workspace-file-staging container (`:1041`). The
kernel mounting it is not an accident — it is the whole of the D.4 feature this bugfix serves;
without that mount there would be no `agent-files` to report. The SRS was written before D.4 and
never updated.

This matters beyond tidiness: `:582` is a **security** statement, and a reader auditing the
sandbox boundary would conclude the code violates it. The load-bearing half — *MCP containers do
not receive this mount* — remains true and is preserved verbatim.

**Edit (a) — `REQUIREMENTS.md`, line 582**, replace:

> User-provided MCP containers do NOT receive this mount; only the built-in `file` tool container does.

with:

> User-provided MCP containers do NOT receive this mount. The containers that do are the platform's own: the built-in `file` tool, the `code_exec` kernel and its staging helpers (§12.3, which is how persisted files reach the interpreter), each of which runs with `network_mode="none"`.

No `docs/traceability.csv` change: `R12.03`'s summary is the requirement's opening line, not this
sub-bullet — verify at apply time and leave it alone if so.

## 12. Deviation Log

Appended by `/build`.

## 13. Follow-ups

> **Verification sweep, 2026-07-17.** Re-checked against the tree during the 0716 follow-up triage.
> Every claim below still holds, but **every `turn_engine.py` line number in this dossier is stale**:
> the agent-skills Phase 0 diff shifted that file by ~165 lines, so `_stage_persisted_files` is now
> `turn_engine.py:813-861` and §5's note is at `:808`. `docker_runsc.py` and `kernel.py` citations
> are all still exact. Two dispositions: **FU-1 has been promoted** — it is the entire subject of
> `2026-07-16-workspace-path-convention`, so it is closed here rather than pending; and **FU-3 is a
> duplicate of `2026-07-16-agent-skills`' FU-6**, which is where its analysis now lives.

- **FU-1: the two tools disagree about what a relative path means — on the same volume.**
  Verified: both mount `smap-agent-fs-{agent_id}` at `/workspace` (`docker_runsc.py:604` for
  `file`, `:917` for the kernel), but `file` roots relative paths at `/workspace`
  (`_safe_relpath`, `file_tool.py:30-41`) while `code_exec` roots them at
  `/workspace/sessions/{room}` (`kernel.py:123`). So the same string handed to both tools reaches
  two different files: `file` writes `notes.md` → `/workspace/notes.md`; `code_exec` then does
  `open('notes.md')` → `/workspace/sessions/{room}/notes.md` → `FileNotFoundError`. This bugfix
  sidesteps the collision for the `agent-files` tree by going absolute, but the divergence itself
  is untouched and is a live source of model confusion whenever an agent uses both tools.
  Deserves a decision: one root, or a per-tool convention stated in the tool descriptions the
  model actually reads.
  **Verified 2026-07-17: confirmed (all citations exact) — and PROMOTED, so this entry is closed.**
  The decision it asks for is the entire subject of `2026-07-16-workspace-path-convention`, which
  says so at its own `:20-22`. This is not pending work; it is a follow-up that graduated into a spec
  and was left behind in the list it came from. Note the subject now appears at **three** altitudes
  across the 0716 dossiers: here (the observation), that dossier (the decision), and that dossier's
  own FU-1 (the deeper "one scheme for all tools" question it declines). Not contingent — the
  divergence is live code today.
- **FU-2: path-preserving staging, if Q-3 is dropped** under §9's fallback.
  **Verified 2026-07-17: the flattening is real, but this is the most contingent entry in the whole
  0716 set — it may never come into being.** The flattening is `turn_engine.py:854` (cited `:683`;
  stale), `wf.path.rsplit("/", 1)[-1]`, so `reports/q1.csv` stages as `agent-files/q1.csv`. But this
  entry exists only if (a) this dossier is built **and** (b) Q-3 (`:50`) then falls back per §9's
  risk clause (`:191`) because the sanitiser cannot be made airtight. If Q-3 ships as decided, there
  is nothing here. It is a follow-up of a decision not yet made about work not yet done — do not
  spec it.
- **FU-3: `_WORKSPACE_MANIFESTS` (`docker_runsc.py:218`) is module-global, in-process,
  unbounded, and never invalidated.** It can lie if the volume is removed out of band, and skills
  staging will add a second cache beside it. Carried from the Skills dossier's FU-6; untouched
  here.
  **Verified 2026-07-17: confirmed (`:218` exact) — DUPLICATE of `2026-07-16-agent-skills`' FU-6, as
  this entry's own last sentence admits. One item, listed twice; dedupe on sight.** The verified
  analysis lives at that FU-6, which also establishes it is one defect with that dossier's FU-19
  (the false-positive and false-negative directions of "SMAP has no reliable model of what is on the
  volume"). Not contingent — live code, independent of either draft. Only the "skills staging will
  add a second cache beside it" rider depends on agent-skills Phase 1.
- **FU-4: no Docker/gVisor test tier.** AC-2/AC-3 can only be proven by `/verify`. This bug is
  the argument for the tier: every unit test in the world would have passed against the wrong
  string, because no test could execute the kernel that defines what the string means.
  **Verified 2026-07-17: confirmed, not contingent, and it is a standing gap rather than this
  dossier's debt.** `pyproject.toml:353-357` defines `wiring` as "real Postgres+Redis+MailHog" — no
  Docker, no gVisor; `docker==7.1.*` (`:42`) is a runtime dep, not a test tier; all four
  `docker_runsc.py` test files assert mock call args only. **Supersedes
  `2026-07-16-workspace-path-convention`' FU-2**, which is the same root cause (nothing in CI runs a
  container, so image/runtime drift is undetectable) narrowed to one instance — though that entry's
  build-stamp check is a much cheaper partial fix worth taking first. Type is `feature`/infra (new
  tier + CI runner); no bugfix dossier should absorb it.
