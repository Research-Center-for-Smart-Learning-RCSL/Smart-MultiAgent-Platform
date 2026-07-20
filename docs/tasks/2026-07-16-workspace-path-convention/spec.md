---
type: bugfix
status: in-progress
created: 2026-07-16
requirements: [R12.03]
depends_on: []
---

# `file` and `code_exec` disagree about what a relative path means

## 1. Summary

The `file` tool and the `code_exec` kernel mount the **same** per-agent volume at `/workspace`,
but resolve relative paths against different roots: `file` roots at `/workspace`, `code_exec` at
`/workspace/sessions/{room}`. The model is not told this — `code_exec`'s tool description says
nothing about its working directory at all. Worse, `file`'s `list` returns **bare filenames**, so
the obvious two-step (`list` → feed a name to `code_exec`) fails by construction: the model is
handed a path by one tool that the other tool cannot open. Both tools report success; the agent
just cannot find its own files.

Discovered as FU-1 of `docs/tasks/2026-07-16-code-exec-agent-files-path/` (the `agent-files`
path bug). That dossier sidesteps the collision for one subtree by going absolute; this one
addresses the divergence itself.

## 2. Observed vs Expected

- **Observed.** Same volume, two roots:
  - `file` — `_safe_relpath` (`file_tool.py:30-41`) joins bare paths onto `_ROOT = "/workspace"`
    (`:23`, `:37`) and rejects anything escaping it; the in-image guard mirrors it
    (`driver/protocol.py:85-99`). Mounted at `/workspace` by `docker_runsc.py:604`.
  - `code_exec` — the kernel `os.chdir(_SESSION_DIR)` (`kernel.py:123`), i.e.
    `/workspace/sessions/{room}` (`:37-41`). Same volume, mounted at `/workspace`
    (`docker_runsc.py:917`).
  - So `file.write("notes.md")` → `/workspace/notes.md`; `code_exec` then runs
    `open('notes.md')` → `/workspace/sessions/{room}/notes.md` → `FileNotFoundError`.
  - **The model is told nothing.** `code_exec`'s description (`builtin_tools.py:195-196`) —
    *"Run a Python snippet in a gVisor sandbox (30s cap). State persists across calls in a chat;
    loaded data and saved files survive. Returns stdout/stderr."* — never mentions the working
    directory. `file`'s description (`:226`) and its schema (`:108`, *"Path under /workspace."*)
    state `/workspace`, which is true for `file` and false for `code_exec`.
  - **`list` actively hands over the broken path.** `driver.py:241-247` emits
    `sorted(os.listdir(path))` joined by newlines — bare names, no directory. The model's only
    discovery mechanism returns strings that are unusable in the other tool.
- **Expected** (confirmed with the user, Q-1 — there is no prior intent source; see §3). A path a
  tool *returns* is a path the model can hand to the other tool and have it work. Where the two
  roots genuinely differ, the tool descriptions must say so, because the description is the only
  contract the model reads.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | How far to fix: (a) tool descriptions only; (b) descriptions **and** `list` returns absolute paths; (c) unify the roots by pointing `code_exec`'s cwd at `/workspace`. | **(b)** — descriptions state each root, and `file` `op=list` returns absolute `/workspace/...` paths. | (a) leaves the trap armed: `list` still emits bare names and relies on the model re-deriving the prefix every time, which is exactly the kind of silent, per-call correctness the description cannot enforce. (c) is the tempting "one root" answer and is **wrong — but not for the reason it first appears.** It would *not* break artifact collection: `_OUTPUTS` (`kernel.py:40`) is an absolute module-level path built from env vars (`:37-39`), `_snapshot`/`_collect_artifacts`/`_capture_figures` (`:64-94`, `:109`) all address it absolutely, and `_run` mkdirs it *before* the chdir (`:117` vs `:123`) — so the collector never moves, and per-room separation comes from `_SESSION_DIR` (`:39`), not from the cwd. It is wrong because the cwd is what makes the **agent's own** documented relative paths resolve: with `cwd=/workspace`, `open('inputs/data.csv')` → `/workspace/inputs/data.csv` (attachments unreadable — the contract at `:119-121`, `docker_runsc.py:979`, pinned by `test_code_exec_kernel.py:174`) and `plt.savefig('outputs/x.png')` → `/workspace/outputs/x.png`, which lands **outside** the directory the collector scans, so the agent's own artifacts are silently never returned. The session cwd is load-bearing; the defect is that it is *undisclosed*, not that it exists. |
| Q-2 | Unify by making `file` session-relative instead? | **No.** | `file` is not room-scoped — it has no chatroom in its signature (`file_tool.py:59-92`, `docker_runsc.py:588-596`) and is reachable from paths that have no room. Rooting it in a session dir is not expressible. |
| Q-3 | Where does the `list` change live — post-process the driver's output in `_build_file_tool`, or fix the driver? | **The driver**, with the formatting extracted into `protocol.py`. | Post-processing in the backend is the exact anti-pattern that caused the sibling bug: `_fix_paths` was a caller laundering its callee's output by substring match. (Historical as of 2026-07-17 — `2026-07-17-agent-files-path-resolution` deleted it by making the staging methods return absolute paths at the source, `docker_runsc.py:1329`. Its removal is the precedent for this decision, not an argument against it.) The callee must state the contract. `driver.py:27-28` already names the seam: *"Heavy deps … live only in the image and are imported lazily so the pure helpers in `protocol` stay testable on any host."* A listing formatter is a pure helper. |
| Q-4 | Data repair? | **None.** | Nothing is persisted. Both defects are per-call computed strings. |

## 4. Reproduction

Deterministic. Preconditions: an agent with **both** `file` and `code_exec` enabled
(`AgentToolType.HOSTED_FILE_WORKSPACE` and `HOSTED_CODE_INTERPRETER`), in a chat room.

1. `file(op="write", path="notes.md", content="hello")` → succeeds; the audit event records
   `/workspace/notes.md` (`file_tool.py:91`, `:108-113`).
2. `file(op="list", path="/")` → returns `notes.md`. (`builtin_tools.py:213` maps `/` to
   `/workspace`.)
3. `code_exec(source="open('notes.md').read()")` → **`FileNotFoundError`**.
4. `code_exec(source="import os; os.getcwd()")` → `/workspace/sessions/{room}`.
5. `code_exec(source="open('/workspace/notes.md').read()")` → `'hello'`.

Steps 2 and 3 are the bug in one breath: the tool returned the string, and the other tool
rejects it. Not reproducible in CI — no Docker/gVisor tier (`wiring` is Postgres+Redis+MailHog
only, `backend/pyproject.toml`). §8 tests the pure formatter and the descriptions; §4 is
`/verify`.

## 5. Root Cause Analysis

1. Two containers mount one volume at `/workspace` (`docker_runsc.py:604` `file`, `:917` kernel)
   — deliberate and correct; the shared volume *is* the feature.
2. The kernel `chdir`s into a per-room subdirectory (`kernel.py:123`) so that the **agent's**
   relative `inputs/` and `outputs/` resolve into the per-room directories the collector scans
   (`:40-41`, `:64-94`). Per-room *separation* comes from `_SESSION_DIR` (`:39`), not from the
   chdir — the chdir exists to make the documented relative form (`:119-121`) work. Also correct
   in isolation.
3. **Nothing reconciles 1 and 2 for the model.** `code_exec`'s description
   (`builtin_tools.py:195-196`) omits the cwd; `file`'s says `/workspace` (`:108`, `:226`) as if
   it were universal. The two tools' conventions were each documented against themselves and
   never against each other.
4. `driver.py:241-247` returns bare names, which is only meaningful relative to an implied root
   the caller has to remember — and the caller is a language model, whose only source for that
   root is the descriptions in step 3.

**Root cause: step 3** — the absent cross-tool path contract. It is the earliest link whose
correction prevents the symptom, and it is the reason step 4's bare names are a trap rather than
a mere terseness: with an accurate contract stated, a bare name is ambiguous; without one, it is
actively misleading. Step 4 is the aggravating factor that makes the failure the *default* path
rather than an edge case, which is why Q-1 fixes both.

This is not "the kernel's cwd is wrong" (Q-1(c)) and not "`file`'s root is wrong" (Q-2). Two
roots are defensible; two **undisclosed** roots, one of which is fed to the model by the other
tool's output, are not.

## 6. Blast Radius and Sibling Suspects

**Blast radius.** Every agent with both tools enabled, every tenant, since both tools have
coexisted. Silent: `file` reports success, `code_exec` reports `FileNotFoundError`, and the
model typically retries or confabulates rather than surfacing a platform fault. Consumes tool
rounds against `MAX_TOOL_ROUNDS = 8` (`turn_engine.py:90`). No data loss, no persisted
corruption.

**Sibling suspects.**

- `agent-files/` reported to `code_exec` → **CONFIRMED, owned by
  `docs/tasks/2026-07-17-agent-files-path-resolution/`** (which supersedes the
  `2026-07-16-code-exec-agent-files-path` dossier this spec was written against), not re-fixed
  here. That dossier makes the reported path absolute; this one makes the *convention* explicit.
  **Shipped 2026-07-17** (`status: implemented`), so the coupling §9 worried about is discharged:
  every path the platform hands the model is already absolute — `/workspace/agent-files/...`
  (`docker_runsc.py:1329`), `/workspace/skills/...`, and
  `/workspace/sessions/{room}/inputs/...`. That leaves `file` `op=list` as the **last remaining
  source of a non-absolute path** anywhere in the model-facing surface, which raises the value of
  this fix rather than lowering it.
- `inputs/` (attachments) → **cleared.** Session-relative, cwd is the session dir, documented
  (`kernel.py:119-121`, `docker_runsc.py:979`), pinned (`test_code_exec_kernel.py:174`).
- `outputs/` → **cleared**, same reasoning; `_OUTPUTS` is under `_SESSION_DIR`.
- `file` `op=read`/`op=write` → **cleared** for correctness (both take an explicit path and root
  it consistently), but they are *how the model gets a path in the first place* only via `list`,
  which is why `list` is the one that changes.
- `_collect_artifacts`' `rel_path` (`kernel.py:90`, `str(path)`) → **cleared.** Already absolute;
  consumed host-side, not by the model as a path to reopen.
- MCP tools → **cleared.** They do not receive the volume mount (`REQUIREMENTS.md:582`, and no
  `volumes` key on the MCP container path).

## 7. Fix Design

1. **State the contract where the model reads it.** `code_exec`'s description
   (`builtin_tools.py:195-196`) gains the working directory and the shared root:

   > Run a Python snippet in a gVisor sandbox (30s cap). State persists across calls in a chat;
   > loaded data and saved files survive. Your working directory is this chat's own session
   > directory: `inputs/` holds files from the conversation and anything you save to `outputs/`
   > is returned as an artifact. The agent's persistent volume — the same files the `file` tool
   > sees — is at `/workspace`; refer to it by absolute path (e.g. `/workspace/notes.md`).
   > Returns stdout/stderr.

   `file`'s description (`:226`) and schema (`:108`) gain the reciprocal: paths are rooted at
   `/workspace`, and `code_exec` must use the absolute form.

2. **Make `list` emit usable paths.** Add a pure helper to `deploy/sandbox/driver/protocol.py`
   (the module `driver.py:27-28` designates for host-testable helpers, and which already has a
   test file):

   ```
   def format_listing(path: str, entries: Sequence[str]) -> str:
       """Absolute /workspace-rooted paths, one per line — usable verbatim in code_exec."""
   ```

   `driver.py:241-247` calls it for both branches — the directory listing and the
   single-file case at `:245`, which must also become absolute rather than
   `os.path.basename(path)`.

3. **No change to the kernel, the cwd, `inputs/`, `outputs/`, or `_tar_staged_inputs`.** Q-1(c)
   and Q-2 are rejected on the evidence in §3.

Why this corrects the root cause rather than masking it: the failing thing is a contract the
model cannot infer. Step 1 supplies it at the only surface the model reads, and step 2 removes
the need to apply it by hand on the one path where the platform, not the model, chooses the
string.

**Deployment coupling (important).** `op=list` runs inside `smap/mcp-runtime`
(`docker_runsc.py:618`/`:634`, `command=["file"]`, image `mcp_image`, default
`smap/mcp-runtime:pinned`, `settings.py:256-259`). Step 2 therefore ships in an **image
rebuild**, step 1 in the backend. Both orderings are safe and degrade to today's behavior, so no
lockstep deploy is required: new backend + old image → `list` still returns bare names, but the
description now tells the model the root (i.e. exactly Q-1 option (a)); old backend + new image →
`list` returns absolute paths that work regardless of the description. Neither combination is
worse than the status quo. Call this out in the release notes anyway — a stale pinned image is
the difference between the fix working and half-working, and nothing in CI detects it (§13
FU-2).

**Data repair:** none (Q-4).

## 8. Regression Test Plan

The failing test comes first.

- **T-1 (fails now).** `backend/tests/unit/test_sandbox_driver_protocol.py` (extend — it already
  loads `deploy/sandbox/driver/protocol.py` via `importlib.util.spec_from_file_location`, so the
  precedent and the harness exist): `format_listing("/workspace", ["notes.md", "a"])` returns
  `"/workspace/notes.md\n/workspace/a"`. Red today: the function does not exist and the behavior
  it replaces returns bare names.
- **T-2.** `format_listing("/workspace/sub", ["x.csv"])` → `"/workspace/sub/x.csv"` — nesting is
  preserved, not flattened.
- **T-3.** Every path `format_listing` emits satisfies `safe_workspace_path(p) == p` — the
  listing can never emit a path its own guard would reject. This ties the new helper to the
  existing invariant (`protocol.py:85-99`) rather than asserting a hand-written string.
- **T-4 (fails now).** `backend/tests/unit/test_builtin_tools_wiring.py` (extend): `code_exec`'s
  description mentions the session working directory **and** `/workspace`; `file`'s mentions
  `/workspace`. A description is this feature's entire user interface, so it is asserted, not
  trusted. Red today — `code_exec`'s description contains neither.
- **T-5.** The empty-directory case returns `""` and not `"/workspace"`, so an empty workspace
  does not read to the model as a workspace containing one mysterious file. **This is a test of
  the helper only.** The model never sees `""`: `builtin_tools.py:222` is
  `clip_tool_output(res.stdout or "(ok)")`, so an empty listing surfaces as `(ok)`. Do not promote T-5 into
  an end-to-end assertion — it would be asserting a string that cannot reach the model.

End-to-end (`/verify`, live sandbox): the §4 reproduction, expecting step 2 to return
`/workspace/notes.md` and step 3 — `open('/workspace/notes.md')` — to succeed.

## 9. Risks and Rollback

- **Ordering against the sibling dossier — DISCHARGED 2026-07-19.** The recommended order (the
  sibling first, this second) has already happened: `2026-07-17-agent-files-path-resolution` is
  `implemented`, so the descriptions this spec writes land into exactly the world it wanted, one
  where every platform-supplied path is already absolute. No ordering risk remains.
- **`list` output grows.** Absolute paths are ~11 chars longer each, against `clip_tool_output`'s
  cap (`tool_registry.py:41`, applied at `builtin_tools.py:222`), so a very large workspace
  truncates marginally sooner. Acceptable: the clip is character-based and already crude
  (Skills FU-11).
- **A model that hardcoded bare `list` output.** Broken today; cannot regress.
- **Rollback:** revert both changes independently. No migration, no persisted state, no API
  contract, no schema change. The image is tag-pinned, so rollback is a re-pin.

## 10. Acceptance Criteria

- [x] AC-1: T-1 and T-4 fail against current code and pass after the fix. Both observed red for
      the documented reason (`AttributeError: no attribute 'format_listing'`; descriptions
      lacking the required substrings) before either fix, green after.
- [ ] AC-2: `file(op="list")` returns absolute `/workspace/...` paths; feeding one verbatim to
      `code_exec`'s `open()` succeeds (`/verify`, live sandbox). **Outstanding** — requires a
      running Docker/gVisor tier and a rebuilt `smap/mcp-runtime`; not reachable from the unit
      tier by construction (§4).
- [x] AC-3: `code_exec`'s tool description states its working directory is the per-chat session
      directory and that the shared volume is at `/workspace`; `file`'s states its `/workspace`
      root. Asserted by test, not by inspection.
      (`test_builtin_tools_wiring.py::test_code_exec_description_states_both_roots`,
      `::test_file_description_states_its_workspace_root`.)
- [x] AC-4: `inputs/` and `outputs/` still work relative to the session cwd;
      `test_code_exec_kernel.py` passes **unmodified**; artifacts are still collected.
- [x] AC-5: Nesting is preserved — a file at `/workspace/sub/x.csv` lists as
      `/workspace/sub/x.csv`, never `x.csv`. (T-2.)
- [x] AC-6: Every path emitted by `format_listing` round-trips through `safe_workspace_path`
      unchanged (T-3).
- [x] AC-7: No backend code post-processes the driver's `list` output — `_build_file_tool`
      (`builtin_tools.py:202-229`) does no string rewriting of `res.stdout` beyond the existing
      `clip_tool_output`. Also verified one layer down: `FileTool.list_` (`file_tool.py:59-67`)
      returns the runner result untouched.

## 11. SRS Delta

**None.** `[R12.03]` (`REQUIREMENTS.md:575-582`) establishes the per-agent volume and its mount
point; §12.1 (`:556`) describes `file` as *"read/write within the agent's sandboxed workspace
directory"*. Both remain true. The SRS does not specify tool descriptions or listing formats, and
should not — they are implementation surfaces that must be free to change with the model-facing
copy.

Note the sibling dossier (`2026-07-17-agent-files-path-resolution`, superseding
`2026-07-16-code-exec-agent-files-path`) carries the one SRS correction this investigation
surfaced (`:582` claims only the `file` container mounts the volume; four do). It is not
duplicated here.

## 12. Deviation Log

- **D-1 (2026-07-19).** §7.1's proposed `file` description asserted that `op=list` returns
  absolute paths. Shipped without that clause. `op=list` executes inside the tag-pinned
  `smap/mcp-runtime` image, which deploys independently of the backend, so against a stale image
  the claim is simply false — and the new-backend/old-image combination is the one §7 calls out
  as safe *precisely because* it degrades to stating the root without promising the listing
  shape. The description now states the root and the absolute form `code_exec` needs, and says
  nothing about what `list` returns. AC-3 is unaffected: it requires the root to be stated, not
  the listing shape. Caught in the Step 7 self-audit after the first commit; corrected in
  `3e7edc9`.

**Build state (2026-07-19, paused).** Implementation and all mechanical gates are complete —
`pytest tests/unit` 5297 passed / 4 skipped (pre-existing host-symlink skips), `ruff check`,
`ruff format --check`, and `mypy .` (787 files) all clean; the integration and wiring tiers were
not run (no Postgres/Redis locally, `socket.gaierror`) and are untouched by this diff. Landed in
`6e38e5c`, `c81b54c`, `3e7edc9`. **Remaining to close out: AC-2 (`/verify` against a live
sandbox with a rebuilt image), then `status: implemented`.** Paused by the user's decision to
spec FU-4 first.

## 13. Follow-ups

> **Verification sweep, 2026-07-17.** Re-checked during the 0716 follow-up triage. All three claims
> hold. Caveat on citations: **every `turn_engine.py` line number in this dossier is stale** (the
> agent-skills Phase 0 diff shifted the file ~165 lines); `driver.py` and `settings.py` citations are
> essentially exact. None of the three is ready to spec: FU-1 is gated on two unbuilt dossiers, FU-2
> is subsumed by `2026-07-16-code-exec-agent-files-path`' FU-4, and FU-3 depends on the test tier
> that FU-4 describes.
>
> **Approval sweep, 2026-07-19.** Citations across the whole dossier re-verified and refreshed
> against `main` before approval; the stale `turn_engine.py` numbers the 07-17 note flags are now
> corrected in place (`MAX_TOOL_ROUNDS` is `:90`). Two substantive updates: `_clip` was renamed
> `clip_tool_output` and moved to `tool_registry.py:41`, and `_fix_paths` no longer exists (see
> Q-3). The sibling dossier shipped, so FU-2's trigger condition is unchanged but its blast radius
> is now this dossier alone.

- **FU-1: `code_exec` cannot see `file`'s writes without an absolute path, and vice versa — by
  design, now merely documented.** The deeper question this spec declines: should the platform
  offer one addressing scheme across every tool the agent has? Doing it properly means a path
  abstraction the tools share, not a third convention bolted on. Revisit if agents keep tripping
  over it once AC-3's descriptions are live; the audit trail (`file_tool.py:102-115`) and
  `skill_reads` metadata make the tripping measurable.
  **Verified 2026-07-17: CONTINGENT on two unbuilt dossiers — not sprecable yet.** The audit trail
  at `file_tool.py:102-115` does exist, but this entry's own trigger ("once AC-3's descriptions are
  live") depends on *this* dossier being built, and its `skill_reads` metadata depends on
  `2026-07-16-agent-skills` Phase 1. It is also not a defect claim — it is a deliberately deferred
  design question (`feature`/`refactor`: a shared path abstraction across tools). Note it is the
  residue of `2026-07-17-agent-files-path-resolution`' FU-1, the same subject at a third altitude.
- **FU-2: nothing in CI detects a stale `smap/mcp-runtime:pinned`.** The `file` tool's behavior
  lives in an image the backend addresses by tag (`settings.py:256-259`), and there is no test
  tier that runs it (§4). A backend expecting absolute listings against an old image degrades
  silently. A build-stamp assertion at sandbox readiness (`docker_runsc.py`'s
  `_ensure_runtime_ready` / the supervisor gate, `settings.py:264-269`) would close it.
  **Verified 2026-07-17: confirmed; the gap is live, its severity is not.** `settings.py:256-259`
  is exact (`mcp_image` defaults to `smap/mcp-runtime:pinned`); `supervisor_url` is `:269-272` —
  the cited `:264-269` lands on that field's comment block. The tag-addressed-image gap exists
  today, but the specific failure named here (a backend expecting absolute listings against an old
  image) only arises once AC-3 ships. **Substantially the same root cause as
  `2026-07-17-agent-files-path-resolution`' FU-4** — nothing in CI runs a container — of which this
  is one instance; that entry is the general form and would subsume this. The build-stamp assertion
  is nonetheless a much cheaper partial fix and worth taking first. Type: `feature`/infra.
- **FU-4: the `file` tool can read every room's session directory — cross-room leak.**
  Surfaced by the `check-security` gate on this task's diff (2026-07-19); **pre-existing, not
  introduced here.** `_safe_relpath` (`file_tool.py:30-41`) admits anything under `/workspace`
  and `FileTool` has no chatroom in its signature (`:44-57`), but the kernel stages each room's
  attachments to `sessions/{chatroom_id}/inputs` (`docker_runsc.py:1197`) on the *same* per-agent
  volume both containers mount (`:807` file, `:1119` kernel). So an agent in room B can
  `file(op="list", path="sessions")` to enumerate room ids and `file(op="read", ...)` room A's
  uploads. Rooms have independent member lists, so this crosses a membership boundary; it stays
  within one agent, hence one project and one tenant.
  This dossier's change neither adds nor removes the capability — `list` already returned the bare
  name `sessions` and `read` already accepted `sessions/{room}/...`. It does raise
  *discoverability* (listings are now pasteable into `code_exec`, and `code_exec`'s description
  now names `/workspace` as the shared volume), which is why it is recorded here rather than left
  to the next audit. Type: `bugfix`, CRITICAL. Fixing it means excluding `sessions/` from the
  `file` tool's reachable subtree or scoping `file` per-room — and Q-2 records why the latter is
  not expressible today, so it needs its own analysis rather than a patch here.
- **FU-3: `list` is not recursive** (`driver.py:243`, `os.listdir`), so discovering a nested
  layout costs one tool round per directory against `MAX_TOOL_ROUNDS = 8`
  (`turn_engine.py:90`). Out of scope — it is a capability gap, not the reported defect — but it
  compounds this one, because the agent burns rounds discovering the tree it was already
  mis-addressing.
  **Verified 2026-07-17: confirmed. `driver.py:243` is exact** (`sorted(os.listdir(path))`);
  `MAX_TOOL_ROUNDS = 8` is now `turn_engine.py:90` (`:83` at the 07-17 sweep, `:82` as written).
  Not contingent — a live capability
  gap. But the fix touches `deploy/sandbox/driver/driver.py` and the in-image protocol, so it needs
  an image rebuild — which is exactly the drift FU-2 says CI cannot detect. Sequence it behind the
  test tier. Type: `feature`.
