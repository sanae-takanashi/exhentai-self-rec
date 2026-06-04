import sys
import types
import unittest
import urllib.request
from unittest.mock import patch

from exh_rec.net import normalize_proxy_url, open_url, proxy_preview


class NetTest(unittest.TestCase):
    def test_normalize_proxy_url_accepts_supported_proxy_schemes(self):
        self.assertEqual(normalize_proxy_url("127.0.0.1:7890"), "http://127.0.0.1:7890")
        self.assertEqual(normalize_proxy_url("https://proxy.test:8443"), "https://proxy.test:8443")
        self.assertEqual(normalize_proxy_url("socks5://127.0.0.1:1080"), "socks5://127.0.0.1:1080")
        self.assertEqual(normalize_proxy_url("socks5h://user:pass@proxy.test:1080"), "socks5h://user:pass@proxy.test:1080")

    def test_normalize_proxy_url_rejects_unsupported_or_malformed_values(self):
        for value in ("ftp://proxy.test:21", "http://", "http://proxy.test/path", "http://proxy.test:bad"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    normalize_proxy_url(value)

    def test_proxy_preview_hides_password(self):
        self.assertEqual(proxy_preview("socks5://user:secret@proxy.test:1080"), "socks5://user@proxy.test:1080")

    def test_open_url_uses_proxy_handler_for_http_proxy(self):
        request = urllib.request.Request("https://example.test/")
        calls = []

        class Opener:
            def open(self, request, timeout):
                calls.append((request.full_url, timeout))
                return "response"

        with patch("exh_rec.net.urllib.request.build_opener", return_value=Opener()) as build:
            response = open_url(request, timeout=10, proxy_url="http://127.0.0.1:7890")

        self.assertEqual(response, "response")
        self.assertEqual(calls, [("https://example.test/", 10)])
        handler = build.call_args.args[0]
        self.assertIsInstance(handler, urllib.request.ProxyHandler)

    def test_open_url_uses_no_environment_proxy_handler_for_socks_proxy(self):
        request = urllib.request.Request("https://example.test/")
        calls = []
        proxy_args = []

        class FakeSocksSocket:
            def set_proxy(self, *args, **kwargs):
                proxy_args.append((args, kwargs))

            def settimeout(self, timeout):
                pass

            def bind(self, source_address):
                pass

            def connect(self, address):
                pass

        fake_socks = types.SimpleNamespace(SOCKS5=2, socksocket=FakeSocksSocket)

        class Opener:
            def open(self, request, timeout):
                calls.append((request.full_url, timeout))
                return "response"

        def fake_proxy_handler(proxies=None):
            proxy_args.append(("handler", proxies))
            return ("proxy-handler", proxies)

        with patch.dict(sys.modules, {"socks": fake_socks}), patch(
            "exh_rec.net.urllib.request.urlopen",
            side_effect=AssertionError("SOCKS path must not use environment-aware urlopen"),
        ), patch("exh_rec.net.urllib.request.ProxyHandler", side_effect=fake_proxy_handler), patch(
            "exh_rec.net.urllib.request.build_opener",
            return_value=Opener(),
        ) as build:
            response = open_url(request, timeout=10, proxy_url="socks5://127.0.0.1:1080")

        self.assertEqual(response, "response")
        self.assertEqual(calls, [("https://example.test/", 10)])
        self.assertEqual(build.call_count, 1)
        self.assertIn(("handler", {}), proxy_args)


if __name__ == "__main__":
    unittest.main()
