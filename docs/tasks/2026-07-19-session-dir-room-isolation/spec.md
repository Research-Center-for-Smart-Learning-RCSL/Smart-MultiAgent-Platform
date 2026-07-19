---
type: bugfix
status: draft
created: 2026-07-19
requirements: [R12.03, R12.03a, R12.05, R31.22]
---

# One room's attachments and artifacts are readable from every other room the agent serves

## 1. Summary

Chat attachments and `code_exec` artifacts are room-scoped data, but they are stored on an
agent-scoped Docker volume. Every chatroom's kernel container mounts that whole volume
read-write at `/workspace` (`docker_runsc.py:1124`), so an agent serving room B can read room A's
uploaded files and generated artifacts — through `code_exec` with a two-character relative path,
or through the `file` tool with an ordinary `read`. Rooms have independent member lists
([R13.04], `REQUIREMENTS.md:643-652`), so this crosses a membership boundary: a file uploaded by
the members of room A can be surfaced to the members of room B by an agent that both rooms share.
The data persists indefinitely — nothing ever prunes a session directory (§6).

Recorded as FU-4 of `docs/tasks/2026-07-16-workspace-path-convention/`, whose `check-security`
gate surfaced it. That entry named only the `file` tool; the analysis below establishes that
`code_exec` is the wider channel and that no path guard can close it.

## 2. Observed vs Expected

- **Observed.** Room-scoped data on an agent-scoped volume, reachable two ways:
  - **The volume is per-agent by construction.** `smap-agent-fs-{agent_id}` is built inline at
    four sites (`docker_runsc.py:807`, `:1119`, `:1196`, `:1255`) and once more in
    `file_tool.py:57`. No room component. Mandated as such by [R12.03] (`REQUIREMENTS.md:595`).
  - **Room data is written onto it.** `stage_kernel_inputs` sets
    `rel_dir = f"sessions/{chatroom_id}/inputs"` (`docker_runsc.py:1197`) and `put_archive`s at
    the volume root (`:1214`). The kernel derives `_SESSION_DIR = _WORKSPACE / "sessions" / _ROOM`
    (`kernel.py:39`) with `_INPUTS`/`_OUTPUTS` beneath it (`:40-41`).
  - **Channel 1 — `code_exec`.** `_create_kernel` mounts the entire volume rw at `/workspace`
    (`docker_runsc.py:1124`) and the kernel `chdir`s to the session dir (`kernel.py:123`). From
    room B, `open('../{roomA}/inputs/x')` reads room A's attachment. This is arbitrary Python
    against a real mount — `safe_workspace_path` governs the `file` tool's RPC, not the kernel's
    filesystem, so **there is no guard to add here.**
  - **Channel 2 — the `file` tool.** `_safe_relpath` (`file_tool.py:30-41`) admits anything under
    `_ROOT = "/workspace"` (`:23`), and `FileTool` has no chatroom in its signature (`:44-57`).
    `file(op="read", path="sessions/{roomA}/inputs/x")` is a legal, successful call.
  - **It never expires.** `_RECONCILE`'s prune is scoped to exactly one of `agent-files/` or
    `skills/` per run (`docker_runsc.py:370-425`, subdir from `:1273`), never `sessions/`. The
    only deletion that reaches session data is whole-volume GC, 60 days after the *agent* is soft
    deleted (`agent_fs_gc.py:379`, retention `:77`).
- **Expected** (no intent source exists — confirmed with the user, Q-1; see §3 and §11). Data
  staged for one chatroom is readable only from that chatroom's own execution context. Data that
  is genuinely agent-scoped — the `file` tool's own state, `agent-files/`, `skills/` — remains
  shared across the agent's rooms, which is what [R12.03] designs for.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | The SRS is silent on cross-room isolation for the sandbox (§11). What is the intended model: (a) isolate `sessions/` only, keeping the rest shared; (b) make the whole volume per-room; (c) declare current behavior correct and document it; (d) isolate `inputs/` only, leave `outputs/` shared. | **(a)** — `sessions/` becomes per-`(agent, room)`; the volume root, `agent-files/` and `skills/` stay per-agent and shared. | (b) destroys the cross-room persistent memory that is the *point* of a per-agent volume and contradicts [R12.03]'s explicit `{agent_id}`-only key, so it would need an SRS reversal rather than a clarification. (c) is defensible for the agent's own files but not for attachments: those are uploaded by room members under that room's ACL, and no one uploading to room A consents to room B. (d) splits a boundary that is currently one directory into two halves with different rules, which is harder to state and harder to keep true; `outputs/` also routinely *derives* from `inputs/`, so leaving it shared leaks the same content one transformation later. `sessions/` is already exactly the room-scoped region and nothing else on the volume is — the boundary exists, it just is not enforced. |
| Q-2 | Scope: fix both channels, or the mount channel first and the `file` tool separately? | **Both, here.** | Fixing only the `file` tool would be security theater — `code_exec` is the wider channel and needs no cleverness to exploit. Fixing only the mount happens to close both, but that is a consequence worth asserting with a test rather than inheriting by luck (AC-3). |
| Q-3 | Where does the per-room volume mount: nested at `/workspace/sessions/{room}`, or at a separate root? | **A separate root, `/session`.** | Nesting preserves every existing path shape and needs no test changes, but its failure mode is fail-open: if the nested mount is missing — a code path that forgets it, a create that half-fails, a legacy tree left behind — the agent volume's own `sessions/{otherRoom}` shows through at exactly the path the agent is already looking at, silently and with no error. A separate root fails closed: no mount means no directory, and the failure is immediate and loud. For a fix whose entire purpose is a containment boundary, the fail-closed shape is worth the visible path change. |
| Q-4 | Data repair for session trees already on existing agent volumes? | **Required — a one-shot purge (§7.6).** | Without it the fix is prospective only: every volume in production keeps its accumulated `sessions/` tree, still readable via both channels, for data going back to the agent's creation. The new mount point means nothing *writes* there any more, which is precisely why the leftovers will never be cleaned up by normal operation. |
| Q-5 | Does the model-facing absolute path change? | **Yes** — `/workspace/sessions/{room}/inputs/x` becomes `/session/inputs/x`. | Follows from Q-3. Note the tool descriptions shipped by `2026-07-16-workspace-path-convention` remain true unmodified: `code_exec`'s says the cwd is "this chat's own session directory" without naming the path, and `inputs/`/`outputs/` stay relative to the cwd. |

## 4. Reproduction

Deterministic. Preconditions: one agent with both `HOSTED_CODE_INTERPRETER` and
`HOSTED_FILE_WORKSPACE` enabled, added to two chatrooms A and B with different member sets. A
file uploaded as an attachment in room A.

1. In room A, upload `confidential.pdf` as a message attachment and prompt the agent to use it.
   It is staged to `/workspace/sessions/{roomA}/inputs/confidential.pdf`
   (`docker_runsc.py:1197`, `:1214`) and named in that turn's system note
   (`turn_engine.py:901-905`).
2. In room B, `code_exec(source="import os; os.listdir('..')")` → every room id the agent has
   ever served, including `{roomA}`.
3. In room B, `code_exec(source="open('../{roomA}/inputs/confidential.pdf','rb').read()[:64]")`
   → room A's bytes. **Channel 1.**
4. In room B, `file(op="read", path="sessions/{roomA}/inputs/confidential.pdf")` → the same
   bytes. **Channel 2.**
5. Steps 2-4 also reach `sessions/{roomA}/outputs/`, i.e. artifacts the agent generated in room A.

Not reproducible in CI — no Docker/gVisor tier (`wiring` is Postgres+Redis+MailHog only). §8
tests the mount wiring, the path derivation, and the purge against a real filesystem; §4 is
`/verify`.

## 5. Root Cause Analysis

1. [R12.03] (`REQUIREMENTS.md:595`) specifies one persistent volume keyed by `{agent_id}`, for
   the `file` tool's state. Correct and deliberate: an agent's own files should follow the agent.
2. `code_exec` needed somewhere to put per-room attachments and artifacts. It reused the volume
   that was already mounted, partitioning by directory name — `sessions/{chatroom_id}`
   (`docker_runsc.py:1197`, `kernel.py:39`).
3. A directory name is a *convention*, not a boundary. Both readers of that volume — the kernel
   (`docker_runsc.py:1124`) and the `file` tool (`file_tool.py:30-41`) — are scoped to the volume,
   and neither takes a chatroom into account. The kernel cannot be, since it runs arbitrary code.
4. [R12.03a] (`REQUIREMENTS.md:596`) then *documented* the arrangement — "the rest of the volume
   — the `file` tool's own state and per-room session directories — is Agent-authored and is never
   reconciled against a table" — asserting that per-room directories exist while stating nothing
   about who may read them. The convention was written down; the boundary was never specified,
   so it was never built.

**Root cause: step 2** — room-scoped data placed on an agent-scoped volume, with directory naming
standing in for isolation. It is the earliest link whose correction prevents the symptom: give the
room-scoped data a room-scoped container and both channels close at once, because neither reader
can reach what is not mounted. Step 3 is not the root cause but explains why a guard cannot be
the fix — one of the two readers executes arbitrary code, so containment has to be below the
application layer. Step 4 is the aggravating factor: the SRS's silence let the gap read as a
design rather than an omission for as long as it has existed.

This is not "`_safe_relpath` is too permissive" — it is exactly as permissive as [R12.03] says the
`file` tool should be. It is not "the kernel's cwd is wrong" — the cwd is load-bearing and stays
(`2026-07-16-workspace-path-convention` §3 Q-1(c)).

## 6. Blast Radius and Sibling Suspects

**Blast radius.** Every agent that belongs to more than one chatroom, every tenant, since
`code_exec` and chat attachments have coexisted. Bounded to one agent's own rooms — the volume is
per-agent, so this is not cross-agent, cross-project, or cross-org. Silent: both channels are
ordinary successful reads that produce no error and no distinct audit signal (the `file` tool
audits the path it was given, `file_tool.py:102-115`, but nothing flags it as another room's).
Data already written persists indefinitely (§2), so the exposure window for any given upload is
unbounded and repair is required (Q-4).

**Sibling suspects.**

- **Headless / run-and-burn `code_exec`** → **cleared.** The no-room path takes the branch at
  `docker_runsc.py:896-903` and gets `_sandbox_tmpfs()` (`:921`), a 100 MiB tmpfs at `/workspace`
  — the named volume is not mounted at all, and the container is removed at `:944-945`.
- **MCP probe and tool-invoke containers** → **cleared.** Also tmpfs, never the volume
  (`docker_runsc.py:702`, `:764`), consistent with [R12.03]'s "User-provided MCP containers do NOT
  receive this mount."
- **`agent-files/` and `skills/`** → **cleared as intended behavior, not as absent.** Both are
  genuinely per-agent projections ([R12.03a], `REQUIREMENTS.md:596`; staged via `_stage_tree`,
  `docker_runsc.py:1332`, `:1371`), so cross-room visibility is the specification, not a defect.
  Q-1 keeps them shared.
- **The volume root's free-form files** → **cleared, same reasoning.** `file`-tool state is
  agent memory by design ([R12.03], `REQUIREMENTS.md:569`).
- **The staging containers** → **confirmed as a site to change, not a separate defect.**
  `stage_kernel_inputs` mounts the agent volume (`docker_runsc.py:1201`) purely because that is
  where it writes; it moves to the session volume with the data (§7.3). The `_stage_tree`
  reconcile container (`:1265`) keeps the agent volume — it serves `agent-files/` and `skills/`.
- **Artifact read-back** → **cleared.** Artifacts reach the host inline in the kernel's JSON
  reply (`kernel.py:82-92` → `docker_runsc.py:1078` → `turn_engine.py:1133-1148`); there is no
  host-side read of the volume (`get_archive` has no callers). So no host path assumes the
  session directory's location.
- **Kernel keying and reaping** → **cleared.** `_session_key` and the container name are already
  per-`(agent, chatroom)` (`docker_runsc.py:439-440`, `:443-444`), so kernel reuse, LRU eviction
  (`:1146-1156`) and idle reaping (`:1416-1432`) need no change.

## 7. Fix Design

Give room-scoped data a room-scoped volume, mounted outside `/workspace`.

1. **New volume.** `smap-agent-session-{agent_id}-{chatroom_id}`, auto-created by Docker on first
   mount exactly as the agent volume is today (no `volumes.create` exists in the repo;
   `agent_fs_gc.py:35-37` documents the auto-create). Introduce it behind a named helper rather
   than a fifth inline f-string — the existing name is duplicated at five sites (§2), and this
   change would otherwise make it seven.
2. **Kernel mount.** `_create_kernel` (`docker_runsc.py:1116-1144`) mounts *both*: the agent
   volume at `/workspace` (unchanged, `:1124`) and the session volume at `/session`. This is the
   first container in the codebase to mount two volumes; every current site assigns a
   single-entry dict, so the assignment at `:1124` becomes a two-key dict rather than gaining an
   `.update()`.
3. **Staging.** `stage_kernel_inputs` (`:1174-1217`) mounts *only* the session volume at
   `/session`, sets `rel_dir = "inputs"`, `put_archive`s at `/session`, and returns
   `/session/inputs/x`. It keeps its no-network, never-started, create-and-remove shape (`:1200`,
   `:1213`, `:1215-1216`).
4. **Kernel path derivation.** `kernel.py:36-41` derives `_SESSION_DIR` from `_WORKSPACE`; it
   instead reads its own env var (`SMAP_KERNEL_SESSION`, default `/session`), with
   `_INPUTS`/`_OUTPUTS` beneath it unchanged (`:40-41`) and the `chdir` unchanged (`:123`). The
   agent-facing contract — cwd is the session dir, `inputs/` and `outputs/` are relative to it —
   is preserved exactly, which is why the tool descriptions need no edit (Q-5).
5. **The `file` tool: no code change.** Once nothing writes session data under `/workspace`, the
   tool's reachable set no longer contains any, and `_safe_relpath` stays exactly as permissive as
   [R12.03] intends. Channel 2 closes as a consequence of the mount, which AC-3 asserts rather
   than assumes.
6. **Data repair (Q-4).** A one-shot purge of `/workspace/sessions/` from existing agent volumes,
   as an armed CLI/worker task modelled on `agent_fs_gc`'s structure (dry-run by default,
   explicit arm flag, per-volume report) reusing the `_RECONCILE`-style no-network container
   pattern (`docker_runsc.py:1266-1289`). Preferred over purging lazily on kernel creation: lazy
   self-healing pays a per-turn cost forever, and gives no completion signal for a repair whose
   whole point is that it finishes.
7. **GC (mandatory, not optional).** `agent_fs_gc` enumerates every volume on the daemon and
   drops any name not matching `smap-agent-fs-` (`agent_fs_gc.py:155-164`, `:295-296`) — by
   design, since the host carries unrelated volumes. The new session volumes therefore would
   **never be reaped**, growing one per `(agent, room)` forever. GC must learn the second name
   shape, with its own classification: a session volume is garbage when its agent is garbage by
   the existing rule **or** when its chatroom no longer exists.

Why this corrects the root cause rather than masking it: the failing property is containment, and
containment for a reader that executes arbitrary code has to be established below the application
layer. Moving the data to a volume that room B's container never mounts is that boundary; a path
check in front of one of the two readers is not.

## 8. Regression Test Plan

The failing tests come first. All are unit-tier — the mount itself cannot run in CI (§4).

- **T-1 (fails now).** `test_workspace_staging.py` (extend): `stage_kernel_inputs` mounts the
  session volume and not the agent volume — assert the host-config `volumes` dict has exactly the
  `smap-agent-session-{agent}-{room}` key bound at `/session`. Red today: it mounts
  `smap-agent-fs-{agent}` at `/workspace` (`docker_runsc.py:1201`).
- **T-2 (fails now).** Same file: `stage_kernel_inputs` returns `/session/inputs/x`. Red today —
  returns `/workspace/sessions/{room}/inputs/x` (pinned at `test_workspace_staging.py:949`, which
  this task updates).
- **T-3 (fails now).** `test_code_exec_kernel.py` (extend): with `SMAP_KERNEL_SESSION` set, the
  kernel's `_INPUTS`/`_OUTPUTS` resolve beneath it and **not** beneath `SMAP_KERNEL_WORKSPACE`.
  Red today — `_SESSION_DIR` is derived from `_WORKSPACE` (`kernel.py:39`).
- **T-4 (fails now).** `test_code_exec_kernel.py` or a sandbox host-config test: `_create_kernel`
  mounts two volumes — the agent volume at `/workspace` **and** the session volume at `/session`.
  Red today — one volume (`docker_runsc.py:1124`). Note no existing test asserts any host-config
  mount shape, so this is new coverage on a load-bearing line.
- **T-5 — the containment assertion (Q-2, AC-3).** A test naming the property directly: for two
  distinct chatrooms of one agent, the session volume names differ, and the set of volumes mounted
  into room B's kernel contains nothing derived from room A. This is the test that would have
  caught the bug, so it is written to fail against the pre-fix wiring.
- **T-6.** The purge (§7.6) against a real temp directory, mirroring
  `test_workspace_volume_reconcile.py`'s approach: a tree with `sessions/`, `agent-files/`,
  `skills/` and a root file loses **only** `sessions/`. Includes the symlinked-`sessions`
  case that `_RECONCILE` already defends against (`docker_runsc.py:392-394`,
  `test_workspace_volume_reconcile.py:433-470`).
- **T-7.** `agent_fs_gc`: a `smap-agent-session-{agent}-{room}` volume is recognised (not silently
  dropped), is retained while its agent and chatroom live, and is collected when either is gone.
  Must not regress the existing "never touch an unparseable name" guarantee
  (`test_agent_fs_gc_race.py:222-225`, `:609-621`).

Existing tests this task must update rather than work around, since they pin the old path as
correct: `test_workspace_staging.py:949` and `:952` (disjoint-roots assertion), `:277-292` (the
note's absolute paths), `test_turn_system_blocks.py:137` (the literal note string), and the
`sessions/r1/inputs` parametrisation in `test_code_exec_kernel.py:175-199`, `:202+`, `:291-293`.
Each change is a contract update following Q-3/Q-5, and each should be reviewed as such — a test
that merely stops asserting the old path without asserting the new one is a silent loss of
coverage.

End-to-end (`/verify`, live sandbox): the §4 reproduction, expecting steps 2-4 to fail to find
room A's data.

## 9. Risks and Rollback

- **Volume proliferation.** One volume per `(agent, room)` instead of one per agent. §7.7 is the
  mitigation and is part of this task, not a follow-up — without it this trades a confidentiality
  bug for an unbounded resource leak.
- **Two volumes per kernel container.** New for this codebase (§7.2). Low risk mechanically, but
  it is the change most likely to fail only at runtime, which no CI tier exercises — call it out
  for `/verify`.
- **Live kernels across the deploy.** A running kernel keeps its old single-mount view until
  reaped (idle 900s, `docker_runsc.py:334`) or evicted. During that window an in-flight room
  still resolves the old paths, and the note it was given names them. Harmless — the data is
  where the note says — but it means the fix is not fully in force until the last pre-deploy
  kernel is gone. Rolling restart of the workers makes it immediate.
- **Attachments staged before the deploy** land at the old path; the turn that follows the deploy
  re-stages to the new one (`turn_engine.py:878-883` runs per turn). No migration of live
  attachment data is needed — only the purge, which removes what is no longer read.
- **The purge is destructive.** It deletes agent-authored data ([R12.03a] calls the region
  agent-authored and never reconciled). Dry-run default and an explicit arm flag are required,
  matching `agent_fs_gc`'s posture (`agent_fs_gc.py:90`, `:130-131`, `:369`).
- **Rollback.** Revert the backend and re-pin the previous `smap/code-exec` image; the old code
  reads `/workspace/sessions/{room}` again. Session volumes created in the interim are then
  orphaned — GC collects them if §7.7 shipped, otherwise they need manual removal. Data written
  to a session volume during the window is not visible to the reverted code; it is not lost, but
  it is not read either. No schema change, no migration, no API contract change.

## 10. Acceptance Criteria

- [ ] AC-1: T-1 through T-7 fail against current code where marked and pass after the fix.
- [ ] AC-2: Chat attachments stage to the per-room session volume and are readable from their own
      room's `code_exec` by the documented relative form (`open('inputs/x')`), unchanged from
      today.
- [ ] AC-3: **The containment property, asserted directly.** From room B, neither `code_exec` nor
      the `file` tool can reach room A's `inputs/` or `outputs/` — verified by T-5 at the wiring
      level and by the §4 reproduction under `/verify`.
- [ ] AC-4: `agent-files/`, `skills/`, and the `file` tool's own root-level state remain visible
      across all of the agent's rooms (Q-1 keeps these shared; a fix that isolates them is wrong).
- [ ] AC-5: `outputs/` artifacts are still collected and returned — `kernel.py:64-94` addresses
      the new location, and artifact persistence (`turn_engine.py:1103-1171`) is unaffected.
- [ ] AC-6: The headless / run-and-burn path still gets a tmpfs and mounts no named volume
      (`docker_runsc.py:921`) — cleared in §6 and pinned so it stays that way.
- [ ] AC-7: The purge removes `sessions/` and nothing else from an agent volume, including when
      `sessions` is a symlink (T-6), and is dry-run unless explicitly armed.
- [ ] AC-8: `agent_fs_gc` recognises session volumes, retains them while agent and chatroom live,
      collects them when either is gone, and still never touches a name it cannot parse (T-7).
- [ ] AC-9: No unbounded growth — after an agent is collected, no volume bearing its id in either
      name shape remains.

## 11. SRS Delta

**Not "None" — the SRS is silent where it must not be.** The analysis established that
`REQUIREMENTS.md` nowhere states that one chatroom's data must not be readable from another. Every
explicit isolation guarantee is project-scoped ([R11.10] `:536`, [R12.13] `:624`) or room-scoped
but confined to one feature ([R30.09] `:2119` activities endpoints, [R13.18] `:678` guest search,
[R11.17] `:553` Concept Map ACL). [R12.03a] (`:596`) names "per-room session directories" while
asserting nothing about their visibility. Q-1's decision is therefore a new requirement, not a
restoration, and must be written down or the next change will re-introduce this.

Amend **[R12.03a]** (`REQUIREMENTS.md:596`) — strike the clause that places session directories on
the per-agent volume, since they no longer live there:

> The rest of the volume — the `file` tool's own state — is Agent-authored and is never
> reconciled against a table.

Add **[R12.03b]** (`REQUIREMENTS.md`, §12.3, after [R12.03a]):

> Per-chatroom execution state — staged attachments (`inputs/`) and generated artifacts
> (`outputs/`) — lives on a per-`(agent, chatroom)` named Docker volume
> (`smap-agent-session-{agent_id}-{chatroom_id}`) mounted read-write at `/session`, and never on
> the per-agent volume of [R12.03]. A chatroom's execution context mounts only its own session
> volume, so one chatroom's attachments and artifacts are not reachable from another — including
> via `code_exec`, which executes arbitrary code and therefore cannot be confined by a path check.
> The per-agent volume of [R12.03] remains shared across all of the Agent's chatrooms by design:
> the `file` tool's state, `/workspace/agent-files/` and `/workspace/skills/` are Agent-scoped,
> not room-scoped. Session volumes are garbage-collected when their Agent is collected under
> [R12.03]'s retention rule, or when their chatroom ceases to exist.

## 12. Deviation Log

Appended by `/build`.

## 13. Follow-ups

- **FU-1: the 100 MB volume quota required by [R12.03] is not enforced.** `REQUIREMENTS.md:595`
  mandates a hard quota "enforced via the `size` volume option on tmpfs or via a dedicated ext4
  loopback mount". No such mechanism exists: the only 100 MB constant
  (`_WORKSPACE_TMPFS_BYTES`, `docker_runsc.py:73`) applies solely to the tmpfs given to the
  containers that do *not* mount the named volume (`:83`, used at `:702`, `:764`, `:921`). The
  four containers that mount it pass no size option. `file_tool.py:24`'s comment "volume quota
  still wins" refers to a quota that does not exist, and the only real cap is per-operation
  (`_MAX_WRITE_BYTES`, `:27`). This task makes it more pressing by multiplying the number of
  volumes, but it is a distinct pre-existing defect against an explicit requirement. Type:
  `bugfix`.
- **FU-2: the agent volume name is constructed inline at five sites.**
  `docker_runsc.py:807`, `:1119`, `:1196`, `:1255`, `file_tool.py:57`, with `agent_fs_gc.py:89`
  holding the prefix separately and `agent_fs_gc._volume_name` being the only helper. §7.1 adds a
  helper for the *new* name rather than propagating the pattern; consolidating the existing five
  is a mechanical refactor deliberately kept out of a security fix's diff. Type: `refactor`.
- **FU-3: `stage_kernel_inputs` never prunes.** Unlike `_stage_tree`, it is a pure overlay
  (`docker_runsc.py:1174-1217`), so `inputs/` accumulates every attachment ever staged for that
  room rather than reflecting the current turn. Contained to one room after this fix, so it stops
  being a confidentiality question and becomes a correctness and quota one — the agent sees stale
  files it was never handed this turn. Related to FU-1. Type: `bugfix`.
- **FU-4: `kernel.py:43-44` documents a host-side read that does not exist.** It claims artifacts
  above `_ARTIFACT_B64_CAP` "are read from the volume host-side"; no such code exists, and
  `turn_engine.py:1135-1136` explicitly drops them ("Large artifact not inlined by the kernel —
  skipped in v1"). So an artifact over 8 MiB is silently lost. Found while confirming that no
  host path depends on the session directory's location (§6). Type: `bugfix`.
- **FU-5: `kernel.py:122-123` swallows a failed `chdir`.** `with contextlib.suppress(Exception)`
  means a session directory that cannot be entered leaves the cwd wherever it was — after this
  change, `/`, with `inputs/` and `outputs/` silently resolving nowhere useful and artifacts never
  collected. The mount makes the failure mode reachable in a new way (an unmounted volume rather
  than an unwritable subdirectory), which is worth a loud failure instead of a suppressed one.
  Type: `bugfix`.
