#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

tls_dir="${PDFRENDERSERVER_TLS_DIR:-certs/local}"
ca_key="$tls_dir/pdfrenderserver-local-ca.key"
ca_cert="$tls_dir/pdfrenderserver-local-ca.crt"
ca_serial="$tls_dir/pdfrenderserver-local-ca.srl"
server_key="$tls_dir/pdfrenderserver.key"
server_csr="$tls_dir/pdfrenderserver.csr"
server_cert="$tls_dir/pdfrenderserver.crt"
server_ext="$tls_dir/pdfrenderserver.ext"

mkdir -p "$tls_dir"
chmod 700 "$tls_dir"

if ! command -v openssl >/dev/null 2>&1; then
  echo "openssl is required. Install it with: sudo apt-get install -y openssl" >&2
  exit 1
fi

san_entries=()

add_san() {
  local value="$1"
  local entry

  if [[ -z "$value" ]]; then
    return
  fi

  if [[ "$value" =~ ^[0-9]+(\.[0-9]+){3}$ || "$value" == *:* ]]; then
    entry="IP:$value"
  else
    entry="DNS:$value"
  fi

  for existing in "${san_entries[@]}"; do
    if [[ "$existing" == "$entry" ]]; then
      return
    fi
  done
  san_entries+=("$entry")
}

add_san "localhost"
add_san "PDFrenderServer"
add_san "127.0.0.1"

if host_name="$(hostname 2>/dev/null)"; then
  add_san "$host_name"
fi

if host_ips="$(hostname -I 2>/dev/null)"; then
  for host_ip in $host_ips; do
    add_san "$host_ip"
  done
fi

for extra_name in "$@"; do
  add_san "$extra_name"
done

if [[ ! -f "$ca_key" || ! -f "$ca_cert" ]]; then
  openssl req \
    -x509 \
    -newkey rsa:4096 \
    -nodes \
    -sha256 \
    -days 3650 \
    -keyout "$ca_key" \
    -out "$ca_cert" \
    -subj "/CN=PDFrenderServer Local CA"
  chmod 600 "$ca_key"
fi

san_csv="$(IFS=,; echo "${san_entries[*]}")"

cat > "$server_ext" <<EOF
authorityKeyIdentifier=keyid,issuer
basicConstraints=CA:FALSE
keyUsage=digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth
subjectAltName=$san_csv
EOF

openssl req \
  -new \
  -newkey rsa:2048 \
  -nodes \
  -keyout "$server_key" \
  -out "$server_csr" \
  -subj "/CN=PDFrenderServer"

openssl x509 \
  -req \
  -in "$server_csr" \
  -CA "$ca_cert" \
  -CAkey "$ca_key" \
  -CAcreateserial \
  -out "$server_cert" \
  -days 825 \
  -sha256 \
  -extfile "$server_ext"

rm -f "$server_csr"
chmod 600 "$server_key"
chmod 644 "$server_cert" "$ca_cert"
rm -f "$ca_serial"

cat <<EOF
Created local TLS files:
  CA certificate:     $ca_cert
  Server certificate: $server_cert
  Server key:         $server_key

The server certificate is valid for:
  $san_csv

To remove browser warnings on this Ubuntu machine, trust the local CA:

  sudo cp "$PWD/$ca_cert" /usr/local/share/ca-certificates/pdfrenderserver-local-ca.crt
  sudo update-ca-certificates

Then restart the browser and run:

  ./scripts/run-dev.sh

Any other client device, including a TV, must also trust this CA certificate
or it will continue to show a certificate warning.
EOF
