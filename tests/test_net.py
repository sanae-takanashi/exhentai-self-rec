import os
import sys
import types
import unittest
import urllib.request
from unittest.mock import patch

from exh_rec.net import apply_proxy_environment, environment_proxy_url, normalize_proxy_url, open_url, proxy_preview


class NetTest(unittest.TestCase):
    def test_environment_proxy_url_upgrades_socks5_to_socks5h(self):
        # Plain socks5:// makes requests/huggingface_hub resolve DNS locally; socks5h
        # routes the lookup through the proxy so blocked hosts stay reachable.
        self.assertEqual(
            environment_proxy_url("socks5://127.0.0.1:1080"),
            "socks5h://127.0.0.1:1080",
        )
        self.assertEqual(
            environment_proxy_url("socks5h://proxy.test:1080"),
            "socks5h://proxy.test:1080",
        )
        self.assertEqual(
            environment_proxy_url("http://127.0.0.1:7890"),
            "http://127.0.0.1:7890",
        )
        self.assertEqual(environment_proxy_url(""), "")

    def test_apply_proxy_environment_sets_socks5h_for_downloads(self):
        keys = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy")
        saved = {key: os.environ.get(key) for key in keys}
        try:
            apply_proxy_environment("socks5://127.0.0.1:1080")
            for key in keys:
                self.assertEqual(os.environ.get(key), "socks5h://127.0.0.1:1080")
            apply_proxy_environment("")
            for key in keys:
                self.assertIsNone(os.environ.get(key))
        finally:
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

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
