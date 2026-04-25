# SMAP Alembic migrations

This tree is the single source of truth for Postgres schema evolution. Policy
is normative in `docs/operations.md` §4 — in particular **N-1 compatibility**:
every migration must run cleanly with both the previous and current
application versions in the fleet, because the single-host Docker Compose
topology performs rolling restarts rather than blue/green cutovers.

## Layout

```
backend/
  alembic.ini
  alembic/
    env.py            ← imports only shared_kernel.db + settings
    script.py.mako    ← revision scaffold
    versions/
      0000_baseline.py          ← empty stamp; Phase C lands the first real DDL
      0001–0004                 ← Phase C: identity, tenancy, invites, audit
      0005–0010                 ← Phase D: api_keys, key_projects, key_groups,
                                            key_usage_events, search_keys, rewrap_progress
      0011–0014                 ← Phase E: agents, rag, graphrag, mcp
      0015–0018                 ← Phase F: workspaces, chatrooms, messages, attachments
      0019–0022                 ← Phase G: wakeup_authored_snapshot, approvals,
                                            instructions, agent_instances
      0023–0024                 ← Phase H: workflows, workflow_runs_steps
      0025–0026                 ← Phase I: notifications, impersonation_access_jti
```

27 migration files total (0000–0026) after all phases A–I are applied.

## Commands

```bash
# Apply everything up to head.
python -m alembic upgrade head

# Stamp the baseline without running DDL (used by bootstrap for scratch envs).
python -m alembic stamp 0000_baseline

# Generate a new revision (hand-review the output!).
python -m alembic revision --autogenerate -m "identity + tenancy tables"

# CI migrations-check job — every revision must round-trip up + down + up.
python -m alembic upgrade head
python -m alembic downgrade base
python -m alembic upgrade head
```

## Policy reminders (docs/operations.md §4)

- Every migration MUST define both `upgrade()` and `downgrade()`. Intentionally
  lossy downgrades raise `RuntimeError("irreversible migration")` with a
  comment explaining why.
- Column additions start NULL-able; a follow-up revision adds NOT NULL and
  defaults after the application has been rolled.
- Index creation prefers `op.create_index(..., postgresql_concurrently=True)`
  in non-transactional mode (`transactional_ddl = False`).
- Autogenerate is a convenience. Every diff is hand-reviewed before commit.

## What lives elsewhere

- Postgres extensions (`pgvector`, `pg_cron`) are created via
  `deploy/compose/postgres/init/00-extensions.sql` **and** reasserted
  idempotently by `smap.bootstrap db-init`. They intentionally do not flow
  through Alembic: extension creation needs superuser, which the application
  role does not possess.
- Neo4j / Qdrant / MinIO provisioning lives in `smap.bootstrap` — Alembic is
  Postgres-only by design.
