from __future__ import annotations

import argparse
import hashlib
import html
import json
import mimetypes
import os
from pathlib import Path
import shutil
import ssl
import subprocess
import sys
import tempfile
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, unquote, urlsplit
from urllib.request import Request, urlopen


SERVER_NAME = "PDFrenderServer"
DEFAULT_CACHE_DIR = "/tmp/pdfrenderserver-cache"
DEFAULT_MAX_BYTES = 250 * 1024 * 1024
DEFAULT_DPI = 150


class ClientError(Exception):
    """An error that can be safely shown to the browser user."""


class RenderError(Exception):
    """Internal render failure with a user-facing message."""


class DocumentCache:
    def __init__(self, cache_dir: Path, dpi: int, max_bytes: int) -> None:
        self.cache_dir = cache_dir
        self.dpi = dpi
        self.max_bytes = max_bytes
        self.documents_dir = self.cache_dir / "documents"
        self.documents_dir.mkdir(parents=True, exist_ok=True)
        self._locks: dict[str, threading.Lock] = {}
        self._locks_lock = threading.Lock()

    def document_id(self, source_url: str) -> str:
        return hashlib.sha256(source_url.encode("utf-8")).hexdigest()[:32]

    def document_dir(self, document_id: str) -> Path:
        return self.documents_dir / document_id

    def pdf_path(self, document_id: str) -> Path:
        return self.document_dir(document_id) / "source.pdf"

    def pages_dir(self, document_id: str) -> Path:
        return self.document_dir(document_id) / "pages"

    def metadata_path(self, document_id: str) -> Path:
        return self.document_dir(document_id) / "metadata.json"

    def _lock_for(self, document_id: str) -> threading.Lock:
        with self._locks_lock:
            lock = self._locks.get(document_id)
            if lock is None:
                lock = threading.Lock()
                self._locks[document_id] = lock
            return lock

    def get_document(self, source_url: str) -> dict[str, Any]:
        source_url = validate_source_url(source_url)
        document_id = self.document_id(source_url)
        lock = self._lock_for(document_id)
        with lock:
            return self._download_and_inspect(document_id, source_url)

    def render_page(self, document_id: str, page_number: int) -> Path:
        metadata = self._load_metadata(document_id)
        if metadata is None:
            raise ClientError("Unknown document. Open the PDF URL again.")

        total_pages = int(metadata["pages"])
        if page_number < 1 or page_number > total_pages:
            raise ClientError(f"Page {page_number} is outside the document range.")

        page_path = self.pages_dir(document_id) / f"page-{page_number:04d}.jpg"
        if page_path.exists():
            return page_path

        lock = self._lock_for(f"{document_id}:{page_number}")
        with lock:
            if page_path.exists():
                return page_path
            self.pages_dir(document_id).mkdir(parents=True, exist_ok=True)
            output_prefix = self.pages_dir(document_id) / f"page-{page_number:04d}"
            command = [
                "pdftoppm",
                "-jpeg",
                "-r",
                str(self.dpi),
                "-f",
                str(page_number),
                "-l",
                str(page_number),
                "-singlefile",
                str(self.pdf_path(document_id)),
                str(output_prefix),
            ]
            completed = subprocess.run(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=120,
                check=False,
            )
            if completed.returncode != 0:
                message = completed.stderr.strip() or "pdftoppm failed"
                raise RenderError(f"Could not render page {page_number}: {message}")
            if not page_path.exists():
                raise RenderError(f"Could not render page {page_number}: no image was created")
            return page_path

    def _download_and_inspect(self, document_id: str, source_url: str) -> dict[str, Any]:
        doc_dir = self.document_dir(document_id)
        pages_dir = self.pages_dir(document_id)
        doc_dir.mkdir(parents=True, exist_ok=True)
        pages_dir.mkdir(parents=True, exist_ok=True)

        with tempfile.NamedTemporaryFile(dir=doc_dir, delete=False) as tmp:
            tmp_path = Path(tmp.name)
            try:
                bytes_written, content_type = download_pdf(source_url, tmp, self.max_bytes)
            except Exception:
                tmp_path.unlink(missing_ok=True)
                raise

        revision = sha256_file(tmp_path)
        previous_metadata = self._load_metadata(document_id)
        pdf_path = self.pdf_path(document_id)

        if previous_metadata and previous_metadata.get("revision") == revision and pdf_path.exists():
            tmp_path.unlink(missing_ok=True)
            return previous_metadata

        pages = inspect_pdf_pages(tmp_path)
        shutil.rmtree(pages_dir, ignore_errors=True)
        pages_dir.mkdir(parents=True, exist_ok=True)
        tmp_path.replace(pdf_path)
        metadata = {
            "id": document_id,
            "source_url": source_url,
            "content_type": content_type,
            "bytes": bytes_written,
            "pages": pages,
            "dpi": self.dpi,
            "revision": revision,
            "created_at": int(time.time()),
        }
        self._save_metadata(document_id, metadata)
        return metadata

    def _load_metadata(self, document_id: str) -> dict[str, Any] | None:
        path = self.metadata_path(document_id)
        if not path.exists():
            return None
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)

    def _save_metadata(self, document_id: str, metadata: dict[str, Any]) -> None:
        path = self.metadata_path(document_id)
        tmp_path = path.with_suffix(".tmp")
        with tmp_path.open("w", encoding="utf-8") as handle:
            json.dump(metadata, handle, indent=2, sort_keys=True)
            handle.write("\n")
        tmp_path.replace(path)


class PDFRenderRequestHandler(BaseHTTPRequestHandler):
    server_version = f"{SERVER_NAME}/1.0"

    def do_GET(self) -> None:
        try:
            self._dispatch_get()
        except (ClientError, RenderError) as exc:
            self.respond_error_page(HTTPStatus.BAD_REQUEST, str(exc))
        except TimeoutError:
            self.respond_error_page(HTTPStatus.GATEWAY_TIMEOUT, "The PDF request timed out.")
        except Exception as exc:
            self.log_error("Unhandled request failure: %s", exc)
            self.respond_error_page(HTTPStatus.INTERNAL_SERVER_ERROR, "The server could not complete the request.")

    def _dispatch_get(self) -> None:
        split = urlsplit(self.path)
        route_path = split.path

        if route_path in ("", "/"):
            self.respond_home()
            return

        if route_path == "/favicon.ico":
            self.send_response(HTTPStatus.NO_CONTENT)
            self.end_headers()
            return

        if route_path == "/_pdfrs/health":
            self.respond_text("ok\n", content_type="text/plain; charset=utf-8")
            return

        if route_path == "/_pdfrs/open":
            values = parse_qs(split.query)
            source_url = values.get("url", [""])[0]
            if not source_url:
                raise ClientError("Enter a PDF URL.")
            self.respond_viewer(source_url)
            return

        if route_path.startswith("/_pdfrs/image/"):
            self.respond_image(route_path)
            return

        source_url = unquote(route_path.lstrip("/"))
        if split.query:
            source_url = f"{source_url}?{split.query}"
        self.respond_viewer(source_url)

    def respond_home(self) -> None:
        body = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{SERVER_NAME}</title>
  <style>
    :root {{ color-scheme: light dark; }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      min-height: 100vh;
      display: grid;
      place-items: center;
      font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: #f3f5f4;
      color: #17211d;
    }}
    main {{
      width: min(720px, calc(100vw - 32px));
      padding: 24px;
    }}
    h1 {{
      margin: 0 0 18px;
      font-size: clamp(28px, 5vw, 44px);
      line-height: 1.05;
      letter-spacing: 0;
    }}
    form {{
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 10px;
    }}
    input, button {{
      min-height: 46px;
      border: 1px solid #aab5af;
      border-radius: 6px;
      font: inherit;
    }}
    input {{
      width: 100%;
      padding: 0 13px;
      background: #ffffff;
      color: #17211d;
    }}
    button {{
      padding: 0 18px;
      background: #1f6f54;
      color: #ffffff;
      cursor: pointer;
      border-color: #1f6f54;
      font-weight: 700;
    }}
    @media (max-width: 560px) {{
      form {{ grid-template-columns: 1fr; }}
      button {{ width: 100%; }}
    }}
  </style>
</head>
<body>
  <main>
    <h1>{SERVER_NAME}</h1>
    <form action="/_pdfrs/open" method="get">
      <input name="url" type="url" inputmode="url" autocomplete="url" placeholder="https://example.com/file.pdf" required autofocus>
      <button type="submit">Open</button>
    </form>
  </main>
</body>
</html>
"""
        self.respond_bytes(body.encode("utf-8"), "text/html; charset=utf-8")

    def respond_viewer(self, source_url: str) -> None:
        metadata = self.cache.get_document(source_url)
        document_id = metadata["id"]
        pages = int(metadata["pages"])
        revision = metadata["revision"]
        safe_title = html.escape(Path(urlsplit(source_url).path).name or "PDF")
        safe_source = html.escape(source_url)
        source_json = json.dumps(source_url)
        image_tags = "\n".join(
            f'      <img class="page-image" id="page-{page}" src="/_pdfrs/image/{document_id}/{page}.jpg?v={revision}" '
            f'alt="Page {page}" loading="lazy" decoding="async">'
            for page in range(1, pages + 1)
        )
        body = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{safe_title} - {SERVER_NAME}</title>
  <style>
    * {{ box-sizing: border-box; }}
    :root {{ --zoom: 1; }}
    html, body {{ margin: 0; min-height: 100%; }}
    body {{
      background: #202422;
      color: #f8faf7;
      font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }}
    .toolbar {{
      position: fixed;
      top: 12px;
      right: 12px;
      z-index: 10;
      display: grid;
      grid-template-columns: repeat(5, 42px);
      gap: 6px;
      padding: 6px;
      background: rgba(18, 24, 21, 0.82);
      border: 1px solid rgba(255, 255, 255, 0.18);
      border-radius: 8px;
      backdrop-filter: blur(10px);
    }}
    .toolbar button, .toolbar a {{
      width: 42px;
      height: 38px;
      display: grid;
      place-items: center;
      border: 0;
      border-radius: 6px;
      background: #f7faf8;
      color: #121815;
      font: 700 14px/1 system-ui, sans-serif;
      text-decoration: none;
      cursor: pointer;
    }}
    .toolbar button:hover, .toolbar a:hover {{ background: #d8efe6; }}
    .counter {{
      position: fixed;
      left: 12px;
      top: 12px;
      z-index: 9;
      min-width: 86px;
      padding: 10px 12px;
      border-radius: 8px;
      background: rgba(18, 24, 21, 0.82);
      color: #f8faf7;
      font-size: 14px;
      text-align: center;
      border: 1px solid rgba(255, 255, 255, 0.18);
    }}
    .document {{
      width: 100%;
      min-height: 100vh;
      padding: 70px 16px 24px;
      display: grid;
      justify-items: center;
      gap: 14px;
    }}
    .source {{
      width: min(calc(100vw - 32px), 1100px);
      color: #cbd6d1;
      font-size: 13px;
      overflow-wrap: anywhere;
      padding-right: 240px;
    }}
    .page-image {{
      width: min(calc(100vw - 32px), calc(960px * var(--zoom)));
      max-width: none;
      min-height: 120px;
      background: #ffffff;
      box-shadow: 0 4px 18px rgba(0, 0, 0, 0.35);
      border-radius: 2px;
    }}
    @media (max-width: 680px) {{
      .toolbar {{
        grid-template-columns: repeat(5, 36px);
        right: 8px;
        top: 8px;
      }}
      .toolbar button, .toolbar a {{ width: 36px; height: 34px; }}
      .counter {{ top: 8px; left: 8px; }}
      .document {{ padding: 62px 8px 18px; gap: 10px; }}
      .source {{ width: calc(100vw - 16px); padding-right: 0; font-size: 12px; }}
      .page-image {{ width: min(calc(100vw - 16px), calc(960px * var(--zoom))); }}
    }}
  </style>
</head>
<body>
  <nav class="toolbar" aria-label="PDF controls">
    <button type="button" id="zoom-out" title="Zoom out" aria-label="Zoom out">-</button>
    <button type="button" id="zoom-in" title="Zoom in" aria-label="Zoom in">+</button>
    <button type="button" id="fit-width" title="Fit width" aria-label="Fit width">Fit</button>
    <button type="button" id="full-screen" title="Full screen" aria-label="Full screen">Full</button>
    <a href="{safe_source}" title="Open source PDF" aria-label="Open source PDF">PDF</a>
  </nav>
  <div class="counter"><span id="current-page">1</span> / {pages}</div>
  <main class="document" data-source={html.escape(source_json)}>
    <div class="source">{safe_source}</div>
{image_tags}
  </main>
  <script>
    const root = document.documentElement;
    const storageKey = "pdfrs.zoom";
    let zoom = Number(localStorage.getItem(storageKey) || "1");
    const clamp = (value) => Math.min(2.4, Math.max(0.55, value));
    const applyZoom = () => {{
      zoom = clamp(zoom);
      root.style.setProperty("--zoom", String(zoom));
      localStorage.setItem(storageKey, String(zoom));
    }};
    document.getElementById("zoom-out").addEventListener("click", () => {{
      zoom -= 0.15;
      applyZoom();
    }});
    document.getElementById("zoom-in").addEventListener("click", () => {{
      zoom += 0.15;
      applyZoom();
    }});
    document.getElementById("fit-width").addEventListener("click", () => {{
      zoom = 1;
      applyZoom();
    }});
    document.getElementById("full-screen").addEventListener("click", () => {{
      if (!document.fullscreenElement) {{
        document.documentElement.requestFullscreen?.();
      }} else {{
        document.exitFullscreen?.();
      }}
    }});
    applyZoom();

    const pageLabel = document.getElementById("current-page");
    const observer = new IntersectionObserver((entries) => {{
      const visible = entries
        .filter((entry) => entry.isIntersecting)
        .sort((a, b) => b.intersectionRatio - a.intersectionRatio)[0];
      if (visible) {{
        pageLabel.textContent = visible.target.id.replace("page-", "");
      }}
    }}, {{ threshold: [0.35, 0.6, 0.85] }});
    document.querySelectorAll(".page-image").forEach((image) => observer.observe(image));
  </script>
</body>
</html>
"""
        self.respond_bytes(body.encode("utf-8"), "text/html; charset=utf-8")

    def respond_image(self, route_path: str) -> None:
        parts = route_path.split("/")
        if len(parts) != 5:
            raise ClientError("Invalid image path.")
        document_id = parts[3]
        page_name = parts[4]
        if not page_name.endswith(".jpg"):
            raise ClientError("Invalid image path.")
        try:
            page_number = int(page_name[:-4])
        except ValueError as exc:
            raise ClientError("Invalid page number.") from exc
        page_path = self.cache.render_page(document_id, page_number)
        self.respond_file(page_path, "image/jpeg")

    def respond_file(self, path: Path, content_type: str | None = None) -> None:
        if content_type is None:
            content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        data = path.read_bytes()
        self.respond_bytes(data, content_type, cache_control="public, max-age=604800")

    def respond_text(self, text: str, content_type: str) -> None:
        self.respond_bytes(text.encode("utf-8"), content_type)

    def respond_bytes(
        self,
        data: bytes,
        content_type: str,
        status: HTTPStatus = HTTPStatus.OK,
        cache_control: str = "no-store",
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", cache_control)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def respond_error_page(self, status: HTTPStatus, message: str) -> None:
        safe_message = html.escape(message)
        body = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{status.value} - {SERVER_NAME}</title>
  <style>
    body {{
      margin: 0;
      min-height: 100vh;
      display: grid;
      place-items: center;
      font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: #f4f2ef;
      color: #2c2420;
    }}
    main {{
      width: min(680px, calc(100vw - 32px));
      padding: 24px;
    }}
    h1 {{ margin: 0 0 12px; font-size: 30px; letter-spacing: 0; }}
    p {{ margin: 0; line-height: 1.5; overflow-wrap: anywhere; }}
    a {{ color: #1f6f54; font-weight: 700; }}
  </style>
</head>
<body>
  <main>
    <h1>{status.value}</h1>
    <p>{safe_message}</p>
    <p><a href="/">Open another PDF</a></p>
  </main>
</body>
</html>
"""
        self.respond_bytes(body.encode("utf-8"), "text/html; charset=utf-8", status=status)

    @property
    def cache(self) -> DocumentCache:
        return self.server.cache  # type: ignore[attr-defined]

    def log_message(self, format: str, *args: Any) -> None:
        sys.stderr.write("%s - - [%s] %s\n" % (self.address_string(), self.log_date_time_string(), format % args))


def validate_source_url(source_url: str) -> str:
    source_url = source_url.strip()
    if not source_url:
        raise ClientError("Enter a PDF URL.")
    split = urlsplit(source_url)
    if split.scheme not in {"http", "https"}:
        raise ClientError("Only http and https PDF URLs are supported.")
    if not split.netloc:
        raise ClientError("The PDF URL must include a host.")
    return source_url


def download_pdf(source_url: str, output, max_bytes: int) -> tuple[int, str]:
    request = Request(
        source_url,
        headers={
            "User-Agent": f"{SERVER_NAME}/1.0",
            "Accept": "application/pdf,*/*;q=0.8",
        },
    )
    try:
        with urlopen(request, timeout=30) as response:
            content_type = response.headers.get("Content-Type", "application/octet-stream")
            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) > max_bytes:
                raise ClientError(f"The PDF is larger than the configured limit of {max_bytes} bytes.")

            total = 0
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise ClientError(f"The PDF is larger than the configured limit of {max_bytes} bytes.")
                output.write(chunk)
            if total == 0:
                raise ClientError("The PDF URL returned an empty response.")
            return total, content_type
    except HTTPError as exc:
        raise ClientError(f"The PDF URL returned HTTP {exc.code}.") from exc
    except URLError as exc:
        raise ClientError(f"Could not download the PDF: {exc.reason}") from exc


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def inspect_pdf_pages(pdf_path: Path) -> int:
    completed = subprocess.run(
        ["pdfinfo", str(pdf_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=60,
        check=False,
    )
    if completed.returncode != 0:
        message = completed.stderr.strip() or "pdfinfo failed"
        raise RenderError(f"The downloaded file could not be read as a PDF: {message}")
    for line in completed.stdout.splitlines():
        if line.startswith("Pages:"):
            pages = int(line.split(":", 1)[1].strip())
            if pages < 1:
                raise RenderError("The PDF has no pages.")
            return pages
    raise RenderError("The PDF page count could not be determined.")


def ensure_tls_certificate(cert_file: Path, key_file: Path) -> None:
    if cert_file.exists() and key_file.exists():
        return
    openssl = shutil.which("openssl")
    if openssl is None:
        raise RuntimeError("openssl is required to generate a self-signed TLS certificate")

    cert_file.parent.mkdir(parents=True, exist_ok=True)
    key_file.parent.mkdir(parents=True, exist_ok=True)
    command = [
        openssl,
        "req",
        "-x509",
        "-newkey",
        "rsa:2048",
        "-nodes",
        "-keyout",
        str(key_file),
        "-out",
        str(cert_file),
        "-sha256",
        "-days",
        "825",
        "-subj",
        "/CN=PDFrenderServer",
        "-addext",
        "subjectAltName=DNS:localhost,DNS:PDFrenderServer,IP:127.0.0.1",
    ]
    subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
    key_file.chmod(0o600)


def build_server(host: str, port: int, cache_dir: Path, dpi: int, max_bytes: int) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), PDFRenderRequestHandler)
    server.cache = DocumentCache(cache_dir=cache_dir, dpi=dpi, max_bytes=max_bytes)  # type: ignore[attr-defined]
    return server


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render remote PDF files as browser-viewable page images.")
    cert_file = os.environ.get("PDFRENDERSERVER_CERT_FILE")
    key_file = os.environ.get("PDFRENDERSERVER_KEY_FILE")
    parser.add_argument("--host", default=os.environ.get("PDFRENDERSERVER_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PDFRENDERSERVER_PORT", "8443")))
    parser.add_argument("--cache-dir", type=Path, default=Path(os.environ.get("PDFRENDERSERVER_CACHE_DIR", DEFAULT_CACHE_DIR)))
    parser.add_argument("--dpi", type=int, default=int(os.environ.get("PDFRENDERSERVER_DPI", str(DEFAULT_DPI))))
    parser.add_argument("--max-bytes", type=int, default=int(os.environ.get("PDFRENDERSERVER_MAX_BYTES", str(DEFAULT_MAX_BYTES))))
    parser.add_argument("--http", action="store_true", help="Run without TLS. Use only for local testing.")
    parser.add_argument("--cert-file", type=Path, default=Path(cert_file) if cert_file else None)
    parser.add_argument("--key-file", type=Path, default=Path(key_file) if key_file else None)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if shutil.which("pdfinfo") is None or shutil.which("pdftoppm") is None:
        sys.stderr.write("pdfinfo and pdftoppm are required. Install poppler-utils.\n")
        return 2

    server = build_server(args.host, args.port, args.cache_dir, args.dpi, args.max_bytes)
    scheme = "http" if args.http else "https"
    if not args.http:
        cert_file = args.cert_file or args.cache_dir / "certs" / "pdfrenderserver.crt"
        key_file = args.key_file or args.cache_dir / "certs" / "pdfrenderserver.key"
        ensure_tls_certificate(cert_file, key_file)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(certfile=cert_file, keyfile=key_file)
        server.socket = context.wrap_socket(server.socket, server_side=True)
        sys.stderr.write(f"Using TLS certificate {cert_file}\n")

    sys.stderr.write(f"{SERVER_NAME} listening on {scheme}://{args.host}:{args.port}/\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        sys.stderr.write("\nShutting down.\n")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
