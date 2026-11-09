"""Every test runs against a throw-away data folder (set before mirrorvoice.config is imported)."""
import http.server
import os
import re
import sys
import tempfile
import threading
from pathlib import Path

_HOME = tempfile.mkdtemp(prefix="mirrorvoice-test-")
os.environ["MIRRORVOICE_HOME"] = _HOME
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402


class _RangeHandler(http.server.BaseHTTPRequestHandler):
    """Serves `self.server.files[path]` with HTTP Range support (like a CDN)."""

    def log_message(self, *a):  # keep test output quiet
        pass

    def do_GET(self):
        data = self.server.files.get(self.path)
        if data is None:
            self.send_error(404)
            return
        self.server.hits.append((self.path, self.headers.get("Range", "")))
        m = re.match(r"bytes=(\d+)-(\d*)", self.headers.get("Range", ""))
        if m and self.server.ranges:
            start = int(m.group(1))
            end = int(m.group(2)) if m.group(2) else len(data) - 1
            body = data[start:end + 1]
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{len(data)}")
        else:
            body = data
            self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def http_files():
    """A local HTTP server: returns (base_url, files dict, server) — put bytes into files['/name']."""
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _RangeHandler)
    srv.files, srv.hits, srv.ranges = {}, [], True
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", srv.files, srv
    srv.shutdown()


@pytest.fixture
def home() -> Path:
    from mirrorvoice.config import HOME

    return HOME
