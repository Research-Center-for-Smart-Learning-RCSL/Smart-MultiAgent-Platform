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

echo "=== SMAP Restore from $BACKUP_DIR ==="
echo "WARNING: This will REPLACE all data in the running stack."
echo "Press Ctrl+C within 5 seconds to abort..."
sleep 5

# ---------- 1. Postgres ----------
if [ -f "$BACKUP_DIR/postgres.dump" ]; then
  echo "[1/5] Restoring Postgres..."
  # Drop and recreate the database, then restore.
  $COMPOSE exec -T postgres dropdb -U smap --if-exists smap
  $COMPOSE exec -T postgres createdb -U smap smap
  # pg_restore emits non-fatal warnings (e.g., "relation already exists") on
  # --clean --if-exists restores. Log stderr to a file so real errors are
  # visible, but don't let non-zero exit (common with warnings) abort the script.
  cat "$BACKUP_DIR/postgres.dump" | \
    $COMPOSE exec -T postgres pg_restore \
      -U smap -d smap --no-owner --no-privileges --clean --if-exists \
      2>"$BACKUP_DIR/pg_restore.log" || true
  if [ -s "$BACKUP_DIR/pg_restore.log" ]; then
    echo "  ⚠ pg_restore warnings/errors logged to $BACKUP_DIR/pg_restore.log"
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
    "${SMAP_MINIO_ROOT_USER:-minioadmin}" "${SMAP_MINIO_ROOT_PASSWORD:-minioadmin}" \
    --quiet 2>/dev/null || true
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
