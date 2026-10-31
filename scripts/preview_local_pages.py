"""Loopback-only, GET-only local page preview; no production workers or writes."""

import argparse
from contextlib import contextmanager
from http import HTTPStatus
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import sqlite3
import sys
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from exh_rec.app import Handler, STATIC_DIR
from exh_rec.local_pages import LocalPageLibrary


def preview_handler(library, database):
    @contextmanager
    def metadata():
        conn = sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        try:
            yield conn
        finally:
            conn.close()

    class PreviewHandler(Handler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            self.send_json({"error": "Read-only preview"}, HTTPStatus.METHOD_NOT_ALLOWED)

        def do_HEAD(self):
            self.send_bytes(b"", "text/plain", HTTPStatus.METHOD_NOT_ALLOWED)

        def do_GET(self):
            path = urlparse(self.path).path
            query = parse_qs(urlparse(self.path).query)
            try:
                if path == "/":
                    html = (STATIC_DIR / "index.html").read_bytes().replace(b"ExHentai Recommender", b"Local Pages Preview")
                    self.send_bytes(html, "text/html; charset=utf-8", cache_control="no-store")
                elif path in {"/static/app.js", "/static/styles.css", "/static/visibility.js"}:
                    self.serve_static(path.removeprefix("/static/"))
                elif path == "/api/health":
                    self.send_json({"service": "exh-local-pages-preview", "status": "ok", "read_only": True})
                elif path == "/api/settings":
                    self.send_json({"bootstrap_tags": [], "local_pages_preview": True, "visual_encoder": "simple"})
                elif path == "/api/recommendations":
                    self.send_json({"items": [], "total": 0, "has_more": False, "next_offset": 0})
                elif path in {"/api/status", "/api/queue-counts", "/api/integrations/hath/status"}:
                    self.send_json({})
                elif path == "/api/local-pages":
                    with metadata() as conn:
                        result = library.catalog(conn)
                    self.send_json(result)
                elif path == "/api/local-pages/compare":
                    with metadata() as conn:
                        result = library.compare(query.get("key", [""])[0], conn)
                    self.send_json(result)
                elif path == "/api/local-pages/image":
                    data, content_type = library.image(query.get("id", [""])[0])
                    self.send_bytes(data, content_type, cache_control="private, no-store")
                else:
                    self.send_json({"error": "Not available in read-only preview"}, HTTPStatus.NOT_FOUND)
            except Exception:
                self.send_json({"error": "Local preview unavailable"}, HTTPStatus.NOT_FOUND)

    return PreviewHandler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library", type=Path, default=ROOT / "data" / "hath-page-library")
    parser.add_argument("--database", type=Path, default=ROOT / "data" / "recommender.sqlite3")
    parser.add_argument("--port", type=int, default=18788)
    args = parser.parse_args()
    library = LocalPageLibrary(args.library)
    library.load()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), preview_handler(library, args.database))
    print(json.dumps({"ready": True, "port": server.server_port, "read_only": True}), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
