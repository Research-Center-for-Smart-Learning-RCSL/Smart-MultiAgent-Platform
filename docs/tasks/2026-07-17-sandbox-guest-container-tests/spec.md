---
type: feature
status: implemented
created: 2026-07-17
requirements: [R24.15, R12.03, R12.03b]
depends_on:
  - 2026-07-16-workspace-path-convention
  - 2026-07-19-session-dir-room-isolation
  - 2026-07-19-workspace-readonly-in-kernel
---

# Test the sandbox images' guest side in CI, and stamp them so a stale one is visible

## 1. Summary

> **Revised 2026-07-20 before approval.** Three dossiers landed between drafting and this
> revision and two of them changed the ground this spec stands on. `2026-07-16-workspace-path-convention`
> changed `list`'s output shape (AC-4, AC-5). `2026-07-19-session-dir-room-isolation` and
> `2026-07-19-workspace-readonly-in-kernel` moved the kernel off the agent volume entirely and
> made `/workspace` read-only there, which **falsified §4's "all three share one volume" claim
> and inverted AC-7** — see Q-7. OQ-1 and OQ-2 are settled and closed. Everything else in this
> dossier re-verified against the tree on 2026-07-20 and stands, including the `ci.yml:185-217`
> citations and the required-gate lines.

Derived from `2026-07-16-code-execution`' FU-4 ("no Docker/gVisor test tier") and
`2026-07-16-workspace-path-convention`' FU-2 (a stale sandbox image is undetectable). Both were
verified against the tree on 2026-07-17, and **the premise they share is false**, which is why this
dossier is much narrower than either entry implies.

**Docker-in-CI already exists, extensively.** `sandbox-images` (`.github/workflows/ci.yml:185-217`)
does not merely build the two images — it **runs code inside both of them**: `:194-204` runs
`smap/mcp-runtime:ci probe` against the baked `server-everything` and asserts the returned JSON has a
non-empty `tools` array; `:205-209` runs `docker run --rm smap/code-exec:ci python -c "print(6*7)"`
and asserts `42`. Four other jobs use Docker (`backend-wiring:113`, `compose-validate:561`,
`compose-boot-prod:605`, `frontend-e2e:432`). "No Docker test tier" is true only of the pytest marker
tiers (`pyproject.toml:353-358`); it is false of CI.

So the real gap is not Docker. It is three specific things:

1. **The `file` driver is never executed anywhere.** `cmd_file` (`deploy/sandbox/driver/driver.py:239`)
   implements `list`/`read`/`write` and no test — unit or container — ever runs it. This is where the
   workspace-path FU-3 bug lives: `:245` is `sorted(os.listdir(path))`, flat and single-level, so a
   nested workspace lists as though it were empty of subtree content.
2. **Nothing detects a stale image.** Verified: `deploy/sandbox` contains **no `LABEL` and no `ARG`**
   — the whole directory is two Dockerfiles, `driver/{driver,protocol}.py`,
   `code-exec/kernel/{kernel,client}.py`, and a `.dockerignore`. There is no version file. So FU-2's
   "hook a check onto the readiness gate" cannot work as written: **there is no stamp to read.** One
   must be created first.
3. **Three workspace roots coexist and nothing asserts they agree** (§4). This is the highest-value
   untested surface and the class of bug the workspace-path-convention work exists to prevent.

**What this dossier deliberately does not do: gVisor.** `code-execution` FU-4 asks for a runsc tier.
That is a different task with a different blocker, and Q-4 explains why bundling them would hold two
cheap certain fixes hostage to an unanswered feasibility question. It is §16 FU-1, with a prototype
as its first acceptance criterion.

## 2. Goals and Non-goals

**Goals.**
- The `file` driver's three operations execute in CI, against the real image, on every PR.
- The three workspace roots are asserted to agree, so a path-convention drift fails a gate instead of
  a user's tool call.
- A sandbox image carries a build stamp, and CI fails when the stamp does not match the sources —
  closing workspace-path FU-2.
- All of it lands in `sandbox-images`, which already builds both images and is already a required
  gate (`ci.yml:772`, `:817`).

**Non-goals.**
- **gVisor / runsc.** Q-4. Nothing here needs it; §16 FU-1 owns it.
- **Testing `docker_runsc.py` (the host side).** It cannot run without runsc — `_base_host_config`
  hardcodes `"runtime": "runsc"` (`contexts/agents/infrastructure/sandbox/docker_runsc.py:387`) and
  `_assert_runsc` (`:364`) raises on anything else. There is no env var or injection point, so this
  is gated on FU-1, not on effort.
- **Fixing FU-3's recursive `list`.** This dossier builds the tier that makes the fix *testable* and
  writes the failing-shape test; the behaviour change belongs to `2026-07-16-workspace-path-convention`,
  which owns the convention. Q-3.
- **A runtime stamp check.** Q-2 rejects putting it on `_ensure_runtime_ready`; §16 FU-2 carries the
  worker-startup variant.
- **The egress proxy and MCP supervisor paths.** Reachable in CI but a separate dossier's worth
  (§16 FU-3).

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Where does the tier live — a new job, or an existing one? | **Extend `sandbox-images`.** | It already builds both images and already `docker run`s them (`ci.yml:191-209`), so a test that needs "run the image with a command" is a step addition, not a new job. It is already a required gate. A new job would rebuild the same images for no gain. Timeout may need lifting from 15 (`:187`). |
| Q-2 | Where does the stale-image check hook? FU-2 proposes `_ensure_runtime_ready`. | **In CI, not at runtime. Reject the readiness-gate hook.** | Three reasons, all structural. (a) **Wrong posture:** that gate's documented contract (`docker_runsc.py:286-291`) is advisory — unreachable supervisor *fails open*, only an explicit 503 fails closed. A stale-image check that fails open detects nothing when it matters; one that fails closed contradicts the documented posture and turns image drift into a hard outage of every agent tool call, which is worse than the drift. (b) **Wrong process:** the supervisor probes the host *runtime* and has no notion of `mcp_image`; teaching it the pins adds config surface to a service whose virtue is doing one stdlib-only thing. (c) **Wrong cache:** the gate's 10 s TTL (`:166`) is sized for a burst of spawns sharing a round-trip; an image-inspect is once-per-process. And the drift FU-2 describes is a *CI* gap — CI is where it belongs. |
| Q-3 | Does this dossier fix FU-3's non-recursive `list`? | **No — it writes the test that exposes it and leaves the fix to the convention's owner.** | `driver.py:245` is flat by construction. Whether `list` should recurse, and what shape it returns if it does, is a *convention* decision (`2026-07-16-workspace-path-convention` owns it), not a test-tier decision. Fixing it here would mean this dossier silently authoring an API contract. AC-4 asserts the *current* flat behaviour so the fix has a green baseline to flip; the flip is the other dossier's AC. **Note (2026-07-20):** that dossier has since landed and changed the entry *shape* to absolute paths without touching flatness, so FU-3 remains open and AC-4 still has a bug to pin — only its expected strings changed. |
| Q-4 | Bundle the gVisor tier in, as `code-execution` FU-4 asks? | **No. Split it, with a prototype as its first AC.** | The two halves share nothing. The guest-side work is cheap, certain, and needs no runsc. The gVisor half is gated on a question the repo cannot answer: **there is zero gVisor provisioning automation anywhere** — every reference (`deploy/README.md:143-160`, `docker-compose.yml:358-360`) delegates to upstream's install guide, `preflight.sh:207-217` only *warns* if `runsc` is absent, and `ci.yml:182-184`'s own comment says runsc is "exercised on the staging box, not here". A CI tier would be the first place in the repo to provision it. Bundling would hold two shippable fixes hostage to that. §16 FU-1 states the prototype. |
| Q-5 | Run the tier on the runner, or inside compose like `backend-wiring`? | **On the runner directly, as `sandbox-images` already does.** | The two precedents pull opposite ways and this must be decided explicitly. `backend-wiring` runs pytest *inside* a container (`ci.yml:141-154`); a container tier must *spawn* containers, so mirroring that shape needs docker-in-docker. `sandbox-images` runs `docker run` from the runner, which is exactly the shape needed. **Gotcha:** the wiring tier bind-mounts only `backend/` (`ci.yml:147`), which is why `test_sandbox_driver_protocol.py:25-29` skips itself there — `deploy/` is absent. `frontend-e2e` hits the same wall and mounts `deploy/` explicitly (`ci.yml:483`). Running on the runner sidesteps both. |
| Q-6 | What feeds the stamp — git SHA or a content hash? | **A hash of `deploy/sandbox/**`.** | The git SHA changes on every commit, so it would mark the image stale on commits that did not touch it — a gate that cries wolf gets disabled. A content hash of the build context is what actually determines whether a rebuild is needed, and it is what the drift check wants to compare. |
| Q-7 | §4's "three roots share one volume" is now false ([R12.03b]). What does AC-7 become? | **Invert it: assert the isolation boundary, not the agreement.** | `f7c4ea6` moved the kernel to `/session` on a per-`(agent, chatroom)` volume and `06798c9` made `/workspace` read-only there, precisely so the roots would *not* share a volume. An AC asserting they agree would now assert the vulnerability those two fixed. The replacement asserts what [R12.03b] actually promises — a property **of the mounts**, which is exactly what a container tier can prove and a unit test cannot. This is currently the only part of those two dossiers with no real-container verification. **Constraint that shapes the test:** no root is expected to pre-exist — `kernel.py:132-133` creates `/session` with `parents=True` and the chdir at `:137-138` is wrapped in `contextlib.suppress`, so a mis-bound mount yields a silently-empty directory on the container layer. Absence proves nothing; the test must assert mount properties and content, not `os.path.exists`. |
| Q-8 | Add the kernel protocol stamp to scope? | **Yes — new AC-12.** | `kernel.py:50` `PROTOCOL_VERSION = 2` must equal `docker_runsc.py:81` `_KERNEL_PROTOCOL_VERSION`. `f7c4ea6`'s message states the pin is a constant-to-constant unit test "since nothing in CI runs a container" — this dossier is the thing that runs a container in CI, so it closes that gap for ~3 lines. Both mismatch directions are silent in production (inputs staged where the kernel does not look, or `/session` unbound), which is the failure class the whole dossier exists to make visible. |
| Q-9 | The two 07-19 dossiers' code has landed but their `status` is `in-progress`. Add them to `depends_on`? | **Yes, both.** | Contract §"Dependencies and sequencing": AC-7's rewritten form asserts behaviour those two dossiers introduce, which is a logical prerequisite regardless of the code already being in the tree. Recording it honestly costs a `/build` gate wait until they close out; omitting it would leave `BOARD.md` claiming this is unblocked when its central AC depends on unfinished work. The frontmatter is the source of truth, so it must say the true thing. |

## 4. Current State

**What is already covered, and by what** — stating this matters because two FUs overestimated the gap:

| Surface | Covered by | Note |
|---|---|---|
| `protocol.py` pure functions | `tests/unit/test_sandbox_driver_protocol.py` | loads `deploy/sandbox/driver/protocol.py` by path via `importlib` (`:19`, `:32-40`); **skips itself when `deploy/` is absent** (`:25-29`) |
| `kernel.py` `_run`, artifact diffing, reaper | `tests/unit/test_code_exec_kernel.py` | loads the file by path (`:21`, `:35-44`), tmpdir workspace |
| readiness gate, runtime assertion | `tests/unit/test_sandbox_readiness_gate.py`, `test_sandbox_runtime_assertion.py` | `_classify_supervisor_status` (`docker_runsc.py:333-348`) is pure and unit-tested |
| image builds | `ci.yml:191-193` | both images, every PR |
| `probe` against baked stdio MCP | `ci.yml:194-204` | real end-to-end: `probe`, `_stdio_params`, `frame_tools` |
| `python -c` in code-exec | `ci.yml:205-209` | asserts `42` |

**What is not covered — this dossier's payload:**
- **`cmd_file`** (`driver.py:239-274`). Never run. All three ops are reachable with
  `-e SMAP_FILE_OP=… -e SMAP_FILE_PATH=… -v vol:/workspace`. `:245`'s flat `sorted(os.listdir(path))`
  is FU-3 — still flat, but both branches now render through `format_listing`
  (`protocol.py:103-113`), which emits **absolute paths one per line** and routes every emitted
  line back through `safe_workspace_path`. That is what AC-4/AC-5 assert against.
- **`cmd_exec`** (`driver.py:277`). Dead on the default path, never exercised.
- **The kernel as a live process** (`kernel.py:180-209`) — the AF_UNIX server loop and its framing
  (`_recv_framed`/`_send_framed`, `:158-177`) plus the `client.py` relay. Only the pure `_run` helper
  is tested. `docker run -d` + `docker exec client.py` reproduces the host's exact call shape
  (`docker_runsc.py:831-836`) with no backend.
- **The three roots — corrected 2026-07-20, they no longer share a volume (Q-7):**
  - `file` tool → `/workspace` on `smap-agent-fs-{agent_id}` (`file_tool.py:23` `_ROOT`;
    guest side `protocol.py:29` `WORKSPACE_ROOT`; `driver.py:241`)
  - kernel → **`/session`** on its own per-`(agent, chatroom)` volume
    `smap-agent-session-{agent_id}-{chatroom_id}` (`kernel.py:42`; host `docker_runsc.py:76`
    `_SESSION_ROOT`), and `_run` **chdirs into it** (`:137-138`), so agent-facing relative paths
    are `inputs/…` / `outputs/…`. The agent volume is mounted **read-only** in this container
    (`docker_runsc.py:1247` vs `:1248`).
  - staging → `/workspace/agent-files/` on the agent volume (`docker_runsc.py:1571`, joined onto
    `_VOLUME_ROOT` at `:70`)

  The old `/workspace/sessions/{room}` survives only as `_LEGACY_SESSIONS_SUBDIR`
  (`docker_runsc.py:85`), commented "Nothing writes here any more."

  **What is still unasserted, and is AC-7's target:** the separation is a property of the mounts
  ([R12.03b]) and nothing executes a check of it. The three `/workspace` literals are also still
  independently hardcoded three times — `file_tool.py:23`, `protocol.py:29`,
  `docker_runsc.py:70` — and `docker_runsc.py:65-69` says so outright: they "must equal ... 
  **nothing enforces that agreement**."
- **The host/kernel protocol stamp** (`kernel.py:50` `PROTOCOL_VERSION = 2` against
  `docker_runsc.py:81` `_KERNEL_PROTOCOL_VERSION`) is pinned only constant-to-constant in a unit
  test, because nothing in CI runs a container. AC-12 (Q-8).
- **matplotlib figure capture** (`kernel.py:97-112`) — needs the real image; matplotlib is installed
  only there (`code-exec/Dockerfile:23-28`).

**The stamp: verified absent.** `deploy/sandbox` holds two Dockerfiles and four Python files. Grep for
`LABEL|ARG ` across the tree: **zero hits**. FU-2 cannot be implemented as written.

**An unused precedent worth knowing:** `compose.test.yml:170-182` defines `egress-proxy-smoke` under
`profiles: [smoke]` — a container that runs a script and exits non-zero on failure. **No CI job
invokes it.** The container-test shape was designed here and never wired up.

**What a test needs from the environment.** `DockerRunscSandbox` is a frozen slots dataclass with
defaults on every field (`docker_runsc.py:262-272`), so `DockerRunscSandbox(mcp_image=…,
code_exec_image=…)` is the whole setup — no app wiring, no settings; `docker_runsc_sandbox_from_settings`
(`:1145-1165`) is the only settings-coupled path and is off the constructor. The docker SDK import is
lazy (`:180-191`), so the module imports without a daemon. The **only hard blocker is `runsc`** — the
supervisor gate self-disables when `supervisor_url` is `""` (`:293-294`), `_egress_env` returns `{}`
on empty config (`:353-355`), and `run_file_op`/`run_code_exec`/kernel/staging all override to
`network_mode="none"` (`:603`, `:701`, `:916`, `:989`, `:1041`) so no network is needed. That is why
§2 can exclude the host side without excluding much else.

**Module-global state is a live hazard for any tier.** `_KERNELS` (`:212`), `_WORKSPACE_MANIFESTS`
(`:218`), `_runtime_ready_at` (`:167`), `_docker_client_instance` (`:177`), `_container_semaphore`
(`:159`) all persist across tests in one process. `_WORKSPACE_MANIFESTS` is the dangerous one: a
second `stage_agent_workspace_files` with the same `(agent_id, manifest_sha)` **silently skips the
container spawn** (`:1029-1032`) — a test that does not reset it passes while testing nothing.

## 5. Design

### Options considered

1. **Extend `sandbox-images` with guest-side `docker run` steps + a build stamp — chosen.** No new
   job, no gVisor, no daemon tricks, inside an existing required gate.
2. **A new pytest marker tier driving `DockerRunscSandbox`.** This is what `code-execution` FU-4
   literally asks for, and it is blocked: `_base_host_config:387` hardcodes `"runtime": "runsc"`, so
   `containers.create` fails at the **daemon** with an unknown-runtime `APIError` before
   `_assert_runsc` (`:364`) is even reached (the code says as much at `:413-414`). Making it work
   needs either runsc on the runner (FU-1's prototype) or a `runtime` field on the dataclass — and
   **that field makes a security assertion configurable**, which is precisely what `_assert_runsc`
   exists to prevent. If it is ever taken, it must be un-settable from settings (no `SandboxSection`
   entry, no env alias) so only a test can reach it. Rejected here.
3. **Wire up the unused `egress-proxy-smoke` profile shape.** A real precedent, but it targets the
   proxy, not the driver. §16 FU-3.
4. **Runtime stamp check on `_ensure_runtime_ready`** — FU-2's own proposal. Rejected by Q-2.

### Decision

Guest-side only, in `sandbox-images`, on the runner. Two independent pieces that could ship as two
commits: **the stamp** (Q-6) and **the `cmd_file` + path-convention tests** (Q-3).

## 6. Detailed Changes

1. **Both Dockerfiles** (`deploy/sandbox/{mcp-runtime,code-exec}/Dockerfile`) — add:
   ```
   ARG SMAP_SANDBOX_STAMP=dev
   LABEL smap.sandbox.stamp="${SMAP_SANDBOX_STAMP}"
   ```
   Place the `LABEL` **last**, after the layers that do real work, so the stamp does not invalidate
   the build cache on every source change.

2. **`ci.yml` `sandbox-images`** — compute the stamp once (a hash over `deploy/sandbox/**` per Q-6),
   using the command settled in OQ-2:
   ```
   git ls-files -s deploy/sandbox | git hash-object --stdin
   ```
   Pass it via `--build-arg` to both builds (`:191`, `:193`), then assert it round-trips:
   `docker image inspect --format '{{index .Config.Labels "smap.sandbox.stamp"}}'` equals the
   expected hash. ~15 lines. This is the whole of FU-2's closure.

3. **`ci.yml` `sandbox-images`** — new steps exercising `cmd_file` against `smap/mcp-runtime:ci` with
   a scratch named volume:
   - `write` → `read` round-trip, asserting the payload survives.
   - `list` on a directory containing a nested subtree, asserting the **current flat** output in
     its **post-`format_listing` shape** — absolute paths, one per line, single level (Q-3/AC-4).
   - `list` on a single file, asserting the absolute path of that file, not its basename
     (`driver.py:249` → `format_listing(dirname, [basename])`).
   - A path-traversal case: `SMAP_FILE_PATH=/workspace/../etc/passwd` must be rejected by
     `safe_workspace_path` (`:239`), not served. This is a security assertion and is the reason
     `cmd_file` being untested matters beyond FU-3.
   - **Cleanup:** `docker volume rm` in an `if: always()` step. Named volumes are auto-created on
     `create` (`docker_runsc.py:600`, `:912`, `:986`, `:1036`) and will otherwise leak one per test.

4. **`ci.yml` `sandbox-images`** — a mount-isolation step asserting [R12.03b] (rewritten per Q-7;
   the old "three roots agree on one volume" step would assert the vulnerability `f7c4ea6` fixed).
   Needs **both images** (OQ-1): the `file` root is only reachable through mcp-runtime's
   ENTRYPOINT, the kernel only exists in code-exec. Three assertions, all against real mounts:
   - **`/session` is not under `/workspace`.** Run the kernel image with an agent volume at
     `/workspace` and a session volume at `/session`; write to `/session/outputs/x`; assert it is
     **absent** from the agent volume when that volume is re-listed through the `file` driver.
   - **`/workspace` is read-only in the kernel** (`docker_runsc.py:1247` `"mode": "ro"`). A write
     attempt inside the code-exec container fails with `EROFS`. Assert the errno, not just
     non-zero exit — a write that fails because the path was wrong would pass a weaker check.
   - **Cross-room absence.** Two session volumes, room A and room B. Content written under room
     A's mount is not present in a container mounting room B's. This is [R12.03b]'s guarantee
     stated as a test.

   **Do not test by absence alone** (Q-7): `kernel.py:132-133` creates `/session` with
   `parents=True` and `:137-138` suppresses a failed chdir, so an unbound mount looks like an
   empty directory rather than an error. Every assertion above is content- or errno-based.

4c. **`ci.yml` `sandbox-images`** - fresh-volume writability (AC-13). `docker volume create` a new
   volume, mount it at `/session`, and run the kernel's startup mkdir as the image's own user
   (no `--user` override - the `USER 10001` directive is part of what is under test). Assert it
   succeeds. Reuses the §6.3 `if: always()` volume cleanup. **This step must create its own
   volume**, not reuse one an earlier step already wrote to; ownership is the property under test
   and a warm volume already has it.

4b. **`ci.yml` `sandbox-images`** — protocol stamp (Q-8/AC-12): read `PROTOCOL_VERSION` out of the
   built code-exec image (`docker run --rm smap/code-exec:ci python -c "…"` importing
   `/opt/kernel/kernel.py`) and assert it equals `_KERNEL_PROTOCOL_VERSION` parsed from
   `docker_runsc.py:81`. This is the first check of that pin against a real image rather than
   against the source file the unit test already reads.

5. **Timeout** — `sandbox-images` is `timeout-minutes: 15` (`ci.yml:187`). Several `docker run`s are
   seconds each, but the two image builds dominate; raise it if the tier lands near the ceiling
   rather than letting CI flake.

**Reuse inventory:**
- `ci.yml:194-209` — the `docker run` + assert-stdout step shape, verbatim. Copy it; do not invent a
  harness.
- `ci.yml:155-167` — the log-dump-on-failure + `upload-artifact` pattern from `backend-wiring`.
- `preflight.sh:220-227` — already inspects for the two `:pinned` images; extend it to check the
  label and the operator side of FU-2 comes free.
- `compose.test.yml:170-182` — the `egress-proxy-smoke` shape, if a scripted-container test is
  preferred over inline `run:` steps.

## 7. NFR Checklist

- **CI time:** several `docker run`s of seconds each against images the job already built. The builds
  dominate and are unchanged. Q-1's job choice means zero extra image builds.
- **Cache:** the `LABEL`-last placement (§6.1) keeps the stamp out of the cache key for source edits.
- **Flake surface:** no network (`network_mode="none"` throughout), no compose, no Postgres, no
  Redis, no daemon-in-daemon. The scratch volume is the only shared state and §6.3 disposes of it.

## 8. Security Considerations

**This tier tests a security boundary, so its own assertions carry weight.**

- **`safe_workspace_path` gains its first executed test** (§6.3). It is the containment control for
  every `file` tool call — `cmd_file` passes `SMAP_FILE_PATH` straight into it (`driver.py:239`), and
  the value originates from an agent's tool arguments, i.e. from persona 3 (malicious content author)
  and reachable by prompt injection. It is currently exercised only as a pure function through
  `test_sandbox_driver_protocol.py`, which **skips itself whenever `deploy/` is absent** (`:25-29`) —
  including inside the wiring tier's container (`ci.yml:147`). A containment control whose test
  silently skips is a coverage finding in its own right.
- **AC-7 is now a security assertion, not a consistency one (Q-7).** [R12.03b] states the room
  boundary is "a property of the mount, which holds whatever code the Agent runs" — the guarantee
  exists precisely because `code_exec` executes arbitrary code and cannot be confined by a path
  check. Nothing currently executes a check of that mount property; `f7c4ea6` and `06798c9` are
  verified by unit tests over host-side config only. AC-7 is the first real-container evidence that
  the boundary those two dossiers assert actually holds. Its false-green mode is documented in Q-7
  and probed in §12.
- **The `runtime` field temptation (§5.2) must not be taken quietly.** `_assert_runsc` (`:364`) is
  not negotiable by design; making it configurable to enable a test inverts the control. If FU-1 ever
  needs it, it requires a security review and must be unreachable from settings.
- **The stamp is not a security control and must not be described as one.** It detects *drift*, not
  tampering: an attacker who can push an image can set the label. It answers "is this image built
  from these sources", which is a correctness question. Q-2's rejection of the runtime hook is partly
  this — a fail-open check reads as a control while being none.
- **No new secret, no new network path, no new endpoint.** The tier runs with `network_mode="none"`
  semantics, no egress secret (`_egress_env` returns `{}` on empty config, `:353-355`), and no
  supervisor (gate self-disables at `:293-294`).

## 9. Quality Notes

**Existing debt in the touched area — record, do not silently fix:**
- `test_sandbox_driver_protocol.py:25-29` skips itself when `deploy/` is missing. Defensible for a
  unit test that loads a file by path; corrosive as the only coverage of a containment control. This
  dossier does not change it — it makes the skip matter less by adding coverage that cannot skip.
- `ci.yml:177-184`'s header comment already states this dossier's whole design fork ("the driver
  itself is runtime-agnostic, so this validates the build + protocol under plain Docker; the gVisor
  (runsc) assertion is host-side and is exercised on the staging box, not here"). Follow it; it was
  right.
- The unused `egress-proxy-smoke` profile (`compose.test.yml:170-182`) is dead config. Not this
  dossier's to remove.

**Patterns to follow:** `ci.yml:194-209`'s assert-on-stdout shape; `backend-wiring`'s `if: always()`
teardown discipline (`:168-174`).

## 10. Risks and Rollback

- **A scratch volume leaks per run** if cleanup is skipped — §6.3's `if: always()` is the guard. On a
  hosted runner the VM is discarded, so this bites self-hosted runners first, i.e. exactly where it
  will not be noticed.
- **The stamp cries wolf if Q-6 is ignored** and a git SHA is used: the gate reddens on commits that
  did not touch `deploy/sandbox`, and a noisy gate gets disabled. The content hash is the design.
- **AC-4 asserts a bug.** Locking in `driver.py:245`'s flat behaviour is deliberate (Q-3) and will
  look wrong to a reviewer who does not read the AC's justification. **This risk's own contingency
  fired:** the workspace-path dossier landed first, so AC-4 and AC-5 were rewritten in their
  post-fix shape on 2026-07-20 before approval. The flatness survives (FU-3 is still open); only the
  entry shape changed.
- **A second, larger contingency also fired.** Two dossiers this spec never anticipated
  (`2026-07-19-session-dir-room-isolation`, `2026-07-19-workspace-readonly-in-kernel`) invalidated
  §4's shared-volume model outright. AC-7 was inverted rather than patched (Q-7). The lesson for
  whoever builds this: **this dossier sits downstream of an area under active change** — re-verify
  §4 against the tree before writing the first step, exactly as this revision did.
- **Rollback:** every piece is additive CI config plus two Dockerfile lines. Reverting the workflow
  steps restores the previous gate exactly; the `LABEL`/`ARG` are inert if nothing inspects them.
  No runtime code, no migration, no data.

## 11. Acceptance Criteria

- [x] AC-1: both images carry `smap.sandbox.stamp`, and `sandbox-images` fails when the label does
      not match the hash of `deploy/sandbox/**` — closing workspace-path FU-2.
- [x] AC-2: the stamp is a content hash of the build context, not a git SHA, and a commit touching
      nothing under `deploy/sandbox/` does **not** redden the gate (Q-6).
- [x] AC-3: `cmd_file` `write` → `read` round-trips in CI against the real image, on a named volume.
- [x] AC-4: `cmd_file` `list` is asserted at its **current** flat, single-level behaviour
      (`driver.py:245`) on a nested tree, in its post-`format_listing` shape — **absolute paths,
      one per line** (`protocol.py:103-113`), not bare names. Listing `/workspace` when
      `/workspace/sub/x.csv` exists yields `/workspace/sub` and not `/workspace/sub/x.csv`. Comment
      must name workspace-path FU-3 as the entry that flips the flatness. Asserting the bug is the
      point (Q-3): the fix needs a green baseline to invert.
- [x] AC-5: `cmd_file` `list` on a single file returns that file's **absolute path**
      (`driver.py:249` via `format_listing(dirname, [basename])`) — not `[basename]`, which is the
      pre-`2026-07-16-workspace-path-convention` behaviour.
- [x] AC-6: a traversal attempt (`/workspace/../etc/passwd`) is rejected by `safe_workspace_path`,
      executed in the image — the first non-skippable test of that control (§8).
- [x] AC-7: [R12.03b]'s mount isolation is asserted against real containers (§6.4, Q-7): `/session`
      content is absent from the agent volume; a write to `/workspace` in the code-exec container
      fails with `EROFS`; room A's session content is absent from a room-B container. Assertions
      are content- or errno-based, never `exists()`-based.
- [x] AC-8: the scratch volume is removed in an `if: always()` step; a run leaves no `docker volume ls`
      residue.
- [x] AC-9: `sandbox-images` stays green and inside its timeout (`ci.yml:187`), and remains in the
      required-gate list (`ci.yml:772`, `:817`).
- [x] AC-10: no change to `docker_runsc.py`, and in particular **no `runtime` field** on
      `DockerRunscSandbox` (§5.2, §8).
- [x] AC-11: nothing is added to the `vue/no-v-html` allowlist, no gVisor is provisioned, and no
      pytest marker is added — this tier is CI steps and two Dockerfile lines (§2).
- [x] AC-13: a **fresh** session volume is writable by the sandbox uid in a real container - mount
      a newly created named volume at `/session` and assert the kernel's own startup sequence
      (`_OUTPUTS`/`_INPUTS` `mkdir`, `kernel.py:132-133`) succeeds. Added 2026-07-20 after this
      exact defect shipped and broke `code_exec` in every chatroom
      (`2026-07-19-session-dir-room-isolation` D-10). **The volume must be created fresh in the
      step**, since a volume left over from an earlier assertion is already owned correctly and
      would pass while testing nothing - the same false-green shape Q-7 warns about. The cheap
      half of this cover already exists as `tests/unit/test_sandbox_image_mountpoints.py`, which
      pins the Dockerfile without a daemon; this AC is the half that pins the *behaviour*.
- [x] AC-12: the kernel protocol stamp is asserted against the **built image** (Q-8):
      `PROTOCOL_VERSION` read out of `smap/code-exec:ci` equals `_KERNEL_PROTOCOL_VERSION`
      (`docker_runsc.py:81`). A deliberate mismatch is seen red before the AC is checked off.

## 12. Test Plan

The deliverable *is* tests, so this section is about how they are proven to work rather than what
they assert.

- **Probe every assertion by breaking it.** AC-3: corrupt the payload and confirm red. AC-4: point
  `list` at a nested tree and confirm the flat output is what actually comes back — if it is not, the
  FU-3 premise is wrong and that is a finding, not a test failure. AC-6: temporarily bypass
  `safe_workspace_path` and confirm red; a traversal test that passes because the path never resolved
  is worthless. A tier whose assertions have never been seen red is not a tier.
- **AC-7's negative cases are the ones that matter**, because Q-7's constraint makes a false green
  easy: `/session` auto-creates. Run each assertion with the mount **deliberately omitted** and
  confirm it still goes red. If omitting the `/session` bind leaves the test green, the test is
  reading a directory on the container layer and proving nothing. Likewise, drop the `ro` flag and
  confirm the `EROFS` assertion reddens.
- **AC-12's negative case**: bump `PROTOCOL_VERSION` in a scratch build and confirm the step fails.
- **AC-1's negative case must be exercised**: build with a mismatched `--build-arg` and confirm the
  step fails. A drift check that has only ever been seen green is the failure mode FU-2 is about.
- **AC-2's negative case**: a commit touching only `backend/` must not redden the gate.
- **Existing unit tests stay green** — `test_sandbox_driver_protocol.py`, `test_code_exec_kernel.py`,
  `test_sandbox_readiness_gate.py`, `test_sandbox_runtime_assertion.py` are untouched.

## 13. SRS Delta

**None** — reconfirmed 2026-07-20 after the AC-7 rewrite. `[R12.03b]` already states the mount
isolation property verbatim, including the read-only agent volume and the per-`(agent, chatroom)`
session volume, so AC-7 asserts an existing requirement rather than introducing one. AC-12's
protocol stamp is a build-coupling mechanism, not a product constraint, exactly like the §6.1 stamp.

`[R24.15]` (CI gates) and `[R12.03]` (sandbox isolation) already govern this; the tier adds
coverage of behaviour they already require, and modifies neither. No new platform capability, no new
user-visible surface, no new constraint on the product — the stamp is a build-hygiene mechanism, not
a requirement. If §16 FU-1's gVisor tier ever lands it may need one, because provisioning runsc in CI
is a statement about where the isolation guarantee is verified; that is FU-1's problem.

## 14. Open Questions

**Both closed 2026-07-20. None remain open.**

- **OQ-1 — CLOSED: both images.** `mcp-runtime/Dockerfile:44` copies in the driver only;
  `code-exec/Dockerfile:34`/`:42` copies in **both** the driver and the kernel. But image contents
  are not what decides it — reachability is. The `file` root is only exercised through
  mcp-runtime's ENTRYPOINT (`mcp-runtime/Dockerfile:62`; host `docker_runsc.py:911-912`, `:927-928`),
  while the kernel is only launched from code-exec (`docker_runsc.py:1256-1257`). code-exec carries
  the driver "only for the optional `exec` subcommand / parity" (`code-exec/Dockerfile:5-6`), so it
  cannot stand in. §6.4 is therefore a step per image over shared volumes.
- **OQ-2 — CLOSED: stable, via the git index.** Use
  `git ls-files -s deploy/sandbox | git hash-object --stdin`. Stability holds for three reasons,
  each verified: (a) `git ls-files -s` emits mode, **blob SHA**, stage and path in a deterministic
  sort, and the blob SHA comes from the index, so it is content-addressed and independent of the
  working tree's line endings; (b) `.gitattributes:2` sets `* text=auto eol=lf` globally, and the
  only `eol=crlf` exceptions are `*.bat`/`*.ps1` (`:24-25`), **neither of which exists under
  `deploy/sandbox`** — the tracked set is 7 files (2 Dockerfiles, 4 `.py`, 1 `.dockerignore`);
  (c) every CI job runs `ubuntu-latest` and no workflow sets `core.autocrlf`. Verified on a Windows
  working tree: `d1d7b201e4d34ba4f835af5ad73ca34eb5c7f93c`. A Linux runner on the same commit
  produces the same value because the input is the index, not the checkout.

## 15. Deviation Log

- **D-1 — OQ-2's quoted stamp value is wrong, and its stated reason for stability is not the
  whole reason.** OQ-2 records `d1d7b201e4d34ba4f835af5ad73ca34eb5c7f93c` as "verified on a Windows
  working tree". That value was produced through a **PowerShell** pipeline, which re-encodes the
  byte stream between `git ls-files -s` and `git hash-object`. Run through `bash` on the same
  index, the command yields `7cd968a3394e7f107bf4b844070e53a11608f9e5` — reproducibly, twice.
  The design is unaffected and OQ-2's conclusion still holds (CI computes and compares within one
  bash run on `ubuntu-latest`), but the stability argument needs a fourth clause: the value is
  stable given a *byte-exact pipe*, which is a property of the shell, not only of the index.
  Anyone re-deriving the number on Windows to check a CI failure would otherwise chase a ghost.
- **D-2 — §6.4's AC-7 steps, written as specified, would have passed with the `/session` mount
  removed.** The spec's Q-7 warns that `/session` auto-creates and instructs "content- or
  errno-based, never `exists()`-based", which the first implementation followed. It was still not
  enough: asserting only "room A's marker is absent from the agent volume" stays green when nothing
  is mounted at all, because the kernel then writes to the container layer and the marker is just as
  absent, for the wrong reason. §12's "run each assertion with the mount deliberately omitted"
  probe caught it. Each AC-7 step now also reads its marker back **from the session volume, in a
  different container than the one that wrote it**; omitting the bind reddens that read. The
  positive-marker rule in the step comments is the generalised form.
- **D-3 — timeout raised 15 → 20 minutes.** §6.5 authorises this ("raise it if the tier lands near
  the ceiling"). Raised pre-emptively rather than after a flake: the added steps are ~14 short
  container runs on top of two builds that already dominated the budget, and a timeout flake in a
  required gate costs more than five minutes of headroom.
- **D-4 — the stamp step gained an empty-set guard not called for in §6.2.** `git ls-files` on a
  path matching nothing exits 0 with no output, and `git hash-object --stdin` then returns the
  empty-blob SHA `e69de29b…` — a plausible, non-empty, permanently constant stamp. Verified by
  running it. Were `deploy/sandbox` ever renamed, the gate would stay green while detecting
  nothing, which is precisely FU-2's failure mode reappearing inside FU-2's own fix. The step now
  fails if no tracked files are found.
- **D-5 — the guest-side containers run `--network none`.** §6 does not specify it; §8 describes
  the tier as having "no new network path" and §7 attributes its low flake surface to
  `network_mode="none"`. Neither was true of the implementation, which used Docker's default
  bridge. Added for production fidelity (the containers under test now match how the host runs
  them) rather than to close an attack path — the workflow triggers on `pull_request`, not
  `pull_request_target`, and references no `secrets.*`, so there was nothing on the runner to
  reach. The two pre-existing smoke steps were left untouched.
- **D-6 — this task's diff is split across commits it does not own.** While implementation was in
  progress, the working tree's pre-existing changes (the two 07-19 dossiers' close-out) were
  committed by the user, sweeping in this task's `deploy/sandbox/code-exec/Dockerfile` stamp lines
  (into `f6f0fb6`, whose subject is "create /session in the code-exec image") and its `status:
  in-progress` / `BOARD.md` move (into `6dac997`). History was left as-is rather than rewritten.
  Consequence for a future reader: `f6f0fb6` contains two unrelated changes, and the stamp lines'
  rationale lives here, not in that commit's message.

## 16. Follow-ups

- **FU-1: the gVisor tier — `code-execution`'s FU-4, split out per Q-4, and its first AC is a
  throwaway prototype.** Nothing in the repo provisions gVisor: every reference delegates to upstream
  (`deploy/README.md:143-160`, `docker-compose.yml:358-360`), `preflight.sh:207-217` only warns, and
  `services/mcp_supervisor/main.py:49-79` merely 503s when `runsc` is absent from
  `docker info --format '{{json .Runtimes}}'`. Whether `ubuntu-latest` can run it is **unsettled from
  the repo**, and `ci.yml:182-184` implies the authors assumed not. Mechanically it is plausible —
  gVisor's default `systrap` platform needs no KVM or nested virt, and the hosted runner has
  passwordless sudo and a local daemon, so `apt-get install runsc` + `/etc/docker/daemon.json` +
  `systemctl restart docker` is expressible. **Settle it with one throwaway branch running that
  install plus `docker run --runtime=runsc alpine true` before specing anything.** If it fails, the
  only remaining roads are a self-hosted runner or §5.2's `runtime` field, and that field needs a
  security review (§8).
  **A concrete first customer, added 2026-07-20:** `2026-07-19-session-dir-room-isolation` FU-10
  fixed the headless scratch mount by passing `uid`/`gid` tmpfs options, verified under runc only.
  tmpfs options are honoured by the runtime, and gVisor implements its own tmpfs in the Sentry, so
  whether that fix takes effect in production is unconfirmed, with both failure modes silent
  (ignored options leave the defect behind a green test; rejected options break every `code_exec`).
  Whatever shape this FU takes, `docker run --runtime=runsc --tmpfs /w:size=1m,uid=10001` plus a
  `stat` is a two-line addition that settles it.
- **FU-2: a runtime stamp check, if still wanted after AC-1.** Q-2 rejects the `_ensure_runtime_ready`
  hook on posture/process/cache grounds. A one-shot at **worker startup** — log-and-warn, never raise
  — has none of those problems and answers "is this deployment running the image it thinks it is".
  Worth its own small dossier; probably worth less than it sounds once CI catches drift on every PR.
- **FU-3: the host-side surfaces this tier cannot reach.** All gated on FU-1: the `put_archive`
  staging round trip (`_tar_single_file` `:81-97` and `_tar_staged_inputs` `:106-139` are pure and
  unit-testable, but the tar → `put_archive` → `os.replace` (`driver.py:258-267`) round trip needs a
  real container, and the two-step create → stage → start dance at `:611-630` is verified nowhere —
  uid ownership in particular, `_SANDBOX_UID` 10001 in the tar `:95` against `useradd --uid 10001` in
  both Dockerfiles, is exactly what breaks silently); the kernel registry lifecycle (`:875-907`,
  `:939-949`, `:1103-1119`, `:1060-1100`); and the egress proxy path, which needs the gateway-less
  `egress_net` (`docker-compose.yml:23-34`) and a signed HMAC from `_egress_env` (`:350-361`).
- **FU-4: `cmd_exec` (`driver.py:274`) is dead code on the default path.** Never exercised anywhere,
  by anything. Either it has a caller nobody has documented, or it should go. Worth ten minutes of
  someone's grep before it accretes a test that legitimises it.
- **FU-5: any pytest-based tier needs an autouse reset fixture for the module globals** (§4). Not
  needed by this dossier — CI steps spawn fresh processes — but it is the first thing FU-1 will trip
  over, and `_WORKSPACE_MANIFESTS` (`:1029-1032`) will make a test pass while testing nothing.
- **FU-6: `egress-proxy-smoke` (`compose.test.yml:170-182`) is wired to no CI job.** A
  container-test shape designed in this repo and never used. Either adopt it (§6's steps could live
  there instead of inline `run:` blocks) or delete it.
- **FU-7: the operator half of FU-2 is still open.** §6's reuse inventory notes that
  `deploy/scripts/preflight.sh:220-227` already inspects for the two `:pinned` images and that
  extending it to read `smap.sandbox.stamp` would "come free". It was left out deliberately: AC-11
  bounds this task to CI steps plus two Dockerfile lines, and no AC covers preflight. The label now
  exists, so the extension is a few lines whenever someone wants the deployed-image drift answer
  rather than the per-PR one. Note the honest limit first — an operator comparing the label against
  a hash of their checkout only learns whether the *sources* match, and the stamp is drift
  detection, not tamper detection (§8).
- **FU-8: the inline kernel-import preamble is repeated three times in `ci.yml`.** Four lines
  (`spec_from_file_location` → `exec_module`) in the room-seeding, AC-13, and AC-12 steps. Tolerable
  at three copies and not worth a shared file today; if a fourth appears, that is the signal to
  adopt FU-6's scripted-container shape rather than keep copying.
- **FU-10 (pre-existing, found while running the job): `Record image digests` has never recorded
  anything, and is green.** The step runs
  `docker images --no-trunc --format '…' smap/mcp-runtime:ci smap/code-exec:ci`, but `docker images`
  accepts **at most one** `[REPOSITORY[:TAG]]` argument and exits 1 on two — verified against
  Docker 28.4.0: two args gives "requires at most 1 argument", one arg prints the digest. The
  failure is masked because the command is piped into `tee` and the step sets no `pipefail`, so the
  step's exit status is `tee`'s. `sandbox-image-digests.txt` is therefore uploaded empty on every
  run, and the job header's stated purpose — "Digests are recorded as a build artifact so the
  `SANDBOX_*_IMAGE` pins can be updated from a reviewed build" — has never been served. Not fixed
  here: it predates this dossier and blocks no AC, and AC-11 bounds this task to the tier plus the
  stamp. Fix is one loop or two invocations, plus `set -o pipefail` so the next such breakage is
  not silent. Worth noting that this is exactly the failure class the dossier was written about,
  found in the job it was written for.
- **FU-9: `sandbox-images` now runs ~14 containers whose failure output is only what the step
  echoes.** §6's reuse inventory suggested `backend-wiring`'s log-dump-on-failure +
  `upload-artifact` pattern (`ci.yml:155-167`). Not adopted: every step here captures container
  stdout/stderr into the step log already, and there is no compose stack whose logs live elsewhere.
  Revisit if a failure ever proves undiagnosable from the step log alone.
