#!/usr/bin/env bash
# SMAP restore script — restores from a backup created by backup.sh.
#
# Usage:
#   ./deploy/scripts/restore.sh <backup-dir>
#
# Prerequisites:
#   - The compose stack MUST be running (data services healthy).
#   - Vault must be unsealed (restore does NOT restore unseal keys).
#   - This is a DESTRUCTIVE operation — it replaces all data.
#
# Restore order:
#   1. Postgres — pg_restore (drops + recreates)
#   2. Vault    — raft snapshot restore or file-storage copy
#   3. MinIO    — mc mirror from backup into buckets
#   4. Neo4j    — neo4j-admin database load
#   5. Redis    — copy dump.rdb and restart
#
# After restore:
#   - Run `alembic upgrade head` to apply any new migrations.
#   - Unseal Vault if it sealed during restore.
#   - Verify with /readyz and the Vault 7-point checklist.

set -euo pipefail

COMPOSE="docker compose -f deploy/compose/docker-compose.yml"
BACKUP_DIR="${1:?Usage: restore.sh <backup-dir>}"

if [ ! -d "$BACKUP_DIR" ]; then
  echo "ERROR: backup directory '$BACKUP_DIR' does not exist."
  exit 1
fi

# ---------- Pre-restore verification ----------
echo "Verifying backup contents in $BACKUP_DIR ..."
_found=0
_missing=0
for _check in \
  "postgres.dump:Postgres database dump" \
  "vault-raft.snap:Vault raft snapshot" \
  "minio:MinIO bucket data" \
  "neo4j.dump:Neo4j database dump" \
  "redis.rdb:Redis RDB snapshot"; do
  _file="${_check%%:*}"
  _label="${_check#*:}"
  if [ -e "$BACKUP_DIR/$_file" ]; then
    echo "  ✓ $_label ($BACKUP_DIR/$_file)"
    _found=$((_found + 1))
  else
    echo "  ✗ $_label ($BACKUP_DIR/$_file) — NOT FOUND, will be skipped"
    _missing=$((_missing + 1))
  fi
done
# Also check vault-file as an alternative to vault-raft.snap
if [ ! -f "$BACKUP_DIR/vault-raft.snap" ] && [ -d "$BACKUP_DIR/vault-file" ]; then
  echo "  ✓ Vault file storage ($BACKUP_DIR/vault-file)"
  _found=$((_found + 1))
  _missing=$((_missing - 1))
fi
# Also check neo4j-dump as an alternative to neo4j.dump
if [ ! -f "$BACKUP_DIR/neo4j.dump" ] && [ -d "$BACKUP_DIR/neo4j-dump" ]; then
  echo "  ✓ Neo4j dump directory ($BACKUP_DIR/neo4j-dump)"
  _found=$((_found + 1))
  _missing=$((_missing - 1))
fi
echo ""
if [ "$_found" -eq 0 ]; then
  echo "ERROR: No restorable backup files found in $BACKUP_DIR."
  echo "Expected at least one of: postgres.dump, vault-raft.snap, minio/, neo4j.dump, redis.rdb"
  exit 1
fi
echo "Found $_found backup(s), $_missing service(s) will be skipped."

echo ""
echo "=== SMAP Restore from $BACKUP_DIR ==="
echo "WARNING: This will REPLACE all data in the running stack."
echo "Press Ctrl+C within 5 seconds to abort..."
sleep 5

# ---------- 1. Postgres ----------
if [ -f "$BACKUP_DIR/postgres.dump" ]; then
  echo "[1/5] Restoring Postgres..."
  # Drop and recreate the database, then restore.
  if ! $COMPOSE exec -T postgres dropdb -U smap --if-exists smap; then
    echo ""
    echo "  CRITICAL: 'dropdb' failed. The existing database may have active connections."
    echo ""
    echo "  Recovery steps:"
    echo "    1. Terminate active connections:"
    echo "       $COMPOSE exec -T postgres psql -U smap -c \\"
    echo "         \"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='smap' AND pid <> pg_backend_pid();\""
    echo "    2. Re-run this restore script."
    echo ""
    exit 1
  fi
  if ! $COMPOSE exec -T postgres createdb -U smap smap; then
    echo ""
    echo "  CRITICAL: 'createdb' failed. The database was dropped but could not be recreated."
    echo ""
    echo "  Recovery steps:"
    echo "    1. Manually create the database:"
    echo "       $COMPOSE exec -T postgres createdb -U smap smap"
    echo "    2. If that fails, check Postgres logs:"
    echo "       $COMPOSE logs postgres --tail 50"
    echo "    3. Once the database exists, re-run this restore script."
    echo ""
    echo "  WARNING: The 'smap' database does NOT exist right now. The app will not work."
    exit 1
  fi
  # pg_restore emits non-fatal warnings (e.g., "relation already exists") on
  # --clean --if-exists restores. Log stderr to a file so real errors are
  # visible, but don't let non-zero exit (common with warnings) abort the script.
  cat "$BACKUP_DIR/postgres.dump" | \
    $COMPOSE exec -T postgres pg_restore \
      -U smap -d smap --no-owner --no-privileges --clean --if-exists \
      2>"$BACKUP_DIR/pg_restore.log" || true
  if [ -s "$BACKUP_DIR/pg_restore.log" ]; then
    echo "  ⚠ pg_restore warnings/errors logged to $BACKUP_DIR/pg_restore.log"
    # Check for genuinely fatal pg_restore errors (not just "already exists" warnings)
    if grep -qiE 'FATAL|could not|out of memory|no space' "$BACKUP_DIR/pg_restore.log" 2>/dev/null; then
      echo ""
      echo "  CRITICAL: pg_restore may have failed with fatal errors."
      echo "  Review: cat $BACKUP_DIR/pg_restore.log"
      echo ""
      echo "  Recovery steps:"
      echo "    1. Inspect the log for the root cause."
      echo "    2. Drop and recreate the database, then re-run this script:"
      echo "       $COMPOSE exec -T postgres dropdb -U smap --if-exists smap"
      echo "       $COMPOSE exec -T postgres createdb -U smap smap"
      echo "    3. Re-run: ./deploy/scripts/restore.sh $BACKUP_DIR"
      echo ""
      exit 1
    fi
  fi
  echo "  ✓ Postgres restored"
else
  echo "[1/5] Skipping Postgres (no postgres.dump found)"
fi

# ---------- 2. Vault ----------
if [ -f "$BACKUP_DIR/vault-raft.snap" ]; then
  echo "[2/5] Restoring Vault (raft snapshot)..."
  $COMPOSE cp "$BACKUP_DIR/vault-raft.snap" vault:/tmp/vault-restore.snap
  $COMPOSE exec -T vault vault operator raft snapshot restore \
    -force /tmp/vault-restore.snap
  $COMPOSE exec -T vault rm -f /tmp/vault-restore.snap
  echo "  ✓ Vault raft snapshot restored (unseal may be required)"
elif [ -d "$BACKUP_DIR/vault-file" ]; then
  echo "[2/5] Restoring Vault (file storage)..."
  cid=$($COMPOSE ps -q vault)
  $COMPOSE stop vault
  if ! docker cp "$BACKUP_DIR/vault-file/." "$cid:/vault/file"; then
    echo "  ERROR: docker cp failed for vault-file; restarting vault anyway"
  fi
  $COMPOSE start vault
  echo "  ✓ Vault file storage restored (unseal required)"
else
  echo "[2/5] Skipping Vault (no snapshot found)"
fi

# ---------- 3. MinIO ----------
if [ -d "$BACKUP_DIR/minio" ]; then
  echo "[3/5] Restoring MinIO buckets..."
  cid=$($COMPOSE ps -q minio)
  $COMPOSE exec -T minio mc alias set local http://localhost:9000 \
    "${SMAP_MINIO_ROOT_USER:?SMAP_MINIO_ROOT_USER must be set}" \
    "${SMAP_MINIO_ROOT_PASSWORD:?SMAP_MINIO_ROOT_PASSWORD must be set}" \
    --quiet 2>/dev/null
  for bucket in chat-uploads rag-sources exports; do
    if [ -d "$BACKUP_DIR/minio/$bucket" ]; then
      docker cp "$BACKUP_DIR/minio/$bucket" "$cid:/tmp/restore-$bucket"
      $COMPOSE exec -T minio mc mirror --overwrite --quiet \
        "/tmp/restore-$bucket" "local/$bucket" 2>/dev/null || true
      $COMPOSE exec -T minio rm -rf "/tmp/restore-$bucket" 2>/dev/null || true
    fi
  done
  echo "  ✓ MinIO buckets restored"
else
  echo "[3/5] Skipping MinIO (no minio/ directory found)"
fi

# ---------- 4. Neo4j ----------
if [ -f "$BACKUP_DIR/neo4j.dump" ] || [ -d "$BACKUP_DIR/neo4j-dump" ]; then
  echo "[4/5] Restoring Neo4j..."
  $COMPOSE stop neo4j
  cid=$($COMPOSE ps -q neo4j)
  if [ -f "$BACKUP_DIR/neo4j.dump" ]; then
    if ! docker cp "$BACKUP_DIR/neo4j.dump" "$cid:/tmp/neo4j.dump"; then
      echo "  ERROR: docker cp failed for neo4j.dump; restarting neo4j anyway"
    else
      $COMPOSE run --rm --no-deps neo4j neo4j-admin database load \
        --from-path=/tmp neo4j --overwrite-destination 2>/dev/null || \
      $COMPOSE run --rm --no-deps neo4j neo4j-admin load \
        --database=neo4j --from=/tmp/neo4j.dump --force 2>/dev/null || true
    fi
  fi
  $COMPOSE start neo4j
  echo "  ✓ Neo4j restored"
else
  echo "[4/5] Skipping Neo4j (no dump found)"
fi

# ---------- 5. Redis ----------
if [ -f "$BACKUP_DIR/redis.rdb" ]; then
  echo "[5/5] Restoring Redis..."
  $COMPOSE stop redis
  cid=$($COMPOSE ps -q redis)
  if ! docker cp "$BACKUP_DIR/redis.rdb" "$cid:/data/dump.rdb"; then
    echo "  ERROR: docker cp failed for redis.rdb; restarting redis anyway"
  fi
  $COMPOSE start redis
  echo "  ✓ Redis restored"
else
  echo "[5/5] Skipping Redis (no redis.rdb found)"
fi

# ---------- Post-restore health verification ----------
echo ""
echo "=== Verifying service health ==="
_healthy=0
_unhealthy=0

# Postgres
echo -n "  Postgres: "
if $COMPOSE exec -T postgres pg_isready -U smap -q 2>/dev/null; then
  echo "✓ ready"
  _healthy=$((_healthy + 1))
else
  echo "✗ NOT ready — check: $COMPOSE logs postgres --tail 20"
  _unhealthy=$((_unhealthy + 1))
fi

# Redis
echo -n "  Redis:    "
_redis_reply=$($COMPOSE exec -T redis redis-cli -a "${SMAP_REDIS_PASSWORD:-}" PING 2>/dev/null || echo "FAIL")
if [ "$_redis_reply" = "PONG" ]; then
  echo "✓ PONG"
  _healthy=$((_healthy + 1))
else
  echo "✗ no PONG (got: $_redis_reply) — check: $COMPOSE logs redis --tail 20"
  _unhealthy=$((_unhealthy + 1))
fi

# Vault
echo -n "  Vault:    "
_vault_status=$($COMPOSE exec -T vault vault status -format=json 2>/dev/null) && {
  _sealed=$(echo "$_vault_status" | grep -o '"sealed":[a-z]*' | head -1 | cut -d: -f2)
  if [ "$_sealed" = "false" ]; then
    echo "✓ unsealed"
    _healthy=$((_healthy + 1))
  else
    echo "⚠ sealed — unseal required before app can start"
    _unhealthy=$((_unhealthy + 1))
  fi
} || {
  echo "✗ unreachable — check: $COMPOSE logs vault --tail 20"
  _unhealthy=$((_unhealthy + 1))
}

# Neo4j
echo -n "  Neo4j:    "
if $COMPOSE exec -T neo4j cypher-shell -u neo4j -p "${SMAP_NEO4J_PASSWORD:-neo4jneo4j}" "RETURN 1" >/dev/null 2>&1; then
  echo "✓ responding"
  _healthy=$((_healthy + 1))
else
  # Neo4j may still be starting after restore — don't treat as critical
  echo "⚠ not responding yet (may still be starting)"
  _unhealthy=$((_unhealthy + 1))
fi

# MinIO
echo -n "  MinIO:    "
if $COMPOSE exec -T minio mc admin info local --json >/dev/null 2>&1; then
  echo "✓ healthy"
  _healthy=$((_healthy + 1))
else
  echo "⚠ not responding (alias may need reconfiguring)"
  _unhealthy=$((_unhealthy + 1))
fi

echo ""
if [ "$_unhealthy" -gt 0 ]; then
  echo "⚠ $_unhealthy service(s) need attention (see above)."
else
  echo "All $_healthy checked services are healthy."
fi

# ---------- Post-restore ----------
echo ""
echo "=== Restore complete ==="
echo ""
echo "Post-restore checklist:"
echo "  1. Unseal Vault if it sealed (3-of-5 keys)"
echo "  2. Run: docker compose exec backend-web python -m smap.bootstrap db-init"
echo "     (applies any new migrations since the backup)"
echo "  3. Verify: curl http://localhost:28000/readyz"
echo "  4. Run the Vault 7-point checklist (deploy/vault/README.md §7)"
echo "  5. Re-index Qdrant: the vector store is NOT included in backups."
echo "     Trigger a full re-index of RAG embeddings so search works correctly:"
echo "     docker compose exec backend-web python -m smap.rag reindex --all"
