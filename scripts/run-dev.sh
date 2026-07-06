#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

args=(
  --host "${PDFRENDERSERVER_HOST:-0.0.0.0}"
  --port "${PDFRENDERSERVER_PORT:-8443}"
  --cache-dir "${PDFRENDERSERVER_CACHE_DIR:-.cache}"
)

if [[ -n "${PDFRENDERSERVER_CERT_FILE:-}" && -n "${PDFRENDERSERVER_KEY_FILE:-}" ]]; then
  args+=(--cert-file "$PDFRENDERSERVER_CERT_FILE" --key-file "$PDFRENDERSERVER_KEY_FILE")
elif [[ -f certs/local/pdfrenderserver.crt && -f certs/local/pdfrenderserver.key ]]; then
  args+=(--cert-file certs/local/pdfrenderserver.crt --key-file certs/local/pdfrenderserver.key)
fi

exec python3 -m pdfrenderserver.server "${args[@]}"
