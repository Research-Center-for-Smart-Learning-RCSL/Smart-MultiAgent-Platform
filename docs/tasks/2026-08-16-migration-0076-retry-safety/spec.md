---
type: bugfix
status: draft
created: 2026-08-16
requirements: [O4.04, R30.02]
depends_on: []
---

# Migration 0076 half-applies and cannot be re-run, in both directions, and its own comment says otherwise

## 1. Summary

`0076_platform_activity_types` runs four transactional DDL statements, then enters an
`autocommit_block`. Alembic's block **unconditionally commits whatever transaction precedes
it**, while the `0076` version stamp is written only after `upgrade()` returns. So if the
concurrent index build or the `create_table` after the block fails, the database is left with
`scope`, a nullable `project_id` and both CHECK constraints committed while `alembic_version`
still reads `0075`. Re-running dies at the first statement with `DuplicateColumn`, and the
operator must hand-drop three objects before any retry. The comment above the block claims the
opposite ("IF NOT EXISTS makes a retry safe"), inherited from `0074` where the entire
`upgrade()` *is* the block and the claim is true.

`downgrade()` has the same defect mirrored, which the audit did not catch: it drops the index
and the opt-in table transactionally, enters a block, and a failure after that point leaves the
table **gone and committed** at version `0076`.

F-3 of `docs/audits/2026-08-16-example-activities-and-agent-packs/findings.md`, whose originally
proposed fix is now known to be a no-op (see §3 Q-1).

## 2. Observed vs Expected

**Observed - `upgrade()`, `backend/alembic/versions/0076_platform_activity_types.py:42-107`:**

| Lines | Statement | Transaction | Idempotent? |
|---|---|---|---|
| `:43-46` | `add_column("scope", Text, nullable=False, server_default 'project')` | pre-block | **No** - `DuplicateColumn` 42701 |
| `:47` | `alter_column("project_id", nullable=True)` | pre-block | Yes (`DROP NOT NULL` is a no-op) |
| `:49-53` | `create_check_constraint("ck_activity_types_scope", …)` | pre-block | **No** - `DuplicateObject` 42710 |
| `:57-61` | `create_check_constraint("ck_activity_types_project_scope", …)` | pre-block | **No** - 42710 |
| `:63-65` | the retry-safety comment | - | false for this file |
| `:66` | `with op.get_context().autocommit_block():` | **commit point** | - |
| `:67-71` | `CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS uq_activity_types_platform_key_active` | autocommit | name-idempotent only |
| `:73-98` | `create_table("project_activity_type_optins", …)` | post-block | **No** - `DuplicateTable` 42P07 |
| `:103-107` | `create_index("ix_project_activity_type_optins_type", …)` | post-block | **No** |

**Observed - `downgrade()`, `:135-149`:** `:138` guard (correctly first), `:140`
`drop_index` with no `IF EXISTS`, `:141` `drop_table`, `:143` `autocommit_block()` which
**commits `:140-141`**, `:144` `DROP INDEX CONCURRENTLY IF EXISTS`, `:146-149` two
`drop_constraint`, `alter_column` NOT NULL, `drop_column`.

**The mechanism**, from Alembic 1.13.3's own source:

- `backend/.venv/Lib/site-packages/alembic/runtime/migration.py:328-337` - entering the block
  commits the open transaction and nulls `self._transaction`. Its docstring at `:313-324` warns
  about exactly this.
- `:373-375` - exiting re-begins a transaction, so post-block DDL does commit correctly.
- `:628` then `:635` - `migration_fn()` runs, **then** `head_maintainer.update_to_step(step)`
  stamps. The stamp is strictly after the body.
- `backend/alembic/env.py:139-146` does not pass `transaction_per_migration`, so it defaults
  `False` (`migration.py:149-151`) and the whole series runs in one outer transaction
  (`env.py:145`).
- `backend/.venv/Lib/site-packages/alembic/ddl/postgresql.py:80` - `transactional_ddl = True`
  for PostgreSQL.

**Expected.** Applying 0076 either completes and stamps, or leaves the schema untouched. A
failed run is retryable with `alembic upgrade head` and no manual DDL. The same in reverse.

**Intent sources.** The migration's own comment at `:63-65`. **[O4.04]**
(`docs/operations.md:146`) is cited in the frontmatter as an intent source that this fix
*corrects* rather than satisfies - see §7.3. [R30.02] is what 0076 exists to implement and must
still hold after the fix.

## 3. Clarifications

| ID | Question | Decision | Rationale |
|---|---|---|---|
| Q-1 | The audit proposed `transaction_per_migration=True` in `env.py`. Does that fix it? | **No - it is a no-op for this defect.** Rejected. | Established from Alembic's source. Under per-migration mode `begin_transaction(_per_migration=True)` opens a real transaction per file (`migration.py:470-475`), but `autocommit_block` still commits whatever precedes it (`:333-337`) - which under that mode is *exactly* 0076's `:43-61`. Identical outcome. Alembic's recommendation of the pairing (`:313-324`) is about tuning the calling environment for short per-file migrations; it protects the *other* migrations, not the one holding the block. `findings.md` has been corrected. |
| Q-2 | How should 0076 be fixed? | **Remove both `autocommit_block`s and both `CONCURRENTLY` keywords**, making each direction a single transaction. | User decision. The decisive fact: this migration already takes `ACCESS EXCLUSIVE` on `activity_types` four times (`:43-46`, `:47`, `:49-53`, `:57-61`), a strictly stronger lock than the `ShareLock` a plain `CREATE UNIQUE INDEX` takes. `CONCURRENTLY` therefore buys nothing here - the stronger lock is already held on the same relation for the migration's duration. `activity_types` is a catalogue table (one row per type per project), and `0049_activities.py:76-80` already builds a unique index on it non-concurrently. |
| Q-3 | Should `downgrade()` be fixed too? | **Yes, in this dossier.** | Not a user question. It carries the same defect and the audit missed it. Unlike the `upgrade()` half, this one is **not** forward-looking: `downgrade()` has not run anywhere, so fixing it protects every deployment including those already past 0076. |
| Q-4 | Is the `upgrade()` fix live code or dead code? | **Dead on any deployment already stamped at 0076; live on the rest.** Ship it regardless. | Alembic never re-executes an applied revision, so editing `upgrade()` is unconditionally safe. The repo holds **no record** of which revision staging or production is at: no tags, no CD workflow (`.github/workflows/` is `ci.yml` + `ci-rerun-on-infra-failure.yml`, whose `alembic upgrade` steps target throwaway CI containers), and `deploy/README.md:140-141` and `docs/runbook-upgrade.md:115-116` both treat the live database as the source of truth. See §9 for the ops action this implies. |
| Q-5 | Should `transaction_per_migration=True` be adopted anyway? | **Not here.** Recorded as FU-1. | It is defensible hygiene aligned with upstream guidance, but it changes failure semantics for all 77 migrations (partial progress becomes durable and stamped rather than rolled back). That is an operational posture change and must not ride along as a side effect of a bugfix, especially now that Q-1 establishes it fixes nothing here. |
| Q-6 | Splitting 0076 into two revisions? | **Rejected.** | Renaming or splitting a revision breaks any database stamped at it ("Can't locate revision identified by …"), requiring a manual `UPDATE alembic_version` everywhere - and Q-4 establishes we cannot currently prove no deployment is stamped there. The append-a-0077 variant still fails on an already-migrated database at `create_table` with `DuplicateTable`. |
| Q-7 | Does any unfinished dossier conflict? | **No - `depends_on: []`.** | `docs/tasks/BOARD.md` lists `2026-07-07-graphrag-two-axis-redesign` and `2026-07-19-large-artifacts-silently-dropped`; neither touches `alembic/`. No sibling dossier from this audit edits a migration. |

## 4. Reproduction

Requires a real PostgreSQL; not reproducible in the unit tier.

1. Fresh database; `alembic upgrade 0075_activity_policies`.
2. Force the concurrent index build at `:67-71` to fail - either by cancelling it from a second
   session (`SELECT pg_cancel_backend(...)`) or by staging data that violates the target
   uniqueness.
3. `alembic upgrade head` - observe the failure.
4. `alembic current` - **observe it reports `0075_activity_policies`.**
5. `\d activity_types` - **observe `scope` is present and `project_id` is nullable.** This is
   the defect made visible: schema advanced, version not.
6. `alembic upgrade head` again - observe `DuplicateColumn` (42701) at `:43`.

**Downgrade variant.** From a database at `0076`, force a failure in `:146-149` and observe
that `project_activity_type_optins` is already dropped and committed while `alembic current`
still reports `0076`; a retry then dies at `:140` with `undefined_object` (42704).

## 5. Root Cause Analysis

1. **Root cause.** Statements exist outside the `autocommit_block` in both directions
   (`:43-61` before, `:73-107` after; `:140-141` before, `:146-149` after). Because the block
   commits what precedes it and the version stamp lands only after the whole body returns, any
   failure after the block's commit point leaves committed schema at an earlier stamped
   version. Removing the block (Q-2) removes the commit point and therefore the root cause.
2. `CONCURRENTLY` is the reason the block exists at all - a concurrent index build cannot run
   inside a transaction. It was adopted without its premise: `0074_activity_admin_listing_indexes.py:36-39`
   justifies `CONCURRENTLY` on `activity_types` because `activity_submissions` traffic "writes
   to these tables' neighbours", and 0074 takes no `ACCESS EXCLUSIVE` at all, so the choice is
   coherent there. 0076 cited `0074`/`0071` (`:63`) and copied the idiom while taking
   `ACCESS EXCLUSIVE` four times, which makes the concurrency pointless.
3. **The comment is inherited, not authored.** `0071_retention_sweep_indexes.py:41`,
   `0072_message_turn_job_idempotency.py:45` and `0074:41` each make the whole `upgrade()` body
   the block, so "IF NOT EXISTS makes a retry safe" is true in all three. A repo-wide grep for
   `autocommit_block` returns exactly those four files, and **0076 is the only one with
   statements outside the block in either direction** - confirming the audit's claim.
4. **An aggravating factor that is itself a defect**: a failed `CREATE UNIQUE INDEX
   CONCURRENTLY` leaves an **INVALID** index that enforces nothing while blocking re-creation
   under the same name. `0072:37-40` states this hazard explicitly ("for a UNIQUE index that is
   worth saying twice, because an invalid unique index enforces nothing"). Under Q-2's fix the
   hazard disappears by construction.

**The documented rule that caused this.** `docs/operations.md:146` ([O4.04]) says migrations
using concurrent index creation "MUST be marked `transactional_ddl = False`", and
`backend/alembic.ini:17-19` repeats it while asserting that env.py "keeps transaction-per-migration
semantics". Both are wrong: Alembic has **no per-revision `transactional_ddl` marker** (it is a
`MigrationContext.configure()` option / dialect attribute, `ddl/impl.py:94`, `:109`,
`ddl/postgresql.py:80`), no migration in the tree sets it, and `env.py:139-146` does not enable
per-migration transactions. A rule naming a mechanism that does not exist is how 0076 came to be
written this way.

## 6. Blast Radius and Sibling Suspects

**Blast radius.** Any deployment applying 0076 where anything after `:66` fails: a wedged deploy
requiring manual DDL surgery, with the migration's own comment telling the operator they will
not need it. Plus every deployment for the `downgrade()` half, which has not run anywhere.

Bounded by the fact that a successful application is unaffected: the schema 0076 produces is
identical before and after this fix. No data, no application behaviour, no API.

**Sibling suspects** - every `autocommit_block` user:

| File | Verdict |
|---|---|
| `0071_retention_sweep_indexes.py:41`, `:53` | **cleared** - the entire `upgrade()` and `downgrade()` bodies are the block; nothing precedes or follows. Retry claim holds. |
| `0072_message_turn_job_idempotency.py:45`, `:54` | **cleared** - same shape. Its `:37-40` INVALID-index comment is the best statement of the hazard in the tree. |
| `0074_activity_admin_listing_indexes.py:41`, `:55` | **cleared** - same shape, and its `CONCURRENTLY` premise is coherent (no `ACCESS EXCLUSIVE` taken). |
| `0076_platform_activity_types.py:66`, `:143` | **confirmed**, both directions. |

Repo-wide grep returns no fifth user. **Not fixed here**: 0071 already breaks the
all-or-nothing property of a fresh install, since its block commits 0000-0070. That is a
property of the `env.py` posture, not of 0071, and belongs to FU-1.

## 7. Fix Design

**7.1 `upgrade()`.** Delete `with op.get_context().autocommit_block():` at `:66` and the
`CONCURRENTLY` keyword at `:67-71`, dedenting the index creation into the migration's single
transaction. Keep `IF NOT EXISTS`, and keep the **index name and predicate byte-identical** -
`uq_activity_types_platform_key_active ON activity_types (key) WHERE project_id IS NULL AND
deleted_at IS NULL`. This is load-bearing:
`backend/contexts/activities/infrastructure/repositories/type_repo.py:111` and `:115`
string-match the index names to raise `ActivityTypeKeyConflict` rather than a raw 500, and
`backend/tests/unit/test_activity_repos.py:358` asserts on the platform name.

**7.2 `downgrade()`.** The same edit at `:143-144`. The `assert_no_platform_types` guard at
`:138` stays exactly where it is - first, before any DDL - and its module-level signature
taking an explicit bind (`:110-121`) is unchanged, since
`backend/tests/integration/test_platform_activity_type_schema.py` calls it directly.

**7.3 Correct the false comments and the stale rule.**

- `:63-65` - the retry-safety comment becomes a statement of why this migration is a single
  transaction: the four `ACCESS EXCLUSIVE` locks it already takes make `CONCURRENTLY`
  pointless, and `activity_types` is a catalogue table. Without this the next author will
  restore `CONCURRENTLY` to match `0071`/`0072`/`0074`.
- `docs/operations.md:146` ([O4.04]) - correct the prescription. Alembic has no per-revision
  `transactional_ddl` marker; the real mechanism is `op.get_context().autocommit_block()`, and
  the rule must state the constraint 0076 violated: **no statement may precede an autocommit
  block within a migration body**, because the block commits what came before it while the
  version stamp lands only after the body returns.
- `backend/alembic.ini:17-19` - remove the claim that env.py "keeps transaction-per-migration
  semantics" (it does not) and the reference to the nonexistent per-revision marker.

**Why this does not mask the symptom.** The symptom is committed schema at an earlier stamped
version; the cause is a commit point inside the migration body. Removing the commit point
removes the class, not the instance - which is why the structural test in §8.3 is worth more
than a test of 0076 specifically.

**Data repair.** None for a successful deployment. A deployment currently wedged mid-0076 is
repaired by the manual DDL the current code forces, which this fix prevents for the future.

## 8. Regression Test Plan

**8.1 The failing test - atomicity.** New `db`-tier test using the in-repo pattern from
`backend/tests/integration/test_egress_allowlist_backfill_migration.py:26-46`
(`MigrationContext.configure(sync_conn)` + `Operations.context(ctx)`, module loaded by path).
Monkeypatch `op.create_table` to raise, run `upgrade()` inside a transaction, and assert both
that the exception propagates and that `scope` does **not** exist on `activity_types`
afterwards. Fails today (the column survives, committed by the block); passes after.

**Must run against a scratch database, not the shared `db`-tier database.**
`test_platform_activity_type_schema.py:199-204` explicitly refuses to execute real DDL for this
reason: "executing the real DDL would tear down the schema every other test in this job is
running against." The new test needs its own database or schema.

**8.2 The downgrade half.** The symmetric assertion: force a failure in `:146-149` and assert
`project_activity_type_optins` still exists.

**8.3 The structural test - the most durable artifact here.** A **unit**-tier test (no database)
that parses every file in `backend/alembic/versions/` and asserts that no statement precedes an
`autocommit_block` within `upgrade()` or `downgrade()`. This catches the class rather than the
instance, needs no PostgreSQL, and is the check that would have failed 0076 at authoring time.
Given [O4.04]'s staleness (§7.3), it is the only mechanism that will actually hold the rule.

**8.4 Existing tests that must stay green, unmodified.**
`backend/tests/integration/test_platform_activity_type_schema.py` end to end - it is the pin
that 0076's *resulting schema* is correct, and this fix must not change that schema. In
particular `test_two_live_platform_types_cannot_share_a_key` (`:160-171`) exercises the index
this fix rebuilds non-concurrently, and `backend/tests/unit/test_activity_repos.py:347-374`
string-matches the index name.

**8.5 Not automatable, and stated as such.** Simulating a mid-`CREATE INDEX CONCURRENTLY`
failure - the actual trigger - requires cancelling a live build from a second session. It is not
reasonably testable in CI. The manual procedure is §4; run it once against a real PostgreSQL and
record the result in the deviation log.

## 9. Risks and Rollback

- **Ops prerequisite, not optional.** Run `alembic current` against staging and production
  before merging (`docs/runbook-upgrade.md:40`, `:115-116`). Nothing in the repo answers where
  they are stamped (Q-4). The result does not change what is built - the `downgrade()` half
  matters everywhere regardless - but it determines whether the `upgrade()` half is live code
  or dead code on those hosts, and it should be recorded in the deviation log so the next
  reader does not have to ask again.
- **The lock argument rests on `activity_types` staying a small catalogue.** If it ever grows
  to millions of rows, a non-concurrent unique index build becomes a real outage. The
  assumption is sound today (one row per type per project; the audit reasons about ">200 live
  types across all projects" as the notable case) but is not pinned by any requirement. The
  §7.3 comment must tie the choice to that assumption so it is re-examined rather than
  inherited, exactly as the `CONCURRENTLY` idiom was.
- **During the migration, reads and writes to `activity_types` block.** They already do, at
  `:43`, `:47`, `:49` and `:57`. Reads of `activity_sessions` and `activity_submissions` are
  unaffected, so a class in progress does not stall.
- **Deviating from the repo idiom.** Three sibling migrations use `CONCURRENTLY`; this one will
  not. Mitigated by §7.3's comment and by §7.3's correction of [O4.04], which is what made the
  idiom look mandatory.
- **Rollback**: `git revert`. On a deployment that has not yet applied 0076, this restores the
  defective-but-functional migration. On one that has, the `upgrade()` half is inert either
  way.

## 10. Acceptance Criteria

- [ ] AC-1: The atomicity test from §8.1 fails before the fix and passes after: a failure in
  `create_table` leaves no `scope` column on `activity_types`.
- [ ] AC-2: The symmetric downgrade test from §8.2 passes: a failure after the table drop
  leaves `project_activity_type_optins` intact.
- [ ] AC-3: `upgrade()` and `downgrade()` each contain no `autocommit_block` and no
  `CONCURRENTLY`, and each is a single transaction.
- [ ] AC-4: The index name and predicate are byte-identical to today, and
  `backend/tests/unit/test_activity_repos.py:347-374` plus
  `backend/tests/integration/test_platform_activity_type_schema.py` pass **unmodified**.
- [ ] AC-5: `assert_no_platform_types` still runs first in `downgrade()`, before any DDL, and
  still fails loudly when a platform row exists.
- [ ] AC-6: The structural test from §8.3 exists, passes for all four `autocommit_block` users
  after the fix, and is demonstrated to fail against the pre-fix 0076.
- [ ] AC-7: The comment at `:63-65` states why this migration is a single transaction and ties
  it to the catalogue-cardinality assumption; `docs/operations.md:146` and
  `backend/alembic.ini:17-19` no longer prescribe a per-revision `transactional_ddl` marker,
  and [O4.04] states the no-statement-before-a-block rule.
- [ ] AC-8: Manual verification against a real PostgreSQL per §4, with the result and the
  `alembic current` readings from §9 recorded in the deviation log.
- [ ] AC-9: Gates green: `ruff check . && ruff format --check .`, `mypy .`, `pytest -q`;
  `db` and `integration` tiers on CI, which is authoritative per the project's remote-CI rule.

## 11. SRS Delta

**Not "None" - this bugfix corrects a documented operational rule that is factually wrong.**

**Amend [O4.04]** (`docs/operations.md:146`), replacing it entirely. The current text reads:

> **[O4.04]** Index creation uses `CREATE INDEX CONCURRENTLY` wherever possible; Alembic offers
> `op.create_index(..., postgresql_concurrently=True)` in non-transactional mode. Migrations
> that use this MUST be marked `transactional_ddl = False`.

Replace with:

> - **[O4.04]** Index creation on a high-write table uses `CREATE INDEX CONCURRENTLY`, issued
>   inside `op.get_context().autocommit_block()`; there is no per-revision `transactional_ddl`
>   marker in Alembic and none is used in this repository. A migration that opens an autocommit
>   block **must place no statement before it in the same function**: the block unconditionally
>   commits the transaction that precedes it, while the revision stamp is written only after
>   the migration body returns, so any earlier statement can be committed at the previous
>   stamped version and make the migration unretryable. `CONCURRENTLY` is not used where the
>   same migration already takes `ACCESS EXCLUSIVE` on the same relation, since the weaker lock
>   buys nothing there.

[R30.02] is unchanged: the schema 0076 produces is identical before and after this fix.

## 12. Deviation Log

Appended by /build. Must record the `alembic current` readings from §9 and the §4 manual
verification result.

## 13. Follow-ups

- **FU-1**: Adopt `transaction_per_migration=True` in `backend/alembic/env.py` as a separate
  operational-posture change (Q-5). Upstream recommends it alongside autocommit blocks
  (`alembic/runtime/migration.py:320-324`), and the all-or-nothing property it would replace is
  **already broken in practice** - `0071_retention_sweep_indexes.py:41`'s block commits
  migrations 0000-0070 on a fresh install. Deciding this deliberately is worth more than
  inheriting the current default by accident.
- **FU-2**: `backend/CLAUDE.md` states the migration range as "0000-0056"; the tree is at 0076.
  It also carries no migration/transaction rule at all, which is why [O4.04] in
  `docs/operations.md` was the only guidance and went stale unnoticed. A one-line pointer from
  `backend/CLAUDE.md` to [O4.04] would put the rule where a migration author actually looks.
- **FU-3**: The repository has **no record of which migration revision any environment is
  stamped at** (Q-4). `docs/runbook-upgrade.md:220-234` has an incident-timeline template with
  a `Migrations applied:` field and no filled-in instance; `docs/release-checklist.md:42` is
  unchecked and says "30 migrations". A deploy log, a CD step that records `alembic current`,
  or a filled-in checklist would make questions like Q-4 answerable without shell access.
- **FU-4**: `docs/release-checklist.md:42` is stale ("30 migrations") and unchecked. Trivial,
  but it is the second stale migration count found by this dossier.
