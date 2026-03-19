#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <backup.db.gz>"
  exit 1
fi

BACKUP_FILE="$1"
if [[ ! -f "$BACKUP_FILE" ]]; then
  echo "Backup file not found: $BACKUP_FILE"
  exit 1
fi

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

mkdir -p "${ROOT_DIR}/instance"

echo "[restore] stopping services ..."
docker compose down

echo "[restore] restoring sqlite db ..."
gunzip -c "$BACKUP_FILE" > "${ROOT_DIR}/instance/fridge.db"

echo "[restore] starting services ..."
docker compose up -d --build
echo "[restore] done."

