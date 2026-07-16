---
type: bugfix
status: in-progress
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

- [ ] AC-1: `_tar_staged_inputs` returns exactly the tar member names it wrote, for any `rel_dir`
      (`docker_runsc.py:138`), and its docstring is true.
- [ ] AC-2: `_fix_paths` no longer exists.
- [ ] AC-3: `stage_agent_workspace_files` returns `/workspace/agent-files/x`; the model's
      `open('/workspace/agent-files/data.csv')` resolves regardless of the kernel's cwd.
- [ ] AC-4: `stage_kernel_inputs` returns `/workspace/sessions/{room}/inputs/x`, **and the file still
      lands at the identical path it does today** — the tar member name is unchanged (§8.4).
- [ ] AC-5: the note (`turn_engine.py:808`) contains only absolute paths; no bare `agent-files/`.
- [ ] AC-6: `test_workspace_staging.py`'s assertions are inverted to the corrected contract, and its
      fake no longer returns the broken form.
- [ ] AC-7: the designer-facing hint (`locales/{en,zh-TW}.json:392`) tells the truth for **both**
      tools, in both locales, via `$t()`.
- [ ] AC-8: `D-code-interpreter-files.md:138`'s exit criterion names a path that can pass; `:110`,
      `:150`, `:178`, `:232` agree with it.
- [ ] AC-9: the `file` tool is unchanged and still accepts both forms — a test pins
      `_safe_relpath('agent-files/x') == _safe_relpath('/workspace/agent-files/x')`.
- [ ] AC-10: the regression test from §8.1 was seen **red** before the fix, for the documented reason.
- [ ] AC-11: gates green — `pytest -q`, `ruff check . && ruff format --check .`, `mypy .`;
      `pnpm test`, `pnpm lint`, `pnpm typecheck` (the locale change touches the frontend).

Absorbed from superseded `2026-07-16-code-exec-agent-files-path` (Q-6):

- [ ] AC-12: a workspace file at `reports/q1.csv` is staged to `agent-files/reports/q1.csv` and
      reported as `/workspace/agent-files/reports/q1.csv` — its directory survives. Both flattening
      sites are fixed (`turn_engine.py:857`, `_safe_input_name` at `docker_runsc.py:127` — Q-7), and
      two files named `q1.csv` in different folders no longer collide into `q1.csv`/`q1-1.csv`.
- [ ] AC-13: staging re-validates the tree-preserving path via **`_safe_workspace_path`**
      (`workspace_service.py:55`), not a third hand-rolled rule (Q-8); traversal, absolute, and
      null-byte paths are rejected at staging even though the API boundary already rejects them.
      Attachments (`stage_kernel_inputs`) keep flattening to a basename — for them it is correct.
- [ ] AC-14: `REQUIREMENTS.md:582` no longer claims only the `file` tool container mounts the volume
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

_None yet._

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
