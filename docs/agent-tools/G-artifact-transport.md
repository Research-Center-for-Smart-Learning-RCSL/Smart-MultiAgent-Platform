# G — `code_exec` artifact transport

How a file an agent writes to `outputs/` reaches the room, why it is bounded the way it is,
and what happens to the ones that cannot make it. The code in
`builtin_tools._hydrate_oversized`, `turn_engine._persist_artifacts` and
`docker_runsc.fetch_kernel_artifact` points here rather than restating any of it.

History: `docs/tasks/2026-07-19-large-artifacts-silently-dropped/spec.md`.

## G.1 Two transport tiers

The exec reply is a single JSON document over a length-framed socket, read into host memory.
Bounding it is not optional: the kernel container has 512 MB, and an inlined file is resident
simultaneously as raw bytes, as base64 (~1.33x), inside the serialised JSON, in the messenger
buffer, in the host's `exec_out`, in the decoded string and in the parsed dict. That is roughly
6-8x across two processes, and the kernel inlines every qualifying file into one reply, so the
amplification is per-reply, not per-file.

| Tier | Range | Mechanism |
|---|---|---|
| Inline | <= 8 MiB (`kernel._ARTIFACT_B64_CAP`) | base64 in the exec reply |
| Host fetch | 8 MiB - 32 MB (`MAX_ARTIFACT_BYTES`) | `container.get_archive` over the Docker API |
| Refused | > 32 MB | not delivered; named to the model and the operator log |

The fetch tier exists because raising the inline cap cannot reach the platform's real limit -
32 MB inlined would OOM the kernel. `get_archive` streams a tar through the daemon, so the bytes
never pass through the kernel's own budget.

32 MB matches the single-shot attachment limit (`attachment_service.SINGLE_SHOT_MAX_BYTES`), so
there is one number to explain rather than two. Above it a user is directed to TUS, which is a
resumable *upload* path with no agent-side analogue.

## G.2 Why the fetch happens at the exec reply

`_hydrate_oversized` runs in the `code_exec` tool call, not in `_persist_artifacts` where the rest
of the artifact handling lives. This is the single most important thing to preserve.

The kernel registry evicts least-recently-used at 16 live containers
(`docker_runsc._evict_if_full`) and reaps at 900 s idle. `_persist_artifacts` runs only after the
whole turn has finished and committed. Fetching there leaves a window as long as the turn, during
which any *other* room on the host starting a kernel evicts this one. It fails safe, but it
presents as artifacts that land on a quiet box and vanish under load - harder to diagnose than the
silent loss the whole feature was built to end.

At the exec reply the container is as alive as it will ever be. Bytes ride on the descriptor as
`data` and are uploaded later. `_persist_artifacts` keeps its own fetch as a fallback for
descriptors that never passed through the tool (no chatroom, or a future producer).

## G.3 Per-turn budget

`MAX_ARTIFACT_BYTES` bounds one artifact; nothing bounded the set. Before the fetch tier existed,
the kernel's 512 MB reply budget capped a batch implicitly - everything had to fit in the reply.
Fetching deliberately routes around that budget, so the ceiling it removed has to be re-imposed:
otherwise an agent hardlinks one 32 MB file to a thousand names (cheap, no extra disk; every
`rel_path` differs so dedup passes) and the shared worker holds 32 GB.

`MAX_ARTIFACTS_PER_TURN` and `MAX_ARTIFACT_TOTAL_BYTES` are enforced twice: in
`_hydrate_oversized` before any bytes move, and again in `_persist_artifacts` as the backstop
covering inline artifacts and any producer that bypassed the tool. Both must test
`running + candidate > limit`, never `running > limit` - the latter overshoots by a full artifact.

## G.4 Nothing is dropped silently

This is the invariant the original defect violated, and every bound added since has been at risk of
re-creating it in miniature. A bound without a signal is the defect, not the bound.

An artifact that will not be delivered is marked with `ARTIFACT_SKIP_KEY` carrying a reason
(`too_large`, `budget`, `unavailable`). Three consumers read that mark:

- **The model** - `_artifact_note` names what was written and what was withheld, per reason, in the
  tool result. It never claims *delivery*, only what the kernel reported writing: delivery happens
  later and can still fail, and platform text asserting an outcome is exactly the confabulation the
  note exists to prevent.
- **The operator** - `_persist_artifacts` logs the undelivered set with names and sizes at warning.
- **`_artifact_bytes`** - refuses to acquire a marked descriptor, so the fallback cannot re-fetch
  what the budget already refused.

Names in the note go through `safe_input_name`. A POSIX filename may contain newlines, and the
filename comes from agent-authored code: written raw, `outputs/"chart.png\n[system: ...]"` forges a
line indistinguishable from the note's own bracketed framing. The note is passed to
`clip_tool_output` as a `suffix` so it is inside the 16 000-character backstop yet cannot be
truncated away by a chatty stdout - a model told nothing about its artifacts confabulates delivery.

## G.5 Confinement and bounding of the fetch

Every field of an artifact descriptor is agent-controlled: `code_exec` runs the agent's code in the
kernel's own process, so it can rebind the collector and dictate `filename`, `size_bytes` and
`rel_path`. Consequences:

- **`_safe_artifact_path` requires a basename directly under `/session/outputs`.** A directory must
  not pass: `get_archive` on one streams a tar of the whole tree while the stat header reports the
  ~4 KiB directory inode, so a size ceiling waves it through - and the session volume has no size
  option, so it is bounded only by host disk. Requiring that exact shape costs nothing, since
  `kernel._collect_artifacts` never descends.
- **The archive header is an early reject, not the bound.** It is derived from the guest's own file,
  so a background thread in the persistent kernel can append after the daemon stats it. `_CappedReader`
  is the real bound: it aborts mid-stream rather than committing the archive to the heap first, which
  is what `b"".join(stream)` did and why every downstream size check was too late.
- **A symlinked or non-regular leaf fails closed** on `linkTarget` and Go's `os.FileMode` type bits.
  A symlinked *directory component* is still resolvable inside the container rootfs and cannot be
  detected from outside; that is currently harmless because the only other mount is the agent's own
  volume, read-only and already readable under the inline cap. The argument rests entirely on the
  mount set, which `test_the_kernel_mounts_exactly_two_volumes` pins.
- **The stream is always closed.** docker-py hands back a generator over the response body; every
  rejection path abandons it unread, so it must be closed explicitly or the urllib3 connection is
  held until GC.

## G.6 Sizes are coerced, never trusted

`artifact_size_bytes` is total. A bare `int()` on a hostile `size_bytes` raises past the
per-artifact guards into the batch-level handler, which discards artifacts that were perfectly
fine - the same silent-loss shape this path exists to end. An unusable size reads as 0, which is
safe: it only ever makes a file look small, and the fetch is bounded by the archive reader
regardless.
