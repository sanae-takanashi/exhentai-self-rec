import json
import socket
import struct
import sys
import threading
import unittest
import urllib.error
import urllib.request
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import Mock, patch

from exh_rec.app import Handler


class HttpDisconnectTest(unittest.TestCase):
    def test_disconnect_during_response_does_not_send_a_second_response(self):
        for error in (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            for stage in ("headers", "body"):
                for kind in ("json", "bytes", "static", "head"):
                    with self.subTest(error=error.__name__, stage=stage, kind=kind):
                        handler = Handler.__new__(Handler)
                        handler.path = "/"
                        handler.close_connection = False
                        handler.send_response = Mock()
                        handler.send_header = Mock()
                        handler.end_headers = Mock(side_effect=error() if stage == "headers" else None)
                        handler.wfile = Mock()
                        if stage == "body":
                            handler.wfile.write.side_effect = error()
                        if kind == "json":
                            handler.send_json({"ok": True})
                        elif kind == "bytes":
                            handler.send_bytes(b"image", "image/png")
                        elif kind == "head":
                            handler.do_HEAD()
                        else:
                            handler.do_GET()
                        handler.send_response.assert_called_once_with(HTTPStatus.OK)
                        if kind != "head" or stage == "headers":
                            self.assertTrue(handler.close_connection)

    def test_request_read_and_final_flush_disconnects_are_quiet(self):
        for error in (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            with self.subTest(error=error.__name__):
                handler = Handler.__new__(Handler)
                handler.close_connection = False
                with patch.object(BaseHTTPRequestHandler, "handle", side_effect=error()):
                    handler.handle()
                self.assertTrue(handler.close_connection)

    def test_unrelated_write_failures_are_not_hidden(self):
        handler = Handler.__new__(Handler)
        handler.send_response = Mock()
        handler.send_header = Mock()
        handler.end_headers = Mock()
        handler.wfile = Mock()
        handler.wfile.write.side_effect = OSError("synthetic disk failure")
        with self.assertRaises(OSError):
            handler.send_bytes(b"image", "image/png")

    def test_browser_reset_and_normal_requests_on_real_server(self):
        ready, release, done = threading.Event(), threading.Event(), threading.Event()
        errors, statuses = [], []

        class Probe(Handler):
            def serve_thumbnail(self, *args):
                ready.set()
                if not release.wait(5):
                    raise AssertionError("reset was not released")
                self.send_bytes(b"x" * 2**20, "image/png")

            def log_request(self, code="-", size="-"):
                statuses.append(int(code))

            def log_message(self, *args):
                pass

            def finish(self):
                try:
                    super().finish()
                finally:
                    done.set()

        class Server(ThreadingHTTPServer):
            def handle_error(self, *args):
                errors.append(type(sys.exc_info()[1]).__name__)

        server = Server(("127.0.0.1", 0), Probe)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            with socket.create_connection(server.server_address, timeout=5) as client:
                client.sendall(b"GET /thumb HTTP/1.0\r\nHost: localhost\r\n\r\n")
                self.assertTrue(ready.wait(5))
                linger_format = "HH" if sys.platform == "win32" else "ii"
                client.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack(linger_format, 1, 0))
            release.set()
            self.assertTrue(done.wait(5))
            # A fresh request also verifies that the server continues serving.
            with opener.open(base + "/api/health", timeout=5) as response:
                self.assertEqual(json.load(response), {"service": "exh-rec", "status": "ok"})
            for method in ("GET", "HEAD"):
                with opener.open(urllib.request.Request(base + "/", method=method), timeout=5) as response:
                    self.assertEqual(response.headers["Cache-Control"], "no-cache, must-revalidate")
                    self.assertGreater(int(response.headers["Content-Length"]), 0)
                    self.assertEqual(bool(response.read()), method == "GET")
            with self.assertRaises(urllib.error.HTTPError) as raised:
                opener.open(base + "/missing", timeout=5)
            self.assertEqual(raised.exception.code, 404)
            self.assertEqual(json.load(raised.exception), {"error": "Not found"})
            raised.exception.close()
            # An upstream reset remains an actual API error for a connected client.
            with patch("exh_rec.app.cached_thumbnail", side_effect=ConnectionResetError("synthetic upstream reset")):
                with patch.object(Probe, "serve_thumbnail", Handler.serve_thumbnail):
                    with self.assertRaises(urllib.error.HTTPError) as raised:
                        opener.open(base + "/thumb", timeout=5)
                    self.assertEqual(raised.exception.code, 500)
                    raised.exception.close()
        finally:
            release.set()
            server.shutdown()
            server.server_close()
            thread.join(5)
        self.assertEqual(errors, [])
        self.assertEqual(statuses, [200, 200, 200, 200, 404, 500])


if __name__ == "__main__":
    unittest.main()
