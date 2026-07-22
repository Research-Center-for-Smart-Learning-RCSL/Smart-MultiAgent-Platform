---
type: bugfix
status: draft
created: 2026-07-22
requirements: [R15.21, R13.25, R13.15]
depends_on: []
---

# The nightly retention sweep starves one policy, duplicates another, and lies in its purge audit

## 1. Summary

Three defects in the nightly `retention_sweep` cron, all in chunked or limited cleanup
code, all reverted together. **F-17**: `_sweep_orphaned_subagent_roots` applies its
`LIMIT 500` *before* its orphan predicate, so on any deployment with more than 500
synthetic sub-agent roots the sweep reaps only the orphans that happen to fall inside an
arbitrary unordered sample, and the `agent_instances` table grows without bound.
**F-42**: `SubagentService.cleanup_expired` is an uncalled second implementation of the
same 30-day `agent_instances` rule, with a different cutoff and a different failure
signal, waiting to be wired up by someone who does not know the live sweep exists.
**V-5 / FU-3**: `RetentionService.purge_once` selects purge victims with no `ORDER BY`,
so a chunked purge deletes an arbitrary subset rather than oldest-first, and then stamps
the `message.purged_by_retention` audit event with `oldest_kept_at: horizon` — an
assertion the code has not established and which is false on every chunk but the last.

User-visible impact: unbounded storage growth on the orchestration side (F-17), no impact
today (F-42), and a compliance audit trail that overstates how much old data has been
erased (V-5).

**Scope rationale.** F-17 and F-42 arrive from
`docs/audits/2026-07-22-agent-to-agent-orchestration/findings.md`; V-5 and FU-3 from
`docs/audits/2026-07-22-conversation-verification-gap/findings.md`. They share the
`retention_sweep` change surface — `backend/app/workers/tasks/retention.py` and the one
service it delegates message purging to — the same nightly cron, the same test file
(`backend/tests/unit/test_retention_deep.py`), and the same defect family: a chunked
sweep whose row limit and whose eligibility predicate are in the wrong relationship. The
honest caveat required by the a2u audit's grouping rule: F-42 is a pure deletion and
would revert independently of the other two.

**Duplicate hand-off — resolved here, not silently.**
`docs/audits/2026-07-22-agent-to-user-conversation/findings.md:680` routes V-5 to
`docs/tasks/2026-07-22-retention-audit-accuracy/`, while
`docs/audits/2026-07-22-agent-to-agent-orchestration/findings.md:1187` routes F-17 and
F-42 to `docs/tasks/2026-07-22-retention-sweep-fixes/`. These overlap, and **neither
directory exists on disk** — a `docs/tasks/**/*retention*` glob returns nothing for
2026-07-22, so no work is discarded by consolidating. The audit that actually *owns* V-5
does not name a slug at all: `docs/audits/2026-07-22-conversation-verification-gap/findings.md:476`
says only "Group with retention work". **Recommendation: `2026-07-22-retention-sweep-fixes`
wins**, this dossier is the single home, and the a2u row at `findings.md:680` should be
amended to point here. `retention-audit-accuracy` is the narrower name and would not
cover F-17 or F-42; the reverse is not true.

**Interaction with `docs/tasks/2026-07-22-subagent-spawn-fail-fast/spec.md`.** That
dossier makes the `subagent_spawn` node fail fast on its `failure` port, so once it lands
no new synthetic roots are created and F-17's population stops growing. That changes
F-17's *urgency*, not its *correctness*: `_sweep_orphaned_subagent_roots` still has to
drain the roots already on disk, and a sweep that samples 500 unordered rows and filters
afterwards cannot drain them. **`depends_on` is deliberately empty.** Adding one would be
wrong in both directions — this fix must work for the existing population whether or not
fail-fast ships, and fail-fast does not need this fix to be correct. The two are
independent and can land in either order.

## 2. Observed vs Expected

### F-17 — orphan sweep starves

- **Observed.** `backend/app/workers/tasks/retention.py:505-519`: the `synth` CTE filters
  only on `parent_id IS NULL AND run_context->>'synthetic_root' = 'true'`, applies
  `LIMIT 500` at `:512`, and has no `ORDER BY`. The orphan predicate —
  `NOT EXISTS (SELECT 1 FROM workflow_runs wr WHERE wr.id = s.wf_run_id::uuid)` — sits in
  the outer query at `:516-518`, applied to the already-truncated 500 rows.
  `retention_sweep` (`:762-776`) calls each policy exactly once per cron tick
  (`subagent_roots` registered at `:746`); there is no loop, cursor, or offset. With 5000
  synthetic roots of which 4800 belong to live runs, each nightly pass filters the same
  arbitrary sample and reaps only the orphans inside it. Because these rows carry
  `destroyed_at IS NULL` they are invisible to `_purge_agent_instances` (`:473-482`, which
  requires `destroyed_at IS NOT NULL`), so nothing else reclaims them.
- **Expected.** `[R15.21]` (`REQUIREMENTS.md:794`) — sub-agent rows are "deleted after 30
  days". The function's own docstring (`retention.py:488-503`) states the intent
  directly: once the owning workflow run is gone, "the whole synthetic subtree is dead
  weight". A sweep that cannot reach past an arbitrary 500-row prefix does not implement
  that.

### F-42 — dead second implementation of the same rule

- **Observed.** `backend/contexts/orchestration/application/subagent_service.py:320-321`
  ← `backend/contexts/orchestration/interfaces/facade.py:347-348` ← nothing. A repo-wide
  grep for `cleanup_expired`, `cleanup_expired_instances` and `delete_older_than_days`
  returns only those two definitions, the repository method at
  `backend/contexts/orchestration/infrastructure/repositories.py:568-577`, and one unit
  test at `backend/tests/unit/test_orchestration_services.py:801-807`. The divergence
  from the live sweep is real and threefold: `repositories.py:569` truncates to midnight
  before subtracting the days, so its cutoff is up to 24 h earlier than
  `retention.py:472`'s `now() - timedelta(days=30)`; it is unbatched, with no `LIMIT` at
  all, unlike `_purge_agent_instances` (`retention.py:479`); and `:577` returns bare
  `result.rowcount` with no `or 0`, unlike `retention.py:483`.
- **Expected.** `[R15.21]` states one 30-day rule. Two implementations of one rule, with
  three behavioural differences and no caller, is not a policy — it is a trap. If a future
  operator wires it into the cron, `retention_sweep`'s
  `total = sum(v for v in report.values() if v > 0)` (`retention.py:777`) would read a
  `-1` rowcount as this module's policy-failure marker.

### V-5 / FU-3 — the purge audit asserts something the code has not established

- **Observed.** `backend/contexts/conversation/application/retention_service.py:52-60`
  selects victims with `WHERE created_at < horizon` and `LIMIT PURGE_CHUNK` (500, `:28`)
  and **no `ORDER BY`** — so which 500 of the eligible rows die is plan-dependent. The
  purge path then stamps `oldest_kept_at: horizon.isoformat()` on every per-room audit
  event at `:104`, and returns `oldest_kept_at=horizon` in the report at `:112`. The
  no-op path at `:63-64` sets the *same field name* to `min(messages.created_at)` — the
  true oldest surviving row. Two quantities, one field. And on the purge path the value
  is simply not true: `backend/app/workers/tasks/retention.py:94-98` loops at most 100
  chunks, so a room holding 1200 messages past the horizon emits chunk 1's event claiming
  nothing older than `horizon` survives while ~700 older rows are still on disk. The
  field is per-room in the audit event (`:100-105`) but the value is global, which is a
  third inconsistency.
- **Expected.** `[R13.25]` (`REQUIREMENTS.md:702`) mandates the event
  `message.purged_by_retention` with `{chatroom_id, count, oldest_kept_at}` by name. A
  field named `oldest_kept_at`, emitted per chatroom, must mean the oldest row that room
  still holds after the purge — the same meaning the no-op path already gives it. Beyond
  the field's existence `[R13.25]` states no ordering contract, so the `ORDER BY` half is
  an internal-consistency fix rather than a stated-intent deviation; see Q-2.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | F-42: repair the divergence or delete the code? | **Delete** `SubagentService.cleanup_expired`, `OrchestrationFacade.cleanup_expired_instances`, `AgentInstanceRepository.delete_older_than_days`, and `test_cleanup_expired`. | `_purge_agent_instances` (`retention.py:471-485`) already owns and ships this policy. Aligning the cutoffs would leave two implementations of one rule and re-open the divergence on the next edit. The a2a audit reaches the same disposition (`findings.md:1052-1054`). |
| Q-2 | V-5: is oldest-first ordering (FU-3) sufficient, or must the field be recomputed? | **Both are needed; ordering alone is not sufficient.** Add `ORDER BY created_at, id` **and** recompute `oldest_kept_at` after the delete. | Oldest-first makes partial progress monotone and fixes FU-3 as a defect in its own right, but it does **not** make `oldest_kept_at: horizon` true: after deleting the oldest 500, the 501st surviving row is still older than `horizon`, so the assertion remains false on every non-final chunk. Only a post-delete `min(created_at)` is the quantity the field's name — and its no-op-path meaning at `:63` — promises. |
| Q-3 | Should `_sweep_orphaned_subagent_roots` gain a chunk loop like `_purge_messages`? | **No.** Keep the single pass at 500. | Once the predicate precedes the limit, each pass reclaims 500 *actual* orphans and the eligible set shrinks by exactly that much, so a backlog drains monotonically over consecutive nights. This matches the single-pass shape of every other `agent_instances` policy (`retention.py:473-482`). Multi-pass is available later without changing this fix's shape. |
| Q-4 | Should a plain btree `messages(created_at)` index ship with the `ORDER BY`? | **Recommended: yes**, in the same migration. Flag for the user — it is the one item here with a write-path cost. | `messages` has no index usable for a global age scan: `0017_messages.py:74-77` creates only `(chatroom_id, created_at DESC) WHERE deleted_at IS NULL`. Without one, `ORDER BY created_at LIMIT 500` becomes a full scan plus top-N sort per chunk. Against that: `retention_service.py:63` **already** runs an unindexed global `min(created_at)` on the no-op path every single night, and the V-5 fix adds a second post-delete `min`. The index turns three unindexed scans per night into index reads; the cost is one more btree on the highest-insert table. |
| Q-5 | Does the sweep need a data-repair migration for the existing orphan population? | **No backfill script.** The corrected sweep is the repair. | See §7. The rows are inert dead weight, not corrupt data; the fixed policy drains them at 500/night with no manual intervention. §7 supplies a read-only sizing query so an operator can decide whether to accelerate. |

## 4. Reproduction

### F-17 (deterministic, requires Postgres)

Preconditions: any project with one agent; migrations at head (`0061_graphrag_owner_index_live_only`).

1. Insert 1000 `agent_instances` rows with `parent_id IS NULL`,
   `run_context = {"synthetic_root": true, "workflow_run_id": "<id of a LIVE workflow_runs row>"}`.
2. Insert 20 more with the same shape but a `workflow_run_id` that exists in no
   `workflow_runs` row (the state `_archive_workflow_runs` leaves behind — see
   `backend/contexts/workflow/interfaces/facade.py:116-160`, which deletes the source rows).
3. Call `_sweep_orphaned_subagent_roots(session)`.
4. **Observed:** returns `0`; the audit row records `rows_affected: 0`; all 20 orphans
   remain. The CTE's unordered `LIMIT 500` (`retention.py:512`) is satisfied by the first
   500 rows the scan produces — all live — and the outer `NOT EXISTS` at `:516-518` then
   matches nothing.
5. **Expected:** returns `20`; all 20 orphan rows and any children deleted.

Nondeterminism, stated plainly: step 4's outcome is *plan-dependent by construction* —
that is the defect. Postgres typically returns the heap-order prefix for an unordered
`LIMIT`, which is why inserting the live rows first makes it reproduce reliably. A
different plan could reap some orphans by luck; none reaps all of them, and none is
reproducible.

### F-42

Not reproducible as a runtime failure — the defect is that the code has no caller. Verify
by grep: `cleanup_expired|delete_older_than_days|cleanup_expired_instances` over
`backend/` returns only the three definitions and one unit test (see §2).

### V-5

1. Seed one chatroom with 1200 messages whose `created_at` predates
   `now() - RETENTION` (`retention_service.py:27`, 5 years + 1 day), the oldest dated
   2019-01-01.
2. Run `_purge_messages(session)` (`retention.py:83-111`).
3. **Observed:** three `message.purged_by_retention` events for that room. The first two
   carry `oldest_kept_at` equal to `horizon` (`retention_service.py:104`) while the room
   still holds hundreds of 2019 and 2020 rows. Which 500 die in each chunk is not
   reproducible across runs (`:52-60`, no `ORDER BY`).
4. **Expected:** each event's `oldest_kept_at` equals that room's true
   `min(created_at)` after that chunk's delete, and chunks proceed oldest-first.

## 5. Root Cause Analysis

**F-17 — root cause: `retention.py:512`.** The `LIMIT 500` is placed inside the `synth`
CTE, which knows only *what shape* a row is (`:510-511`, synthetic and parentless), not
*whether it is eligible* (`:516-518`, its run is gone). The chain: the CTE truncates the
candidate set → the outer query intersects that truncated set with the orphan predicate →
`root_ids` (`:521`) is the intersection, usually empty → `:522-524` short-circuits and
emits `rows_affected: 0` → `retention_sweep` (`:769-771`) records a healthy zero and the
gauge `RETENTION_LAST_ROWS` reads zero, which is indistinguishable from "nothing to do".
Moving the predicate above the limit prevents every downstream link, so `:512` is the
root cause. The missing `ORDER BY` is an **aggravating factor**, not the cause: with the
predicate correctly placed, an unordered limit over an eligible-only set still drains
monotonically. It is worth fixing for reproducibility, not for correctness.

**F-42 — root cause: `subagent_service.py:320-321` was written and never wired.** There
is no causal chain to a symptom because there is no symptom. The defect is latent: three
divergences (`repositories.py:569` midnight truncation; no `LIMIT`; bare `rowcount` at
`:577`) sit behind an interface whose name promises the `[R15.21]` policy that
`retention.py:471-485` actually implements.

**V-5 — root cause: `retention_service.py:104` and `:112` assert a value the function has
not computed.** The chain: `:52-60` selects an arbitrary 500 eligible rows (no ordering)
→ `:91-93` deletes exactly those → `:95-107` emits one event per affected room carrying
`horizon`, a *threshold*, in a field whose no-op-path meaning at `:63-64` is an *observed
minimum* → `retention.py:94-98` repeats this up to 100 times, producing up to 100 audit
rows each asserting the room's floor is `horizon`. The earliest link whose correction
prevents the symptom is the emission itself: computing the surviving `min(created_at)`
after the delete makes the field true regardless of which rows the chunk took. The
missing `ORDER BY` at `:52-60` is a **second, independent defect** (FU-3) — it makes
partial progress non-monotone and the victim set irreproducible — and it is an
**aggravating factor** for V-5 in that it maximises how wrong the field can be, but
correcting it alone does not make the field true (Q-2).

## 6. Blast Radius and Sibling Suspects

**Blast radius.**

- **F-17**: storage and index degradation on `agent_instances` only; no functional
  misbehaviour. The sweep is not inert in general — `workflow/interfaces/facade.py:116-160`
  does archive and delete `workflow_runs` rows, so the orphan predicate genuinely becomes
  true. The `RETENTION_LAST_ROWS{worker="subagent_roots"}` gauge (`retention.py:771`)
  reports a healthy `0` throughout, so the leak is silent.
- **F-42**: none while uncalled. Deleting it touches three files plus one test and no
  runtime path.
- **V-5**: audit and compliance record accuracy for an event `[R13.25]` mandates by name.
  No message is retained or deleted incorrectly. No consumer reads the field
  operationally — `retention.py:97-100` uses only `messages_deleted` and
  `attachments_objects_removed` — which is what keeps this an accuracy defect rather than
  a behavioural one, and also means changing the field's value cannot break a caller.
- **FU-3**: on a backlog exceeding 50 000 messages (100 chunks × 500), the rows left for
  the next night are an arbitrary rather than a newest-first remainder.

**Sibling suspects.** Every chunked or limited retention/cleanup job in the codebase,
checked for both shapes — limit-before-filter, and missing `ORDER BY` with an ordering
claim attached.

| Site | Limit before filter? | `ORDER BY`? | Verdict |
|---|---|---|---|
| `backend/app/workers/tasks/retention.py:505-519` `_sweep_orphaned_subagent_roots` | **Yes** — `LIMIT` at `:512`, predicate at `:516-518` | absent | **Confirmed — F-17, in scope** |
| `backend/contexts/conversation/application/retention_service.py:52-60` `purge_once` | No — `created_at < horizon` at `:58` precedes `LIMIT` at `:59` | absent | **Confirmed — FU-3/V-5, in scope.** The only cleared-on-shape-A site that also carries an ordering *assertion* (`:104`), which is what makes its missing `ORDER BY` a defect rather than a shrug |
| `backend/contexts/orchestration/infrastructure/repositories.py:568-577` `delete_older_than_days` | No limit at all | n/a | **Confirmed — F-42, in scope.** The opposite failure: unbatched, so one call could delete an unbounded number of rows in one transaction |
| `backend/app/workers/tasks/retention.py:473-482` `_purge_agent_instances` | **Cleared** — the full predicate `destroyed_at IS NOT NULL AND destroyed_at < :cutoff` is repeated inside the `LIMIT 500` subquery at `:478-479` | absent | Cleared. Self-draining (deleted rows leave the eligible set) and emits no ordering claim |
| `backend/app/workers/tasks/retention.py:460-465` `_prune_idle_sessions` | **Cleared** — `WHERE last_used_at < :cutoff` inside the `LIMIT 1000` subquery at `:463` | absent | Cleared, same reasoning |
| `backend/app/workers/tasks/retention.py:631-642` `_sweep_instructions_chains` | **Cleared** — the `HAVING bool_and(...) AND max(...) < :cutoff` filter at `:636-638` is inside the `LIMIT 500` at `:639` | absent | Cleared for both shapes. Noted as FU-2: the limit can split one chain across passes, leaving a partially-deleted chain between nights |
| `backend/app/workers/tasks/retention.py:229-237, :264` `_purge_soft_deleted_tenancy` | **Cleared** — `conds` applied inside the batch select at `:264`, org ids at `:232` | **present** (`.order_by(tbl.c.id)`) | Cleared. The only sweep in the module that already orders; note the key is `id`, not `deleted_at`, so it is deterministic but not oldest-first — acceptable, since it makes no ordering claim |
| `backend/contexts/audit/application/audit_query_service.py:99-104` `purge_old_logs` | **Cleared** — `created_at < :cutoff` repeated inside the `LIMIT 1000` subquery at `:102` | absent | Cleared |
| `backend/contexts/notification/infrastructure/repositories.py:172-182` `purge_old_read` | **Cleared** — both predicates at `:176-177` precede `.limit(batch_size)` at `:180` | absent | Cleared |
| `backend/contexts/conversation/interfaces/facade.py:281-294` `purge_old_attachments` | **Cleared** — all three predicates at `:283-285` precede `.limit(500)` at `:286`, and are re-asserted on the outer `DELETE` at `:290-292` | absent | Cleared — the strictest of the group |
| `backend/contexts/workflow/interfaces/facade.py:123-143` `archive_old_runs` | **Cleared** — `WHERE wr.ended_at < :cutoff AND wr.id NOT IN (...)` at `:138-139` precedes `LIMIT 500` at `:140` | absent | Cleared. Idempotent via `ON CONFLICT DO NOTHING` (`:141`), so a re-sampled row is harmless |
| `backend/contexts/keys/interfaces/facade.py:228-253` `rollup_usage_events` | **Cleared** — `WHERE at < :cutoff` precedes `LIMIT 1000` in the `old` CTE at `:233` | absent | Cleared |
| `backend/contexts/knowledge/interfaces/facade.py:416-436` `list_pending_collection_teardowns` (driving `retention.py:373`) | **Cleared** — the cap is a post-filter `break` at `:435`, applied per candidate after the live-config check | n/a | Cleared. This is the correct shape expressed in Python rather than SQL |
| `backend/app/workers/agent_fs_gc.py:664-778` `sweep_once` | **Cleared** — no `LIMIT` anywhere. `_GC_BATCH_SIZE` (`:89`, `:293`, `:318`) chunks an `IN (...)` read over an already-complete id list; it never truncates the candidate set | n/a | Cleared. It enumerates the authoritative external store first and diffs against the DB (`:14-20`) — the inverse of the F-17 mistake |
| `retention.py:416-455` (`_expire_invites`, `_expire_oc_transfers`, `_expire_approvals`, `_purge_expired_tokens`), `:569-622` `_purge_exports_bucket`, `:657-677` `_cleanup_tus_parts`, `:680-707` `_scrub_stale_presence` | Unlimited — no `LIMIT` to misplace | n/a | Cleared trivially |

**Conclusion of the sweep:** the limit-before-filter shape appears exactly once
(`retention.py:512`). Every other limited sweep repeats its full predicate inside the
limited subquery. The missing-`ORDER BY` shape is near-universal but harmless everywhere
except `retention_service.py:52-60`, because every other site is self-draining (the rows
it takes are the rows it deletes) and none of them publishes a claim about what it left
behind. That distinction — self-draining plus no ordering claim — is the reason those
sites are cleared rather than fixed, and it is worth carrying into any future sweep.

## 7. Fix Design

### 7.1 F-17 — put the predicate above the limit

Rewrite the candidate query at `backend/app/workers/tasks/retention.py:505-519` so the
orphan test and the shape test live in the same `WHERE`, with the limit applied to the
already-eligible set and a deterministic order:

```sql
SELECT ai.id
FROM agent_instances ai
WHERE ai.parent_id IS NULL
  AND ai.run_context->>'synthetic_root' = 'true'
  AND ai.run_context->>'workflow_run_id' IS NOT NULL
  AND NOT EXISTS (
    SELECT 1 FROM workflow_runs wr
    WHERE wr.id = (ai.run_context->>'workflow_run_id')::uuid
  )
ORDER BY ai.spawned_at
LIMIT 500
```

**Why this corrects rather than masks.** The symptom is "eligible rows past the sample
are never reached". A masking fix would raise the limit, or add an offset cursor, or loop
— each of which merely widens or walks the sample while leaving the truncate-then-filter
inversion in place, and each of which reintroduces the bug the moment the population
outgrows the new bound. Making the limit operate on the eligible set instead removes the
inversion: every row the query returns is an orphan, so every pass makes exactly 500 rows
of progress and the backlog is guaranteed to drain. `ORDER BY spawned_at` additionally
makes the drain oldest-first and reproducible, which is what lets an operator watch
`RETENTION_LAST_ROWS{worker="subagent_roots"}` fall to zero and trust it.

The `::uuid` cast is carried over unchanged from `:517` (Q-6 of §3 is not present here;
see FU-1). The children-before-roots delete order at `:526-535` is untouched —
`agent_instances.parent_id` is `ON DELETE SET NULL` (`0022_agent_instances.py:29-31`), so
reversing it would orphan the children as parentless rows, exactly as the existing
docstring at `:501-503` warns.

**Index.** `agent_instances` has indexes on `parent_id`, `agent_id`, and a partial one on
`destroyed_at` (`0022_agent_instances.py:44-53`) — none supports this predicate. New
migration `0062_retention_sweep_indexes.py` (down_revision `0061_graphrag_owner_index_live_only`):

```sql
CREATE INDEX ix_agent_instances_synthetic_root
ON agent_instances (spawned_at)
WHERE parent_id IS NULL AND run_context->>'synthetic_root' = 'true';
```

Without it the new `ORDER BY` would sort the whole synthetic-root population every night
rather than stopping at 500.

**Data repair for the existing orphan population.** No backfill script and no migration
DML (Q-5). The rows are inert dead weight, not corrupt or misleading data; nothing reads
them, and `subagent_service.py:73-76`'s get-or-create only ever looks up *alive roots for
a live run*, so a stale orphan cannot be mistakenly reused. The corrected sweep **is** the
repair: it drains 500 orphans per night, unattended, and the population stops growing
entirely once `docs/tasks/2026-07-22-subagent-spawn-fail-fast/spec.md` lands. Before
deploying, an operator can size the backlog with this read-only query and decide whether
to run the policy manually a few times rather than wait:

```sql
SELECT count(*) FROM agent_instances ai
WHERE ai.parent_id IS NULL
  AND ai.run_context->>'synthetic_root' = 'true'
  AND ai.run_context->>'workflow_run_id' IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM workflow_runs wr
                  WHERE wr.id = (ai.run_context->>'workflow_run_id')::uuid);
```

### 7.2 F-42 — delete the second implementation

Remove, in one commit:

- `backend/contexts/orchestration/application/subagent_service.py:320-321` (`cleanup_expired`)
- `backend/contexts/orchestration/interfaces/facade.py:347-348` (`cleanup_expired_instances`)
- `backend/contexts/orchestration/infrastructure/repositories.py:568-577` (`delete_older_than_days`)
- `backend/tests/unit/test_orchestration_services.py:801-807` (`test_cleanup_expired`)

`AgentInstanceRepository` (`repositories.py:391`) is a concrete class with no Protocol or
ABC declaring this method — grep for `Protocol` in `backend/contexts/orchestration/`
returns nothing — so no interface declaration needs updating alongside it.

**Why this corrects rather than masks.** Aligning the three divergences (midnight
truncation, missing `LIMIT`, bare `rowcount`) would leave two correct implementations of
one requirement, which is the condition that produced the divergence in the first place
and would produce it again on the next edit to either. `[R15.21]` describes one rule;
`retention.py:471-485` already implements it, is wired into `_POLICIES` at `:743`, and is
test-covered at `test_retention_deep.py:457-473`. Deleting the unreachable twin is the
only change that makes the rule unambiguous. No data repair applies — the code never ran.

### 7.3 V-5 + FU-3 — order the victims, and report what actually survived

Two changes in `backend/contexts/conversation/application/retention_service.py`:

1. **`:52-60` — order the victim select.** Add `.order_by(t.messages.c.created_at, t.messages.c.id)`
   before `.limit(PURGE_CHUNK)`. The `id` tiebreak matters: `created_at` is not unique, and
   an `ORDER BY` on a non-unique key with `LIMIT` is exactly the non-reproducibility
   recorded as V-6 in the same audit. This makes each chunk take the genuinely oldest 500
   and makes partial progress monotone.

2. **`:95-113` — compute `oldest_kept_at` after the delete, per room and globally.** After
   the `DELETE` at `:91-93` and within the same transaction, run one aggregate restricted
   to the affected rooms:

   ```python
   sa.select(t.messages.c.chatroom_id, sa.func.min(t.messages.c.created_at))
     .where(t.messages.c.chatroom_id.in_(by_room.keys()))
     .group_by(t.messages.c.chatroom_id)
   ```

   Emit each room's own value in its event at `:104` — a room the chunk emptied has no
   surviving row and correctly reports `oldest_kept_at: null`. Set the report's
   `oldest_kept_at` (`:112`) from a global `min(created_at)`, the identical expression the
   no-op path already uses at `:63`, so both paths return the same quantity.

**Why this corrects rather than masks.** The masking fix is the one Q-2 rejects: add
`ORDER BY` and declare the field fixed because the value is now "more nearly true". It is
not true — after deleting the oldest 500, the oldest survivor is still older than
`horizon`, so a non-final chunk still emits a false floor, just a less wrong one. The
alternative masking fix — renaming the field to `horizon` — would violate `[R13.25]`,
which names `oldest_kept_at` explicitly. Recomputing it removes the defect at its source:
the field then reports an observed fact rather than an unverified assertion, means the
same thing on both code paths, and is per-room in a per-room event. Ordering ships
alongside because it is a real defect in its own right (FU-3) and because it makes each
chunk's recomputed floor advance monotonically, which is what an audit reader expects
from a sequence of purge events.

**Data repair.** None possible and none warranted. The already-written audit rows are
`audit_logs` entries, which are append-only and protected by a trigger requiring
`SET ROLE smap_audit_retention` to bypass
(`backend/contexts/audit/application/audit_query_service.py:90, :97`). Rewriting a
compliance trail to correct it is worse than leaving it: any pre-fix
`message.purged_by_retention` row should be read as "at least `count` messages older than
`oldest_kept_at` were deleted from this room", which is true, rather than as a floor
assertion. If §11's SRS note is adopted this reading is documented rather than folklore.

**Cost.** Two extra queries per chunk (one grouped `min`, one global `min`), up to 100
chunks per night. See Q-4 for the `messages(created_at)` index that makes these — and the
already-nightly unindexed `min` at `:63` — index reads instead of full scans.

## 8. Regression Test Plan

Failing tests first, in this order. `/build` writes each, watches it fail for the stated
reason, then applies the corresponding fix from §7.

### T-1 — F-17, plan-independent (unit)

**File:** `backend/tests/unit/test_retention_deep.py`, new method in the existing
`TestSweepOrphanedSubagentRoots` class (`:476`).
**`test_candidate_query_filters_before_limit`** — mock `session.execute` as
`test_deletes_children_before_roots` does at `:481-488`, take
`str(session.execute.call_args_list[0][0][0])`, and assert:
`sql.index("NOT EXISTS") < sql.index("LIMIT")`, and `"ORDER BY" in sql`.
**Why it fails today:** `retention.py:512` places `LIMIT 500` inside the CTE, textually
*before* the `NOT EXISTS` at `:516-518`, so the index comparison is false; and no
`ORDER BY` appears anywhere in the statement. This assertion style matches the file's
existing convention (`:471-473`).

### T-2 — F-17, behavioural proof (integration, `pytest.mark.db`)

**File:** `backend/tests/integration/test_retention_subagent_root_sweep.py` (new),
modelled on `backend/tests/integration/test_retention_restore_barrier.py` — same
`pytestmark = pytest.mark.db` (`:31`) and the same id-tracked cleanup helper pattern
(`:38-50`).
**`test_orphans_beyond_the_limit_are_reaped`** — insert 1000 synthetic roots pointing at
a live `workflow_runs` row, then 20 pointing at a `workflow_run_id` with no row, then call
`_sweep_orphaned_subagent_roots`. Assert the return value is `20` and that a follow-up
`SELECT count(*)` over the orphan predicate returns `0`.
**Why it fails today:** the unordered `LIMIT 500` at `retention.py:512` is satisfied by the
first 500 rows the scan yields — all live — so the outer `NOT EXISTS` matches nothing and
the function returns `0`. Fragility stated openly: today's outcome is plan-dependent by
construction, which is the defect itself; inserting the live rows first makes the seq-scan
prefix deterministic in practice. T-1 is the plan-independent companion, which is why both
ship.
**`test_children_are_deleted_before_roots`** — insert one orphan root with two children;
assert both children and the root are gone and no row is left with `parent_id IS NULL` and
a non-synthetic `run_context`. Passes today; it is the characterization guard that the
rewrite must not regress the `ON DELETE SET NULL` ordering (`retention.py:501-503`).

### T-3 — F-42 (unit)

**File:** `backend/tests/unit/test_retention_deep.py`, new class
`TestSingleAgentInstanceRetentionPath`.
**`test_no_second_agent_instance_retention_implementation`** — import
`OrchestrationFacade`, `SubagentService` and `AgentInstanceRepository` and assert
`not hasattr(..., "cleanup_expired_instances")`, `not hasattr(..., "cleanup_expired")`,
`not hasattr(..., "delete_older_than_days")`.
**Why it fails today:** all three exist at `facade.py:347`, `subagent_service.py:320` and
`repositories.py:568`. The test is the executable form of `[R15.21]`'s "one 30-day rule"
and will fail again if anyone reintroduces a second path. `test_cleanup_expired`
(`test_orchestration_services.py:801-807`) is deleted in the same commit.

### T-4 — V-5, report field (unit)

**File:** `backend/tests/unit/test_retention_deep.py`, `TestRetentionServicePurgeOnce` (`:32`).
**`test_purge_reports_surviving_min_not_horizon`** — drive `db.execute.side_effect` with
the victim select, the attachment select, the delete, then a post-delete grouped `min`
and a global `min` returning `datetime(2019, 1, 1, tzinfo=UTC)`. Assert
`report.oldest_kept_at == datetime(2019, 1, 1, tzinfo=UTC)`.
**Why it fails today:** `retention_service.py:112` returns `horizon` — `_NOW - RETENTION`
(`test_retention_deep.py:24`, `retention_service.py:27,49`) — and the function issues no
post-delete query at all, so the mocked side-effect entries are never consumed.

### T-5 — V-5, per-room audit value (unit)

**File:** same class.
**`test_purge_audit_carries_each_rooms_own_oldest`** — two rooms in one chunk (extend the
fixture at `:68-73`) with different surviving minima. Assert the two emitted events carry
*different* `oldest_kept_at` values, each equal to its own room's post-delete minimum.
**Why it fails today:** `retention_service.py:104` writes `horizon.isoformat()` on every
event, so both values are identical and neither is room-specific — the exact
`{chatroom_id, count, oldest_kept_at}` triple `[R13.25]` requires is not being produced.

### T-6 — V-5, emptied room (unit)

**File:** same class.
**`test_emptied_room_reports_null_oldest_kept`** — one room whose every message is in the
chunk; the post-delete grouped `min` returns no row for it. Assert the event's
`metadata["oldest_kept_at"] is None`.
**Why it fails today:** `:104` emits `horizon.isoformat()`, asserting a floor for a room
that now holds nothing.

### T-7 — FU-3, oldest-first ordering (unit)

**File:** same class.
**`test_purge_selects_oldest_first`** — compile the victim select from
`db.execute.call_args_list[0][0][0]` and assert `"ORDER BY"` is present and that both
`created_at` and `id` appear in the ordering clause.
**Why it fails today:** `retention_service.py:52-60` chains `.where(...).limit(...)` with
no `order_by`, so the compiled SQL contains no `ORDER BY` at all.

### T-8 — no-op path unchanged (existing, must keep passing)

`test_purge_empty_returns_oldest_kept` (`test_retention_deep.py:88-104`) and
`test_purge_uses_correct_horizon` (`:128-144`) pin the no-op path's `min(created_at)`
semantics. They must pass unmodified after the fix — that they already assert the
*correct* meaning of the field is the strongest evidence the purge path is the wrong half.

## 9. Risks and Rollback

**Risks.**

- **F-17 rewrite reaps more than intended.** The corrected query returns up to 500 rows
  where today it typically returns zero, and each root deletion cascades to its children
  (`retention.py:526-530`). If the orphan predicate were wrong, the fix would turn a
  harmless leak into data loss. Mitigation: the predicate is carried over verbatim from
  `:516-518` — the fix moves it, it does not restate it — and T-2's live-root arm proves
  no row belonging to a live `workflow_runs` is touched.
- **Backlog drain surprises an operator.** The first corrected run may delete 500 rows and
  the `subagent_roots` gauge jumps from 0 to 500 for many consecutive nights. This is the
  fix working. §7.1's sizing query lets an operator predict it before deploying.
- **`ORDER BY` cost on `messages`.** Covered by Q-4. If the index is declined, the purge
  gains a full scan per chunk — bounded by the fact that it only runs when rows past the
  5-year horizon exist, and mitigated by the fact that `:63` already scans nightly.
- **`oldest_kept_at` type change.** The field can now be `null` for an emptied room
  (T-6). No operational consumer reads it (`retention.py:97-100`), and JSONB represents
  null natively, so the risk is confined to any external log-analytics query that assumes
  a string. Worth a line in the release note.
- **F-42 deletion breaks an unknown caller.** Grep says there is none (§2). `mypy .` and
  `pytest -q` are the backstop.

**Rollback.** All three fixes are code-only and independently revertible:

- F-17: revert the query in `retention.py:505-519`. The migration's index is additive and
  can stay; if it must go, `DROP INDEX IF EXISTS ix_agent_instances_synthetic_root` is
  safe at any time. Rows already reaped are gone — but they were unreferenced dead weight,
  so there is nothing to restore.
- F-42: revert the deletion. No state involved.
- V-5/FU-3: revert `retention_service.py`. Audit rows written under the fix stay correct;
  reverting simply resumes writing the old, weaker value.

Because the whole change is exercised by one nightly cron
(`backend/app/workers/main.py:318`, 03:30), a bad deploy has a full day of observation
before it can act twice.

## 10. Acceptance Criteria

- [ ] **AC-1** Every regression test in §8 (T-1 through T-7) is written first, fails
  against current code for the stated reason, and passes after the fix; T-8's existing
  tests pass unmodified.
- [ ] **AC-2** `_sweep_orphaned_subagent_roots` applies its orphan predicate and its
  `LIMIT` in one `WHERE`/`LIMIT` pair, with the predicate evaluated before truncation,
  ordered by `spawned_at`.
- [ ] **AC-3** With 1000 live synthetic roots and 20 orphans present, one call to
  `_sweep_orphaned_subagent_roots` reaps all 20 and no live-run root (T-2).
- [ ] **AC-4** Children are still deleted before roots; no `agent_instances` row is left
  with `parent_id` nulled by this sweep.
- [ ] **AC-5** Migration `0062_retention_sweep_indexes.py` creates
  `ix_agent_instances_synthetic_root`, applies cleanly with `alembic upgrade head` from
  `0061_graphrag_owner_index_live_only`, and downgrades cleanly.
- [ ] **AC-6** `SubagentService.cleanup_expired`, `OrchestrationFacade.cleanup_expired_instances`,
  `AgentInstanceRepository.delete_older_than_days` and `test_cleanup_expired` no longer
  exist; a repo-wide grep for all three names returns nothing.
- [ ] **AC-7** `_purge_agent_instances` (`retention.py:471-485`) remains the sole
  `agent_instances` 30-day retention path, and its existing test at
  `test_retention_deep.py:457-473` still passes.
- [ ] **AC-8** `RetentionService.purge_once` selects victims ordered by
  `(created_at, id)` ascending.
- [ ] **AC-9** On the purge path, `PurgeReport.oldest_kept_at` is the post-delete global
  `min(messages.created_at)` — the same quantity the no-op path at `:63-64` returns — and
  is never `horizon`.
- [ ] **AC-10** Each `message.purged_by_retention` event carries that chatroom's own
  post-delete `min(created_at)`, or `null` if the chunk emptied the room.
- [ ] **AC-11** Q-4 is answered by the user and the answer is recorded in §12; if yes,
  migration 0062 also creates `ix_messages_created_at`.
- [ ] **AC-12** Definition of Done: `pytest -q`, `ruff check . && ruff format --check .`,
  `mypy .` all clean in `backend/`.

## 11. SRS Delta

**Two small corrections, both to `[R13.25]` (`REQUIREMENTS.md:702`), which this analysis
showed to be under-specified rather than wrong.**

1. `[R13.25]` names `oldest_kept_at` but never defines it, which is how one field came to
   carry two meanings. Proposed addition: "`oldest_kept_at` is the `created_at` of the
   oldest message the chatroom still holds **after** the purge, or `null` if the purge
   left the room empty. It is an observed value, never the retention horizon."
2. `[R13.25]` states no ordering contract for a chunked purge. Proposed addition: "A purge
   that cannot complete in one pass deletes oldest-first, so partial progress is monotone
   and each event's `oldest_kept_at` advances."

`[R15.21]` (`:794`) needs no change — it already states one 30-day rule; the code had two.

## 12. Deviation Log

Appended by `/build`.

## 13. Follow-ups

- **FU-1** — `retention.py:517`'s `s.wf_run_id::uuid` cast (carried into the rewritten
  query) aborts the whole `subagent_roots` policy if any `run_context.workflow_run_id`
  is not a parseable uuid. Every value is written canonically by
  `subagent_service.py:86`, so this is defence-in-depth, not a live defect. A
  `~ '^[0-9a-f-]{36}$'` guard or a `wr.id::text` comparison would close it; the latter
  costs the PK index.
- **FU-2** — `_sweep_instructions_chains` (`retention.py:631-642`) is cleared for both
  defect shapes in §6, but its `LIMIT 500` sits on `instructions` rows rather than on
  `chain_id`, so one chain can be split across nightly passes, leaving a partially-deleted
  chain visible in between. Bounded and self-healing; worth tightening to whole chains.
- **FU-3** — `_purge_soft_deleted_tenancy` orders by `id` (`retention.py:232, :264`),
  which is deterministic but not oldest-first. It makes no ordering claim so nothing is
  wrong today; if it ever gains an audit field describing what it left behind, it acquires
  V-5's shape.
- **FU-4** — the `subagent_roots` policy reports `rows_affected: 0` (`retention.py:523`)
  identically for "nothing to do" and "the query could not reach anything", which is why
  F-17 survived undetected. The same critique `agent_fs_gc.py:758-764` already applies to
  itself. Consider emitting an eligible-but-unreaped count alongside the reaped count for
  every capped policy, so a capped pass is distinguishable from an idle one.
- **FU-5** — `docs/audits/2026-07-22-conversation-verification-gap/findings.md:491-495`
  (that audit's FU-2): the retention purge never emits `message.deleted`, while
  `frontend/src/slices/conversation/utils/mergeMessages.ts:10-11` documents that
  out-of-window deletions arrive via that event. Same function, adjacent concern,
  deliberately out of scope here — it is a contract gap, not a chunking defect.
- **FU-6** — if Q-4 is declined, revisit `ix_messages_created_at` the first time a
  deployment accumulates messages past the 5-year horizon; both the no-op `min` at
  `retention_service.py:63` and the new `ORDER BY` become full scans at that point.
</content>
