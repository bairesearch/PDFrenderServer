from __future__ import annotations

import shutil
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.request import urlopen

from pdfrenderserver.server import build_server


def sample_pdf_bytes() -> bytes:
    text_stream = b"BT /F1 24 Tf 72 720 Td (PDFrenderServer smoke test) Tj ET\n"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        f"<< /Length {len(text_stream)} >>\nstream\n".encode("ascii") + text_stream + b"endstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]

    output = b"%PDF-1.4\n"
    offsets = [0]
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(output))
        output += f"{index} 0 obj\n".encode("ascii") + obj + b"\nendobj\n"

    xref_offset = len(output)
    xref = f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode("ascii")
    for offset in offsets[1:]:
        xref += f"{offset:010d} 00000 n \n".encode("ascii")
    trailer = (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_offset}\n%%EOF\n"
    ).encode("ascii")
    return output + xref + trailer


class PDFOriginHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path != "/sample.pdf":
            self.send_response(404)
            self.end_headers()
            return
        data = sample_pdf_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "application/pdf")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args) -> None:
        return


def start_server(server: ThreadingHTTPServer) -> threading.Thread:
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return thread


class SmokeTest(unittest.TestCase):
    def test_renders_remote_pdf_page_as_jpeg(self) -> None:
        if shutil.which("pdfinfo") is None or shutil.which("pdftoppm") is None:
            self.skipTest("poppler-utils is not installed")

        origin = ThreadingHTTPServer(("127.0.0.1", 0), PDFOriginHandler)
        start_server(origin)
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)

        renderer = build_server(
            "127.0.0.1",
            0,
            cache_dir=Path(temp_dir.name) / "cache",
            dpi=72,
            max_bytes=5 * 1024 * 1024,
        )
        start_server(renderer)
        self.addCleanup(renderer.shutdown)
        self.addCleanup(renderer.server_close)
        self.addCleanup(origin.shutdown)
        self.addCleanup(origin.server_close)

        source_url = f"http://127.0.0.1:{origin.server_port}/sample.pdf"
        viewer_url = f"http://127.0.0.1:{renderer.server_port}/{source_url}"

        with urlopen(viewer_url, timeout=10) as response:
            html = response.read().decode("utf-8")
        self.assertIn("sample.pdf", html)
        self.assertIn("/_pdfrs/image/", html)

        image_path = html.split('/_pdfrs/image/', 1)[1].split('"', 1)[0]
        image_url = f"http://127.0.0.1:{renderer.server_port}/_pdfrs/image/{image_path}"
        with urlopen(image_url, timeout=20) as response:
            image = response.read()
            content_type = response.headers["Content-Type"]

        self.assertEqual(content_type, "image/jpeg")
        self.assertTrue(image.startswith(b"\xff\xd8"))


if __name__ == "__main__":
    unittest.main()
