---
type: bugfix
status: draft
created: 2026-07-16
requirements: [R12.03, R12.05]
---

# Deleting an agent workspace file does not delete it: staging overlays the volume instead of reconciling it

## 1. Summary

Split from `2026-07-16-agent-skills`' FU-6 and FU-19, which the 2026-07-17 verification sweep
established are **one defect with two faces**: SMAP has no reliable model of what is on the
`smap-agent-fs-{agent_id}` volume. FU-19 is the false-negative face (files are there that we believe
are gone); FU-6 is the false-positive face (we believe files are staged; they may not be). They share
one function, one cause, and one fix. `2026-07-16-code-exec-agent-files-path`'s FU-3 is the same item
as FU-6 listed a second time, and closes with this.

**The user-visible harm: deletion is not deletion.** A designer deletes a file from an Agent's
workspace. The row goes (`workspace_service.py:187`), the MinIO object goes if its refcount hits zero
(`:189-195`), and an audit record asserts the removal happened (`agent.workspace_file_removed`,
`:197-211`). The bytes stay on the volume. `stage_agent_workspace_files` `put_archive`s the surviving
files *over* the existing tree (`docker_runsc.py:1053`) and never removes anything, so the deleted
file remains readable by `code_exec` at its absolute path and still appears in the `file` tool's
`list` (`driver.py:241-248`, a raw `os.listdir` with no knowledge of `agent_workspace_files`). It
survives for the Agent's entire life: `[R12.03]` binds the volume's destruction to the *Agent's* own
60-day post-deletion window, so nothing is supposed to reclaim it while the Agent lives — correctly.

The second face is the cache. `_WORKSPACE_MANIFESTS` (`:218`) is a module-global, in-process,
unbounded, never-invalidated `dict[agent_id, sha]`. On a hit (`:1029-1032`) the function returns
paths **without creating a container**, so it never discovers that the volume is gone; the model is
then told, in the system prompt and with no hedge, that files exist which do not.

## 2. Observed vs Expected

**Observed.** `stage_agent_workspace_files` (`docker_runsc.py:1008-1058`) is the whole surface:

- The cache hit at `:1029-1032` returns `_fix_paths(...)` derived purely from its arguments. It
  touches no Docker API — so a hit is **unfalsifiable by construction**, and `_ensure_runtime_ready()`
  (`:1034`) is skipped along with everything else.
- On a miss it creates a container with `command=["true"]` (`:1046`) — never started — and relies on
  `put_archive` (`:1053`) extracting into the mounted volume. Tar extraction **overlays**: it writes
  the members it carries and touches nothing else. There is no `rm`, no prune, no reconcile anywhere
  in the module.
- The cache is written at `:1057` after `put_archive` returns, which for a never-started container
  proves only that the daemon accepted the archive.

The caller computes `manifest_sha` over `"\n".join(sorted(f"{wf.path}:{wf.sha256}" for wf in chosen))`
(`turn_engine.py:845-847`). That key faithfully describes the *intended* set — deleting a file does
change the sha, so the cache correctly misses and re-stages. **The re-stage is the problem, not the
key**: it extracts the survivors over the tree and leaves the deleted file's bytes untouched.

**Expected.** `[R12.03]` (`REQUIREMENTS.md:582`) is the only requirement about this volume, and it
governs it as the `file` tool's own persistent state — 100 MB, rw at `/workspace`, withheld from
user-provided MCP containers. **It does not know the volume has a region mirroring a DB table**; that
idea arrives with `docs/agent-tools/D-code-interpreter-files.md` and the SRS never absorbed it. So
there is no requirement stating that `/workspace/agent-files/` should equal the `agent_workspace_files`
rows — which is precisely why this shipped. §11 drafts it. The intent that does exist is the delete
endpoint's own contract (`agent_workspace.py:130-151` → `workspace_service.py:177-211`): it exists to
remove a file, it audits that it did, and the agent can still read it.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | How should the volume be reconciled, given `command=["true"]` never runs? | **One container, one `python -c` doing clear-then-extract.** `put_archive` the tar to `/tmp`, then start a real command that empties `/workspace/agent-files/` and unpacks the archive over it, then `wait` and read the exit code. | The `code_exec` image runs Python already (`_create_kernel:925` proves it), so no new image and no `deploy/sandbox` change — which matters because CI cannot detect image drift (`code-exec-agent-files-path` FU-4). One container keeps the spawn cost identical to today's miss path and leaves no window between clear and extract. Two containers would double the spawn and open exactly that window. |
| Q-2 | Fix `_WORKSPACE_MANIFESTS` or remove it? | **Remove it.** | It buys only the container spawn: the bytes are already downloaded from MinIO by the caller (`turn_engine.py:853`) before this function is called, and the hit path still builds the entire tar — up to `_MAX_AGENT_FILES_BYTES` (128 MiB) — in memory purely to derive path strings, then discards it (`:1031`, `_,`). Against that it is unbounded, per-process (so wrong under multiple Arq workers by construction), never invalidated by any caller, and asserts a fact about *host* state it cannot observe. Making it correct costs more than deleting it. |
| Q-3 | Does removing the cache regress latency? | Accepted, and it is smaller than it looks. | Every turn with workspace files now spawns one container instead of zero-on-repeat. That is real, but the turn already pays a MinIO download and a 128 MiB tar on **both** paths today; the spawn is `_get_semaphore()`-bounded (`:1042`) and off the token-streaming path. If it proves material, the answer is a *verified* cache (§13 FU-2), not this one. |
| Q-4 | Is the stale file a compliance violation? | **No, and the dossier does not claim it.** | The MinIO object is deleted and the row is removed — the system of record honours the delete. Only the projection is stale, on a per-agent volume whose lifecycle `[R12.03]` already binds to that Agent. There is no §-level erasure requirement in `REQUIREMENTS.md` to violate, and `[R8.12]`'s tenancy rules do not reach a single-file delete. The defensible framing is the audit trail: it asserts a removal that did not fully happen. |
| Q-5 | Cross-tenant exposure? | **None. Stated as a negative.** | The volume name is derived from the turn's own `agent_id` at every construction site (`docker_runsc.py:1036`, `file_tool.py:56-57`, `agent_fs_gc.py:68-69`), never from tool arguments; `_safe_relpath` (`file_tool.py:30-41`) confines paths under `/workspace`; no other agent's volume is mounted in that container; `[R12.03]` withholds the mount from user MCP containers; and the staging container is `network_mode="none"` (`:1040`). This is a retention bug inside one agent's own sandbox, visible to exactly the principals who could already read the file. |

## 4. Reproduction

**Face A — deletion is not deletion (FU-19).** Deterministic.

1. Agent A has `code_exec` and the `file` tool enabled. Upload `secret.csv` via
   `POST /api/agents/{A}/workspace-files`.
2. Run a turn. The system note reads `[Files available in the code_exec workspace:
   agent-files/secret.csv]` (`turn_engine.py:808`), and the file is on the volume.
3. Delete it: `DELETE /api/agents/{A}/workspace-files/{file_id}`. The row goes, the object goes, and
   `agent.workspace_file_removed` is audited (`workspace_service.py:197-211`).
4. Run another turn. The manifest sha changed, so staging re-runs and `put_archive`s the remaining
   files — **over** the tree.
5. Ask the agent to `file`/`list` `/workspace/agent-files/` → **`secret.csv` is listed**. Ask
   `code_exec` to `open('/workspace/agent-files/secret.csv')` → **it reads**. It is merely unnamed in
   the system note.

**Face B — the cache lies (FU-6).** Requires the volume to vanish out of band.

1. Agent A stages files in worker process W. `_WORKSPACE_MANIFESTS[A] = sha`.
2. Remove the volume out of band: `docker volume rm smap-agent-fs-{A}` (or a `docker volume prune`,
   a `compose down -v`, a node replacement). **Nothing inside SMAP does this for a live Agent** —
   `agent_fs_gc` only targets Agents soft-deleted past 60 days (`agent_fs_gc.py:47-60`), and those are
   not running turns. Operator action or host migration is the honest trigger; do not invent one.
3. Next turn on W with the same file set: cache hit at `:1030` → returns paths → **no container is
   created**, so Docker never auto-creates the volume either.
4. The model is told the files exist. `code_exec` gets `FileNotFoundError`; `file`/`list` shows a set
   that contradicts the system prompt.

Note the asymmetry that hides this: on a **miss**, the container create auto-materialises a new empty
volume and `put_archive` refills it — correct result, nobody notices. Only the hit path fails, and it
fails silently in the one direction that has no error to raise.

## 5. Root Cause Analysis

**`stage_agent_workspace_files` is an overlay, not a reconciliation.** It is written as "make these
files present" when its contract needs to be "make the tree equal this set". Every symptom follows:

1. `put_archive` (`:1053`) adds and replaces; it cannot remove. Nothing else in the module ever
   deletes a path inside a volume — the deletion authority lives in the image, where the `file`
   driver renames and unlinks (`_tar_single_file`'s docstring, `:82-86`). **This is the root cause**:
   the earliest link whose correction prevents the symptom, and the only one at the layer that
   decides what the volume holds.
2. Because staging cannot remove, a deleted file has no path to removal at all — the delete endpoint
   never touches the volume (`workspace_service.py` imports no sandbox code) and no sweep reclaims it
   while the Agent lives (`agent_fs_gc` correctly waits for the Agent's own 60-day window).
3. The cache then asserts the overlay succeeded and was sufficient, from a place that cannot observe
   either.

**Why the cache is structurally wrong, not just unbounded.** `docker_runsc.py:206-207`'s own comment
explains that state is module-global here because the sandbox dataclass is `frozen=True, slots=True`
(`:262`) and rebuilt every turn. `_KERNELS` (`:212`) inherited that and *also* got
`_MAX_LIVE_KERNELS = 16` (`:209`), an idle TTL (`:210`), an `asyncio.Lock` (`:213`), an
`_evict_if_full` (`:901`), and a reaper. `_WORKSPACE_MANIFESTS`, declared six lines later, inherited
the global-ness and none of the bounding. It is a claim about the world stored somewhere that cannot
see the world — and under multiple Arq workers each process holds a different claim, none
authoritative.

**Aggravating: there is no lock, so even a correct per-process reconcile is unsound.** The only lock
in the module is `_kernels_guard` (`:213`), which guards the kernel registry and is in-process;
`_get_semaphore()` (`:1042`) is a concurrency limiter, not a mutex. Two workers staging the same
Agent concurrently interleave arbitrarily and the volume ends up a **union** of two manifests, with
both workers writing their own sha and both believing they are right. Worker B can extract between
worker A's clear and A's extract. Compare `_get_or_create_kernel` (`:875-907`), which solves exactly
this for kernels by probing for a container by deterministic name to "adopt a kernel another
worker/process may already be running" (`:892`). Staging has no analogue. §7 takes the cheap half of
this; the rest is FU-1.

**Why it is invisible.** `_stage_persisted_files` is wrapped in two nested best-effort swallows
(`turn_engine.py:776-779`, `:809-811`), both deliberate and documented ("a fault here must never
abort the turn", `:756-757`) — correct for a transient fault. But there is nothing to swallow: the
hit path *succeeds*. And the system note (`:808`) states the file list as fact, with no hedge and no
provenance, so the model cannot doubt it.

## 6. Blast Radius and Sibling Suspects

**Blast radius.** Every Agent with workspace files. Face A affects every one of them on every delete
— it is not conditional on anything. Face B needs the volume to vanish out of band, so it is rare in
practice but silent when it happens. Confined to a single Agent's own sandbox (Q-5). No bad data is
persisted anywhere SMAP owns; the repair is the volume's own contents, which §7 fixes on the next
turn.

**Sibling suspects.**

| Site | Verdict |
|---|---|
| `stage_agent_workspace_files` (`:1008-1058`) | **CONFIRMED** — both faces. |
| `stage_kernel_inputs` (`:975-1006`) | **CONFIRMED, same shape, not the same bug.** Identical `command=["true"]` + `put_archive` trick (`:995`, `:1003`), also never reconciles. But it stages per-room session inputs under `sessions/{room}/inputs/` with no DB table behind them, so nothing can go stale *relative to a source of truth* — there is no delete to honour. It has no cache, so no FU-6 face. Fix it only if §7's clear-then-extract helper is shared; otherwise leave it and record why. |
| `_WORKSPACE_MANIFESTS` (`:218`) | **CONFIRMED** — the only unbounded registry in the module; `_KERNELS` (`:212`) is the counterexample three lines above. |
| `run_file_op`'s write path (`:588-674`) | **cleared** — it stages to `.smap-stage-{uuid4}` and the in-image driver renames/unlinks, so deletion authority is where it belongs. It is the in-file precedent for create → `put_archive` → `start` → `wait` → exit code that §7 follows. |
| `WorkspaceFileService.delete` (`workspace_service.py:177-211`) | **CONFIRMED as a design gap, deliberately not fixed here.** It removes the row and the object and never touches the volume. Fixing it *there* would make the tenancy/agents service Docker-aware — a cross-layer dependency this codebase avoids. §7 makes the next turn authoritative instead, which is the smaller change and also covers files deleted while a worker was down. FU-3 records the latency this accepts. |
| `agent_fs_gc` | **cleared for this task, broken for another.** It correctly declines to touch a live Agent's volume. It also never purges anything at all — a separate defect with its own dossier (`2026-07-17-agent-fs-gc-retention-race`). Not this task's axis. |

**Not in scope, and explicitly different subjects:** `2026-07-16-agent-skills` FU-18 (`break` vs
`continue` in `_stage_persisted_files`) is a *selection-policy* bug in the application layer — which
files get staged — while this is a *materialisation* bug in infrastructure. Fixing one gives zero
leverage on the other. FU-15 / `2026-07-16-code-exec-agent-files-path` is about what path strings the
model is told, not what bytes are on disk.

## 7. Fix Design

**(a) Reconcile instead of overlay — `docker_runsc.py:1039-1058`.** Replace the never-started
`command=["true"]` container with one that runs. Order matters: `put_archive` on a *created*
container extracts immediately, so the archive must land somewhere the clear will not destroy.

1. `_create_verified(client, image=self.code_exec_image, command=["python", "-c", _RECONCILE],
   ...)` — and note this **fixes a second thing**: `stage_agent_workspace_files` and
   `stage_kernel_inputs` are the only two sites in the module that call `client.containers.create`
   directly (`:1043`, `:992`) and hand-roll `_assert_runsc`, bypassing `_create_verified`
   (`:406-421`) whose docstring (`:407-415`) records that create-then-assert-before-start *is* the
   security property. Routing through it removes that divergence rather than adding a third instance.
2. `put_archive` the tar to `/tmp` (already a tmpfs, `:1048`) while the container is created.
3. `start`, then `wait` with a timeout, then read the exit code and both log streams — the
   established shape at `:616-666`.
4. `_RECONCILE` is a module-level Python source string: empty `/workspace/agent-files/` (creating it
   if absent, owned by `_SANDBOX_UID`), then extract `/tmp/archive.tar` over it. It must be
   idempotent and must not follow symlinks out of `agent-files/`.
5. `finally: _remove_quietly(container)` (`:400-404`).

A non-zero exit must raise, not warn: the caller's best-effort swallow (`turn_engine.py:776-779`)
will turn it into a logged warning and a turn that runs without the files, which is the correct
degradation — but it must be *reached*, not skipped by a silent success.

**(b) Delete `_WORKSPACE_MANIFESTS` (Q-2).** Remove the constant (`:218`), the hit branch
(`:1029-1032`), and the write (`:1057`). `manifest_sha` stays in the signature — the caller computes
it (`turn_engine.py:845-847`) and it remains the natural cache key if FU-2 ever adds a verified one —
but this function stops asserting anything about the host it cannot check. **Removing the hit branch
also deletes the wasted 128 MiB tar** that hit path built and discarded.

Keep `_fix_paths` (`:1026-1027`) exactly as it is. It looks like dead code and is not:
`_tar_staged_inputs` writes tar members under `rel_dir` (`:133`) but returns path strings hardcoded
under `inputs/` (`:138`), so the `.replace` is load-bearing for the `agent-files/` caller. That
inconsistency is real but belongs to `2026-07-16-code-exec-agent-files-path`, whose fix replaces the
hack with an explicit prefix. **Land that dossier first if both are queued** — it touches both
branches this task rewrites.

**(c) Concurrency.** The cheap half: two workers reconciling the *same* manifest concurrently is
benign (same bytes, same clear, converges). Two workers reconciling *different* manifests is not, and
the clear makes the window worse than today's overlay. A cross-process lock is the real answer and is
out of scope (FU-1); what is in scope is that §7(a) must be safe to retry and must converge — clear
then extract, no partial state that a second run cannot fix.

**Data repair: automatic.** The first turn after the fix reconciles each Agent's volume, evicting
every stale file accumulated to date. No migration. Worth stating in the release note: files users
thought they deleted stop being readable at that point, which is the intended outcome.

## 8. Regression Test Plan

The module docstring (`:21-24`) sets the constraint: "Tests should swap in a fake `SandboxRunner`
rather than touch this class", imports are lazy so it loads without Docker, and there is **no Docker
test tier** (`pyproject.toml:353-358`). Everything below is unit-level against fakes.

Today `put_archive` appears in **zero** test files and neither staging function has any coverage at
the Docker layer. The only client fake is `test_sandbox_runtime_assertion.py:41-56` (`_FakeClient`)
with `_FakeContainer` (`:17-38`), which models `reload`/`kill`/`remove`/`start`/`attrs` and **no**
`put_archive`, `wait`, `logs`, or `volumes`. Extending it is a prerequisite for every test here.

1. **`tests/unit/test_workspace_volume_reconcile.py` (new) — the headline.** Stage manifest
   `{a.csv, b.csv}`, then stage `{a.csv}`, and assert the container's command clears the tree before
   extracting — i.e. that `b.csv` cannot survive. *Fails now*: `command=["true"]`, nothing clears,
   and the assertion has nothing to hook.
2. **Same file — the container actually runs.** Assert `start` and `wait` are called and a non-zero
   exit raises. *Fails now*: the container is never started, so `wait` is never called and an
   in-container failure is unobservable.
3. **Same file — `_create_verified` is used.** Assert gVisor is asserted before `start`. *Fails now*:
   the site hand-rolls `_assert_runsc` on a container it never starts.
4. **Same file — no cache.** Two consecutive stages of the identical manifest both create a
   container. *Fails now*: the second is a cache hit that creates nothing. This is the test that pins
   FU-6: **a hit path that touches no Docker API can never notice a missing volume**, and the only
   way to assert the absence of a lie is to assert the presence of the check.
5. **`tests/unit/test_workspace_staging.py` — must stay green.** It drives
   `TurnEngine._stage_persisted_files` over a fake runner (`:33-39`, `:64-70`) and its docstring
   (`:1-9`) documents AC-12 — a prior manifest bug where "the sandbox's cache key described bytes
   that were never written". **That is this bug's direct ancestor, one layer up**, and its continued
   passing proves the caller's contract is unchanged.
6. **A module-global reset fixture.** No existing test resets `_WORKSPACE_MANIFESTS`, so it currently
   persists across the whole unit suite. §7(b) deletes it, which removes the hazard — but the same
   fixture discipline applies to `_KERNELS` and is worth a line in the new file.

## 9. Risks and Rollback

- **The clear is destructive and runs against a live volume.** A bug in `_RECONCILE`'s path handling
  — a bad prefix, a symlink followed, a `..` — deletes data outside `agent-files/`, including the
  Agent's own `file`-tool state, which `[R12.03]` says is persistent and which no other copy exists
  of. This is the one real danger in this task. The clear must be rooted at a literal absolute path,
  must not follow symlinks, and §8 must cover the traversal cases before this ships.
- **Latency: one container spawn per turn per Agent with files** (Q-3), where repeats previously
  cost zero. Bounded by `_get_semaphore()` (`:1042`), off the streaming path, and against a MinIO
  download and a 128 MiB tar the turn already pays. Accepted; FU-2 is the escape hatch.
- **The concurrency window widens before it narrows.** Today two workers overlay; after this they
  clear-then-extract, so worker B's clear can transiently remove files worker A just wrote. Both
  converge on the next turn, and neither state is worse than today's silent union — but it is a real
  regression in the interleaved case, and it is why FU-1 is filed rather than ignored.
- **`_create_verified` changes the failure mode.** Today a gVisor assertion failure happens on a
  container that was never going to run; after this it fails a container mid-lifecycle. The
  `finally: _remove_quietly` already covers it.
- **Rollback:** (a) and (b) are independent. Reverting (a) restores the overlay; reverting (b)
  restores the cache. Neither leaves persistent state behind — the volume simply stops being
  reconciled.

## 10. Acceptance Criteria

- [ ] AC-1: §8.1 fails before the fix and passes after — a file dropped from the manifest is removed
      from the volume, not merely unnamed.
- [ ] AC-2: end-to-end, §4's Face A no longer reproduces: after a delete and one turn,
      `file`/`list` on `/workspace/agent-files/` does not show the file and `code_exec` cannot open
      it by absolute path.
- [ ] AC-3: §8.2 passes — the staging container is started, waited on, and a non-zero exit raises
      rather than silently succeeding.
- [ ] AC-4: §8.3 passes — staging goes through `_create_verified`; `grep` shows no direct
      `containers.create` in `stage_agent_workspace_files`.
- [ ] AC-5: §8.4 passes — `_WORKSPACE_MANIFESTS` no longer exists; every stage creates a container.
- [ ] AC-6: the clear cannot escape `/workspace/agent-files/` — tests cover a symlink pointing out
      of the tree and a member with `..` in its path.
- [ ] AC-7: `test_workspace_staging.py` is unchanged and green — the caller's contract is untouched.
- [ ] AC-8: `stage_agent_workspace_files`' docstring no longer claims idempotency via a cache; it
      describes reconciliation.
- [ ] AC-9: backend gates green — `pytest -q`, `ruff check . && ruff format --check .`, `mypy .`.

## 11. SRS Delta

`[R12.03]` (`REQUIREMENTS.md:582`) governs the volume as the `file` tool's own scratch state and does
not know it has a region mirroring a DB table — which is why this defect had no requirement to
violate. Add the missing invariant to §12, after `[R12.03]`:

> - **[R12.03a]** The `/workspace/agent-files/` region of an Agent's volume is a **projection** of
>   that Agent's `agent_workspace_files` rows, not independent state. Each turn that stages it makes
>   the region equal the staged set: a file no longer in the set is removed from the volume, not
>   merely omitted from the Agent's file listing. Reconciliation is authoritative over any cached
>   belief about the volume's contents. The rest of the volume — the `file` tool's own state and
>   per-room session directories — is Agent-authored and is never reconciled against a table.

The designer-upload feature is otherwise absent from the SRS entirely — `agent_workspace_files`,
`agent-files`, and the `agent-workspace` bucket appear nowhere in `REQUIREMENTS.md`, including §21.4's
persistence map (`:958-999`) and §21.5's bucket list (`:1367-1371`). That whole gap is
`2026-07-16-agent-skills` FU-1/FU-9's sweep and is not fixed here; `[R12.03a]` closes only the
invariant this task depends on.

## 12. Deviation Log

Appended by `/build`.

## 13. Follow-ups

- **FU-1: staging has no cross-process lock, so concurrent reconciles of different manifests
  interleave.** `_get_semaphore()` (`:1042`) bounds container count, not per-agent serialisation;
  `_kernels_guard` (`:213`) guards a different registry and is in-process. Two Arq workers can clear
  and extract into one volume simultaneously and neither result is either manifest.
  `_get_or_create_kernel` (`:875-907`) already solves the cross-process case for kernels by adopting
  a container by deterministic name (`:892`) — staging has no analogue. The right shape is
  `RedisBuildLockStore`'s (`contexts/knowledge/infrastructure/redis_lock.py:60-111`): `SET NX EX`
  with a fencing token and token-checked release, whose docstrings (`:27-30`, `:36-37`) already record
  two audit findings about the failure modes a naive lock hits. Mirror it in the agents context
  rather than importing across the boundary (`pyproject.toml:328` exempts that module from the
  import-linter contract). Out of scope: this task's fix converges on the next turn in every
  interleaving, so the lock is a latency-of-correctness improvement, not a correctness one.
- **FU-2: a *verified* manifest cache, if Q-3's latency proves material.** The version this task
  deletes was unfixable because it lived in a place that could not observe the volume. A correct one
  needs the sha to survive the process (Redis or a column beside `agent_workspace_files`) **and** a
  cheap liveness probe (`client.volumes.get(name)`, no container). Note a durable sha alone would fix
  FU-6 and **not** FU-19 — pruning needs the prior file *list*, not a hash of it, which is why this
  task reconciles unconditionally instead.
- **FU-3: the volume is only reconciled on a turn that stages.** A file deleted from an Agent that
  never runs again keeps its bytes on the volume until `[R12.03]`'s 60-day sweep after the Agent
  itself is deleted. §7 accepts this: the alternative is making `WorkspaceFileService.delete`
  Docker-aware, which is the cross-layer coupling §6 declined. If a deletion SLA is ever required,
  the honest fix is an async reconcile job keyed on the delete, not a synchronous call from the
  service.
- **FU-4: `stage_kernel_inputs` (`:975-1006`) keeps the never-started-container trick.** Same shape
  as the bug (`command=["true"]` at `:995`, `put_archive` at `:1003`, no reconcile) but no source of
  truth to drift from and no cache, so nothing can go stale. Left alone deliberately. If §7(a)'s
  clear-then-extract becomes a shared helper, converge them and delete the divergence; until then
  this is the module's second site that bypasses `_create_verified`.
