#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

source .env || true

DB_FILE="${ROOT_DIR}/instance/fridge.db"
BACKUP_DIR="${ROOT_DIR}/data/backups"
mkdir -p "${BACKUP_DIR}"

if [[ ! -f "${DB_FILE}" ]]; then
  echo "[backup] sqlite db not found: ${DB_FILE}"
  exit 1
fi

TS="$(date +%F_%H%M%S)"
OUT="${BACKUP_DIR}/fridge_sqlite_${TS}.db.gz"

gzip -c "${DB_FILE}" > "${OUT}"
echo "[backup] created: ${OUT}"

RETENTION="${BACKUP_RETENTION_DAYS:-14}"
find "${BACKUP_DIR}" -name "fridge_sqlite_*.db.gz" -mtime +"${RETENTION}" -delete
echo "[backup] retention cleanup complete (days=${RETENTION})"

