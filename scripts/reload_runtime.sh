#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(dirname "$0")"
REPO_ROOT="$(realpath "$SCRIPT_DIR/..")"

cd "$REPO_ROOT"

docker compose up -d --build api worker
