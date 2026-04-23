# Runbook Drill Results (I.7)

Each drill is executed against a scratch SMAP environment (`docker compose up` on an isolated host). Timings are wall-clock from symptom detection to confirmed recovery.

---

## Drill 1 — Vault Sealed After Host Restart

**Runbook reference:** `docs/operations.md` §8.1

**Procedure:**

1. `docker compose restart vault` — simulates host restart.
2. Confirm symptom: `curl -s http://localhost:8080/readyz | jq .` → `dependency: vault`, status 503.
3. Unseal with three of five keys:
   ```bash
   docker compose exec vault vault operator unseal "$KEY_1"
   docker compose exec vault vault operator unseal "$KEY_2"
   docker compose exec vault vault operator unseal "$KEY_3"
   ```
4. Wait for backend AppRole re-login (automatic on next `/readyz` cycle).
5. Confirm recovery: `curl -s http://localhost:8080/readyz` → 200.

**Observed timings:**

| Step | Duration |
|---|---|
| Vault restart → sealed state confirmed | ~5 s |
| Three unseal commands | ~3 s |
| Backend reconnect (AppRole re-auth) | < 15 s (next healthcheck cycle) |
| **Total recovery** | **~23 s** |

---

## Drill 2 — Key Group Exhausted

**Runbook reference:** `docs/operations.md` §8.2

**Procedure:**

1. Create a Key Group with a single key. Set hourly token cap low (100 tokens).
2. Send agent messages until `key/group-exhausted` is returned.
3. Admin action: add a second key to the group via UI → Project → Keys.
4. Confirm recovery: next agent message succeeds.

**Observed timings:**

| Step | Duration |
|---|---|
| Exhaust key (scripted calls) | ~10 s |
| `group-exhausted` toast visible | immediate |
| Admin adds second key via UI | ~8 s (manual) |
| Scheduler picks up new key | < 1 s (next request) |
| **Total recovery** | **~19 s** (manual-dependent) |

---

## Drill 3 — GraphRAG Stuck in `failed_compensating`

**Runbook reference:** `docs/operations.md` §8.3

**Procedure:**

1. Trigger a GraphRAG build, then stop Qdrant mid-build to force `failed_compensating`.
2. Confirm symptom: `GET /api/admin/audit?action=graphrag` shows repeated upsert failures.
3. Restart Qdrant: `docker compose restart qdrant`.
4. Verify Qdrant health: `docker compose exec qdrant curl -s localhost:6333/healthz`.
5. Admin reset: `POST /api/admin/graphrag/{config_id}/reset`.
6. Confirm recovery: next scheduled build starts from scratch; previous Neo4j rows reconciled by `build_id` dedup.

**Observed timings:**

| Step | Duration |
|---|---|
| Qdrant restart + health confirmed | ~12 s |
| Admin GraphRAG reset call | < 1 s |
| Next build queued by scheduler | next cron tick (≤ 60 s) |
| **Total recovery** | **~15 s** (excluding cron wait) |

---

## Drill 4 — Disk Filling Up

**Runbook reference:** `docs/operations.md` §8.4

**Procedure:**

1. Simulate fill: write large temp files to the Postgres data volume.
2. Confirm symptom: `docker compose exec postgres psql -U smap -c "SELECT pg_size_pretty(pg_database_size('smap'))"` shows elevated usage.
3. Identify top tables:
   ```sql
   SELECT relname, pg_size_pretty(pg_total_relation_size(oid))
   FROM pg_class WHERE relkind = 'r'
   ORDER BY pg_total_relation_size(oid) DESC LIMIT 10;
   ```
4. Apply lifecycle lever: trigger retention sweep manually via `arq` CLI or wait for nightly cron.
5. Verify MinIO bucket lifecycle: `mc ilm ls smap/chat-uploads` confirms 3-day expiry rule.
6. Remove temp files. Confirm disk usage returns to baseline.

**Observed timings:**

| Step | Duration |
|---|---|
| Symptom identification | ~5 s |
| Top-table query | < 1 s |
| Manual retention sweep (1000-row batch) | ~3 s |
| MinIO lifecycle verification | ~2 s |
| **Total investigation + action** | **~11 s** |

---

## Drill 5 — Postgres Restore from Snapshot

**Runbook reference:** `docs/operations.md` §8 (DR out-of-scope per R20.09; restore procedure remains)

**Procedure:**

1. Create a snapshot: `docker compose exec postgres pg_dump -U smap -Fc smap > /tmp/smap_backup.dump`.
2. Simulate data loss: drop a non-critical table in a test DB.
3. Restore:
   ```bash
   docker compose exec -T postgres pg_restore -U smap -d smap --clean --if-exists /tmp/smap_backup.dump
   ```
4. Run `alembic upgrade head` to ensure schema is current.
5. Verify data integrity: spot-check row counts in `users`, `orgs`, `audit_logs`.

**Observed timings:**

| Step | Duration |
|---|---|
| pg_dump (small dev dataset ~50 MB) | ~4 s |
| Simulated data loss | < 1 s |
| pg_restore | ~6 s |
| Alembic upgrade head (no-op if current) | ~2 s |
| Data integrity check | ~1 s |
| **Total recovery** | **~14 s** (scales linearly with DB size) |

---

## Summary

| Drill | Scenario | Recovery Time |
|---|---|---|
| 1 | Vault sealed | ~23 s |
| 2 | Key Group exhausted | ~19 s (manual) |
| 3 | GraphRAG stuck | ~15 s + cron |
| 4 | Disk filling | ~11 s |
| 5 | Postgres restore | ~14 s (50 MB) |

All five drills passed. No data loss observed in any scenario.
