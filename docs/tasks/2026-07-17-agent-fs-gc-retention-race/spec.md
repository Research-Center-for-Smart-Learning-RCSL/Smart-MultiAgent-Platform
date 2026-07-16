---
type: bugfix
status: draft
created: 2026-07-17
requirements: [R12.03, R8.12, R8.11]
---

# The nightly agent-FS GC never purges anything: retention deletes its input 90 minutes first

## 1. Summary

`[R12.03]` (`REQUIREMENTS.md:582`) promises that a soft-deleted Agent's `smap-agent-fs-{agent_id}`
volume is "retained for the 60-day recovery window, then removed by the nightly cleanup". The
nightly cleanup exists (`app/workers/agent_fs_gc.py`), is correctly written, and is wired
(`app/workers/main.py:320`, 05:00). It has almost certainly never removed a volume in production.

It finds its work by selecting `agents` rows with `deleted_at` older than 60 days
(`agent_fs_gc.py:47-60`). `retention_sweep` runs at **03:30** (`main.py:302`) and hard-deletes
exactly those rows with exactly the same 60-day cutoff (`retention.py:147`, `:233-241`). Ninety minutes
later the GC asks for them and gets nothing, so it logs `"agent_fs_gc: no volumes past retention"`
(`:120`) and exits successfully. Every night. The same id list drives the MinIO side
(`_purge_workspace_objects`, `:96-112`), so the `agent-workspace/{agent_id}/` prefix leaks too.

Two consequences, one of them a requirement violation: **disk grows without bound** (every volume and
every workspace object of every Agent ever deleted, retained forever), and **user data outlives the
tenancy deletion that was supposed to erase it** — `[R8.12]` (`:346-348`) cascades a project deletion
to its Agents, and `[R12.03]` promises the volume goes with them. Neither happens.

Found while verifying `2026-07-16-agent-skills`' FU-6/FU-19, whose staging defects share this
dossier's root cause family — SMAP infers the volume's existence from state that can be wrong or
gone — but nothing else. Those two are about *what is on* the volume during a turn; this is about
whether the volume is ever *removed*. Different files, different mechanism, different fix.

## 2. Observed vs Expected

**Observed.** `agent_fs_gc.run_once` (`:115-129`) is a single funnel: `ids =
_list_purgeable_agent_ids(ts)` (`:118`), `if not ids: return 0` (`:119-121`), then both
`_purge_volumes(names)` (`:125`) and `_purge_workspace_objects(ids)` (`:126`) are driven by that one
list. `_list_purgeable_agent_ids` (`:40-65`) is:

```
WHERE agents.deleted_at IS NOT NULL AND agents.deleted_at < now - 60 days
```

`_purge_soft_deleted_tenancy` (`retention.py:141-247`) computes `cutoff = now() - timedelta(days=60)`
(`:147`) and loops `_SOFT_DELETE_TABLES` (`:51-57`), issuing `sa.delete(tbl)` for rows matching
`deleted_at IS NOT NULL AND deleted_at < cutoff` (`:233-241`). `agents_tbl` is in that tuple and gets
no retention guard — only `chatrooms_tbl`, `projects_tbl`, and `orgs_tbl` get the extra `~*_retained`
conditions (`:234-240`).

Crons: `retention_sweep` at **03:30** (`app/workers/main.py:302`); `agent_fs_gc` at **05:00**
(`:320`).

So on the night an Agent's `deleted_at` first crosses the cutoff, retention deletes the row at 03:30
and the GC looks for it at 05:00. The row is gone. The volume and the MinIO prefix are never
referenced again, and the Agent's id is unrecoverable — nothing else in the schema records it.

**Expected.** `[R12.03]` (`REQUIREMENTS.md:582`): the volume is "retained for the 60-day recovery
window, **then removed by the nightly cleanup**". `agent_fs_gc.py:1-17`'s own module docstring states
the same policy as fact: "Every night this worker walks the `agents` table, finds rows whose
`deleted_at` is older than 60 days, removes the matching volume AND the MinIO prefix." Both describe
behaviour that does not occur. `[R8.12]` (`:346-348`) makes a project deletion cascade to its Agents;
`[R8.11]` (`:345`) sets the 60-day window.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | Reorder the crons, or stop depending on ordering? | **Stop depending on ordering.** The GC must not need a live `agents` row. | Moving `agent_fs_gc` to 03:00 would work until someone changes a cron, and it cannot fix §5's cascade path at all — those Agents never get a `deleted_at` to age, so no ordering saves them. A fix that a schedule edit can silently undo is not a fix. |
| Q-2 | How should the GC find its work instead? | **Reclaim by enumerating what exists and diffing against what should**: list `smap-agent-fs-*` volumes and `agent-workspace/` prefixes, extract the uuid, and purge those with no live `agents` row — with a grace period covering the recovery window. | This is the `_purge_rag_source_orphans` pattern (`retention.py:248-257`), which exists for exactly this reason: its own comment says a proactive teardown can be missed, so a backstop sweeps from any path. Docker is the authority on which volumes exist; the DB is the authority on which should. Neither alone is enough, which is the whole lesson of FU-6. |
| Q-3 | Does the grace window still hold if the row is gone? | **Yes — carry it on the artifact, not the row.** Use the volume's own `CreatedAt` plus a last-touch marker rather than `agents.deleted_at`. | A reclamation sweep must not delete a volume whose Agent is alive but idle, and it must not delete one whose Agent was deleted yesterday. Both need a timestamp that survives the row. See §7 for why `CreatedAt` alone is insufficient and what carries the recovery window. |
| Q-4 | Fix the cascade path (§5) here too? | **Yes.** It is the same defect reached by a second road and Q-2's fix closes it for free. | A reclamation sweep does not care *why* there is no `agents` row. Fixing only the cron race would leave every project-cascaded Agent's volume leaking forever, which is the larger population in practice. |
| Q-5 | Purge the already-leaked volumes? | **Yes — that is the fix, not a separate migration.** | Q-2's sweep is retroactive by construction: it enumerates what is on the daemon today. Every volume leaked since the feature shipped is reclaimed on the first run. §9 covers the blast radius of that first run, which is the real risk in this task. |

## 4. Reproduction

Deterministic; no timing dependency beyond the two crons.

1. Create Agent A in project P. Use its `file` tool once so `smap-agent-fs-{A}` is created (Docker
   auto-creates the named volume on container create — `docker_runsc.py:1041`), and upload a
   workspace file so `agent-workspace/{A}/` is populated.
2. Soft-delete A. `agents.deleted_at = T`.
3. Advance 60 days (or set `deleted_at` to `now - 61 days`).
4. Let the nightly workers run in their configured order.
   - 03:30 `retention_sweep` → `_purge_soft_deleted_tenancy` hard-deletes A's row.
   - 05:00 `agent_fs_gc.run_once` → `_list_purgeable_agent_ids` returns `[]` → logs
     `"agent_fs_gc: no volumes past retention"` → returns 0.
5. `docker volume ls | grep smap-agent-fs-{A}` — **still there**. `agent-workspace/{A}/` — still
   there. Both are now unreferenced by any row, forever.

**The narrow case where it does work, which is why this was not noticed:** retention's delete is
batched at `.limit(200)` per table per run (`retention.py:240-241`). If more than 200 Agents cross
the cutoff on the same night, the overflow survives 03:30 and the GC catches those at 05:00. So the
worker is not dead code — it purges exactly the Agents that retention did not get to. In any
deployment deleting fewer than 200 Agents per night, that is none, ever.

## 5. Root Cause Analysis

Two independent roads to the same leak. Both are instances of one mistake.

**Road A — the cron race.**
1. `agent_fs_gc` takes its work list from a table another worker owns and prunes on the same
   cutoff (`agent_fs_gc.py:47-60` vs `retention.py:147`).
2. `retention_sweep` (03:30) runs before `agent_fs_gc` (05:00) (`main.py`).
3. The row is deleted before the GC reads it. **Root cause: the GC's input is derived from state
   whose lifetime is shorter than the GC's own trigger condition.** The 90-minute gap is the
   aggravating factor; the dependency is the defect. Reversing the crons would hide it.

**Road B — the cascade.** Independent of any schedule, and it is the larger population.
1. `agents.project_id` is `ForeignKey("projects.id", ondelete="CASCADE")`
   (`contexts/agents/infrastructure/tables.py:22`).
2. `[R8.12]` (`REQUIREMENTS.md:346-348`) makes a project deletion cascade to its Agents. It is the
   *project* that is stamped; nothing stamps `agents.deleted_at` per row.
3. So a project-deleted Agent has `deleted_at IS NULL` and is **never selectable** by
   `_list_purgeable_agent_ids` (`:52`) — not at 03:30, not at 05:00, not ever.
4. When retention hard-deletes the project (`retention.py:233-241`, `projects_tbl` precedes
   `agents_tbl` in `_SOFT_DELETE_TABLES:51-57`), the FK cascade removes the Agent rows and the id
   is gone.
5. The volume and the MinIO prefix are unreachable. **Root cause: the same one — the GC can only
   see Agents that were deleted one specific way.**

**The mistake behind both:** the GC infers the set of purgeable artifacts from the `agents` table
rather than from the artifacts themselves. Docker is the authority on which volumes exist; the DB is
the authority on which ones should. The GC consults only the second, and only through a window that
closes before it looks. `retention.py:248-257` (`_purge_rag_source_orphans`) already learned this
lesson in the RAG context — its comment says the sweep exists because a proactive teardown can be
missed — and `agent_fs_gc` did not inherit it.

**Why it is invisible.** `run_once:119-121` treats an empty list as the success case and logs
`"agent_fs_gc: no volumes past retention"` at `info`. That is the message a healthy idle GC emits.
There is no metric, no gauge of volumes-vs-rows, and no alert. The failure mode is a worker that
looks perfectly healthy while doing nothing, forever.

## 6. Blast Radius and Sibling Suspects

**Blast radius.** Unbounded disk growth on the Docker host: one volume per Agent ever deleted, each
up to the 100 MB quota `[R12.03]` grants, never reclaimed. Plus the `agent-workspace/{agent_id}/`
MinIO prefix, which has **no TTL by design** (`smap/bootstrap/minio_init.py:7`, `:39`, `:153`) —
`agent_fs_gc` is its only reclamation path, so it is a permanent leak with no backstop. For a
self-hosted product where the operator owns the disk, this is a slow, silent capacity bug that
surfaces as a full volume months later with no obvious cause.

The data-protection half is the sharper one: `[R8.11]`-`[R8.14]` (`REQUIREMENTS.md:345-353`) define
tenancy deletion with a 60-day window, after which data is "irreversibly purged"
(`agent_fs_gc.py:8-9` uses that phrase). A designer-uploaded workspace file survives its project's
deletion, its Agent's deletion, and the recovery window — on disk and in MinIO — with no row left
that anyone could use to find it. That is data outliving the deletion that was supposed to erase it,
against a cited requirement.

**Sibling suspects — other workers that derive their work list from rows another sweep deletes.**

| Site | Verdict |
|---|---|
| `agent_fs_gc._purge_volumes` (`:72-93`) | **CONFIRMED** — the reported defect. |
| `agent_fs_gc._purge_workspace_objects` (`:96-112`) | **CONFIRMED** — same `ids` list (`run_once:126`), same failure, and worse: the bucket has no TTL, so this is the only path that would ever remove those objects. |
| `retention._purge_rag_source_orphans` (`:248-257`) | **cleared, and it is the exemplar.** It reclaims from any path precisely because a proactive teardown can be missed; it does not depend on a row surviving. Q-2 copies it. |
| `retention._purge_soft_deleted_tenancy`'s F-24 teardown (`:186-230`) | **cleared** — it calls `purge_project_source_infra_batch(teardown_ids)` **before** the Postgres cascade erases the rows carrying the blob keys. This is the correct ordering, in the same function, ~40 lines above the loop that breaks it for Agents. Strong evidence the ordering hazard was understood and that Agents were simply missed. |
| `retention._purge_agent_instances` (`:324`), `_prune_idle_sessions` (`:311`) | **not this pattern** — self-contained, 30-day, no external consumer of their rows. |
| Chatroom / workflow volumes or buckets | **cleared** — no per-chatroom or per-workflow Docker volume exists; `smap-agent-fs-{agent_id}` is the only agent-keyed artifact (`docker_runsc.py:1036`, `file_tool.py:56-57`, `agent_fs_gc.py:68-69` all construct the same name). |

**Explicitly not in scope:** `2026-07-16-agent-skills` FU-6 (the lying manifest cache) and FU-19
(staging overlays rather than reconciles). Same root-cause family — inference about the volume from
state that can be wrong — but they concern what is *on* the volume during a turn, in
`docker_runsc.py`. This task never touches that file. They are being spec'd separately.

## 7. Fix Design

**Reclaim by enumeration, not by inference.** Replace `_list_purgeable_agent_ids`'s row-driven
selection with a sweep that asks Docker and MinIO what exists, then asks the DB what should:

1. **Enumerate** `client.volumes.list()` filtered to names matching `smap-agent-fs-{uuid}`, and the
   `agent-workspace/` bucket's top-level `{uuid}/` prefixes. Parse the uuid out of each.
2. **Diff** against the live `agents` table in one query: `SELECT id FROM agents WHERE id IN (...)`.
   An artifact whose uuid has a row with `deleted_at IS NULL` is **live** — keep, unconditionally.
3. **Purge** an artifact whose uuid has no row at all, or a row with `deleted_at < now - 60d`,
   **provided the grace check in Q-3 passes**.

**The grace window when the row is gone (Q-3).** An orphan has no `deleted_at` to age against, so
the sweep needs a timestamp that lives on the artifact. Docker volumes carry `CreatedAt`
(`volumes.get(name).attrs["CreatedAt"]`), but that is the *creation* time — an Agent created a year
ago and deleted yesterday would be purged immediately, destroying data inside its recovery window.
`CreatedAt` alone is therefore **wrong** and must not be used as the sole gate.

Two correct options; the implementer picks one and records it as a D-n:

- **(a) Mark on delete.** When an Agent (or a project cascading to Agents) is soft-deleted, write a
  marker into the volume — a `/workspace/.smap-deleted-at` file — and treat its absence as "not yet
  marked, do not purge". Precise, but it means `AgentService.soft_delete` and the project cascade
  become Docker-aware call sites, which is a cross-layer dependency this codebase otherwise avoids.
- **(b) A durable tombstone table.** `agent_fs_tombstones(agent_id, deleted_at)` written on soft
  delete and on project cascade, with **no FK to `agents`** — so retention's delete cannot take it.
  The GC reads tombstones, not `agents`. Rows are removed after the purge succeeds. This keeps the
  timestamp in the DB where the rest of the retention policy lives, survives the cascade by
  construction, and is the smaller change. **Recommended.**

Option (b) makes Road B's fix explicit: the project cascade must write tombstones for the Agents it
takes down, which is the step `[R8.12]` implies and nothing implements.

**Orphans with no tombstone** (everything leaked before this fix, and anything a future gap misses)
still need reclaiming. Gate those on a conservative floor: purge an orphaned artifact only if it has
no live `agents` row **and** no tombstone **and** its `CreatedAt` is older than the 60-day window —
the volume of an Agent deleted within the window will have a tombstone, so the absence of one plus
age means the row is long gone. This is the `_purge_rag_source_orphans` posture: a backstop that
errs toward keeping.

**Do not reorder the crons.** It would mask Road A and does nothing for Road B (Q-1). The GC should
be correct at any hour; if the implementer wants defence in depth, that is a D-n, not the fix.

**Observability is part of the fix, not a nice-to-have.** `run_once` must stop treating an empty
work list as success. Emit a metric for volumes-seen / rows-live / purged, and log at `warning` when
the sweep finds orphans it declined to purge. §5's "why it is invisible" is the reason this bug
reached production; a fix that leaves the same blind spot invites the next one.

**Data repair: this fix is the repair (Q-5).** The sweep is retroactive by construction — every
volume and prefix leaked since the feature shipped is enumerated on the first run and reclaimed if
it passes the floor. No migration needed. See §9 for the danger in that.

## 8. Regression Test Plan

Failing tests first. `agent_fs_gc` has **no test file today** — `tests/unit/` has nothing for it
(the module is not referenced by any test), so this task creates one. Note the module's Docker SDK
import is lazy by design (`agent_fs_gc.py:14-16`, `:78`), which makes it fakeable without a daemon —
and there is no Docker test tier (`pyproject.toml:353-358`: `unit`, `integration`, `e2e`, `wiring`
= "real Postgres+Redis+MailHog"; no Docker, no gVisor).

1. **`tests/unit/test_agent_fs_gc_race.py` (new) — the headline.** With a fake volume client and an
   `agents` table containing **no row** for a volume's uuid, `run_once` purges it. *Fails now*:
   `_list_purgeable_agent_ids` returns `[]` and `run_once` returns 0 at `:119-121`. This is the test
   that pins Road A **and** Road B — both present as "an artifact whose uuid has no row".
2. **Same file — the ordering, end to end.** Integration (`integration` marker, real Postgres):
   insert an Agent with `deleted_at = now - 61d`, run `_purge_soft_deleted_tenancy`, *then*
   `run_once`, and assert the volume is purged. *Fails now*: the row is gone by the time the GC
   looks, which is the defect stated as a test.
3. **Same file — the cascade.** Insert a project and an Agent with `deleted_at IS NULL`, soft-delete
   the *project*, run retention, assert a tombstone exists and the volume is purged. *Fails now*:
   nothing writes a tombstone and the Agent was never selectable.
4. **Same file — the guards, which are what makes the first run safe.**
   - A live Agent's volume is never purged, whatever its `CreatedAt`.
   - An Agent soft-deleted 1 day ago is never purged (inside the recovery window).
   - A volume whose name does not parse as `smap-agent-fs-{uuid}` is never touched.
   - A volume with no row but `CreatedAt` inside the window is **kept** (the conservative floor).
5. **`tests/unit/` — MinIO half.** `_purge_workspace_objects` reclaims an orphaned
   `agent-workspace/{uuid}/` prefix and leaves a live Agent's alone. *Fails now*: driven by the same
   empty list.

## 9. Risks and Rollback

- **The first run is the whole risk, and it is a large one.** This fix is retroactive by design
  (Q-5), so the first sweep on a long-lived deployment will delete every volume and workspace prefix
  leaked since the feature shipped — potentially years of them, all at once, irreversibly. Every
  guard in §8.4 exists to bound that. Strongly recommend the implementer ship a **dry-run mode
  first** (enumerate, log what would be purged, purge nothing) and require an operator to read the
  output before enabling. That is a D-n decision, but shipping this without one is reckless: a bug
  in the uuid parse or the liveness join deletes live Agents' data.
- **`volumes.list()` on a host with many volumes** is a single Docker API call returning everything;
  it is not paginated. On a large host this is a big response and the uuid-parse filter runs
  client-side. Acceptable nightly, but it should not be a per-turn path.
- **The liveness join must be a single query, not N.** `_purge_workspace_objects` (`:103-112`)
  already loops per agent; a naive port would issue one `SELECT` per volume. Use `id IN (...)`.
- **Tombstones are a new write on a hot-ish path** (Agent soft-delete, project cascade). They must
  not fail the delete: a tombstone write error should log and let the deletion proceed — the orphan
  floor in §7 is the backstop for exactly that.
- **Fail-open, not fail-closed.** Unlike the key-group work, the safe direction here is to *keep* an
  artifact when uncertain. Every ambiguous case (unparseable name, DB unreachable, tombstone
  missing but volume young) must keep. A GC that leaks is today's bug; a GC that deletes live data
  is a much worse one.
- **Rollback:** the sweep is one worker. Reverting restores today's behaviour (leak, no data loss).
  Deletions it already performed are not recoverable, which is why the dry run matters.

## 10. Acceptance Criteria

- [ ] AC-1: §8.1 fails before the fix and passes after — an artifact whose uuid has no `agents` row
      is purged.
- [ ] AC-2: §8.2 passes — an Agent soft-deleted past 60 days has its volume purged **even though
      `retention_sweep` deleted its row first**, with the crons in their current order.
- [ ] AC-3: §8.3 passes — an Agent taken down by a project cascade has its volume and MinIO prefix
      purged, and `agents.deleted_at` is never consulted to achieve it.
- [ ] AC-4: §8.4's four guards pass — live Agents, in-window Agents, unparseable names, and
      young orphans are all kept.
- [ ] AC-5: §8.5 passes — the `agent-workspace/{uuid}/` prefix is reclaimed on the same terms.
- [ ] AC-6: `run_once` no longer treats an empty work list as success: a metric reports
      volumes-seen / live / purged / declined, and declined orphans log at `warning`.
- [ ] AC-7: the GC is correct at any cron hour — a test runs `run_once` **before**
      `_purge_soft_deleted_tenancy` and after, and both purge the same set.
- [ ] AC-8: `agent_fs_gc.py:1-17`'s module docstring describes what the code does; `[R12.03]`'s
      "removed by the nightly cleanup" is true.
- [ ] AC-9: backend gates green — `pytest -q`, `ruff check . && ruff format --check .`, `mypy .`.

## 11. SRS Delta

`[R12.03]` (`REQUIREMENTS.md:582`) already states the intended behaviour and needs no change — the
code deviates from it, which is what makes this a bugfix rather than a feature.

One gap worth closing in the same change, if the user agrees: `[R8.12]` (`:346-348`) says a project
deletion cascades to its Agents but does not say what happens to each Agent's *artifacts*. That
silence is what let Road B ship. Proposed addition to §8.2, after `[R8.12]`:

> - **[R8.12a]** Artifacts whose lifecycle is bound to an Agent — its `smap-agent-fs-{agent_id}`
>   volume and its `agent-workspace/{agent_id}/` objects ([R12.03]) — are purged on the same 60-day
>   schedule whether the Agent was deleted directly or cascaded from its Project or Org. Reclamation
>   must not depend on the Agent's row still existing: the row is removed when the recovery window
>   closes, which is the same moment the artifacts become purgeable.

Note `agent-workspace` is missing from the §21.5 bucket list (`:1367-1371`) and from the §21.4
persistence map — recorded as FU-3, and already covered by `2026-07-16-agent-skills` FU-1/FU-9's
documentation sweep.

## 12. Deviation Log

Appended by `/build`.

## 13. Follow-ups

- **FU-1: `_purge_workspace_objects` has no `list_objects` pagination guard.** `:106` iterates
  `client.list_objects_sync(bucket, prefix=prefix)` per agent. Fine for a per-agent prefix; if Q-2's
  sweep enumerates the bucket's top level instead, the shape changes and the loop should be
  revisited for a bucket with many thousands of prefixes.
- **FU-2: nothing reclaims a volume for an Agent that was hard-deleted by a path other than
  retention.** Admin tooling, a manual `DELETE`, or a future feature would leave the same orphan.
  Q-2's sweep covers it by construction, which is the argument for enumeration over inference —
  worth stating in the ADR if one is written.
- **FU-3: the whole designer-upload feature is absent from the SRS.** `agent_workspace_files`,
  `agent-files`, and the `agent-workspace` bucket appear nowhere in `REQUIREMENTS.md` — not in §21.4's
  persistence map (`:958-999`), not in §21.5's bucket list (`:1367-1371`). `[R12.03]` (`:582`)
  governs the volume only as the `file` tool's own scratch state and does not know it has a region
  mirroring a DB table. The design doc `docs/agent-tools/D-code-interpreter-files.md` is the only
  intent source. This is why FU-6/FU-19 have no requirement to violate. Folds into
  `2026-07-16-agent-skills` FU-1/FU-9's sweep.
- **FU-4: `retention.py`'s `_SOFT_DELETE_TABLES` ordering is load-bearing and undocumented.**
  `projects_tbl` precedes `agents_tbl` (`:51-57`), so the FK cascade at `tables.py:22` erases Agent
  rows before the Agent iteration runs — making the `agents_tbl` entry partly dead for
  project-cascaded rows. The F-24 teardown 40 lines above (`:186-230`) shows the hazard was
  understood for RAG sources. Nothing states the invariant or tests it; a reorder would silently
  change behaviour.
