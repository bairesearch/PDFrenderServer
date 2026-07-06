#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
exec python3 -m pdfrenderserver.server \
  --http \
  --host "${PDFRENDERSERVER_HOST:-127.0.0.1}" \
  --port "${PDFRENDERSERVER_PORT:-8000}" \
  --cache-dir "${PDFRENDERSERVER_CACHE_DIR:-.cache}"

