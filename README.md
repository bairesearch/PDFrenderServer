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

## Prerequisites

Runtime packages:

```bash
sudo apt-get install -y python3 poppler-utils openssl
```

`python3` runs the web server, `poppler-utils` provides `pdfinfo` and `pdftoppm`, and `openssl` creates a self-signed HTTPS certificate for LAN use.

## Run From The Repository

From this repository:

```bash
./scripts/run-dev.sh
```

This starts HTTPS on port `8443` by default:

```text
https://127.0.0.1:8443/
```

For the exact no-port URL shape in the project brief, run on port `443`:

```bash
sudo python3 -m pdfrenderserver.server --host 0.0.0.0 --port 443
```

The generated certificate is self-signed. Browsers will normally require a one-time warning bypass unless the certificate is installed as trusted on the client device.

For local development without TLS:

```bash
./scripts/run-http-dev.sh
```

## Systemd

An example unit file is provided at `systemd/pdfrenderserver.service`. A typical install is:

```bash
sudo mkdir -p /opt/PDFrenderServer
sudo cp -a . /opt/PDFrenderServer/
sudo cp systemd/pdfrenderserver.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now pdfrenderserver
```

The service binds to `0.0.0.0:443` and uses `/var/cache/pdfrenderserver` for downloaded PDFs, rendered pages, and the self-signed certificate.

## Tests

Run the smoke test:

```bash
python3 -m unittest discover -s tests
```

The test starts a temporary PDF origin server, starts PDFrenderServer, requests a rendered viewer page, and verifies the generated JPEG page image.

