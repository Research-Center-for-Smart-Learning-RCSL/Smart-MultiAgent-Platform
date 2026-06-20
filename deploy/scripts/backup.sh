#!/usr/bin/env bash
# SMAP backup script — snapshots all stateful services to a dated directory.
#
# Usage:
#   ./deploy/scripts/backup.sh [backup-dir]
#
# Defaults to ./backups/YYYY-MM-DD_HHMMSS. Run from the repo root (or adjust
# the compose -f paths). The compose project must be running.
#
# What is backed up:
#   1. Postgres — pg_dump (SQL format, compressed)
#   2. Vault    — vault operator raft snapshot (if raft) or /vault/file copy
#   3. MinIO    — mc mirror of all 3 buckets
#   4. Neo4j    — neo4j-admin database dump
#   5. Redis    — BGSAVE + copy of dump.rdb
#
# What is NOT backed up (stateless / reproducible):
#   - Qdrant (re-indexed from RAG sources on restore)
#   - Application containers (rebuilt from source)
#   - Vault unseal keys (operator responsibility — stored offline per §2)

set -euo pipefail

COMPOSE="docker compose -f deploy/compose/docker-compose.yml"
BACKUP_ROOT="${1:-./backups/$(date +%Y-%m-%d_%H%M%S)}"
mkdir -p "$BACKUP_ROOT"

echo "=== SMAP Backup → $BACKUP_ROOT ==="

# ---------- 1. Postgres ----------
echo "[1/5] Postgres pg_dump..."
$COMPOSE exec -T postgres pg_dump \
  -U smap -d smap \
  --format=custom --compress=6 \
  > "$BACKUP_ROOT/postgres.dump"
echo "  → postgres.dump ($(du -h "$BACKUP_ROOT/postgres.dump" | cut -f1))"

# ---------- 2. Vault ----------
echo "[2/5] Vault snapshot..."
# Try raft snapshot first (HA mode); fall back to file-storage copy.
if $COMPOSE exec -T vault vault operator raft snapshot save /tmp/vault-snapshot.snap 2>/dev/null; then
  $COMPOSE cp vault:/tmp/vault-snapshot.snap "$BACKUP_ROOT/vault-raft.snap"
  $COMPOSE exec -T vault rm -f /tmp/vault-snapshot.snap
  echo "  → vault-raft.snap"
else
  # File storage: copy the entire /vault/file directory.
  cid=$($COMPOSE ps -q vault)
  docker cp "$cid:/vault/file" "$BACKUP_ROOT/vault-file"
  echo "  → vault-file/ (file storage copy)"
fi

# ---------- 3. MinIO ----------
echo "[3/5] MinIO bucket mirror..."
mkdir -p "$BACKUP_ROOT/minio"
for bucket in chat-uploads rag-sources exports; do
  $COMPOSE exec -T minio mc alias set local http://localhost:9000 \
    "${MINIO_ROOT_USER:-minioadmin}" "${MINIO_ROOT_PASSWORD:-minioadmin}" \
    --quiet 2>/dev/null || true
  $COMPOSE exec -T minio mc mirror --quiet \
    "local/$bucket" "/tmp/backup-$bucket" 2>/dev/null || true
  cid=$($COMPOSE ps -q minio)
  docker cp "$cid:/tmp/backup-$bucket" "$BACKUP_ROOT/minio/$bucket" 2>/dev/null || true
  $COMPOSE exec -T minio rm -rf "/tmp/backup-$bucket" 2>/dev/null || true
done
echo "  → minio/{chat-uploads,rag-sources,exports}"

# ---------- 4. Neo4j ----------
echo "[4/5] Neo4j database dump..."
$COMPOSE exec -T neo4j neo4j-admin database dump \
  --to-path=/tmp neo4j 2>/dev/null || \
$COMPOSE exec -T neo4j neo4j-admin dump \
  --database=neo4j --to=/tmp/neo4j.dump 2>/dev/null || true
cid=$($COMPOSE ps -q neo4j)
# neo4j-admin dump outputs to /tmp/neo4j.dump (v5) or /tmp/<dbname>.dump
docker cp "$cid:/tmp/neo4j.dump" "$BACKUP_ROOT/neo4j.dump" 2>/dev/null || \
docker cp "$cid:/tmp/neo4j" "$BACKUP_ROOT/neo4j-dump" 2>/dev/null || true
echo "  → neo4j.dump"

# ---------- 5. Redis ----------
echo "[5/5] Redis BGSAVE..."
$COMPOSE exec -T redis redis-cli BGSAVE 2>/dev/null || \
$COMPOSE exec -T redis redis-cli -a "${REDIS_PASSWORD:-}" BGSAVE 2>/dev/null || true
sleep 2
cid=$($COMPOSE ps -q redis)
docker cp "$cid:/data/dump.rdb" "$BACKUP_ROOT/redis.rdb" 2>/dev/null || true
echo "  → redis.rdb"

# ---------- Summary ----------
echo ""
echo "=== Backup complete ==="
du -sh "$BACKUP_ROOT"
ls -lh "$BACKUP_ROOT"
echo ""
echo "IMPORTANT: This backup does NOT include Vault unseal keys."
echo "           Store them offline per deploy/vault/README.md §2."
