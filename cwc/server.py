"""Local dev server: the search page plus a small JSON API.

    python -m cwc.server            # http://localhost:8787
    python -m cwc.server --port 9000

API:
    GET /api/search?q=Ezekiel+4:9&limit=20&from=2023-01-01&to=2023-12-31
    GET /api/stats
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import search as S
from .index import DB, video_words

STATIC = Path(__file__).resolve().parent.parent / "static"
_local = threading.local()


def con():
    # one read-only connection per thread; reopened if the index is rebuilt
    mtime = DB.stat().st_mtime
    if getattr(_local, "mtime", None) != mtime:
        _local.con = S.connect(DB)
        _local.mtime = mtime
    return _local.con


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body if isinstance(body, bytes) else json.dumps(body, default=list).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        u = urlparse(self.path)
        qs = {k: v[0] for k, v in parse_qs(u.query).items()}
        try:
            if u.path == "/api/search":
                limit = min(int(qs.get("limit", 20)), 100)
                return self._send(200, S.search(con(), qs.get("q", ""), limit, qs.get("from"), qs.get("to"),
                                                qs.get("mode", "words")))
            if u.path == "/api/transcript":
                words, times = video_words(con(), qs.get("id", ""))
                return self._send(200, {"w": " ".join(words), "t": times})
            if u.path == "/api/stats":
                return self._send(200, S.stats(con()))
        except FileNotFoundError:
            return self._send(503, {"error": "No index yet. Run: python -m cwc.ingest && python -m cwc.index"})
        except Exception as e:
            return self._send(500, {"error": str(e)})
        path = (STATIC / (u.path.lstrip("/") or "index.html")).resolve()
        if not str(path).startswith(str(STATIC)) or not path.is_file():
            path = STATIC / "index.html"
        self._send(200, path.read_bytes(), mimetypes.guess_type(path.name)[0] or "application/octet-stream")

    def log_message(self, fmt, *args):
        if "/api/" in (args[0] if args else ""):
            super().log_message(fmt, *args)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--host", default="127.0.0.1")
    a = ap.parse_args()
    if not DB.exists():
        print("Warning: no index yet (data/sermons.db). Run python -m cwc.ingest, then python -m cwc.index")
    print(f"Sermon search running at http://localhost:{a.port}")
    ThreadingHTTPServer((a.host, a.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
