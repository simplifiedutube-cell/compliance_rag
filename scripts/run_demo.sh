#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -f .env ]; then
  echo "No .env found. Copy .env.example to .env and add your keys."; exit 1
fi

python -m index.build "$@"
echo
echo "Starting demo on http://127.0.0.1:8000"
uvicorn app.main:app --port 8000
