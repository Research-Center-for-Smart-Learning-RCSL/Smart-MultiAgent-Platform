---
type: bugfix
status: implemented
created: 2026-07-17
requirements: [R12.05, R12.03]
supersedes: 2026-07-16-code-exec-agent-files-path
---

# `agent-files/` paths do not resolve inside `code_exec` — the note and the staging use different roots

## 1. Summary

Split from `2026-07-16-agent-skills`' FU-15, which found this during review and deliberately did not
fix it. Every claim below was re-verified against the tree on 2026-07-17, and the entry **understated
the root cause** — see §5.

A designer uploads `data.csv` to an agent's workspace. The turn stages it, tells the model the file
is at `agent-files/data.csv`, and the model's `open('agent-files/data.csv')` raises
`FileNotFoundError`. The file is on the volume. The path is wrong.

`stage_agent_workspace_files` writes to `/workspace/agent-files/` and reports `agent-files/x`
(`contexts/agents/infrastructure/sandbox/docker_runsc.py:1008-1056`). The kernel `chdir`s to
`/workspace/sessions/{room}` before executing (`deploy/sandbox/code-exec/kernel/kernel.py:37-41`,
`:116-122`), so the model's relative path resolves to `/workspace/sessions/{room}/agent-files/data.csv`
while the file sits at `/workspace/agent-files/data.csv`.

**The `file` tool is unaffected** — `_safe_relpath` roots every path at `/workspace`
(`contexts/agents/application/tools/file_tool.py:30-41`), so `agent-files/x` resolves correctly there.
One string, two tools, two meanings. That is why nobody caught it.

The defect is not confined to code. It is written into three places as though it were correct:
- **The model-facing note** (`turn_engine.py:808`) flattens two coordinate systems into one sentence.
- **The designer-facing UI hint** (`slices/agents/locales/{en,zh-TW}.json:392`) names both tools and
  gives one path form — true for `file`, false for Code Interpreter.
- **The spec's own exit criterion** (`docs/agent-tools/D-code-interpreter-files.md:138`) is
  `open('agent-files/data.csv')` **and reads it**. That criterion could never have passed.

## 2. Observed vs Expected

| | |
|---|---|
| **Observed** | Model is told `[Files available in the code_exec workspace: agent-files/data.csv]`. `open('agent-files/data.csv')` → `FileNotFoundError`. The file exists at `/workspace/agent-files/data.csv`. |
| **Expected** | The path the model is told is the path the model can open. |
| **Scope** | `code_exec` only. The `file` tool resolves `agent-files/x` correctly (`file_tool.py:30-41`) and is not touched. |
| **Not observed** | `inputs/x` (the triggering message's attachments) works — see §5, it is correct **by coincidence**. |

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | How is the path made to resolve? | **The model-facing note carries absolute paths**: `/workspace/agent-files/x` and `/workspace/sessions/{room}/inputs/x`. | It does not touch the volume layout, the kernel, or the `chdir` — and the `chdir` is deliberate, with a comment (`kernel.py:118-120`) saying it exists so `inputs/`/`outputs/` resolve at the session dir. An absolute note is the only option that leaves that intent intact while being unambiguous from any cwd. It matches the precedent the skills block already set (`/workspace/skills/{name}/`, `contexts/skills/domain/models.py:61`, `:143`). And it is **safe for the `file` tool**: `_safe_relpath` accepts absolute and relative alike (`file_tool.py:37` — `candidate = raw if raw.startswith("/") else posixpath.join(_ROOT, raw)`), so one absolute form feeds both tools. |
| Q-2 | Why not a per-room symlink `/workspace/sessions/{room}/agent-files → /workspace/agent-files`? | **Rejected.** | It would keep every documented relative form working, which is its whole appeal. But it puts a symlink inside a security sandbox whose containment control is path-prefix-based (`_safe_relpath:38-40` asserts `normed.startswith(_ROOT + "/")` — `normpath` does **not** resolve symlinks, so the check would pass on a path that traverses one). It also risks the kernel's artifact diffing (`kernel.py:97-112`, `_snapshot()`) walking the link and re-persisting every workspace file as a turn artifact. Cheaper-looking, materially riskier. |
| Q-3 | Why not stage `agent-files` under the session dir? | **Rejected.** | It breaks the persistence model. `agent-files` is per-**agent** and persists across rooms and turns, keyed by `manifest_sha` on one volume (`docker_runsc.py:1015-1017`, `_WORKSPACE_MANIFESTS`). Sessions are per-room. Staging per-room would duplicate every file per room and make the manifest cache describe a location it does not own. |
| Q-4 | Fix `_tar_staged_inputs`' coordinate-system split too, or only the symptom? | **Fix it. Return what was actually staged; delete `_fix_paths`.** | §5 shows the split *is* the root cause — the symptom is downstream of it. Patching only the note leaves a helper whose docstring says "Returns (archive, staged_relative_paths)" while returning paths it did not stage, plus a `_fix_paths` string-rewrite that patches the symptom for one caller. The next caller walks into the same hole. Q-1 without Q-4 is a bandaid on the wrong layer. |
| Q-5 | Does the designer-facing UI hint change? | **Yes — it is part of the defect.** | `locales/{en,zh-TW}.json:392` tells the designer their files are at `agent-files/<path>` "to this agent's Code Interpreter and File Workspace tools". The designer reads that, writes `open('agent-files/…')`, and hits the bug. A fix that corrects the model's note and leaves the human's note lying has fixed half of it. AC-7. |
| Q-6 | `2026-07-16-code-exec-agent-files-path` (draft) specs this same bug and **rejects Q-4** — its Q-2 calls the hardcoded `"inputs"` "not a bug", its AC-4 requires `test_code_exec_kernel.py:164-185` pass *unmodified*. Which wins? | **This dossier, merged.** 0716 is superseded; its Q-3 and its SRS Delta are absorbed here (Q-7, §11). | Both dossiers are splits of agent-skills FU-15 that were written without knowledge of each other — the duplication is the finding, not a tie to break. On the mechanism: 0716's `report_prefix` param keeps a default whose correctness is *coincidental* (§5.1) and adds a second way to say what `rel_dir` already says. Its stated reason for rejecting Q-4 — that `inputs/x` "is documented as the return contract (`:979`)" — cites a docstring §7 shows is **wrong twice**. A contract that misdescribes its own function does not bind. 0716's AC-4 is therefore preserving a test that pins a defect, which is the same error as `test_workspace_staging.py`'s fake (§8, FU-4). Its Q-2 was right about one thing: *`stage_kernel_inputs`' resolved file location must not move* — kept verbatim as AC-4's second clause. |
| Q-7 | 0716's Q-3: workspace files are flattened — `reports/q1.csv` stages as `q1.csv`, and two `q1.csv` in different folders collide via the disambiguator (`docker_runsc.py:129-131`). Absorb it? | **Yes.** New AC-12/AC-13. | It is the same note, the same turn, the same staged tree: fixing the prefix while the basename stays wrong still fails the exit criterion for any nested file, so AC-3 would pass over a still-broken feature. **0716 mislocates it and understates it**: it blames `turn_engine.py:857` (`wf.path.rsplit("/", 1)[-1]`), but `_tar_staged_inputs` calls `_safe_input_name` (`:127`) which reduces to a basename by construction (`sanitize.py:8`) — so passing `wf.path` through changes nothing on its own. **Two** sites flatten; both must move. |
| Q-8 | 0716 §9 makes Q-3 contingent: "if the sanitiser cannot be made airtight, ship steps 1-2 and 4 alone" and fall back to FU-2. Does that fallback apply? | **No — the airtight sanitiser already exists and already ran.** | `_safe_workspace_path` (`workspace_service.py:55-73`) rejects `..`, absolute paths, null bytes, and over-length, and every upload passes through it (`:106`); `path` is the row's identity (`:111` `get_by_path`). So `wf.path` is *already* normalised and traversal-free before staging reads it. 0716 §7.3 proposed inventing a path-aware rule ("reuse `_safe_relpath`'s reasoning") without noticing the rule it wanted was already enforced one layer up. Staging still re-validates rather than trusting the row — defense in depth across a sandbox boundary, and the DB is not an input the sandbox should trust — but it **reuses `_safe_workspace_path`**, it does not author a third rule. AC-13. |

## 4. Reproduction

No test reproduces this today (§8 explains why the existing test cannot). The manual path:

1. An agent with `hosted_code_interpreter` enabled (`turn_engine.py:767-770` gates staging on it).
2. Upload a workspace file (`purpose=agent_workspace`), e.g. `data.csv`.
3. Send a message that triggers a turn. `_stage_persisted_files` (`:813`) runs, and the system note
   (`:808`) reads `[Files available in the code_exec workspace: agent-files/data.csv]`.
4. Have the agent run `code_exec` with `open('agent-files/data.csv')`.
5. **`FileNotFoundError`.** `os.listdir('/workspace/agent-files')` from the same cell shows the file.

Step 4 is `D-code-interpreter-files.md:138`'s exit criterion verbatim.

## 5. Root Cause Analysis

**The proximate cause** is that `turn_engine.py:808` builds one flat sentence out of two path families
that resolve against different roots:

```
return "[Files available in the code_exec workspace: " + ", ".join(all_paths) + "]"
```

`all_paths` is fed by two producers (`:777` and `:799`) whose outputs are *not* in the same coordinate
system. The note implies one root. There are two.

**The actual root cause is one function returning paths in a different coordinate system than it
writes them.** `_tar_staged_inputs(rel_dir, files)` (`docker_runsc.py:106-138`) — docstring: *"Returns
(archive, staged_relative_paths)"*:

- tar member name (`:133`): `posixpath.join(rel_dir, name)` — **respects `rel_dir`**
- returned path (`:138`): `posixpath.join("inputs", name)` — **hardcodes `"inputs"`, ignores `rel_dir`**

So it does not return the paths it staged. It returns them as if `rel_dir` were always `inputs`. Both
callers are downstream of that:

| Caller | `rel_dir` | Writes to | Returns | Kernel cwd | Resolves to | |
|---|---|---|---|---|---|---|
| `stage_kernel_inputs` (`:975`) | `sessions/{room}/inputs` | `/workspace/sessions/{room}/inputs/x` | `inputs/x` | `/workspace/sessions/{room}` | `/workspace/sessions/{room}/inputs/x` | correct |
| `stage_agent_workspace_files` (`:1008`) | `agent-files` | `/workspace/agent-files/x` | `inputs/x` → `_fix_paths` → `agent-files/x` | `/workspace/sessions/{room}` | `/workspace/sessions/{room}/agent-files/x` | **broken** |

Two things fall out of that table, and neither is in FU-15:

1. **`stage_kernel_inputs` is correct by coincidence.** The hardcoded `"inputs"` happens to equal the
   session-relative form of what it staged, because its `rel_dir` ends in `inputs` and the kernel
   chdirs to that dir's parent. Change either the `chdir` target or the `rel_dir` and it breaks
   silently. Nothing states this coupling; nothing tests it.
2. **`_fix_paths` (`docker_runsc.py:1023-1024`) patches a string, not a coordinate system.** Its
   comment is honest about what it is doing — *"`_tar_staged_inputs` returns paths with a hardcoded
   `inputs/` prefix. We need to replace it with `agent-files/` for our directory."* — and it produces
   a path that is correct **relative to `/workspace`** and wrong **relative to the kernel's cwd**. It
   is load-bearing (deleting it naively breaks every `agent-files/` path), which is exactly why it
   reads as a fix and is actually the bug's last mile.

**Why it survived review:** `agent-files/x` is *correct for the `file` tool* (`file_tool.py:30-41`
roots at `/workspace`, no chdir). The same literal string is right in one tool and wrong in the other,
and the only tool that gets it wrong is the one with no test that executes it.

## 6. Blast Radius and Sibling Suspects

**Blast radius.** Every `code_exec` turn for an agent with persisted workspace files, on every
platform, since the feature shipped. Not intermittent, not environmental — the path is wrong 100% of
the time. Failure mode is a `FileNotFoundError` inside the model's own code, so it surfaces as the
agent apologising and retrying, not as an error the operator sees. There is no data loss: the file is
on the volume and the `file` tool reads it fine.

**Sibling suspects — the sweep, done at spec time:**
- **`stage_kernel_inputs` (`:975`)** — same helper, same hardcoded prefix. **Correct by coincidence**
  (§5). In scope for Q-4's fix; its *output* must not change.
- **`_fix_paths` (`:1023`)** — the other consumer of the split. Deleted by Q-4.
- **`kernel.py`'s `_OUTPUTS`/`_INPUTS` (`:40-41`)** — session-rooted and consistent with the chdir.
  Not affected.
- **The `file` tool (`file_tool.py:30-41`)** — verified correct, accepts both forms (`:37`). Not
  touched, and Q-1 is chosen partly because it stays true for this tool.
- **`driver.py`'s `cmd_file` (`deploy/sandbox/driver/driver.py:237-243`)** — a third root
  (`/workspace`, no chdir). Consistent with the `file` tool. Not affected by this fix, but it is the
  same *class* of hazard — see `2026-07-17-sandbox-guest-container-tests`, whose AC-7 asserts the
  three roots agree and would catch this bug's shape.
- **The skills block** — already uses absolute `/workspace/skills/{name}/`
  (`contexts/skills/domain/models.py:61`, `:143`), i.e. the convention Q-1 adopts. Not affected.

## 7. Fix Design

**1. `_tar_staged_inputs` returns what it staged** (`docker_runsc.py:138`):
```python
-            staged.append(posixpath.join("inputs", name))
+            staged.append(posixpath.join(rel_dir, name))
```
Its return becomes **volume-relative** (`sessions/{room}/inputs/x`, `agent-files/x`) and matches its
docstring for the first time.

**2. `_fix_paths` is deleted** (`docker_runsc.py:1022-1024`, and its two call sites `:1027`, `:1056`).
It exists solely to undo step 1's old behaviour.

**3. Each staging method returns an absolute, model-usable path.** Both now prefix `/workspace/`:
- `stage_kernel_inputs` → `/workspace/sessions/{room}/inputs/x`
- `stage_agent_workspace_files` → `/workspace/agent-files/x`

The conversion is explicit at each call site rather than implied by a shared helper — that implication
is what broke. Note `stage_kernel_inputs`' docstring (`:978`) currently says *"Returns the
workspace-relative paths actually staged (e.g. `inputs/x`)"*; it is wrong twice (not
workspace-relative, not what was staged) and must be rewritten.

**4. `turn_engine.py:808`** needs no logic change — `all_paths` now holds absolute paths and the note
becomes true. The sentence stays as-is.

**5. The designer-facing hint** (`slices/agents/locales/{en,zh-TW}.json:392`) is corrected per Q-5.
The `file` tool genuinely accepts `agent-files/<path>`, so the string must distinguish the two tools
rather than simply swapping one path for another. **No hardcoded text — both locales, `$t()` as
today.**

**6. The docs.** `docs/agent-tools/D-code-interpreter-files.md:138` (the exit criterion), `:110`,
`:150`, `:178`, `:232`. `:138` is the one that matters: it is why a broken feature was signed off.

**Reuse inventory:** `_safe_relpath` (`file_tool.py:30-41`) — the existing absolute/relative
normaliser and the containment control; do not write a second one. `posixpath` throughout — never
`os.path` (host is Windows for some devs, guest is Linux).

## 8. Regression Test Plan

**Write the test first, and watch it fail for the documented reason** — `FileNotFoundError` on
`agent-files/data.csv`, not an assertion tweak.

**The existing test is the problem, not the baseline.** `tests/unit/test_workspace_staging.py:37-39`
is a fake whose `stage_agent_workspace_files` returns `[f"agent-files/{f.filename}" for f in files]`,
and `:84`/`:95` assert `out == ["agent-files/a.csv", "agent-files/b.csv"]`. **The fake hardcodes the
same wrong answer the real code produces, and the assertion enshrines it.** It passes while testing
nothing about path resolution. These assertions must be *inverted*, not extended — and that inversion
is the clearest proof the bug is real.

1. **Unit — `_tar_staged_inputs` returns its own tar member names.** Pure, no Docker: build the
   archive, read the member names back out of the tarball, assert the returned list equals them for
   `rel_dir="agent-files"` **and** `rel_dir="sessions/r1/inputs"`. This is the root cause in one
   assertion, and it fails today for both.
2. **Unit — the note is absolute.** Both staging paths produce `/workspace/…`-rooted strings; the
   note at `:808` contains no bare `agent-files/`.
3. **Unit — update `test_workspace_staging.py`.** Its fake must return what the real method now
   returns.
4. **Guard — `stage_kernel_inputs`' resolved location is unchanged.** Its *return* changes
   (`inputs/x` → `/workspace/sessions/{room}/inputs/x`) but the file must still land at the same
   place. Assert the tar member name, which is the thing that must not move.
5. **Container (deferred, noted).** The real proof is `open('/workspace/agent-files/data.csv')`
   succeeding inside the image. That needs the tier `2026-07-17-sandbox-guest-container-tests` builds.
   Not a blocker — 1-4 pin the defect — but say so rather than implying unit tests prove the fix.

## 9. Risks and Rollback

- **`stage_kernel_inputs`' return value changes shape** even though its behaviour does not. Anything
  that pattern-matches `inputs/` on that return breaks. Grep before changing; `turn_engine.py:804` is
  the only production consumer found, and it only concatenates.
- **The model may have learned the old form.** Agents whose instructions or few-shot examples hardcode
  `open('agent-files/…')` keep failing after the fix — they were failing before, so this is not a
  regression, but a designer who "fixed" it by writing `/workspace/agent-files/…` by hand is now
  doubly right and nothing breaks. `_safe_relpath` accepts both, so the `file` tool tolerates either.
- **The `chdir` is load-bearing** (`kernel.py:118-122`) and this fix deliberately does not touch it.
  Any implementer tempted to "simplify" by removing it breaks `inputs/`/`outputs/`, which is the
  documented contract for attachments and artifacts.
- **Rollback:** all changes are string/return-value shaped, in one file plus a locale pair and a doc.
  No migration, no volume change, no data. Revert restores the previous (broken) behaviour exactly.

## 10. Acceptance Criteria

- [x] AC-1: `_tar_staged_inputs` returns exactly the tar member names it wrote, for any `rel_dir`
      (`docker_runsc.py:138`), and its docstring is true.
      → `test_tar_staged_inputs_returns_the_paths_it_wrote`, parametrized over both callers' `rel_dir`.
- [x] AC-2: `_fix_paths` no longer exists. → `rg '_fix_paths' backend/` returns nothing.
- [x] AC-3: `stage_agent_workspace_files` returns `/workspace/agent-files/x`; the model's
      `open('/workspace/agent-files/data.csv')` resolves regardless of the kernel's cwd.
      → **verified in a container**, not merely asserted: see §14.
- [x] AC-4: `stage_kernel_inputs` returns `/workspace/sessions/{room}/inputs/x`, **and the file still
      lands at the identical path it does today** — the tar member name is unchanged (§8.4).
      → `test_tar_staged_inputs_builds_dirs_and_files` pins the member names; §14 confirms
      `inputs/upload.csv` still opens from the kernel's cwd, i.e. the file did not move.
- [x] AC-5: the note (`turn_engine.py:808`) contains only absolute paths; no bare `agent-files/`.
      → `test_the_note_the_model_reads_carries_only_absolute_paths`.
- [x] AC-6: `test_workspace_staging.py`'s assertions are inverted to the corrected contract, and its
      fake no longer returns the broken form.
- [x] AC-7: the designer-facing hint (`locales/{en,zh-TW}.json:392`) tells the truth for **both**
      tools, in both locales, via `$t()`.
- [x] AC-8: `D-code-interpreter-files.md:138`'s exit criterion names a path that can pass; `:110`,
      `:150`, `:178`, `:232` agree with it. → `:110`/`:178`/`:232` were already accurate about where
      bytes *land*; only what was *reported* was wrong, so `:109` now states the distinction rather
      than rewriting correct text (D-5).
- [x] AC-9: the `file` tool is unchanged and still accepts both forms — a test pins
      `_safe_relpath('agent-files/x') == _safe_relpath('/workspace/agent-files/x')`.
      → `test_file_tool_reads_both_path_forms_identically`; `file_tool.py` has no diff.
- [x] AC-10: the regression test from §8.1 was seen **red** before the fix, for the documented reason.
      → `AssertionError: 'inputs/data.csv' != 'sessions/r1/inputs/data.csv'`, both parametrizations,
      before any production edit. The `[sessions/r1/inputs]` case failing is §5's "correct by
      coincidence" made visible.
- [x] AC-11: gates green — `pytest -q` (4786 unit passed), `ruff check .`, `ruff format --check .`
      (768 files), `mypy .` (768 files, no issues); `pnpm test` (691 passed), `pnpm lint`,
      `pnpm typecheck`, `pnpm build` all exit 0.
      **`tests/wiring/` is not run**: it needs real Postgres/Redis/MailHog and fails identically on a
      clean tree (`socket.gaierror` on `redis:6379`) — environmental, confirmed by stashing the diff.
      One `mypy` error exists in the tree (`knowledge/infrastructure/qdrant_teardown.py:36`) and is
      **not** from this task — it belongs to unrelated uncommitted work sharing the working tree, and
      is not present when checking this task's modules.

Absorbed from superseded `2026-07-16-code-exec-agent-files-path` (Q-6):

- [x] AC-12: a workspace file at `reports/q1.csv` is staged to `agent-files/reports/q1.csv` and
      reported as `/workspace/agent-files/reports/q1.csv` — its directory survives. Both flattening
      sites are fixed (`turn_engine.py:857`, `_safe_input_name` at `docker_runsc.py:127` — Q-7), and
      two files named `q1.csv` in different folders no longer collide into `q1.csv`/`q1-1.csv`.
      → `test_workspace_staging_preserves_the_designers_tree`; §14 opens the nested file for real.
- [x] AC-13: staging re-validates the tree-preserving path via the shared-kernel rule, not a third
      hand-rolled one (Q-8); traversal, absolute, and non-printable paths are rejected at staging even
      though the API boundary already rejects them. Attachments (`stage_kernel_inputs`) keep
      flattening to a basename — for them it is correct.
      → `test_the_shared_rule_rejects_unstageable_paths` (5 cases),
      `test_attachments_still_flatten_to_a_basename`,
      `test_no_accepted_path_can_escape_the_staging_dir`. **Rule promoted to `shared_kernel`, not
      reused in place — see D-1**; leading-slash asymmetry, D-2; and "rejected" means *that file is
      skipped and logged*, not the batch raised — D-10.
- [x] AC-14: `REQUIREMENTS.md:582` no longer claims only the `file` tool container mounts the volume
      (§11), and its "MCP containers do NOT receive this mount" clause is preserved.

## 11. SRS Delta

**No requirement governs the path shape, and none is added here.** `[R12.05]` (`REQUIREMENTS.md:588`)
defines `code_exec` on the curated image and `[R12.03]` (`:575`) the sandbox constraints; neither
states a path convention, and neither is violated by the current code — which is precisely how this
shipped. The fix restores the *documented* behaviour (`D-code-interpreter-files.md:138`) rather than
changing an intended one, so it is a bugfix against a design doc. `2026-07-16-workspace-path-convention`
owns the convention itself (§13 FU-1).

**But `[R12.03]` contains a false statement, inherited from superseded 0716 §11 and re-verified here.**
`REQUIREMENTS.md:582` (the Lifetime bullet) reads, in part:

> User-provided MCP containers do NOT receive this mount; only the built-in `file` tool container does.

The second clause is false. **Four** containers mount `smap-agent-fs-{agent_id}` at `/workspace`: the
`file` tool (`docker_runsc.py:604`), the persistent `code_exec` kernel (`:917`), the attachment-staging
container (`:990`), and the workspace-file-staging container (`:1041`). The kernel's mount is not an
accident — it is the whole of the feature this dossier repairs; without it there would be no
`agent-files` to report. The SRS predates that feature and was never updated.

This is in scope because `:582` is a **security** statement: an auditor reading it would conclude the
code violates the sandbox boundary, and this dossier's own §6 sweep had to establish the true mount set
to bound the blast radius. The load-bearing half — *MCP containers do not receive this mount* — is true
and is preserved verbatim.

**Edit — `REQUIREMENTS.md:582`**, replace the clause above with:

> User-provided MCP containers do NOT receive this mount. The containers that do are the platform's own: the built-in `file` tool, the `code_exec` kernel, and the two staging helpers that populate `inputs/` and `agent-files/` — each of which runs with `network_mode="none"`.

No `docs/traceability.csv` change: `R12.03`'s summary is the requirement's opening line, not this
sub-bullet. AC-14.

Worth naming: **the absence of a stated path convention is what let two roots coexist.** A requirement
fixing the workspace path contract belongs with `2026-07-16-workspace-path-convention`, which owns
that decision; this dossier deliberately does not author it (§13 FU-1).

## 12. Deviation Log

- **D-1: the reuse target moved to `shared_kernel`.** §7's inventory said to reuse `_safe_relpath`
  (`file_tool.py:30-41`) and Q-8/AC-13 said to reuse `_safe_workspace_path` (`workspace_service.py:55`).
  **Both would be upward imports** — `file_tool` and `workspace_service` are `application`,
  `docker_runsc` is `infrastructure`, and CLAUDE.md forbids infrastructure importing application. The
  rule is therefore *promoted* to `shared_kernel/storage/sanitize.py` as `validate_workspace_relpath`
  (validate/normalise) and `safe_workspace_relpath` (validate + per-component clean, the staging rule).
  `workspace_service._safe_workspace_path` now delegates to the former, so there is still exactly one
  rule and no layer boundary is crossed. This is the same file both layers already imported
  `safe_input_name` from, so it is the established home, not a new one.
- **D-2: `safe_workspace_relpath` rejects a leading `/`; `validate_workspace_relpath` strips it.**
  AC-13 says "absolute … rejected at staging", but the upload boundary has always *stripped* a leading
  slash (`_safe_workspace_path`'s `.strip("/")`), and its output keys the row (`get_by_path`). Making
  the shared rule reject would change what upload accepts and re-key existing rows — out of scope and
  a data risk. The split resolves it: upload normalises user input, staging checks an invariant on a
  path that has already been normalised, so an absolute path arriving at staging means something
  upstream broke and must not be papered over. Consequence: a stored path can never be absolute, so
  the staging rejection is defense-in-depth that should never fire on real data.
  **This asymmetry is only safe because validation is idempotent — which it was not; see D-7.**
- **D-3: tree preservation introduced a file/directory name collision, found in self-audit and fixed
  here.** `reports` (a file) and `reports/q1.csv` are both legal, distinct rows. Preserving the tree
  made staging emit a *file* member and a *directory* member for `agent-files/reports`, so extraction
  fails — and since `turn_engine` swallows staging faults to protect the turn (`:778`), the result
  would have been **every** workspace file silently vanishing for that agent over one odd upload. The
  flattening code could not hit this, so the fix created it. `_tar_staged_inputs` now reserves any
  name some file needs as a directory. Regression:
  `test_a_file_does_not_take_a_name_another_file_needs_as_a_folder`.
- **D-4: `_disambiguate` now loops and is directory-aware.** The old inline disambiguator
  (`docker_runsc.py:129-131`) suffixed with `len(seen)` **once** and did not re-check, so a second
  collision silently overwrote; and its `rpartition(".")` on a full path would have mangled a
  *directory* name once paths carried directories (`a.b/c` → `a-1.b/c`). Not called out in the spec
  because the spec did not anticipate tree-carrying names reaching it.
- **D-5: `D-code-interpreter-files.md:110`/`:178`/`:232` were left as-is.** AC-8 asked them to "agree
  with" the corrected `:138`. On reading, they already did: all three describe where bytes *land*
  (`agent-files/`, `/workspace/agent-files/`), which was never wrong — only what was *reported* was.
  Rewriting correct text would obscure the actual lesson, so `:109` now names the land/report
  distinction instead.
- **D-7: validation was not idempotent, and one ordinary upload permanently killed the feature.**
  Found by the security audit (Step 5.6), not by me. `validate_workspace_relpath` stripped whitespace
  *before* slashes, so uploading `path=/ /x.csv` stored ` /x.csv` — which re-validates to `/x.csv` and
  is then rejected by D-2's leading-slash check. Staging raised for the **whole batch**, and
  `turn_engine.py:778` swallows staging faults, so any designer with `RESOURCE_CREATE_EDIT` could, with
  one upload and no traversal, silently and permanently stop every workspace file reaching that agent's
  model. Fix: whitespace is stripped per component before any slash handling, so the function is
  idempotent — the property D-2 silently assumed. Regression:
  `test_upload_normalisation_is_idempotent`, plus a 3908-path idempotence fuzz at build time.
- **D-8: staging rewrote the path it was told to stage — this dossier's own defect, one layer over.**
  Found by the quality audit. `safe_workspace_relpath` ran `safe_input_name` per component, and that
  function does `lstrip(".")`, so the stored `.config/app.json` staged as `agent-files/config/app.json`.
  The designer sees the stored path in the UI; the model got a different one. Having just fixed "the
  model is told a path that does not resolve", the fix introduced "the designer is told a path that is
  not where the file is". Staging now re-validates and returns the stored path **unchanged**, so stored
  == staged == reported. Regression: `test_the_stored_path_is_the_staged_path`.
- **D-9: containment no longer rests on an accident, and is now asserted rather than argued.**
  D-8 removed `safe_input_name` from the staging path — which the security audit had identified as
  *load-bearing*: its `lstrip(".") or "file"` was what guaranteed no component could be `.`, `..`, or
  empty (e.g. `.<ZWSP>.` survives `normpath` as an ordinary component, and only that `lstrip` collapsed
  it). Removing it therefore required re-proving the boundary, not assuming it: `validate_workspace_relpath`
  now rejects non-printable characters outright, so the `<ZWSP>` case dies at the door instead of being
  laundered downstream. Verified by fuzz — **86,350 accepted paths, 0 escapes, 0 `''`/`.`/`..`
  components** — and pinned permanently by `test_no_accepted_path_can_escape_the_staging_dir`, which
  asserts the property over an adversarial alphabet rather than listing blocked strings. The
  non-printable rejection also closes an unrelated hole neither audit was looking for: a newline in a
  path would have forged a section of the one-line note the model reads.
- **D-10: one unstageable path now costs one file, not all of them.** Both audits converged on this
  independently. `_tar_staged_inputs` raised for the batch; with `turn_engine.py:778` swallowing the
  fault, the blast radius of a single bad row was every workspace file, every turn, silently. It now
  skips and logs. This is the same failure shape as D-3, and my own comment there named the hazard
  while the `ValueError` path had exactly the property the comment condemned. Regression:
  `test_one_unstageable_path_costs_one_file_not_all_of_them`.
- **D-11: the manifest-cache hit no longer builds a tar it throws away.** Pre-existing (the cache path
  called `_tar_staged_inputs` purely to recompute names, copying up to `_MAX_AGENT_FILES_BYTES` =
  128 MiB into a `BytesIO` on the path the cache exists to make cheap). Fixed here rather than deferred
  only because D-10's refactor extracted `_staged_members` and made it a three-line change.
- **D-6: §8.5's container proof was not deferred — it was performed.** §8.5 said the real proof needs
  the tier `2026-07-17-sandbox-guest-container-tests` builds, and that unit tests alone should not be
  implied to prove the fix. That reasoning was about gVisor; the defect is about a cwd and two roots,
  which reproduce in any container. §14 records the run. This does **not** discharge that dossier —
  the check is manual and not in CI, which is exactly its point (see FU-4 there).

## 13. Follow-ups

- **FU-1: nothing states the workspace path convention, which is why this bug is possible.** Three
  roots coexist — `/workspace` (`file` tool and `driver.py`'s `cmd_file`),
  `/workspace/sessions/{room}` (kernel, via `chdir`), `/workspace/agent-files/` (staging) — and no
  requirement, doc, or test says how they relate. This dossier fixes one crossing; it does not write
  the rule. `2026-07-16-workspace-path-convention` owns it, and
  `2026-07-17-sandbox-guest-container-tests`' AC-7 would *enforce* it once written.
- **FU-2: `stage_kernel_inputs` was correct only by coincidence, and after this fix it still is —
  just differently.** Its correctness depends on the kernel's `chdir` target matching its `rel_dir`'s
  parent (§5). Q-4's fix makes the return honest, which removes the *silent* coupling, but nothing
  yet asserts that the kernel's cwd and the staging roots agree. That assertion is the container
  tier's AC-7.
- **FU-3: the exit criterion at `D-code-interpreter-files.md:138` could never have passed, and the
  feature shipped anyway.** That is a process finding, not a code one: an exit criterion was written,
  the feature was marked done, and the criterion was either never executed or executed and its
  failure ignored. Worth one look at whether other `Exit criteria` blocks in `docs/agent-tools/` share
  the property of never having been run.
- **FU-4: `test_workspace_staging.py`'s fake returned the same wrong string as production** (§8),
  which is why the test suite has been green over a 100%-reproducible bug since the feature shipped.
  The general hazard — a hand-written fake that encodes the implementation's mistake rather than the
  contract — is worth a sweep across the other sandbox fakes.
- **FU-5: ~~a bad stored path costs the agent every workspace file~~ — FIXED here, see D-10.** Raised
  as a follow-up on the reasoning that it was unreachable via the API; the security audit then showed
  it was reachable with one ordinary upload (D-7), which is the argument against deferring an
  all-or-nothing silent failure because you believe its trigger cannot occur. Kept as a numbered entry
  because entries are append-only.
- **FU-6: nothing asserts that every writer of `agent_workspace_files.path` goes through
  `_safe_workspace_path`.** D-2's reasoning — and the choice to reject rather than normalise at
  staging — rests on that invariant holding. The security audit verified it holds today by inspection:
  one writer (`workspace_service.py:131`), no rename/move endpoint, no backfill in `0038`/`0039`,
  `facade.py:75` does not expose `upsert`. By inspection only; a repository-level guard would make it
  structural. D-10 lowers the cost of it being wrong from "every file" to "that file".
- **FU-7: the model-facing note is not unambiguously parseable, and it is the same defect class this
  dossier fixes.** `turn_engine.py:808` joins paths with `", "`, and a filename may contain a comma —
  `a,b.csv` yields `[… : /workspace/agent-files/a,b.csv, /workspace/agent-files/c.csv]`, which no
  reader can split correctly. Pre-existing and out of scope, but it is the same shape as the bug this
  dossier exists for: a string that is true to write and ambiguous to read. Found by the quality audit,
  which also caught that this task's first note test encoded the false invariant by splitting on `,` —
  corrected. Worth doing alongside `2026-07-16-workspace-path-convention`.
- **FU-8: `turn_engine.py:761-763` (application) imports `docker_runsc` (infrastructure) directly**,
  against CLAUDE.md's layering, despite a `SandboxRunner` protocol existing. Pre-existing. Named here
  because D-1 justifies the `shared_kernel` placement by reasoning about a boundary this import already
  breaks, and this task's new note test leans further on it by monkeypatching the concrete infra symbol.
- **FU-9: the TUS `agent_workspace` branch (`tus.py:177-200`) is dead code.** It performs full AuthZ
  and then calls `TusService.create`, which rejects any `purpose` outside
  `("chat_attachment", "rag_source", "knowmap_source")` (`tus_service.py:118`) — so it always 422s. It
  fails closed, and its deadness is *why* FU-6's "one writer" claim holds, but it is a route that looks
  supported and is not. Found by the security audit.
- **FU-11: the D-3 rename is silent, so the hint's promise has one residual exception.** The
  designer-facing hint (AC-7) states files are at `/workspace/agent-files/<path>`, which D-8 made true
  in general — stored == staged == reported. The exception is D-3's conflict: upload both `reports` and
  `reports/q1.csv` and the file is staged as `reports-1`, so the model is told `reports-1` while the UI
  still lists `reports`, with nothing logged to the designer. Rare (it needs a file and a folder of the
  same name) and strictly better than the alternatives (dropping the file, or failing the batch), but
  it is a quiet divergence of exactly the kind this dossier is about. The honest fix is to reject the
  conflicting *upload* at the API — a file and a directory cannot share a name on the target
  filesystem, so the row should never exist. Out of scope: it changes upload validation.
- **FU-10: `_mkdirs`' per-level directory members are load-bearing for a reason nothing records.**
  Emitting a DIRTYPE member for *every* intermediate component (not just the leaf's parent) is what
  makes the extractor replace a symlink the agent's own `code_exec` could have pre-planted at
  `agent-files/sub` on the persistent volume. A comment now says not to optimise it to leaf-only, but
  the behaviour is Docker's `Unpack` semantics and was reasoned, **not** empirically verified by the
  audit. Worth an actual test in the container tier
  (`2026-07-17-sandbox-guest-container-tests`).

## 14. Verification Record

The §4 reproduction and `D-code-interpreter-files.md:138`'s exit criterion, executed for the first
time (D-6). The production `_tar_staged_inputs`/`_workspace_abspath` build the archives; a real
container with a named volume at `/workspace` extracts them via `put_archive`; the probe `chdir`s to
`/workspace/sessions/room-1`, reproducing `kernel.py:123`:

```
reported to the model:
    /workspace/agent-files/data.csv
    /workspace/agent-files/reports/q1.csv
    /workspace/agent-files/.config/app.json
    /workspace/sessions/room-1/inputs/upload.csv
cwd: /workspace/sessions/room-1
MISSING  [old note] agent-files/data.csv
OPENS    [new note] /workspace/agent-files/data.csv
OPENS    [new note] /workspace/agent-files/reports/q1.csv
OPENS    [new note] /workspace/agent-files/.config/app.json
OPENS    [new note] /workspace/sessions/room-1/inputs/upload.csv
OPENS    [cwd-rel] inputs/upload.csv
escaped?   False
```

`MISSING` is the bug, still reproducing on demand — every other line is a path the model is now told
and can open. `reports/q1.csv` is AC-12; `.config/app.json` is D-8 (a stored path staging unrewritten);
the last two are AC-4 in both halves — the new absolute form resolves **and** the attachment did not
move from where the kernel's cwd expects it.

The input set also included `../../etc/passwd`, which is **absent from the reported list**: it was
skipped and logged while the other three staged normally (D-10), and `escaped? False` confirms it
wrote nothing (D-9).

**Not covered:** the host has no `runsc` runtime, so this ran under `runc`. gVisor is not implicated
in a cwd-vs-root defect, but the guest image itself was not exercised — `python:3.12-slim` stood in
for `smap/code-exec:pinned`. That gap is `2026-07-17-sandbox-guest-container-tests`', and this run
argues for it rather than against it: the check exists only in a scratch script and nothing in CI
would catch the regression.
