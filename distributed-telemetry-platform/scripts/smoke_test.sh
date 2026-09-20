#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")/.."
docker compose exec -T producer python - < scripts/smoke_test.py
