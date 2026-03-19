#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

echo "[deploy] backup sqlite"
bash ops/backup_sqlite.sh

echo "[deploy] pull latest code"
git pull --rebase

echo "[deploy] rebuild and restart"
docker compose up -d --build

echo "[deploy] status"
docker compose ps

