---
type: bugfix
status: in-progress
created: 2026-07-19
requirements: [R12.03, R12.03a, R12.03b, R31.22]
depends_on: [2026-07-19-session-dir-room-isolation]
---

# The shared agent volume is writable from `code_exec`, which reopens the cross-room channel

## 1. Summary

`2026-07-19-session-dir-room-isolation` moved per-chatroom session state onto its own volume, so
no room's `inputs/`/`outputs/` are present in another room's container. But the *agent* volume is
still mounted **read-write** in every room's kernel (`docker_runsc.py:1190-1193`) and shared across
all of that agent's rooms by design. An agent can therefore copy session data onto `/workspace` in
room A and read it back in room B - deliberately, or under prompt injection. That dossier's
`check-security` gate recorded this as FU-7, and [R12.03b] was amended to stop denying it
(`REQUIREMENTS.md:597`). This task closes it.

The kernel process itself never writes to `/workspace`: every write it performs lands under
`/session` or `/tmp` (§2). The read-write bind serves only agent-authored Python, and nothing in
the codebase depends on that.

## 2. Observed vs Expected

- **Observed.**
  - The kernel container binds the agent volume `rw` (`docker_runsc.py:1190-1193`, the two-key
    volumes dict added by the session-isolation task).
  - **The kernel needs none of it.** `_SESSION_DIR` is `/session` (`kernel.py:42`); `_OUTPUTS` and
    `_INPUTS` mkdir beneath it (`:126-127`); the cwd is `/session` (`:132`); figures save to
    `_OUTPUTS` (`:118`); the socket is on `/tmp` (`:38`). There is no `/workspace` literal in the
    file, and reads are confined to `_OUTPUTS` (`:76`, `:88-93`).
  - So the write capability exists solely for agent-authored code, and it is the residual channel:
    `shutil.copy('/session/inputs/x.pdf', '/workspace/x.pdf')` in room A, `open('/workspace/x.pdf')`
    in room B.
  - **It also keeps a separate accepted risk alive.** `2026-07-16-agent-workspace-volume-reconcile`
    FU-7 (`spec.md:440-448`) accepts that a symlink "the agent's own `code_exec` left at a staged
    path" may cause `put_archive` to write through it to elsewhere on the volume. The `file` tool
    cannot create symlinks - its write path is a staged `os.replace` (`driver.py:262`) - so
    `code_exec` is the only way one gets onto that volume.
- **Expected** (Q-1). `code_exec` reads the agent volume and does not write it. Writes to agent
  state go through the `file` tool, which is already the documented owner of that region
  ([R12.03a], `REQUIREMENTS.md:596`: "the `file` tool's own state").

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Close the channel by mounting the agent volume `ro` in the kernel, or leave it open and documented? | **`ro` in the kernel only.** The `file` tool, the reconcile stager, and the purge keep `rw`. | Leaving it documented was the interim state, not the destination - [R12.03b] currently has to carry a caveat saying an Agent can move data between its own rooms, which is a boundary that exists on paper only. The cost is bounded and the benefit is not: it closes the residual channel *and* retires the symlink vector above, because `code_exec` is the only writer that can plant one. Each of the four mount sites builds its own dict from a fresh `_base_host_config()` (`docker_runsc.py:846`, `:1183`, `:1337`, `:1397`), so the change is genuinely one site. |
| Q-2 | What breaks? | Agent-authored Python that writes under `/workspace`. Nothing else. | Verified: the kernel writes nothing there (§2); **no test** asserts a code_exec write to `/workspace` or a cross-tool file handoff; **no skill contract** expects a script to write ([R31.22] `REQUIREMENTS.md:2172` stages scripts and reports paths, nothing more - and `skills/` is a reconciled projection whose contents are pruned next turn anyway, `REQUIREMENTS.md:596`); **no system prompt** grants it. The one user-facing blurb (`frontend/src/slices/agents/locales/en.json:367`) mentions charts carrying over, which is `/session/outputs`. |
| Q-3 | The `code_exec` description says the volume is "the same files the `file` tool sees … refer to it by absolute path". Does it need to change? | **Yes - say read-only and name the write path.** | It never said "write", but a model can reasonably read "the agent's persistent volume, at /workspace" as read/write (`builtin_tools.py:203-208`). An agent that discovers the restriction by `PermissionError` mid-task wastes a tool round against `MAX_TOOL_ROUNDS = 8` and may confabulate. The description is the only contract the model reads - the same reasoning as `2026-07-16-workspace-path-convention`. |
| Q-4 | Does this affect the headless / run-and-burn path? | **No.** | It mounts no named volume at all - `_sandbox_tmpfs()` gives it a tmpfs `/workspace` (`docker_runsc.py:954`), pinned by `test_headless_code_exec_mounts_no_named_volume`. A tmpfs stays writable and is discarded with the container. |
| Q-5 | Data repair? | **None.** | Nothing is persisted wrongly. Any data an agent already laundered onto `/workspace` is indistinguishable from legitimate agent state and must not be guessed at. |

## 4. Reproduction

Deterministic. Preconditions: one agent with `HOSTED_CODE_INTERPRETER` in two chatrooms A and B
with different member sets, and an attachment uploaded in room A.

1. In room A: `code_exec(source="import shutil; shutil.copy('/session/inputs/confidential.pdf', '/workspace/leak.pdf')")`
   → succeeds today.
2. In room B: `code_exec(source="open('/workspace/leak.pdf','rb').read()[:64]")` → room A's bytes.
3. After the fix, step 1 raises `OSError: [Errno 30] Read-only file system` and step 2 finds nothing.

Step 1 is the whole defect: the copy is what the mount permits and the boundary does not.

Not reproducible in CI - no Docker/gVisor tier, and gVisor is Linux-only. §8 tests the mount wiring
and the description; §4 is `/verify` on the Linux staging host.

## 5. Root Cause Analysis

1. [R12.03] gives the agent one persistent volume, shared across its rooms, mounted read-write
   (`REQUIREMENTS.md:595`). Correct: an agent's own files should follow the agent.
2. The `code_exec` kernel was given that mount so the agent could reach its own files, at a time
   when session state lived on the same volume and the read-write bind was doing double duty
   (`docker_runsc.py:1190-1193`).
3. `2026-07-19-session-dir-room-isolation` split session state onto its own mount. **The kernel's
   need to write `/workspace` went away with it** - the kernel now writes only `/session` - but the
   bind mode did not change.
4. So a write capability that no longer serves any platform purpose remains, and it is exactly the
   capability that lets an agent move room-scoped data onto agent-scoped storage.

**Root cause: step 3** - a capability outliving its reason. Not "the volume should not be shared"
(it should; [R12.03] is right) and not "the agent should not reach its own files from `code_exec`"
(it should; reading is the point). The defect is narrowly that *writing* is still permitted where
nothing needs it, and that this is the one permission that converts a shared read into a
cross-room transfer.

## 6. Blast Radius and Sibling Suspects

**Blast radius.** Every agent in more than one chatroom, every tenant. Bounded to one agent's own
rooms (the volume is per-agent), so not cross-agent or cross-tenant. Requires the agent to act on
attacker-influenced instructions while in the room holding the data - so, unlike the pre-split bug,
not a passive read from the attacker's own room.

**Sibling suspects.**

- **The `file` tool's container** → **cleared, must stay `rw`.** Separate container per call
  (`docker_runsc.py:833-875`, mount at `:849`), and it is the sanctioned write path for agent state
  ([R12.03a]). Its `op=write` is a staged `os.replace` (`driver.py:262`), which cannot create a
  symlink.
- **The reconcile stager** (`docker_runsc.py:1337-1339`) and **`purge_legacy_session_dirs`**
  (`:1397-1399`) → **cleared, must stay `rw`.** Both exist to modify the volume, and neither runs
  agent-authored code.
- **The session volume at `/session`** → **cleared, must stay `rw`.** The agent legitimately writes
  its artifacts there, and it is room-scoped, so a write cannot cross a boundary.
- **Headless / run-and-burn** → **cleared** (Q-4).
- **MCP probe / tool-invoke containers** → **cleared.** They never receive the volume
  (`docker_runsc.py:702`, `:764`), per [R12.03].
- **The future Local Shell** (`docs/agent-tools/F-local-shell-stub.md:59-63`) states it "shares the
  per-agent `/workspace` volume with Code Interpreter" → **not built; flagged as FU-1** so it
  inherits the read-only rule rather than silently reopening this.

## 7. Fix Design

1. **Mount `ro` in the kernel.** `docker_runsc.py:1190-1193` - the agent volume entry becomes
   `{"bind": _VOLUME_ROOT, "mode": "ro"}`. The session volume entry is untouched (`rw`). No other
   mount site changes; each builds its own dict (Q-1).
2. **Say so where the model reads it.** `builtin_tools.py:203-208` gains that `/workspace` is
   read-only from `code_exec` and that the `file` tool is the way to write agent state - so the
   model routes correctly instead of discovering it by exception.
3. **No kernel change.** `kernel.py` never touches `/workspace` (§2). The stale `Dockerfile`
   comments at `deploy/sandbox/code-exec/Dockerfile:45,52` ("writes land in /workspace (tmpfs) or
   /tmp only"; `WORKDIR /workspace`) are already wrong after the session split and are corrected
   here since this task makes them actively misleading.

Why this corrects the root cause rather than masking it: the channel is a *capability*, and the
only durable way to remove a capability is to not grant it. A guard inside the kernel would be
guarding against arbitrary code, which is the thing that cannot be guarded - the same argument the
session split rested on.

**Data repair:** none (Q-5).

## 8. Regression Test Plan

- **T-1 (fails now).** `test_workspace_staging.py`: `_create_kernel`'s host config mounts the agent
  volume `ro` and the session volume `rw`. Red today - both are `rw` (`docker_runsc.py:1190-1193`).
  Note no test asserts the kernel's bind modes at all today, so this is new coverage on the line
  that carries the property.
- **T-2 (fails now).** `test_builtin_tools_wiring.py`: the `code_exec` description states
  `/workspace` is read-only and names the `file` tool as the write path. Red today.
- **T-3.** The other three mount sites keep `rw` - `run_file_op`, `_stage_tree`, and
  `purge_legacy_session_dirs`. Asserted so a future "make it consistent" sweep cannot quietly
  read-only the containers whose whole job is writing.
- **T-4.** The headless path still mounts no named volume (Q-4) - already covered by
  `test_headless_code_exec_mounts_no_named_volume`; extended only if T-1 changes its shape.

End-to-end (`/verify`, Linux staging): the §4 reproduction, expecting step 1 to fail with a
read-only filesystem error and step 2 to find nothing.

## 9. Risks and Rollback

- **An agent that writes to `/workspace` from `code_exec` today breaks.** This is the whole
  behaviour change. Nothing in the repo does it (Q-2), but a *tenant's* agent may have learned the
  habit from the current description, and its instructions may encode it. The failure is loud
  (`OSError`) and self-describing, and step 2 of §7 redirects the model, but this belongs in the
  release notes: it is the one user-visible regression.
- **Skill scripts** that write beside themselves would break. No skill contract expects this
  ([R31.22]) and `skills/` is a reconciled projection that discards such writes next turn anyway -
  but a third-party skill could still be doing it. Same release-note treatment.
- **Docker `ro` binds are enforced by the kernel, not by gVisor policy**, so this does not depend on
  the runsc runtime being configured correctly - one of the few controls in this area that holds
  even if the sandbox policy is misapplied. Worth stating: it makes this a genuine defence-in-depth
  layer rather than a second lock on the same door.
- **Rollback:** revert one dict entry. No migration, no persisted state, no API contract, no image
  change (the kernel is untouched, so no lockstep deploy - unlike the session split).

## 10. Acceptance Criteria

- [x] AC-1: T-1 and T-2 fail against current code and pass after the fix. T-1 observed red as
      `AssertionError: assert 'rw' == 'ro'`; T-2 red on the absent substring.
- [x] AC-2: `code_exec` can still **read** `/workspace` - the mount is present, only its mode
      changed, so `agent-files/`, `skills/` and the `file` tool's state stay reachable by absolute
      path. (T-1 asserts the bind still exists; a `ro` bind is a readable one.)
- [ ] AC-3: `code_exec` **cannot write** anywhere under `/workspace`; the §4 reproduction's step 1
      fails. **Outstanding - needs `/verify`.** The wiring is pinned by T-1, but only a live
      container proves the kernel enforces `ro`. Blocked on this host: gVisor is Linux-only and
      `docker_runsc.py:620` rejects any runtime that is not `runsc`.
- [x] AC-4: The `file` tool can still read *and write* - its container keeps `rw`
      (`test_the_writing_containers_keep_read_write`).
- [x] AC-5: Artifacts still work - `/session` stays `rw` (asserted in T-1 alongside the `ro`
      assertion), and nothing in the artifact path touches `/workspace`.
- [x] AC-6: The staging and purge containers keep `rw` (T-3 drives `run_file_op`,
      `stage_agent_workspace_files` and `purge_legacy_session_dirs` for real and checks each).
- [x] AC-7: `code_exec`'s description states the read-only rule and names the `file` tool as the
      write path (`test_code_exec_description_states_the_workspace_is_read_only`).

## 11. SRS Delta

Two amendments, both narrowing claims this task makes false.

Amend **[R12.03]** (`REQUIREMENTS.md:595`) - "mounted read-write at `/workspace`" is no longer true
of every container that receives it. Replace that clause with:

> mounted at `/workspace` - read-write in the built-in `file` tool's container and in the platform's
> staging helpers, and **read-only in the `code_exec` kernel**, which needs only to read it

Amend **[R12.03b]** (`REQUIREMENTS.md:597`) - strike the residual-channel caveat added on
2026-07-19, which this task removes the basis for. Replace the sentence beginning "That shared
volume is writable from every chatroom's execution context…" with:

> That shared volume is mounted **read-only** in a chatroom's `code_exec` kernel, so an Agent cannot
> copy session data onto it and read that data back in another chatroom. Writes to Agent-scoped
> state go through the `file` tool, whose container is not room-scoped and whose write path cannot
> create a symlink. The isolation of session state is therefore a property of the mounts on both
> sides - what is present in a chatroom's context, and what that context may write - rather than of
> the Agent's behaviour.

## 12. Deviation Log

- **D-1.** §7.3 said the stale `Dockerfile` comments would be "corrected". They were rewritten
  rather than patched: the original single claim ("writes land in /workspace (tmpfs) or /tmp only")
  cannot be made true, because where writes land now depends on which of the two invocation paths
  the host uses - run-and-burn gets a tmpfs `/workspace`, the live kernel gets a read-only
  `/workspace` plus a writable `/session`. The `WORKDIR` was kept, not dropped as FU-3 suggested:
  it is still correct for the run-and-burn path, and the comment now says so.

**Build state (2026-07-19).** Landed in `<this commit>`. Gates: `pytest tests/unit` 5341 passed /
6 skipped (pre-existing), `ruff check`, `ruff format --check`, `mypy .` (791 files) clean.
Integration and wiring tiers not run (no local Postgres/Redis) and untouched by this diff.

All ACs met except **AC-3**, which needs `/verify` on a Linux host with gVisor - the same blocker
as the two other outstanding verifications. The diff is one bind mode, one description string, and
one Dockerfile comment block; no image rebuild and no lockstep deploy, since `kernel.py` is
untouched.

## 13. Follow-ups

- **FU-1: the Local Shell stub inherits the old assumption.**
  `docs/agent-tools/F-local-shell-stub.md:59-63` says it "shares the per-agent `/workspace` volume
  with Code Interpreter". Not built, so not a defect today - but built as written it would reopen
  exactly this channel, since a shell is agent-authored code with a writable mount. The stub should
  state the read-only rule before anyone implements it. Type: `docs`.
- **FU-2: `2026-07-16-agent-workspace-volume-reconcile` FU-7 can be closed.** That entry accepts the
  risk that a symlink left by the agent's own `code_exec` causes `put_archive` to write through it
  (`spec.md:440-448`). With the kernel read-only, `code_exec` can no longer plant one, and the
  `file` tool's staged `os.replace` never could. The accepted risk should be re-examined and, if the
  reasoning holds, retired - not left standing as a caveat with no remaining cause. Type: `docs`,
  contingent on this task shipping.
- **FU-3: the `code_exec` image's `Dockerfile` comments were already stale before this task.**
  `deploy/sandbox/code-exec/Dockerfile:45` claims "writes land in /workspace (tmpfs) or /tmp only"
  and `:52` sets `WORKDIR /workspace`; both were overtaken by the session split (the cwd is set to
  `/session` at `kernel.py:132`, and `/workspace` is a named volume, not a tmpfs, for this
  container). §7.3 corrects them because this task makes them misleading, but the `WORKDIR` itself
  is now meaningless and could simply be dropped. Type: `chore`.
