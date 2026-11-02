from contextlib import closing
from email.message import Message
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import socket
import sys
import tempfile
import threading
import types
import unittest
import urllib.error
import urllib.request
from unittest.mock import Mock, patch

from exh_rec import hath_recovery as recovery
from exh_rec.local_pages import LocalPageLibrary, SCHEMA
from scripts import recover_hath_metadata as cli


def hit(gid="1"):
    return {"url": f"https://exhentai.org/g/{gid}/abcdef/", "gid": gid}


def found(gid="1", more=False):
    return {"candidates": [hit(gid)], "has_more": more, "outcome": "candidates"}


class SearchTest(unittest.TestCase):
    def test_only_typed_site_lookups_can_be_constructed(self):
        self.assertEqual(recovery.search_url("gid", "42"), "https://exhentai.org/?f_search=gid%3A42")
        self.assertEqual(recovery.search_url("sha1", "a" * 40), "https://exhentai.org/?f_shash=" + "a" * 40)
        for kind, value in [("url", "https://evil.invalid"), ("gid", "1&f_search=other"), ("gid", "0"), ("sha1", "../private"), ("sha1", "a" * 41), ("gid", 1)]:
            with self.subTest(kind=kind, value=value), self.assertRaises(recovery.RecoveryError):
                recovery.search_url(kind, value)

    def test_parser_uses_links_not_arbitrary_script_urls(self):
        text = '<table class="itg"><a href="/g/1/abcdef/"><div class="glink">Title</div></a><a href="/g/1/abcdef/">Again</a></table><script>"/g/2/badc0de/"</script>'
        self.assertEqual(recovery.parse_search(text), found())

    def test_candidate_urls_reject_other_hosts_ports_credentials_and_suffixes(self):
        text = '<table class="itg">' + ''.join(f'<a href="{url}">No</a>' for url in [
            "https://evil.invalid/g/1/abc/", "https://exhentai.org.evil.invalid/g/2/abc/",
            "https://user@exhentai.org/g/3/abc/", "http://exhentai.org/g/4/abc/",
            "https://exhentai.org:444/g/5/abc/", "https://exhentai.org/g/6/abc/?leak=1",
        ]) + '<a href="//e-hentai.org/g/7/abc/">Mirror</a></table>'
        self.assertEqual(recovery.parse_search(text)["candidates"], [{"url": "https://e-hentai.org/g/7/abc/", "gid": "7"}])

    def test_explicit_empty_search_is_not_proof_of_absence(self):
        for text in ("No hits found", "No unfiltered results found"):
            result = recovery.parse_search(text)
            self.assertEqual(result["outcome"], "no_visible_hits")

    def test_missing_results_auth_challenges_and_rate_bans_stop(self):
        cases = [('', 'empty_search_response'), ('<h1>Unknown response</h1>', 'unrecognized_search_page'),
                 ('<form><input name="password"></form>No hits found', 'access_required'),
                 ('<form id="challenge-form">No hits found</form>', 'access_required'),
                 ('Sad Panda', 'access_required'), ('cf-chl-test No hits found', 'access_required'),
                 ('Temporarily banned due to an excessive request rate', 'rate_limited')]
        for text, reason in cases:
            with self.subTest(reason=reason), self.assertRaisesRegex(recovery.RecoveryError, "^" + reason + "$"):
                recovery.parse_search(text)

    def test_pagination_marks_candidate_enumeration_partial(self):
        text = '<a class="glink" href="/g/1/abcdef/">Title</a><a id="unext" href="/?next=1">Next</a>'
        self.assertTrue(recovery.parse_search(text)["has_more"])
        with self.assertRaises(recovery.RecoveryError):
            recovery.parse_search('No hits found<a id="unext" href="/?next=1">Next</a>')

    def test_empty_and_nonempty_results_cannot_be_mixed(self):
        with self.assertRaises(recovery.RecoveryError):
            recovery.parse_search('No hits found<a class="glink" href="/g/1/abcdef/">Title</a>')

    def test_unknown_page_diagnostics_are_fixed_markers_and_counts_only(self):
        text = '<html><title>FAKE_SECRET</title><form action="https://private.invalid/?secret=FAKE_SECRET"><input name="f_search" value="FAKE_SECRET"></form><a href="/g/1/abc">FAKE_SECRET</a><img src="FAKE_SECRET"></html>'
        with self.assertRaises(recovery.RecoveryError) as raised:
            recovery.parse_search(text, {"http_status": 200, "response_bytes": 500, "raw": "FAKE_SECRET"})
        diagnostics = raised.exception.diagnostics
        self.assertEqual(str(raised.exception), "unrecognized_search_page")
        self.assertEqual(diagnostics["http_status"], 200)
        self.assertEqual(diagnostics["response_bytes"], 500)
        self.assertEqual(diagnostics["forms"], 1)
        self.assertEqual(diagnostics["gallery_anchors"], 1)
        self.assertEqual(diagnostics["canonical_candidates"], 0)
        self.assertEqual(diagnostics["missing_slash_candidates"], 1)
        self.assertTrue(diagnostics["search_form"])
        self.assertNotIn("FAKE_SECRET", json.dumps(diagnostics))
        self.assertNotIn("private.invalid", json.dumps(diagnostics))

    def test_login_wording_is_stopped_and_never_treated_as_empty_results(self):
        for text in ("This page requires you to log on.", "You must be logged in to search. No hits found"):
            with self.subTest(text=text), self.assertRaises(recovery.RecoveryError) as raised:
                recovery.parse_search(text)
            self.assertEqual(str(raised.exception), "access_required")
            self.assertTrue(raised.exception.diagnostics["login_marker"])

    def test_diagnostic_allowlist_rejects_strings_invalid_counts_and_statuses(self):
        self.assertEqual(recovery.safe_diagnostics({"raw": "FAKE_SECRET", "forms": True, "images": -1,
            "http_status": 999, "anchors": "FAKE_SECRET", "search_form": 1, "text_chars": 100000000}), {})
        self.assertEqual(recovery.safe_diagnostics("FAKE_SECRET"), {})


class Response(io.BytesIO):
    def __init__(self, body=b"No hits found", content_type="text/html", url=None):
        super().__init__(body)
        self.headers = Message()
        self.headers["Content-Type"] = content_type
        self.url = url or recovery.search_url("gid", "1")

    def geturl(self):
        return self.url


class TransportTest(unittest.TestCase):
    def test_socks_credentials_and_remote_dns_reach_existing_adapter_correctly(self):
        original = socket.create_connection
        for proxy, username, password in [
            ("socks5://proxy.invalid:1080", None, None),
            ("socks5h://fake%40user:FAKE%3Apassword@proxy.invalid:1080", "fake@user", "FAKE:password"),
        ]:
            with self.subTest(proxy=proxy):
                sock = Mock()
                fake_socks = types.SimpleNamespace(SOCKS5=2, socksocket=lambda: sock)
                def open_response(*args, **kwargs):
                    socket.create_connection(("exhentai.org", 443), timeout=30)
                    return Response()
                with patch.dict(sys.modules, {"socks": fake_socks}), patch.object(urllib.request, "build_opener") as build:
                    build.return_value.open.side_effect = open_response
                    self.assertEqual(recovery.RecoveryClient("fake=FAKE_SECRET", proxy).lookup("gid", "1")["outcome"], "no_visible_hits")
                sock.set_proxy.assert_called_once_with(2, "proxy.invalid", 1080, rdns=True, username=username, password=password)
                sock.connect.assert_called_once_with(("exhentai.org", 443))
                self.assertIs(socket.create_connection, original)

    def test_only_saved_cookie_goes_to_constructed_main_site_get(self):
        with patch.object(urllib.request, "build_opener") as build:
            build.return_value.open.return_value = Response()
            client = recovery.RecoveryClient("test_cookie=FAKE_SECRET")
            self.assertEqual(client.lookup("gid", "1")["outcome"], "no_visible_hits")
            request = build.return_value.open.call_args.args[0]
            self.assertEqual(request.get_method(), "GET")
            self.assertIsNone(request.data)
            self.assertEqual(request.host, "exhentai.org")
            self.assertEqual(request.get_header("Cookie"), "test_cookie=FAKE_SECRET")
            self.assertIsInstance(build.call_args.args[1], recovery.NoRedirect)
            self.assertEqual(build.call_args.args[0].proxies, {})

    def test_redirect_policy_never_creates_a_second_request(self):
        handler = recovery.NoRedirect()
        request = urllib.request.Request(recovery.BASE, headers={"Cookie": "fake=secret"})
        for target in ("https://evil.invalid/", "https://ehgt.org/", "https://exhentai.org/login"):
            self.assertIsNone(handler.redirect_request(request, None, 302, "Redirect", {}, target))

    def test_real_redirect_does_not_send_fake_cookie_to_second_server(self):
        received = []
        class Destination(BaseHTTPRequestHandler):
            def do_GET(self):
                received.append(self.headers.get("Cookie"))
                self.send_response(200)
                self.end_headers()

            def log_message(self, *args):
                pass

        destination = ThreadingHTTPServer(("127.0.0.1", 0), Destination)
        target = f"http://127.0.0.1:{destination.server_port}/"
        class Redirect(Destination):
            def do_GET(self):
                self.send_response(302)
                self.send_header("Location", target)
                self.end_headers()

        source = ThreadingHTTPServer(("127.0.0.1", 0), Redirect)
        threads = [threading.Thread(target=server.serve_forever, daemon=True) for server in (destination, source)]
        for thread in threads:
            thread.start()
        try:
            with patch.object(recovery, "search_url", return_value=f"http://127.0.0.1:{source.server_port}/"):
                with self.assertRaisesRegex(recovery.RecoveryError, "^redirect_blocked$"):
                    recovery.RecoveryClient("fake=FAKE_SECRET").lookup("gid", "1")
            self.assertEqual(received, [])
        finally:
            for server in (source, destination):
                server.shutdown()
                server.server_close()
            for thread in threads:
                thread.join(timeout=5)

    def test_request_spacing_is_at_least_five_seconds(self):
        now, sleeps = [10.0], []
        def sleep(delay):
            sleeps.append(delay)
            now[0] += delay
        with patch.object(urllib.request, "build_opener") as build:
            build.return_value.open.side_effect = lambda *args, **kwargs: Response()
            client = recovery.RecoveryClient("fake=secret", interval=1, clock=lambda: now[0], sleep=sleep)
            client.lookup("gid", "1")
            client.lookup("gid", "1")
            self.assertEqual(sleeps, [5.0])

    def test_response_size_content_type_and_destination_are_bounded(self):
        fixtures = [(Response(content_type="image/gif"), "access_required"),
                    (Response(url="https://evil.invalid/"), "redirect_blocked"),
                    (Response(b"x" * (recovery.MAX_RESPONSE + 1)), "response_limit")]
        for response, reason in fixtures:
            with self.subTest(reason=reason), patch.object(urllib.request, "build_opener") as build:
                build.return_value.open.return_value = response
                with self.assertRaisesRegex(recovery.RecoveryError, "^" + reason + "$"):
                    recovery.RecoveryClient("fake=secret").lookup("gid", "1")

    def test_errors_never_echo_raw_bodies_urls_or_credentials(self):
        for code, reason in [(302, "redirect_blocked"), (403, "access_required"), (429, "rate_limited"), (509, "rate_limited"), (500, "http_error")]:
            with self.subTest(code=code), patch.object(urllib.request, "build_opener") as build:
                build.return_value.open.side_effect = urllib.error.HTTPError("private-url", code, "FAKE_SECRET", {}, io.BytesIO(b"FAKE_SECRET"))
                with self.assertRaises(recovery.RecoveryError) as raised:
                    recovery.RecoveryClient("fake=FAKE_SECRET").lookup("gid", "1")
                self.assertEqual(str(raised.exception), reason)
        with patch.object(urllib.request, "build_opener", side_effect=ValueError("FAKE_SECRET")):
            with self.assertRaisesRegex(recovery.RecoveryError, "^transport_failed$"):
                recovery.RecoveryClient("fake=FAKE_SECRET").lookup("gid", "1")

    def test_invalid_interval_and_missing_credentials_fail_before_network(self):
        with self.assertRaises(recovery.RecoveryError):
            recovery.RecoveryClient("")
        with self.assertRaises(recovery.RecoveryError):
            recovery.RecoveryClient("fake=secret", interval=float("nan"))

    def test_transport_attaches_status_and_size_without_exporting_html(self):
        with patch.object(urllib.request, "build_opener") as build:
            build.return_value.open.return_value = Response(b"<html>FAKE_SECRET</html>")
            with self.assertRaises(recovery.RecoveryError) as raised:
                recovery.RecoveryClient("fake=FAKE_SECRET").lookup("gid", "1")
        self.assertEqual(raised.exception.diagnostics["http_status"], 200)
        self.assertEqual(raised.exception.diagnostics["response_bytes"], 24)
        self.assertNotIn("FAKE_SECRET", json.dumps(raised.exception.diagnostics))

    def test_empty_http_200_is_a_stopped_response_not_an_empty_search_result(self):
        with patch.object(urllib.request, "build_opener") as build:
            build.return_value.open.return_value = Response(b"")
            with self.assertRaises(recovery.RecoveryError) as raised:
                recovery.RecoveryClient("fake=FAKE_SECRET").lookup("gid", "1")
        self.assertEqual(str(raised.exception), "empty_search_response")
        self.assertEqual(raised.exception.diagnostics["http_status"], 200)
        self.assertEqual(raised.exception.diagnostics["response_bytes"], 0)


class PlanAndRecoveryTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="hath-recovery-"))
        self.library_root = self.root / "library"
        (self.library_root / "images").mkdir(parents=True)
        self.database = self.root / "metadata.sqlite3"
        with closing(sqlite3.connect(self.database)) as conn:
            conn.executescript("""
                CREATE TABLE galleries(gid TEXT);
                CREATE TABLE hath_downloads(gallery_url TEXT);
                CREATE TABLE settings(key TEXT, value TEXT);
                INSERT INTO galleries VALUES ('3');
                INSERT INTO hath_downloads VALUES ('https://exhentai.org/g/1/abcdef/');
                INSERT INTO settings VALUES ('cookie_header', 'fake=FAKE_SECRET');
                INSERT INTO settings VALUES ('network_proxy', '');
                INSERT INTO settings VALUES ('request_interval_seconds', '3');
            """)
        rows = []
        for gid in ("1", "2", "3"):
            pages = []
            for position in (1, 5, 10):
                blob = f"synthetic-{gid}-{position}".encode()
                digest = hashlib.sha256(blob).hexdigest()
                (self.library_root / "images" / f"{digest}.png").write_bytes(blob)
                pages.append({"sha256": digest, "suffix": "png", "bytes": len(blob), "position": position, "width": 100, "height": 200})
            rows.append({"key": gid + "-1280", "gid": gid, "resolution": "1280", "ordering": "filename_lexical",
                         "image_count": 10, "pages": pages, "cover": None, "cover_vector": None, "body_vector": None})
        recovery.write_new(self.library_root / "index.json", {"schema": SCHEMA, "galleries": rows})
        self.library = LocalPageLibrary(self.library_root)

    def plan(self, limit=3):
        return recovery.recovery_plan(self.library, self.database, limit)

    def test_plan_is_network_free_excludes_known_metadata_and_prioritizes_body(self):
        before = hashlib.sha256(self.database.read_bytes()).hexdigest()
        with patch.object(urllib.request, "urlopen", side_effect=AssertionError("no network")):
            plan = self.plan(1)
        self.assertEqual(plan["missing"], 2)
        self.assertEqual(plan["unselected"], 1)
        self.assertEqual([sample["position"] for sample in plan["selected"][0]["samples"]], [5, 10])
        self.assertNotIn("FAKE_SECRET", json.dumps(plan))
        self.assertEqual(before, hashlib.sha256(self.database.read_bytes()).hexdigest())

    def test_runtime_settings_are_allowlisted_read_only_and_keep_minimum_interval(self):
        before = hashlib.sha256(self.database.read_bytes()).hexdigest()
        cookie, proxy, interval = recovery.runtime_settings(self.database)
        self.assertEqual(cookie, "fake=FAKE_SECRET")
        self.assertEqual((proxy, interval), ("", 5))
        self.assertEqual(before, hashlib.sha256(self.database.read_bytes()).hexdigest())
        with closing(recovery.connect_read_only(self.database)) as conn:
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute("DELETE FROM settings")

    def test_missing_database_is_not_created(self):
        missing = self.root / "absent.sqlite3"
        with self.assertRaises(sqlite3.OperationalError):
            recovery.recovery_plan(self.library, missing)
        self.assertFalse(missing.exists())

    def test_changed_sample_fails_before_search(self):
        self.library.load()
        image = self.library.rows["1-1280"]["pages"][1]
        (self.library_root / "images" / f"{image['sha256']}.png").write_bytes(b"changed")
        with self.assertRaises(ValueError):
            self.plan()

    def test_exact_gid_and_sample_are_corroboration_not_database_repair(self):
        before = hashlib.sha256(self.database.read_bytes()).hexdigest()
        events = []
        result = recovery.recover(self.plan(1), lambda kind, value: found(), record=events.append)
        candidate = result["items"][0]["candidates"][0]
        self.assertEqual(candidate["relationship"], "exact_gid_and_sample")
        self.assertEqual(len(candidate["sample_matches"]), 2)
        self.assertEqual(result["summary"]["status"], "partial")
        self.assertEqual(result["summary"]["pending"], 1)
        self.assertEqual(len(events), 3)
        self.assertEqual(before, hashlib.sha256(self.database.read_bytes()).hexdigest())

    def test_different_gid_and_shared_samples_never_become_original_identity(self):
        result = recovery.recover(self.plan(1), lambda kind, value: found("9" if kind == "gid" else "8"))
        candidates = result["items"][0]["candidates"]
        self.assertEqual([row["relationship"] for row in candidates], ["original_gid_local_or_sample", "possible_update_chain", "shared_sample_other_gid"])

    def test_unknown_exceptions_are_sanitized_and_stop_all_later_requests(self):
        calls, events = [], []
        def lookup(kind, value):
            calls.append(kind)
            raise RuntimeError("FAKE_SECRET")
        result = recovery.recover(self.plan(), lookup, record=events.append)
        self.assertEqual(calls, ["gid"])
        self.assertEqual(result["summary"]["stop_reason"], "lookup_failed")
        self.assertEqual(result["summary"]["completed"], 0)
        self.assertNotIn("FAKE_SECRET", json.dumps([result, events]))

    def test_rate_limit_stops_without_retry(self):
        def lookup(kind, value):
            raise recovery.RecoveryError("rate_limited")
        result = recovery.recover(self.plan(), lookup)
        self.assertEqual(result["summary"]["requests"], 1)
        self.assertEqual(result["summary"]["status"], "stopped")
        self.assertEqual(result["summary"]["stop_reason"], "rate_limited")

    def test_stop_diagnostics_are_resanitized_before_report_and_event_write(self):
        events = []
        def lookup(*args):
            error = recovery.RecoveryError("unrecognized_search_page")
            error.diagnostics = {"raw": "FAKE_SECRET", "forms": 2, "http_status": 200, "login_marker": False}
            raise error
        result = recovery.recover(self.plan(), lookup, record=events.append)
        expected = {"forms": 2, "http_status": 200, "login_marker": False}
        self.assertEqual(events[0]["diagnostics"], expected)
        self.assertEqual(result["items"][0]["stop_diagnostics"], expected)
        self.assertEqual(result["summary"]["requests"], 1)
        self.assertNotIn("FAKE_SECRET", json.dumps([result, events]))
        markdown = recovery.markdown_report(result)
        self.assertIn("unrecognized_search_page", markdown)
        self.assertIn('"http_status": 200', markdown)
        self.assertNotIn("FAKE_SECRET", markdown)

    def test_request_budget_is_explicitly_partial(self):
        result = recovery.recover(self.plan(), lambda kind, value: found(), max_requests=1)
        self.assertEqual(result["summary"]["status"], "partial")
        self.assertEqual(result["summary"]["completed"], 0)
        self.assertEqual(result["summary"]["stop_reason"], "request_budget")

    def test_lookup_cache_reuses_hashes_without_double_weighting(self):
        plan = self.plan(1)
        plan["selected"][0]["samples"].append(dict(plan["selected"][0]["samples"][0]))
        result = recovery.recover(plan, lambda kind, value: found())
        self.assertEqual(result["summary"]["requests"], 3)
        self.assertEqual(len(result["items"][0]["candidates"][0]["sample_matches"]), 2)

    def test_extra_transport_fields_are_not_exported_and_invalid_links_stop(self):
        result = recovery.recover(self.plan(1), lambda kind, value: {**found(), "raw": "FAKE_SECRET"})
        self.assertNotIn("FAKE_SECRET", json.dumps(result))
        result = recovery.recover(self.plan(1), lambda kind, value: {**found(), "candidates": [{"url": "https://evil.invalid/"}]})
        self.assertEqual(result["summary"]["status"], "stopped")
        self.assertNotIn("evil.invalid", json.dumps(result))

    def test_contradictory_or_duplicate_search_evidence_is_rejected(self):
        fixtures = [
            {**found(), "outcome": "no_visible_hits"},
            {**found(), "candidates": [hit(), hit()]},
            {"candidates": [], "has_more": True, "outcome": "no_visible_hits"},
            {"candidates": [], "has_more": False, "outcome": "candidates"},
        ]
        for fixture in fixtures:
            with self.subTest(fixture=fixture):
                result = recovery.recover(self.plan(1), lambda *args: fixture)
                self.assertEqual(result["summary"]["stop_reason"], "invalid_search_evidence")
                self.assertEqual(result["summary"]["requests"], 1)

    def test_failed_durable_write_aborts_before_any_later_lookup(self):
        calls = []
        def lookup(*args):
            calls.append(args)
            return found()
        def record(event):
            raise OSError("synthetic write failure")
        with self.assertRaises(OSError):
            recovery.recover(self.plan(), lookup, record=record)
        self.assertEqual(len(calls), 1)

    def test_candidate_pagination_is_retained_but_not_followed(self):
        result = recovery.recover(self.plan(1), lambda kind, value: found(more=True))
        self.assertTrue(all(row["has_more"] for row in result["items"][0]["lookups"]))
        self.assertEqual(result["summary"]["requests"], 3)

    def test_full_plan_can_complete_but_does_not_claim_all_results(self):
        result = recovery.recover(self.plan(), lambda kind, value: {"candidates": [], "has_more": False, "outcome": "no_visible_hits"})
        self.assertEqual(result["summary"]["status"], "completed_scope")
        self.assertEqual(result["summary"]["pending"], 0)
        self.assertIn("no_visible_candidates", recovery.markdown_report(result))

    def test_report_creation_never_overwrites_existing_evidence(self):
        path = self.root / "report.json"
        recovery.write_new(path, {"ok": True})
        with self.assertRaises(FileExistsError):
            recovery.write_new(path, {"ok": False})
        self.assertEqual(json.loads(path.read_text())["ok"], True)

    def test_output_ancestors_must_be_regular_directories(self):
        self.assertEqual(recovery.validate_output_directory(self.root / "new" / "report"), self.root / "new" / "report")
        with self.assertRaises(recovery.RecoveryError):
            recovery.validate_output_directory(self.root)
        path = self.root / "not-a-directory"
        path.write_text("fixture")
        with self.assertRaises(recovery.RecoveryError):
            recovery.validate_output_directory(path / "report")

    def run_cli(self, output, *extra):
        argv = ["recover_hath_metadata", "--library", str(self.library_root),
                "--database", str(self.database), "--output", str(output), *extra]
        stream = io.StringIO()
        with patch("sys.argv", argv), patch("sys.stdout", stream):
            code = cli.main()
        return code, json.loads(stream.getvalue())

    def test_cli_defaults_to_plan_without_settings_or_network(self):
        output = self.root / "dry-run"
        with patch.object(cli, "runtime_settings", side_effect=AssertionError("no cookie reads")), patch.object(cli, "RecoveryClient", side_effect=AssertionError("no network")):
            code, result = self.run_cli(output, "--limit", "1")
        self.assertEqual(code, 0)
        self.assertEqual(result["mode"], "plan")
        self.assertEqual({path.name for path in output.iterdir()}, {"plan.json"})
        self.assertNotIn("FAKE_SECRET", (output / "plan.json").read_text())

    def test_cli_execute_persists_private_evidence_and_sanitized_summary(self):
        output = self.root / "executed"
        before = hashlib.sha256(self.database.read_bytes()).hexdigest()
        with patch.object(cli, "RecoveryClient") as client:
            client.return_value.lookup.side_effect = lambda *args: found()
            code, result = self.run_cli(output, "--limit", "1", "--execute")
        self.assertEqual(code, 0)
        self.assertEqual(result["summary"]["status"], "partial")
        self.assertEqual({path.name for path in output.iterdir()}, {"plan.json", "events.jsonl", "report.json", "report.md"})
        events = [json.loads(line) for line in (output / "events.jsonl").read_text().splitlines()]
        self.assertEqual(len(events), 3)
        self.assertNotIn("https://", json.dumps(result))
        self.assertTrue(all("FAKE_SECRET" not in path.read_text() for path in output.iterdir()))
        self.assertEqual(before, hashlib.sha256(self.database.read_bytes()).hexdigest())

    def test_cli_refuses_existing_output_and_hides_exception_details(self):
        with patch.object(cli, "runtime_settings", side_effect=AssertionError("no settings")):
            code, result = self.run_cli(self.root, "--execute")
        self.assertEqual(code, 1)
        self.assertEqual(result, {"ok": False, "error": "recovery_failed"})
        with patch.object(cli, "recovery_plan", side_effect=RuntimeError("FAKE_SECRET")):
            code, result = self.run_cli(self.root / "failed")
        self.assertEqual(code, 1)
        self.assertNotIn("FAKE_SECRET", json.dumps(result))


if __name__ == "__main__":
    unittest.main()
