---
type: bugfix
status: implemented
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
(`agent_fs_gc.py:47-60`). `retention_sweep` (`app/workers/tasks/retention.py` — note the `tasks/`
segment; every `retention.py:N` cite below refers to this file) runs at **03:30** (`main.py:302`) and
hard-deletes exactly those rows with exactly the same 60-day cutoff (`:147`, `:233-241`). Ninety minutes
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
| Q-3 | Does the grace window still hold if the row is gone? | ~~**Yes — carry it on the artifact.**~~ **Superseded during implementation (D-1): nothing needs to carry it.** The absence of a row *is* proof the window elapsed, because retention is the only path that removes one and it requires 60+ days. See §7. | The original rationale — "a reclamation sweep must not delete a volume whose Agent was deleted yesterday" — is satisfied by the row that still exists in that case. The design only needed an artifact-borne timestamp for orphans, and orphans are past the window by construction. Q-6's whole hazard class disappears with the state that would have gone stale. |
| Q-4 | Fix the cascade path (§5) here too? | **Yes.** It is the same defect reached by a second road and Q-2's fix closes it for free. | A reclamation sweep does not care *why* there is no `agents` row. Fixing only the cron race would leave every project-cascaded Agent's volume leaking forever, which is the larger population in practice. |
| Q-5 | Purge the already-leaked volumes? | **Yes — that is the fix, not a separate migration.** | Q-2's sweep is retroactive by construction: it enumerates what is on the daemon today. Every volume leaked since the feature shipped is reclaimed on the first run. §9 covers the blast radius of that first run, which is the real risk in this task. |
| Q-6 | What happens to a tombstone when an Agent is **restored**? | **Dissolved by D-1 — there is no tombstone.** Restore is safe because it clears `deleted_at`, and the sweep reads `deleted_at` live. A restored-then-redeleted Agent ages from its latest deletion because that is the only timestamp there is. | The question was real and the hazard was real: `[R8.13]` makes `agent` restorable (`AgentRepository.restore`, `repositories.py:294-302`), and a tombstone carrying the **original** `deleted_at` would purge a redeleted Agent's volume on the next nightly run, skipping the recovery window. That it took three lifecycle rules to defend a timestamp nothing needed is what exposed the tombstone as the wrong shape. Kept here as the argument for D-1, not as work. |
| Q-7 | Ship the dry-run mode §9 recommends? | **Yes, and it is AC-10, not a D-n.** | §9 calls shipping without one "reckless" and then files it as an optional implementer choice — those two cannot both be right. The first run is irreversible and retroactive across years of artifacts; a bug in the uuid parse or the liveness join destroys live Agents' data. If the guard is load-bearing enough to be called reckless to omit, it is load-bearing enough to be an acceptance criterion. Default the worker to dry-run and require an explicit env flag to arm it. |

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
2. **Diff** against the `agents` table in one query: `SELECT id, deleted_at FROM agents WHERE id IN
   (...)`. One query, not N (§9).
3. **Decide** per artifact, on this table alone:

| `agents` row for the uuid | Artifact age | Decision |
|---|---|---|
| exists, `deleted_at IS NULL` | any | **keep** — live |
| exists, `deleted_at >= now-60d` | any | **keep** — inside the recovery window |
| exists, `deleted_at < now-60d` | any | **purge** — window closed, retention has not reached it yet |
| **no row** | `>= 60d` | **purge** — orphan (Road A after 03:30, Road B, and every pre-fix leak) |
| **no row** | `< 60d` | **keep** — the young-orphan floor |

**Why no timestamp needs to survive the row (supersedes Q-3, D-1).** The original design held that an
orphan has no `deleted_at` to age against and so needs one carried on the artifact — a volume marker
or a tombstone table. That premise is false, and the invariant is the whole fix:

> **The absence of an `agents` row is itself proof that the 60-day window elapsed.**

The only sanctioned path that removes an `agents` row is `_purge_soft_deleted_tenancy`, and every
route through it requires 60+ days of deletion: directly on `agents.deleted_at < cutoff`
(`retention.py:234`), or by FK cascade (`tables.py:22`) from a project or org whose *own* `deleted_at`
was already past the same cutoff. There is no path that hard-deletes an `agents` row inside the
window. So "orphan" already encodes "past window" — there is nothing left to time.

Road B needs no cascade write at all under this rule. A project soft-deleted at `T` leaves its Agents'
`deleted_at` at `NULL`, so the sweep sees **live rows and keeps the volumes** for the project's whole
recovery window — and a project restore ([R8.13]) is safe for free, because nothing was stamped to
un-stamp. At `T+60d` retention hard-deletes the project, the FK cascade takes the Agent rows, and the
volumes become orphans purged that night. Exactly `[R8.12a]`, with no cross-context write and no
Docker-aware service.

**The `CreatedAt` floor is sufficient and costs nothing.** A volume is created before its Agent is
deleted, so `CreatedAt <= deleted_at < now-60d` for every sanctioned orphan: **the floor can never
block a legitimate purge.** It only ever catches artifacts whose row's absence is *not* explained by
retention — chiefly the young-orphan race, where a volume exists because Docker auto-created it on
container create (`docker_runsc.py:1041`) but the `agents` row is not committed or visible yet. That
race is the floor's real job, and it is why the floor is a guard rather than a formality.

One path removes `agents` rows outside the window: `hard_delete_user` (`app/api/v1/admin_users.py:191`
-> `AdminService.hard_delete_user`), the admin GDPR purge. Under enumeration its Agents' volumes are
reclaimed on the next nightly run instead of never — the intent of a GDPR purge, not a regression.
The superseded tombstone design purged those identically (no tombstone + old `CreatedAt`), so this is
not a difference between the designs.

**The invariant is load-bearing, so pin it.** This sweep is correct only while retention remains the
only windowed remover of `agents` rows. That is the same undocumented coupling as FU-4. State it in
the module docstring and pin it with the test in §8.6 — a future path that hard-deletes an Agent
inside its window would silently turn this GC into a data-loss bug, and nothing else would catch it.

**Fail-open everywhere (§9).** If the `agents` query raises, purge nothing this run: an artifact whose
liveness cannot be established is kept. Never infer "no row" from an error.

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
3. **Same file — the cascade (D-1 shape).** Insert a project and an Agent with `deleted_at IS NULL`,
   soft-delete the *project* at `now-61d`, run `_purge_soft_deleted_tenancy` (which hard-deletes the
   project and cascades the Agent row away), then `run_once`, and assert the volume and the MinIO
   prefix are purged. *Fails now*: the Agent was never selectable, at any hour, by any cutoff.
   Assert `agents.deleted_at` is never consulted for this path — it is `NULL` throughout (AC-3).
4. **Same file — the guards, which are what makes the first run safe.**
   - A live Agent's volume is never purged, whatever its `CreatedAt`.
   - An Agent soft-deleted 1 day ago is never purged (inside the recovery window).
   - A volume whose name does not parse as `smap-agent-fs-{uuid}` is never touched.
   - A volume with no row but `CreatedAt` inside the window is **kept** (the conservative floor).
   - **The restore guards (Q-6).** D-1 removes the mechanism that made these dangerous, so they now
     assert the *absence* of a regression rather than a lifecycle rule. Keep them: they are cheap,
     and they fail closed on data loss rather than on a leak, which nothing else in §8 does.
     - **The redelete regression — the sharp one.** Soft-delete an Agent at `T-100d`, restore it at
       `T-99d`, soft-delete it again at `T-1d`, run `run_once`. The volume must be **kept**: the
       Agent has been deleted for one day, not a hundred. Under D-1 this passes because `deleted_at`
       is the only timestamp and restore rewrites it — but it is exactly the case any future
       reintroduction of a carried timestamp would break, which is why it stays in the suite.
     - **A project restore keeps its Agents' volumes.** Soft-delete a project, restore it, assert its
       Agents' volumes are kept. Under D-1 this holds because those Agents' `deleted_at` was never
       stamped, so they read as live throughout.
5. **`tests/unit/` — MinIO half.** `_purge_workspace_objects` reclaims an orphaned
   `agent-workspace/{uuid}/` prefix and leaves a live Agent's alone. *Fails now*: driven by the same
   empty list.
6. **Same file — the invariant D-1 rests on (`integration`, real Postgres).** The sweep is correct
   only while retention is the only path that removes an `agents` row inside the 60-day window. Pin
   it directly: insert an Agent soft-deleted `59` days ago, run `_purge_soft_deleted_tenancy`, and
   assert **the row survives** — that is the guarantee "no row implies past window" is derived from.
   This test passes today; it is a characterization test, and it is the tripwire for a future change
   that hard-deletes an Agent inside its window and would silently turn this GC into a data-loss bug
   (FU-4's coupling, stated as a test).
7. **Same file — the dry-run default (AC-10).** With no opt-in flag set, an artifact that satisfies
   every purge condition is **not** removed, and the would-purge set is still reported. Asserts the
   default configuration is inert.

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

- [x] AC-1: §8.1 fails before the fix and passes after — an artifact whose uuid has no `agents` row
      is purged. `test_orphan_volume_with_no_agents_row_is_purged`. **Caveat on "fails before":** the
      fix is a rewrite, so against the pre-fix module the suite *errors* (no `_docker_client` seam
      exists to fake) rather than failing on the observed defect. The old code could not pass — but
      the defect was demonstrated by reading, not by a red-then-green run on the same test. Recorded
      rather than glossed.
- [x] AC-2: covered by `test_expired_agent_row_still_present_is_purged` +
      `test_orphan_volume_with_no_agents_row_is_purged` — the two states an Agent past 60 days can be
      in, whether or not retention reached its row. **Not** the §8.2 integration test: see D-5.
- [x] AC-3: an Agent whose row is gone by cascade is purged, and `deleted_at` is never consulted for
      it — the orphan branch never reads it (`_classify`, the `agent_id in rows` miss). Covered by
      the same orphan test. **Not** the §8.3 integration test: see D-5.
- [x] AC-4: all four guards pass, plus two the spec did not ask for (unparseable `CreatedAt`, and a
      volume the daemon refuses). `test_live_agent_volume_is_never_purged_however_old`,
      `test_agent_deleted_inside_the_window_is_kept`, `test_unparseable_volume_name_is_never_touched`,
      `test_young_orphan_is_kept_by_the_floor`. Additionally verified against the **real daemon**: of
      144 live volumes on the dev host, the parser claimed 0.
- [x] AC-5: `test_orphaned_workspace_prefix_is_reclaimed`, `test_live_agents_workspace_prefix_is_left_alone`,
      `test_recently_touched_orphan_prefix_is_kept`.
- [x] AC-6: `AGENT_FS_GC_ARTIFACTS` reports seen / live / retained / purged / would_purge / declined
      per kind; an empty work list is no longer logged as success.
      `test_report_counts_seen_live_and_declined`. **Refined by D-3:** "declined orphans log at
      warning" now means *unjudgeable* artifacts only — `test_the_healthy_steady_state_does_not_warn`
      pins that the normal in-window state stays silent, because a warning that fires every night
      forever is not a signal.
- [x] AC-7: correct at any cron hour — `_classify` reaches "purge" from both states an Agent past 60
      days can be in (row present → `expired`; row gone → `orphan`), so the purged set does not
      depend on whether retention ran first. Both pinned.
- [x] AC-8: the module docstring now describes the enumerate-and-diff sweep, states the invariant it
      rests on, and documents the dry-run default. `[R12.03]`'s "removed by the nightly cleanup" is
      true **once armed** (D-2).
- [x] AC-9: `test_redeleted_agent_ages_from_its_latest_deletion`. The project-restore case folded
      into `test_live_agent_volume_is_never_purged_however_old` — under D-1 they are the same
      scenario (a project's Agents are never stamped, so they read as live), and the quality gate
      correctly flagged the separate test as a duplicate asserting nothing new.
- [x] AC-10: `test_dry_run_is_the_default_and_purges_nothing` (no `armed` fixture — the default
      configuration is inert while still reporting a non-empty would-purge set) and
      `test_arming_the_sweep_makes_it_purge`.
- [x] AC-11: the invariant is documented in the module docstring and pinned by
      `TestTheInvariantTheSweepRestsOn`, which asserts retention's cutoff on **`agents`, `projects`
      and `orgs`** — not just the direct sweep. The quality gate caught that guarding `agents` alone
      would have left Road B (the FK cascade, §5's larger population) unpinned, which is precisely
      the road this dossier exists for. Mutation-verified: shrinking retention's window to 1 day
      fails the test, so it is not vacuous. **No migration** is introduced (D-1); the DoD's migration
      gate is N/A.
- [x] AC-12: backend gates green — `pytest -q` (4749 unit tests pass; the 45 integration/wiring
      failures are pre-existing and byte-identical on a clean tree — verified by stashing), `ruff
      check .` and `ruff format --check .` clean across 765 files, `mypy` clean on every file this
      task touched. Two pre-existing `mypy` errors remain in `contexts/skills/infrastructure/repositories.py`,
      untouched here → FU-8.

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
>   closes, which is the same moment the artifacts become purgeable. Restoring an Agent under
>   [R8.13] restarts its recovery window: a subsequent deletion is measured from that later
>   deletion, never from the earlier one.

The trailing sentence is Q-6 stated as policy. `[R8.13]` grants a restore right but is silent on what
a restore does to the *deletion clock* — the same species of silence that let Road B ship. D-1 makes
the code satisfy it for free (`deleted_at` is the only clock and restore rewrites it), but the
sentence is still worth writing down: it is the property §8.4's redelete test defends, and the next
implementer to reach for a carried timestamp needs to find it stated somewhere.

Note `agent-workspace` is missing from the §21.5 bucket list (`:1367-1371`) and from the §21.4
persistence map — recorded as FU-3, and already covered by `2026-07-16-agent-skills` FU-1/FU-9's
documentation sweep.

## 12. Deviation Log

- **D-1 — the `agent_fs_tombstones` table (§7 option b) is dropped; no migration is introduced.**
  Agreed with the user before any code was written. Implementation planning showed the design's
  premise was false: §7 held that an orphaned artifact has no `deleted_at` to age against, but the
  absence of an `agents` row is *itself* proof the 60-day window elapsed, because
  `_purge_soft_deleted_tenancy` is the only path that removes one and every route through it requires
  60+ days. "Orphan" already encodes "past window", so nothing needs to carry a timestamp. This drops
  the migration, the cross-context writes into `ProjectService.soft_delete` (which would have made a
  tenancy service Docker-aware, against the SoC rule in CLAUDE.md), and the entire Q-6 stale-tombstone
  data-loss class — there is no state left to go stale. Road B is fixed *better* without it: a
  project's Agents keep `deleted_at IS NULL`, so the sweep reads them as live and keeps their volumes
  for the project's whole recovery window, then reclaims them as orphans once the FK cascade fires.
  The `CreatedAt` floor §7 already required proves sufficient and free (`CreatedAt <= deleted_at <
  now-60d` for every sanctioned orphan, so it never blocks a legitimate purge). Cost: the sweep is
  coupled to the invariant, which is undocumented today — mitigated by AC-11 (docstring + §8.6
  characterization test). Supersedes Q-3 and dissolves Q-6.
- **D-2 — the dry-run mode §9 recommended is an acceptance criterion (AC-10), not an implementer
  option.** §9 called shipping without it "reckless" and then filed it as a D-n; those cannot both be
  true. The worker defaults to dry-run and requires an explicit opt-in to arm (`SMAP_AGENT_FS_GC_ARMED`).
  Consequence the operator must know: **the leak persists until someone arms it.** This fix does not
  reclaim a byte on deploy — it reports what it *would* reclaim, and waits.
- **D-3 — hardening added beyond the spec, from the `check-quality` / `check-security` gates.** Each
  closes a real defect the spec did not anticipate; none change the design:
  - **The blast-radius guard.** The spec required refusing to infer "no row" from an *error*, but a
    query that *succeeds and returns nothing* carries the same meaning with none of the alarm — it
    says every artifact on the host is an orphan. A wrong DSN, a re-seeded replica, or a restore in
    progress all look like that, and one armed run would destroy every tenant's data. `rows` being
    empty is **not** the signal (a host whose agents were all reaped legitimately has exactly that,
    and purging is the point); the signal is the `agents` table being empty *altogether*, which no
    live deployment holding agent volumes can be. One extra probe, asked only when nothing matched.
  - **`volume.remove(force=False)`.** The daemon's "volume is in use by container X" refusal is the
    only guard in the design that does not depend on the DB join being right. `force=True` discards
    exactly the check that would catch a misclassification of a *running* agent.
  - **Canonical-only uuid parsing.** `uuid.UUID()` is not a validator: it accepts braces, a `urn:uuid:`
    prefix, arbitrary hyphen placement, no hyphens, uppercase, and non-ASCII digits, mapping them all
    to one id. The old docstring called the parse "strict" and it was not. No exploit exists today
    (every writer to the bucket goes through the canonical `agent_workspace_key`, and the sandbox has
    no direct MinIO access), but the entire safety of an irreversible worker rests on this parse.
  - **`retained` split out of `declined` (refines AC-6).** Every agent soft-deleted in the last 60
    days is `in_window` — the healthy state of any real deployment. Counting those as `declined` and
    logging them at `warning` would emit thousands of warnings nightly, forever, burying the signal
    §5 says was missing in the first place. `declined` now means only "could not be judged", which is
    what AC-6 intended by "declined orphans".
  - **A scoped `job_timeout` (`AGENT_FS_GC_TIMEOUT_S = 3600`), mirroring `graphrag_build`.** The
    first armed run reclaims years of artifacts, and the work runs in `asyncio.to_thread`, which arq
    cannot cancel — the default 600 s would mark the job failed while the thread kept deleting, with
    metrics never published. (The audit also alleged retry amplification; **refuted** — `arq.cron`
    already defaults to `max_tries=1`. Verified, not assumed.)
  - **`SOFT_DELETE_RETENTION_DAYS` exported from `retention.py` and imported by the GC.** The
    invariant was two independent `60` literals coupled only by a test. Now the coupling is code.
    This is the one edit to `retention.py` in this task, and it is behaviour-preserving.
  - **FU-5 fixed rather than deferred** — `agent_workspace_bucket` is now a public accessor on the
    MinIO client. The rewrite would otherwise have re-introduced the `client._cfg` private reach on a
    line it was already touching.
- **D-5 — §8.2, §8.3 and §8.6's `integration` (real-Postgres) tests were not written; their ACs are
  met at the unit tier instead.** Stated plainly because it is a real reduction in coverage, not a
  technicality. The DB-backed tier does not run in this environment: `tests/wiring` fails with
  `socket.gaierror` (no resolvable `redis`/`postgres` hostnames) and `tests/integration` has 2
  pre-existing failures — 45 in total, byte-identical with and without this change, verified by
  stashing. Writing tests that cannot be run would have meant shipping unverified test code and
  claiming a green gate I never saw.
  - What is genuinely covered: the *semantics* those tests target. §8.2/§8.3 both reduce to "an
    artifact whose uuid has no row is purged", which is pinned directly; the orphan branch cannot
    read `deleted_at` (AC-3) because there is no row to read.
  - What is genuinely lost: proof that `_purge_soft_deleted_tenancy` **actually** removes the row,
    and that the FK cascade **actually** fires, against real Postgres. §8.6's compiled-statement test
    substitutes for the first (it asserts the DELETE's cutoff on `agents`/`projects`/`orgs`, and
    mutation-testing confirms it fails when the window is shrunk) but nothing here executes the
    cascade. The FK is asserted by reading `tables.py:22`, not by exercising it.
  - Recorded as **FU-9**. This gap should close before the sweep is armed in production.
- **D-4 — `_parse_created_at` was rewritten after the quality gate found it silently wrong.** Docker
  emits Go's RFC3339Nano, which *trims trailing zeros*, so `.5`, `.123` and `.123456789` are all
  producible. The first implementation filtered digits out of the whole tail, conflating the fraction
  with the timezone offset: `.123Z` returned `None` (→ `orphan_age_unknown` → declined forever, a
  partial reinstatement of the very never-purges bug this task fixes), and `.12+08:00` returned a
  **wrong time** with the offset folded into the microseconds. Both were invisible because the test
  fake emitted second precision. The fake now emits a fraction by default.

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
- **FU-5: ~~`_purge_workspace_objects` reaches into a private attribute.~~ FIXED in this task** (D-3)
  — `MinioClient.agent_workspace_bucket` is now a public accessor. One other site still reaches into
  `_cfg` for the same bucket: `contexts/agents/application/workspace_service.py:194`. Out of scope
  here (this task never touches that file); it should adopt the new accessor.
- **FU-6: purging an expired agent's workspace prefix leaves `agent_workspace_files` rows dangling.**
  In the `expired` branch the `agents` row still exists, so its `agent_workspace_files` rows do too —
  and this sweep deletes the MinIO objects they point at, leaving DB pointers to destroyed blobs.
  Harmless while the agent stays deleted (nothing reads them) and self-correcting once retention
  reaps the row, but a restore inside the window would resurrect an Agent whose workspace listing
  references objects that are gone. Narrow: it needs a restore *after* the window closed but *before*
  retention ran, which `[R8.13]`'s own "does not re-check the 60-day age" makes reachable.
- **FU-7: `shared_kernel/storage/minio_client.py:23` imports from `app.config.settings`** — an upward
  dependency against CLAUDE.md's rule that `shared_kernel` never imports from the app layer.
  Pre-existing; this task only adds a property beside the five that already do it.
- **FU-8: two pre-existing `mypy` errors in `contexts/skills/infrastructure/repositories.py`** (`:192`,
  `:329` — `"Result[Any]" has no attribute "rowcount"`). Untouched by this task; they make a bare
  `mypy .` non-green, so the DoD gate was evaluated per-file.
- **FU-9: the DB-backed tests §8.2/§8.3 (and the cascade half of §8.6) are still owed** — see D-5.
  Nothing in this repo currently executes the FK cascade from `projects` to `agents`, which is Road
  B's actual mechanism and therefore the load-bearing half of this fix. Close before arming.
- **FU-10: the arming flag is read straight from `os.environ`**, bypassing `app/config/settings.py`
  where every other operational toggle lives, so the armed state of an irreversible destructive
  worker is invisible to config introspection. `retention.py:515`'s `SMAP_TUS_STAGING_DIR` sets the
  same precedent, so this is a consistency question for both.
- **FU-11: `tests/integration/test_permission_matrix.py::test_matrix_shape_is_25x6` fails on a clean
  tree** (`assert 26 == 25` — the matrix grew and the test did not). Unrelated to this task; noted
  because it is a genuine red test hiding in a tier whose other failures are environmental.
