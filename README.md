# PDFrenderServer

PDFrenderServer is a small HTTPS web server for viewing PDFs on devices whose browsers cannot render PDF files natively. It downloads a PDF from a supplied URL, renders pages to JPEG images with Poppler, and serves a scrollable image viewer with overlay zoom controls.

## URL format

Open a PDF directly:

```text
https://PDFrenderServerIPAddress/https://example.com/file.pdf
```

Open the manual entry form:

```text
https://PDFrenderServerIPAddress/
```

The server also supports local testing over HTTP with `--http`.

## Installation

A new Ubuntu 24.04 LTS installation needs only the system packages below plus this repository. There are no Python packages to install with `pip`, and a virtual environment is not required.

### Prerequisites

Install the runtime packages:

```bash
sudo apt-get update
sudo apt-get install -y python3 poppler-utils openssl ca-certificates
```

`python3` runs the web server, `poppler-utils` provides `pdfinfo` and `pdftoppm`, `openssl` creates TLS certificates, and `ca-certificates` lets Ubuntu trust a local certificate authority.

### Get The Source

Clone or copy this repository onto the Ubuntu host:

```bash
git clone <repository-url> PDFrenderServer
cd PDFrenderServer
```

If the repository is already on the machine, just change into the repository directory before running the commands below.

### Run From The Repository

From this repository:

```bash
./scripts/run-dev.sh
```

This starts HTTPS on port `8443` by default:

```text
https://127.0.0.1:8443/
```

If you have not configured trusted local TLS yet, the browser will warn that the site is not secure. That warning means the certificate is not trusted by the browser; it does not mean the server failed to start HTTPS.

### Configure Trusted Local TLS

To use `https://127.0.0.1:8443/` without a browser certificate warning, create a local certificate authority and a server certificate:

```bash
./scripts/setup-local-tls.sh
```

Then trust the generated local CA on this Ubuntu machine:

```bash
sudo cp certs/local/pdfrenderserver-local-ca.crt /usr/local/share/ca-certificates/pdfrenderserver-local-ca.crt
sudo update-ca-certificates
```

Restart the browser, then run:

```bash
./scripts/run-dev.sh
```

`run-dev.sh` automatically uses `certs/local/pdfrenderserver.crt` and `certs/local/pdfrenderserver.key` when they exist.

For LAN use, include the server IP address or DNS name when generating the certificate. Every address you type in the browser must be listed in the certificate:

```bash
./scripts/setup-local-tls.sh 192.168.1.50 pdfrenderserver.local
```

Each client device must trust `certs/local/pdfrenderserver-local-ca.crt`. If a TV or another browser does not trust that CA, it will still show a warning even though the server is using HTTPS.

For the exact no-port URL shape in the project brief, run on port `443`:

```bash
sudo python3 -m pdfrenderserver.server --host 0.0.0.0 --port 443
```

To run on port `443` with the trusted local certificate:

```bash
sudo python3 -m pdfrenderserver.server --host 0.0.0.0 --port 443 --cert-file certs/local/pdfrenderserver.crt --key-file certs/local/pdfrenderserver.key
```

For local development without TLS:

```bash
./scripts/run-http-dev.sh
```

### Install As A System Service

For a persistent LAN server, install the included systemd service:

```bash
sudo mkdir -p /opt/PDFrenderServer
sudo cp -a . /opt/PDFrenderServer/
sudo cp systemd/pdfrenderserver.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now pdfrenderserver
```

The service binds to `0.0.0.0:443` and uses `/var/cache/pdfrenderserver` for downloaded PDFs, rendered pages, and its default TLS certificate. To use the trusted local certificate with systemd, pass `--cert-file` and `--key-file` in `systemd/pdfrenderserver.service` after copying the certificate files to a readable location on the server.

## Tests

Run the smoke test:

```bash
python3 -m unittest discover -s tests
```

The test starts a temporary PDF origin server, starts PDFrenderServer, requests a rendered viewer page, and verifies the generated JPEG page image.
