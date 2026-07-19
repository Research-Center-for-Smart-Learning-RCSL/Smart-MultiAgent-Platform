---
type: bugfix
status: in-progress
created: 2026-07-19
requirements: [R12.03, R12.05, R13.10]
---

# A `code_exec` artifact over 8 MiB is silently destroyed

## 1. Summary

An agent that writes a file larger than 8 MiB to `outputs/` loses it. The kernel emits a
descriptor with `b64: None` (`kernel.py:91-93`), the host skips any descriptor without `b64`
(`turn_engine.py:1133-1136`), and **nothing anywhere records that it happened** - no log, no audit
event, no metric, no signal to the model, no signal to the user. The agent believes it produced a
chart or a dataset; the user sees a reply without it; the platform retains nothing to explain the
gap. The in-code justification is worse than absent: `kernel.py:52-53` tells the next reader that
oversized artifacts "are read from the volume host-side", and that code has never existed.

Recorded as FU-4 of `docs/tasks/2026-07-19-session-dir-room-isolation/`.

## 2. Observed vs Expected

- **Observed.**
  - `_ARTIFACT_B64_CAP = 8 * 1024 * 1024` (`kernel.py:54`). `_collect_artifacts` sets `b64` only
    when `size <= _ARTIFACT_B64_CAP` (`:91-93`) but appends the descriptor unconditionally
    (`:94-102`), so an oversized artifact arrives with `filename`, `mime`, `size_bytes` and
    `rel_path` populated and `b64: None`.
  - `_persist_artifacts` drops it: `if not b64: continue` (`turn_engine.py:1133-1136`). The
    adjacent undecodable-b64 branch logs at debug (`:1139-1141`); this one logs nothing.
  - The return value of `_persist_artifacts` is discarded at its only call site
    (`turn_engine.py:1843`), so nothing compares "produced" against "persisted".
  - The audit event `attachment.agent_artifact` fires per *successful* upload only
    (`attachment_service.py:374-388`), so a drop leaves no audit trace.
  - **The model is told nothing, ever.** The artifact list goes to `artifact_sink`
    (`builtin_tools.py:185-188`) and nowhere else; the `ToolResult` carries only stdout/stderr
    (`:189-195`). The model receives no artifact names, no sizes, no success signal, and no failure
    signal - for *any* artifact, not just dropped ones.
  - **The description promises otherwise, unconditionally:** *"anything you save to `outputs/` is
    returned as an artifact"* (`builtin_tools.py:203-209`), with no size caveat.
  - **The comment justifying the cap is false.** `kernel.py:52-53` says larger artifacts "are read
    from the volume host-side". `get_archive` has zero occurrences in `backend/`; every Docker
    archive call is `put_archive` (`docker_runsc.py:871`, `:1296`, `:1366-1367`, `:1423`). The
    `continue` at `turn_engine.py:1136`, labelled "skipped in v1", is the artifact's terminal fate,
    not a deferral to another path.
- **Expected** (Q-1; no intent source exists - the SRS does not mention artifacts at all, §11). An
  artifact the agent produces is either returned to the room, or its loss is explicit to the model,
  the user, and the operator. Silent destruction of user-visible work is not an acceptable outcome
  at any size.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | How far to fix: (a) surface the drop but keep losing the file; (b) raise `_ARTIFACT_B64_CAP`; (c) implement the host-side read the comment already promises; (d) (c) plus a hard ceiling above which (a) applies? | **(d).** | (b) is bounded by memory and cannot reach the platform's real limit: the kernel container is capped at 512 MB (`docker_runsc.py:85`, `:634`), and an inlined artifact is resident simultaneously as raw bytes, as base64 (~1.33x), inside the serialised JSON, in the messenger buffer, in the host's `exec_out`, in the decoded string and in the parsed dict - roughly 6-8x across two processes, and `_collect_artifacts` inlines *every* qualifying file into one reply (`kernel.py:84-102`), so the amplification is per-reply, not per-file. Raising the cap to the 32 MB the platform accepts would OOM the kernel. (a) alone institutionalises the data loss. (c) alone still needs a ceiling, because MinIO will accept far more than is sensible to move through a tool round. (d) returns what can be returned and is honest about the rest. |
| Q-2 | What is the ceiling? | **32 MB**, matching the single-shot attachment limit (`attachments.py:76-81`). | A user can upload 32 MB in one shot, so an agent producing 32 MB is within what the platform already treats as a normal object. Above it the user is directed to TUS (1 GiB, `attachment_service.py:52`), which is a resumable *upload* path with no agent-side analogue - inventing one for artifacts is a feature, not this fix. Using the same number means one limit to explain rather than two. |
| Q-3 | Should the model be told which artifacts were returned? | **Yes - a summary line in the tool result.** | Today it is told nothing even on success, so it cannot confirm its own output landed, and the description's unconditional promise is the only thing it has to go on. A model that knows a file was too large can react (downsample, split, write a summary instead); a model that is told nothing will confabulate that the chart was delivered. This is the same argument as `2026-07-16-workspace-path-convention`: the tool result is the only contract the model reads. |
| Q-4 | Should the user see it? | **Yes - the drop is logged at warning and audited.** Not surfaced in the room. | An operator needs to know an artifact was lost, and the audit trail is where a "where did my file go" support question is answered. Injecting a platform message into the conversation is a product decision about room content, which this dossier should not make unilaterally; the model's summary (Q-3) already gives it the material to tell the user itself. |
| Q-5 | Data repair? | **None possible.** | Dropped artifacts were never persisted anywhere. The bytes remain on the session volume under `/session/outputs/` for as long as that volume lives, so a specific past artifact could in principle be recovered by hand, but there is no record of which ones were dropped - that is precisely the defect. |

## 4. Reproduction

Deterministic. Preconditions: an agent with `HOSTED_CODE_INTERPRETER` in a chatroom.

1. `code_exec(source="open('outputs/big.bin','wb').write(b'x' * (9 * 1024 * 1024))")` → reports
   success; stdout shows the byte count.
2. The turn completes. The reply carries **no attachment**, and the model is given no indication.
3. Backend logs contain nothing about `big.bin`. `attachment.agent_artifact` is not emitted.
4. `code_exec(source="import os; os.path.getsize('outputs/big.bin')")` → `9437184`. The file is
   there; it simply never left the sandbox.
5. Repeat with 7 MiB → the artifact appears in the room. The boundary is invisible to everyone
   except whoever reads `kernel.py:54`.

Not reproducible in CI - no Docker/gVisor tier. §8 covers the kernel helper, the host skip path and
the new transport seam as unit tests; §4 is `/verify` on the Linux staging host.

## 5. Root Cause Analysis

1. The exec reply is a single JSON document over a length-framed socket (`kernel.py:187`,
   `client.py:59-61`), read by the host with `exec_run(demux=True)` into memory
   (`docker_runsc.py:1076-1085`). Bounding what goes into it is correct - an unbounded reply is an
   OOM in a 512 MB container.
2. So the kernel caps inlining at 8 MiB (`kernel.py:54`) and emits a `b64`-less descriptor above it
   (`:91-93`). Correct in isolation: the descriptor carries `rel_path` and `size_bytes`, which is
   exactly what a second retrieval path would need.
3. **The second retrieval path was never built.** `kernel.py:52-53` describes it as though it
   exists. `turn_engine.py:1135` calls the gap "v1" as though something would follow.
4. Nothing closes the loop: `_persist_artifacts` discards its count at the call site
   (`turn_engine.py:1843`), and no code compares descriptors produced against artifacts persisted.

**Root cause: step 3** - a designed two-tier transport whose second tier was never implemented,
while both the code comment and the tool description continued to describe the completed design.
Step 4 is the aggravating factor that turned an unimplemented feature into an *invisible* one: had
anything counted descriptors against uploads, this would have surfaced as a metric the first time
an agent produced a large file.

This is not "the cap is too low" (Q-1(b) shows it cannot be raised far) and not "the kernel should
inline everything" (step 1 is sound).

## 6. Blast Radius and Sibling Suspects

**Blast radius.** Every agent with `code_exec`, every tenant, since the feature shipped. Scales
with the usefulness of the agent: data analysis, image generation and report building are exactly
the tasks that produce large files, so the loss concentrates on the highest-value use cases. No
corruption, no cross-tenant exposure - the failure is loss and silence.

**Sibling suspects.**

- **Matplotlib auto-capture** (`kernel.py:112-118`) → **confirmed, same path.** Figures are saved
  into `_OUTPUTS` and collected by the same `_collect_artifacts`, so a large figure is dropped
  identically. Fixed by the same change; no separate work.
- **The undecodable-b64 branch** (`turn_engine.py:1139-1141`) → **cleared but adjacent.** It logs at
  debug, so a corrupt payload is at least traceable. Worth raising to warning alongside this fix so
  both loss paths have the same visibility.
- **`_persist_artifacts`' whole-batch failure** (`turn_engine.py:1167-1171`) → **cleared.** Logs at
  warning with `exc_info`, and returns 0.
- **Dedup on `rel_path`** (`turn_engine.py:1124-1132`) → **cleared.** The blank-key hazard is
  already handled and commented.
- **Attachment staging into `inputs/`** → **cleared.** Bounded by `_MAX_STAGED_BYTES` before
  staging (`turn_engine.py:865-869`), and an attachment too large to stage is reported to the model
  as unstaged rather than silently omitted - which is the pattern this fix should follow on the way
  out.
- **The `file` tool's 10 MB write cap** (`file_tool.py:27`) → **cleared.** It raises on exceeding,
  so the caller learns.

## 7. Fix Design

1. **Build the second tier.** After a reply arrives, any descriptor with `b64: None` and
   `size_bytes <= 32 MB` is fetched host-side from the live kernel container with
   `container.get_archive(rel_path)`, which streams a tar through the Docker API rather than
   inflating the kernel's 512 MB budget. The single-member tar is unpacked in the host process and
   handed to the existing `upload_agent_artifact` path unchanged.
2. **Retire the false comment.** `kernel.py:52-53` is rewritten to describe what the cap is for
   (bounding the exec reply) and to point at the host-side retrieval as the actual second tier.
3. **Ceiling and honesty.** A descriptor above 32 MB (Q-2), or one whose `get_archive` fails - the
   kernel may have been evicted or reaped between the exec and the fetch (`docker_runsc.py`'s LRU
   eviction and 900 s idle reaper) - is **reported, not swallowed**: a warning log naming the file
   and its size, and an entry in the tool result the model reads (Q-3).
4. **Tell the model what it produced.** The `code_exec` `ToolResult` gains a short artifact summary:
   which files were returned, and which were not with why. The description
   (`builtin_tools.py:203-209`) is corrected to state the limit rather than promise unconditionally.
5. **Close the loop.** `_persist_artifacts`' count is compared against the descriptors it was given,
   and a shortfall is logged at warning. This is what would have made the original defect visible.

Why this corrects the root cause rather than masking it: step 3 of §5 is a missing implementation,
and steps 1-2 supply it. Steps 3-5 ensure that whatever *still* cannot be delivered leaves a trace
in all three places a human might look - the log, the model's context, and the operator's metrics -
so the next gap of this shape cannot be silent.

**Data repair:** none possible (Q-5).

## 8. Regression Test Plan

- **T-1 (fails now).** `test_code_exec_kernel.py`: a file above `_ARTIFACT_B64_CAP` yields a
  descriptor with `b64 is None` and a correct `size_bytes`/`rel_path`. Red today only in the sense
  that no test asserts it at all - the oversized branch (`kernel.py:91-93`) has never been
  exercised, which is why nothing caught the missing second tier.
- **T-2 (fails now).** `test_turn_artifacts.py`: given a `b64: None` descriptor within the ceiling,
  the host fetches it and persists it. Red today - the fetch does not exist and the descriptor is
  skipped.
- **T-3 (fails now).** A descriptor above the ceiling is **not** persisted and **is** logged at
  warning with its filename and size. Red today: the skip is silent.
- **T-4.** A `get_archive` failure (kernel evicted mid-turn) degrades to the same reported-not-
  swallowed path as T-3, not to an exception that fails the turn. The artifact is one output of a
  turn whose text response may still be valuable.
- **T-5.** The model-visible summary lists returned and unreturned artifacts (Q-3), and the
  description states the limit (asserted like the other description tests in
  `test_builtin_tools_wiring.py`).
- **T-6.** `_persist_artifacts` logs a warning when persisted count < descriptors given (§7.5).
- **T-7.** The existing small-artifact path is unchanged: inline b64 still round-trips
  (`test_code_exec_kernel.py:137-155` passes unmodified).

End-to-end (`/verify`, Linux staging): the §4 reproduction, expecting step 2 to deliver `big.bin`
to the room and a >32 MB variant to produce a warning and a model-visible note.

## 9. Risks and Rollback

- **Host memory.** The fetch buffers one artifact (≤32 MB) in the worker process. Concurrent turns
  multiply that; the sandbox semaphore already bounds concurrency, but the ceiling should be
  reviewed against worker sizing before raising it further.
- **A new Docker API call on the turn path.** `get_archive` against a live kernel adds a round trip
  per oversized artifact. Only oversized ones pay it; the common case is untouched.
- **Kernel lifetime.** The fetch must happen while the container lives. Eviction
  (`_evict_if_full`) or the 900 s idle reaper between exec and fetch loses the artifact - hence T-4.
  Fetching immediately after the exec reply, inside the same held handle lock, minimises the window.
- **The model sees new text.** The artifact summary consumes tool-result budget against
  `clip_tool_output`'s 16 000 characters (`tool_registry.py:38`). Keep it to one line per artifact.
- **Rollback.** Revert; the kernel change is comment-only, so no image rebuild is strictly required
  for the backend half - but §7.2 touches `kernel.py`, so a rebuild is needed to keep the comment
  honest. No migration, no schema change, no API contract change.

## 10. Acceptance Criteria

- [x] AC-1: T-1 through T-6 fail against current code where marked and pass after the fix. **With
      one honest correction:** T-1 passed on first run. The kernel's oversized branch was already
      correct - it emits a complete descriptor with `b64: None` - so the defect was purely the
      missing host tier. The test is still worth having (the branch had no coverage at all, which is
      why nothing noticed the tier was absent), but it was never red. The genuinely red one is
      `test_persist_artifacts_dedupes_skips_and_binds`, which **asserted the drop as correct**
      (`count == 1`) and now asserts retrieval (`count == 2`).
- [ ] AC-2: An artifact between 8 MiB and 32 MB is delivered to the room. **Outstanding - needs
      `/verify`**, since only a live container exercises `get_archive`.
- [x] AC-3: An artifact above 32 MB is **not** silently dropped - warning log naming file and size,
      plus a model-visible note. (`test_an_artifact_above_the_ceiling_is_never_fetched`,
      `test_artifact_note_names_what_came_back_and_what_did_not`.)
- [x] AC-4: A fetch failure does not fail the turn
      (`test_a_failed_fetch_does_not_cost_the_other_artifacts` - the other artifacts still land).
- [x] AC-5: Artifacts at or below 8 MiB still travel inline, unchanged (T-7:
      `test_new_output_files_become_artifacts` passes unmodified).
- [x] AC-6: `code_exec`'s description states the 32 MB limit and that the result names what came
      back (`test_code_exec_description_states_the_artifact_limit`).
- [x] AC-7: `kernel.py`'s comment describes the real mechanism and records that the claim it
      replaced was false.
- [x] AC-8: Undelivered artifacts are logged at warning with count and names
      (`test_an_unfetchable_artifact_is_reported_not_swallowed`). The sibling undecodable-b64 path
      was raised from debug to warning at the same time (§6).

## 11. SRS Delta

**None, but the silence is itself notable.** `REQUIREMENTS.md` does not mention `code_exec`
artifacts at all - not their capture, naming, lifetime, size, nor their delivery into the room. The
term `outputs/` appears nowhere. So there is no documented behaviour to restore, and §2's "Expected"
rests on Q-1 rather than on an SRS clause.

Writing an artifact requirement is worth doing, but not here: it would have to settle artifact
retention (do they follow [R13.10]'s 3-day attachment lifecycle?), quota accounting, and the
relationship to TUS - none of which this defect requires an answer to. Recorded as FU-1 rather than
drafted speculatively.

## 12. Deviation Log

- **D-1.** §7.5 proposed comparing `_persist_artifacts`' returned count against the descriptors it
  was given, and logging a shortfall. Implemented as an explicit `dropped` list built where each
  loss occurs, rather than a count subtraction at the end. The subtraction would have said only
  *how many* went missing; the list names them and their sizes, which is what an operator answering
  "where did my file go" actually needs. It also correctly ignores dedup, which a naive
  produced-minus-persisted comparison would have miscounted as loss.
- **D-2.** §7 did not mention the undecodable-b64 branch; §6 had cleared it as "adjacent". It was
  raised from `debug` to `warning` here anyway - it is the same class of silent loss, and leaving
  one of the two paths quiet would have preserved exactly the asymmetry that hid this defect.
- **D-3.** `_single_member_tar_bytes` bounds the read twice: the caller checks the tar header size
  and the extractor reads one byte past the ceiling to detect an understated header. Not in §7,
  which treated the size check as a single gate. The stream describes a path the agent's own code
  controls, so trusting the header alone would let a grown file be silently truncated into a
  corrupt artifact - worse than the loss this task is fixing.

**Build state (2026-07-19).** Gates: `pytest tests/unit` 5370 passed / 6 skipped, `ruff check`,
`ruff format --check`, `mypy .` (792 files) clean. Integration and wiring tiers not run (no local
Postgres/Redis) and untouched.

All ACs met except **AC-2**, which needs `/verify` on Linux + gVisor - only a live container
exercises `get_archive`. This joins the four other verification items blocked on the same tier.

**Deploy note.** `kernel.py` changes are comment-only, so the backend half works against the
current image; the image rebuild only keeps the comment honest. No lockstep requirement.

## 13. Follow-ups

- **FU-1: the SRS says nothing about `code_exec` artifacts.** No requirement covers capture,
  naming, size, lifetime, quota, or room delivery (§11). This fix therefore encodes behaviour with
  no intent source, which is how the original gap survived. A requirement should be drafted once the
  retention question is settled. Type: `docs`/SRS.
- **FU-2: artifacts have no retention policy.** Chat attachments follow [R13.10]
  (`REQUIREMENTS.md:664`, 3-day lifecycle) and messages a 5-year horizon
  (`retention_service.py:27`). An agent artifact is uploaded via `upload_agent_artifact` and bound
  to a message, so it presumably inherits the message's - but nothing states it, and nothing tested
  it. Type: `bugfix` or `docs` depending on what is found.
- **FU-3: `_OUTPUTS` is never cleaned.** `_collect_artifacts` diffs on mtime (`kernel.py:88-89`), so
  a file untouched by a later call is simply not re-reported - but it stays on the session volume
  forever, against a volume with no enforced quota (`2026-07-19-session-dir-room-isolation` FU-1).
  A long-lived room accumulates every artifact it ever produced. Type: `bugfix`.
