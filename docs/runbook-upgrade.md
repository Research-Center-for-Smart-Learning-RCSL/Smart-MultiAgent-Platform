# SMAP Upgrade / Downgrade Runbook

Covers rolling upgrades and emergency rollbacks for a single-host Docker Compose
deployment. Assumes the operator has SSH access and `docker compose` v2+.

---

## Prerequisites

- Current version tag noted: `git describe --tags` → e.g. `v1.0.0`
- Backup completed: `bash deploy/scripts/backup.sh`
- Preflight passes on the new code: `bash deploy/scripts/preflight.sh --prod`
- No active workflow runs in progress (check `/api/admin/workflow-runs?status=running`)

---

## 1. Upgrade (zero-downtime rolling)

### 1.1 Pull and build

```bash
cd /opt/smap                        # or wherever the repo lives
git fetch origin
git checkout v1.1.0                 # target release tag
```

### 1.2 Run database migrations

Migrations are forward-only and designed for zero-downtime (N-1 compatible):

```bash
docker compose -f deploy/compose/docker-compose.yml \
  -f deploy/compose/docker-compose.prod.yml \
  exec backend-web alembic upgrade head
```

Verify:

```bash
docker compose exec backend-web alembic current
# Should show the latest revision matching the new code.
```

### 1.3 Rebuild images

```bash
docker compose -f deploy/compose/docker-compose.yml \
  -f deploy/compose/docker-compose.prod.yml \
  build --no-cache backend-web backend-worker frontend
```

### 1.4 Rolling restart

Restart one service at a time to maintain availability:

```bash
# Frontend (stateless, fast)
docker compose -f deploy/compose/docker-compose.yml \
  -f deploy/compose/docker-compose.prod.yml \
  up -d --no-deps frontend

# Backend workers (process in-flight tasks before stopping)
docker compose -f deploy/compose/docker-compose.yml \
  -f deploy/compose/docker-compose.prod.yml \
  up -d --no-deps backend-worker

# Backend web (3 replicas — Docker rolling-updates one at a time)
docker compose -f deploy/compose/docker-compose.yml \
  -f deploy/compose/docker-compose.prod.yml \
  up -d --no-deps backend-web
```

### 1.5 Post-upgrade verification

```bash
# Health checks
curl -fsS http://localhost:10080/api/readyz | python3 -m json.tool
curl -fsS http://localhost:10080/api/healthz

# Verify version
curl -fsS http://localhost:10080/api/version
# Should return {"version": "1.1.0", ...}

# Check logs for startup errors
docker compose logs --since 2m backend-web backend-worker | grep -i error

# Verify all services healthy
docker compose ps
```

---

## 2. Downgrade (emergency rollback)

Use when a release introduces a critical production bug and fixing forward is
not viable within the incident window.

### 2.1 Decision criteria

Downgrade is safe when:
- The new release has NO irreversible migrations (check `alembic history` for
  `DROP COLUMN`, `DROP TABLE`, or data transforms without reverse).
- The failing release was deployed < 24 hours ago (limits data divergence).

Downgrade is NOT safe when:
- New migrations dropped columns/tables that old code reads.
- Significant user data was created under the new schema.

In those cases: fix forward, or restore from backup.

### 2.2 Procedure

```bash
# 1. Note the current (broken) revision
docker compose exec backend-web alembic current
# e.g. "0031_some_new_migration (head)"

# 2. Checkout the previous known-good tag
git checkout v1.0.0

# 3. Downgrade the database to match the old code
#    Target the revision that the old code expects:
docker compose exec backend-web alembic downgrade -1
#    Or to a specific revision:
#    docker compose exec backend-web alembic downgrade 0030

# 4. Rebuild and restart with old code
docker compose -f deploy/compose/docker-compose.yml \
  -f deploy/compose/docker-compose.prod.yml \
  build --no-cache backend-web backend-worker frontend

docker compose -f deploy/compose/docker-compose.yml \
  -f deploy/compose/docker-compose.prod.yml \
  up -d frontend backend-worker backend-web

# 5. Verify
curl -fsS http://localhost:10080/api/readyz
docker compose ps
```

### 2.3 Full restore from backup (last resort)

When downgrade is not safe due to irreversible migrations:

```bash
# Stop all services
docker compose -f deploy/compose/docker-compose.yml \
  -f deploy/compose/docker-compose.prod.yml down

# Restore from the pre-upgrade backup
bash deploy/scripts/restore.sh /path/to/backup/YYYYMMDD-HHMMSS

# Checkout old code and restart
git checkout v1.0.0
docker compose -f deploy/compose/docker-compose.yml \
  -f deploy/compose/docker-compose.prod.yml \
  up -d
```

---

## 3. Migration safety rules

All Alembic migrations in this project follow these invariants:

1. **N-1 compatible** — the old code can run against the new schema. This is
   achieved by: adding columns as nullable, creating new tables before code
   references them, and deferring drops to N+1.
2. **One logical change per migration** — simplifies targeted rollback.
3. **No data-destructive operations in the same release** — column drops happen
   in the release AFTER the code stops reading them.

When writing new migrations, test the round-trip:

```bash
alembic upgrade head    # apply
alembic downgrade -1    # revert
alembic upgrade head    # re-apply (must be idempotent)
```

---

## 4. Sandbox image upgrades

gVisor sandbox images (`smap/mcp-runtime`, `smap/code-exec`) are pinned by
digest. To upgrade:

```bash
# Rebuild
docker compose --profile sandbox-build build

# Record new digests
docker inspect --format='{{index .RepoDigests 0}}' smap/mcp-runtime:pinned
docker inspect --format='{{index .RepoDigests 0}}' smap/code-exec:pinned

# Update .env with new digest values
# SANDBOX_MCP_IMAGE=smap/mcp-runtime@sha256:abc...
# SANDBOX_CODE_EXEC_IMAGE=smap/code-exec@sha256:def...

# Restart backend to pick up new image references
docker compose up -d --no-deps backend-web backend-worker
```

---

## 5. Vault upgrades

Vault version upgrades require special care:

1. Take a Vault snapshot: `vault operator raft snapshot save vault-backup.snap`
   (or backup the file storage volume).
2. Stop Vault: `docker compose stop vault`
3. Update the Vault image tag in `docker-compose.yml`.
4. Start Vault: `docker compose up -d vault`
5. Vault may seal on version change — unseal with 3-of-5 keys.
6. Verify: `vault status`, check `vault version`.

---

## 6. Incident timeline template

Record for every upgrade/downgrade:

```
Upgrade: v1.0.0 → v1.1.0
Date: YYYY-MM-DD HH:MM UTC
Operator: <name>
Duration: <X> minutes
Pre-backup: /backups/YYYYMMDD-HHMMSS/
Migrations applied: 0031_xxx, 0032_yyy
Issues encountered: <none | description>
Rollback performed: yes/no
Post-verification: all green / partial (details)
```
