#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
exec python3 -m pdfrenderserver.server \
  --host "${PDFRENDERSERVER_HOST:-0.0.0.0}" \
  --port "${PDFRENDERSERVER_PORT:-8443}" \
  --cache-dir "${PDFRENDERSERVER_CACHE_DIR:-.cache}"

