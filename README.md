# PDFrenderServer

PDFrenderServer is a small HTTPS web server for viewing PDFs on devices whose browsers cannot render PDF files natively. It downloads a PDF from a supplied URL, renders pages to JPEG images with Poppler, and serves a scrollable image viewer with overlay zoom controls.

## URL Format

Open the manual entry form:

```text
https://PDFrenderServerIPAddress:8443/
```

Open a PDF directly:

```text
https://PDFrenderServerIPAddress:8443/https://example.com/file.pdf
```

Example for a server at `192.168.0.87`:

```text
https://192.168.0.87:8443/
https://192.168.0.87:8443/https://example.com/file.pdf
```

If the service is installed on standard HTTPS port `443`, omit `:8443`.

## Installation

A new Ubuntu 24.04 LTS installation needs only the system packages below plus this repository. There are no Python packages to install with `pip`, and a virtual environment is not required.

### Prerequisites

Install the runtime packages:

```bash
sudo apt-get update
sudo apt-get install -y python3 poppler-utils openssl ca-certificates
```

`python3` runs the web server, `poppler-utils` provides `pdfinfo` and `pdftoppm`, `openssl` creates TLS certificates, and `ca-certificates` provides Ubuntu's certificate store tools.

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

This starts PDFrenderServer on all network interfaces using HTTPS port `8443`:

```text
https://127.0.0.1:8443/
https://LANIPAddress:8443/
```

Keep the terminal open while testing. Browser certificate warnings are expected with the default local certificate; see `SSL/TLS` below.

### Firewall

If UFW is active and you want to access PDFrenderServer from a TV or another LAN device, allow port `8443`:

```bash
sudo ufw allow 8443/tcp comment 'PDFrenderServer HTTPS dev'
sudo ufw reload
sudo ufw status verbose
```

The status output should show an allow rule for `8443/tcp`.

### Install As A System Service

For a persistent LAN server, install the included systemd service:

```bash
sudo mkdir -p /opt/PDFrenderServer
sudo cp -a . /opt/PDFrenderServer/
sudo cp systemd/pdfrenderserver.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now pdfrenderserver
```

The service binds to `0.0.0.0:443` and uses `/var/cache/pdfrenderserver` for downloaded PDFs and rendered pages. Certificate behavior is covered in `SSL/TLS` below.

If UFW is active and the service uses port `443`, allow standard HTTPS:

```bash
sudo ufw allow 443/tcp comment 'PDFrenderServer HTTPS'
sudo ufw reload
sudo ufw status verbose
```

## SSL/TLS (untested)

PDFrenderServer uses HTTPS by default, but the development certificate is not publicly trusted. At this stage, the simplest setup is to accept or bypass the browser warning on the TV.

The warning means the browser does not trust the certificate issuer. It does not mean PDFrenderServer failed to start HTTPS.

### Default Certificate

When no certificate is configured, PDFrenderServer generates a local certificate automatically. This is enough for encrypted LAN traffic, but browsers and TVs will usually show a warning.

Current practical TV URL:

```text
https://LANIPAddress:8443/
```

For example:

```text
https://192.168.0.87:8443/
```

### Optional Trusted Local TLS

For a local Ubuntu browser, you can create and trust a local certificate authority:

```bash
./scripts/setup-local-tls.sh
sudo cp certs/local/pdfrenderserver-local-ca.crt /usr/local/share/ca-certificates/pdfrenderserver-local-ca.crt
sudo update-ca-certificates
```

Restart the browser, then run:

```bash
./scripts/run-dev.sh
```

This only helps client devices that trust `certs/local/pdfrenderserver-local-ca.crt`. Many TVs do not provide a practical way to install that trust.

### Optional Public Certificate For TV

To avoid TV warnings without importing a local CA, use a real DNS name and a publicly trusted certificate:

```text
https://pdfrender.example.com/https://example.com/file.pdf
```

Recommended deployment:

1. Choose a DNS name you control, for example `pdfrender.example.com`.
2. Configure your router, Pi-hole, dnsmasq, or other local DNS so the TV resolves that name to the Ubuntu server's LAN IP address.
3. Obtain a publicly trusted TLS certificate for that DNS name, for example with Let's Encrypt/Certbot.
4. Run PDFrenderServer with that certificate and key.

DNS validation is usually the best certificate validation method for a LAN-only server because the PDFrenderServer host does not need to accept inbound traffic from the public internet. With Certbot's manual DNS flow:

```bash
sudo apt-get install -y certbot
sudo certbot certonly --manual --preferred-challenges dns -d pdfrender.example.com
```

After the certificate is issued, run PDFrenderServer with the generated files:

```bash
sudo python3 -m pdfrenderserver.server --host 0.0.0.0 --port 443 --cert-file /etc/letsencrypt/live/pdfrender.example.com/fullchain.pem --key-file /etc/letsencrypt/live/pdfrender.example.com/privkey.pem
```

For a systemd service using a Let's Encrypt certificate, use the example override:

```text
systemd/pdfrenderserver-letsencrypt.override.example.conf
```

### HTTP Without TLS

For local debugging only:

```bash
./scripts/run-http-dev.sh
```

This starts a plain HTTP server on `127.0.0.1:8000` by default. Do not use this for normal TV/LAN viewing unless you intentionally want no HTTPS.

## Tests

Run the smoke test:

```bash
python3 -m unittest discover -s tests
```

The test starts a temporary PDF origin server, starts PDFrenderServer, requests a rendered viewer page, and verifies the generated JPEG page image.
