from contextlib import closing
from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
import urllib.error
import urllib.request
from unittest.mock import Mock, patch

from exh_rec import hath_metadata_repair as repair
from exh_rec import hath_recovery as recovery
from exh_rec.db import SCHEMA
from scripts import apply_hath_metadata as cli
from tests import test_hath_recovery as fixtures


def target(gid="1"):
    return {"gid": gid, "token": "abcdef", "url": f"https://exhentai.org/g/{gid}/abcdef/", "key": gid + "-1280"}


def entry(gid=1):
    return {"gid": gid, "token": "abcdef", "title": "Fetched &amp; verified", "title_jpn": "Original",
            "category": "Manga", "uploader": "fixture", "posted": "1700000000", "filecount": "20",
            "rating": "4.5", "tags": ["language:english"], "thumb": "https://ehgt.org/t/fixture.jpg"}


class MetadataTransportTest(unittest.TestCase):
    def response(self, body=None, content_type="application/json", url=repair.EH_API_URL):
        return fixtures.Response(json.dumps(body if body is not None else {"gmetadata": [entry()]}).encode(), content_type, url)

    def test_fixed_api_allowlisted_metadata_and_pacing(self):
        clock, sleep = Mock(return_value=10), Mock()
        client = repair.MetadataClient("fake=FAKE_SECRET", interval=0, clock=clock, sleep=sleep)
        with patch.object(urllib.request, "build_opener") as build:
            build.return_value.open.side_effect = [self.response(), self.response()]
            self.assertEqual(client.fetch([target()]), [entry()])
            client.fetch([target()])
            request = build.return_value.open.call_args.args[0]
            self.assertEqual(request.full_url, repair.EH_API_URL)
            self.assertEqual(json.loads(request.data), {"method": "gdata", "namespace": 1, "gidlist": [[1, "abcdef"]]})
            self.assertEqual(request.get_header("Accept-encoding"), "identity")
            self.assertTrue(any(isinstance(handler, recovery.NoRedirect) for handler in build.call_args.args))
            self.assertTrue(any(isinstance(handler, urllib.request.ProxyHandler) and not handler.proxies
                                for handler in build.call_args.args))
        sleep.assert_called_once_with(5)

    def test_identity_duplicate_partial_and_api_errors_reject_whole_batch(self):
        for rows in ([], [entry(2)], [entry(), entry()], [{"gid": 1, "error": "FAKE_SECRET"}],
                     [{**entry(), "token": "bad"}], [{**entry(), "gid": True}]):
            with self.subTest(rows=rows), self.assertRaises(recovery.RecoveryError):
                repair.parse_response(json.dumps({"gmetadata": rows}), [target()])

    def test_invalid_fields_and_thumbnail_origins_fail_closed(self):
        for change in ({"title": ""}, {"filecount": "0"}, {"rating": "nan"}, {"tags": "bad"},
                       {"thumb": "https://evil.invalid/image"}, {"thumb": "http://ehgt.org/image"},
                       {"thumb": "https://user:secret@ehgt.org/image"}, {"parent_key": "../bad", "parent_gid": "2"}):
            with self.subTest(change=change), self.assertRaises(recovery.RecoveryError):
                repair.validated_entry({**entry(), **change}, target())
        self.assertEqual(repair.validated_entry({**entry(), "raw_secret": "FAKE_SECRET"}, target()), entry())

    def test_response_guards_and_sanitized_failures(self):
        cases = [(self.response(url="https://evil.invalid"), "redirect_blocked"),
                    (self.response(content_type="image/png"), "unsupported_metadata_media_type"),
                    (fixtures.Response(b"x" * (repair.MAX_RESPONSE + 1), "application/json", repair.EH_API_URL), "response_limit")]
        encoded = self.response()
        encoded.headers["Content-Encoding"] = "gzip"
        cases.append((encoded, "unsupported_response_encoding"))
        for response, reason in cases:
            with self.subTest(reason=reason), patch.object(urllib.request, "build_opener") as build:
                build.return_value.open.return_value = response
                with self.assertRaisesRegex(recovery.RecoveryError, "^" + reason + "$"):
                    repair.MetadataClient("fake=FAKE_SECRET").fetch([target()])
        for code, reason in [(302, "redirect_blocked"), (429, "rate_limited"), (403, "access_required"), (500, "http_error")]:
            with self.subTest(code=code), patch.object(urllib.request, "build_opener") as build:
                build.return_value.open.side_effect = urllib.error.HTTPError("FAKE_SECRET", code, "FAKE_SECRET", {}, io.BytesIO())
                with self.assertRaisesRegex(recovery.RecoveryError, "^" + reason + "$"):
                    repair.MetadataClient("fake=FAKE_SECRET").fetch([target()])
        for body in (b"FAKE_SECRET", b'{"gmetadata":[],"gmetadata":[]}'):
            with self.assertRaises(recovery.RecoveryError) as raised:
                repair.parse_response(body, [target()])
            self.assertNotIn("FAKE_SECRET", str(raised.exception))

    def test_socks_uses_existing_scoped_adapter(self):
        with patch.object(repair, "patched_socks_create_connection") as socks, patch.object(urllib.request, "build_opener") as build:
            build.return_value.open.return_value = self.response()
            repair.MetadataClient("fake=FAKE_SECRET", "socks5h://proxy.invalid:1080").fetch([target()])
            self.assertEqual(socks.call_args.args[1:], ("proxy.invalid", 1080))
            self.assertTrue(socks.call_args.kwargs["rdns"])

    def test_text_mime_requires_valid_json_and_exact_identities(self):
        for media_type in ("text/html", "text/plain", "text/json"):
            with self.subTest(media_type=media_type), patch.object(urllib.request, "build_opener") as build:
                build.return_value.open.return_value = self.response(content_type=media_type)
                client = repair.MetadataClient("fake=FAKE_SECRET")
                self.assertEqual(client.fetch([target()]), [entry()])
                self.assertTrue(client.diagnostics["json_object"])
                self.assertTrue(client.diagnostics["metadata_array"])
                self.assertNotIn("FAKE_SECRET", json.dumps(client.diagnostics))
        for body, reason in [(b"", "empty_metadata_response"), (b"<html>FAKE_SECRET</html>", "unrecognized_metadata_response"),
                             (b"Temporarily banned due to an excessive request rate", "rate_limited"),
                             (b'{"error":"FAKE_SECRET"}', "metadata_api_error")]:
            with self.subTest(reason=reason), patch.object(urllib.request, "build_opener") as build:
                build.return_value.open.return_value = fixtures.Response(body, "text/html", repair.EH_API_URL)
                client = repair.MetadataClient("fake=FAKE_SECRET")
                with self.assertRaisesRegex(recovery.RecoveryError, "^" + reason + "$"):
                    client.fetch([target()])
                self.assertTrue(client.diagnostics["html_content_type"])
                self.assertEqual(client.diagnostics["response_bytes"], len(body))
                self.assertNotIn("FAKE_SECRET", json.dumps(client.diagnostics))


class EvidenceTest(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.PlanAndRecoveryTest()
        self.fixture.setUp()
        self.directory = self.fixture.root / "evidence"
        self.directory.mkdir()
        self.plan = self.fixture.plan()
        recovery.write_new(self.directory / "plan.json", self.plan)
        events = []
        def lookup(kind, value):
            gid = value if kind == "gid" else next(row["gid"] for row in self.plan["selected"]
                if any(sample["sha1"] == value for sample in row["samples"]))
            return fixtures.found(gid)
        recovery.recover(self.plan, lookup, record=events.append)
        with (self.directory / "events.jsonl").open("x", encoding="utf-8") as stream:
            for event in events:
                stream.write(json.dumps(event) + "\n")

    def test_rebuild_uses_journal_not_report_or_preview_and_hashes_samples(self):
        targets, evidence = repair.targets_from_evidence(self.directory, self.fixture.library)
        self.assertEqual(targets, [target("1"), target("2")])
        self.assertEqual(evidence["library_index_sha256"], self.plan["library_index_sha256"])
        sample = self.plan["selected"][0]["samples"][0]
        (self.fixture.library_root / "images" / (sample["sha256"] + ".png")).write_bytes(b"tampered")
        with self.assertRaises(ValueError):
            repair.targets_from_evidence(self.directory, self.fixture.library)

    def test_stale_and_partial_evidence_cannot_become_apply_targets(self):
        with self.assertRaises(recovery.RecoveryError):
            repair.targets_from_evidence(self.directory, self.fixture.library, now=datetime.now(timezone.utc) + timedelta(days=2))
        events = (self.directory / "events.jsonl").read_text(encoding="utf-8").splitlines()
        (self.directory / "events.jsonl").write_text("\n".join(events[:-1]) + "\n", encoding="utf-8")
        with self.assertRaises(recovery.RecoveryError):
            repair.targets_from_evidence(self.directory, self.fixture.library)

    def test_stages_are_bound_to_exact_evidence_age_and_unique_gids(self):
        targets, evidence = repair.targets_from_evidence(self.directory, self.fixture.library)
        stage = self.fixture.root / "stage.json"
        recovery.write_new(stage, {"schema": repair.SCHEMA, "created_at": datetime.now(timezone.utc).isoformat(),
                                   "evidence": evidence, "entries": [entry()]})
        self.assertEqual(repair.staged_rows([stage], targets, evidence), [(target(), entry())])
        with self.assertRaises(recovery.RecoveryError):
            repair.staged_rows([stage, stage], targets, evidence)
        with self.assertRaises(recovery.RecoveryError):
            repair.staged_rows([stage], targets, {**evidence, "plan_sha256": "changed"})
        with self.assertRaises(recovery.RecoveryError):
            repair.staged_rows([stage], targets, evidence, now=datetime.now(timezone.utc) + timedelta(days=2))

    def test_cli_persists_only_fixed_failure_reason_and_never_retries(self):
        output = self.fixture.root / "failed-fetch"
        with patch.object(cli, "runtime_settings", return_value=("fake=FAKE_SECRET", "", 5)), \
             patch.object(cli.MetadataClient, "fetch", side_effect=recovery.RecoveryError("rate_limited")) as fetch, \
             patch("sys.argv", ["apply_hath_metadata.py", "--evidence", str(self.directory), "--library", str(self.fixture.library_root),
                                "--database", str(self.fixture.database), "--output", str(output), "--fetch", "--confirmed"]), \
             patch.object(cli, "missing_targets", return_value=[target(), target("2")]), patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(cli.main(), 1)
            fetch.assert_called_once()
        failure = json.loads((output / "failure.json").read_text(encoding="utf-8"))
        self.assertEqual(failure, {"ok": False, "error": "rate_limited"})
        self.assertFalse((output / "metadata.json").exists())

    def test_cli_failure_retains_safe_transport_markers(self):
        output = self.fixture.root / "transport-failure"
        diagnostics = {"http_status": 200, "html_content_type": True, "json_content_type": False, "response_bytes": 18}
        def fail(client, targets):
            client.diagnostics = diagnostics
            raise recovery.RecoveryError("unrecognized_metadata_response")
        with patch.object(cli, "runtime_settings", return_value=("fake=FAKE_SECRET", "", 5)), \
             patch.object(cli.MetadataClient, "fetch", fail), \
             patch("sys.argv", ["apply_hath_metadata.py", "--evidence", str(self.directory), "--library", str(self.fixture.library_root),
                                "--database", str(self.fixture.database), "--output", str(output), "--fetch", "--confirmed"]), \
             patch.object(cli, "missing_targets", return_value=[target(), target("2")]), patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(cli.main(), 1)
        failure = json.loads((output / "failure.json").read_text(encoding="utf-8"))
        self.assertEqual(failure["diagnostics"], diagnostics)
        self.assertNotIn("FAKE_SECRET", json.dumps(failure))


class InsertTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="hath-metadata-insert-"))
        self.database = self.root / "live.sqlite3"
        with closing(sqlite3.connect(self.database)) as conn:
            conn.executescript(SCHEMA)
            conn.execute("INSERT INTO galleries(url,gid,title) VALUES ('existing','2','Preserve me')")
            conn.execute("INSERT INTO settings VALUES ('fixture', 'FAKE_SECRET')")
            conn.execute("INSERT INTO feedback(gallery_url,vote) VALUES ('existing',1)")
            conn.commit()

    def test_consistent_backup_insert_only_and_no_training_side_effects(self):
        rows = [(target(), entry()), (target("2"), entry(2))]
        result = repair.apply_metadata(self.database, rows, self.root / "backup.sqlite3", self.root / "receipt.json")
        self.assertEqual((result["inserted"], result["skipped"]), (1, 1))
        with closing(sqlite3.connect(self.database)) as conn, closing(sqlite3.connect(self.root / "backup.sqlite3")) as old:
            self.assertEqual(old.execute("SELECT COUNT(*) FROM galleries").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT title FROM galleries WHERE gid='2'").fetchone()[0], "Preserve me")
            self.assertEqual(conn.execute("SELECT title,review_excluded,page_count,detail_fetched_at,samples_json,visual_embedding_json FROM galleries WHERE gid='1'").fetchone(),
                             ("Fetched & verified", 1, 20, None, "[]", None))
            for table in ("settings", "feedback", "gallery_feature_snapshots", "model_training_runs", "feature_weights", "bootstrap_tags"):
                self.assertEqual(conn.execute("SELECT * FROM " + table).fetchall(), old.execute("SELECT * FROM " + table).fetchall())
        again = repair.apply_metadata(self.database, rows, self.root / "backup2.sqlite3", self.root / "receipt2.json")
        self.assertEqual(again["inserted"], 0)

    def test_backup_failure_blocks_write(self):
        with patch.object(repair, "connect_read_only", side_effect=RuntimeError("fixture")):
            with self.assertRaises(RuntimeError):
                repair.apply_metadata(self.database, [(target(), entry())], self.root / "backup.sqlite3", self.root / "receipt.json")
        with closing(sqlite3.connect(self.database)) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM galleries").fetchone()[0], 1)

    def test_late_insert_failure_rolls_back_entire_batch_preserving_backup(self):
        with closing(sqlite3.connect(self.database)) as conn:
            conn.executescript("CREATE TRIGGER reject_three BEFORE INSERT ON galleries WHEN NEW.gid='3' BEGIN SELECT RAISE(ABORT,'fixture'); END;")
        with self.assertRaises(sqlite3.IntegrityError):
            repair.apply_metadata(self.database, [(target(), entry()), (target("3"), entry(3))],
                                  self.root / "backup.sqlite3", self.root / "receipt.json")
        with closing(sqlite3.connect(self.database)) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM galleries").fetchone()[0], 1)
        self.assertTrue((self.root / "backup.sqlite3").exists())

    def test_existing_backup_is_never_clobbered(self):
        backup = self.root / "backup.sqlite3"
        backup.write_bytes(b"preserve")
        with self.assertRaises(recovery.RecoveryError):
            repair.apply_metadata(self.database, [(target(), entry())], backup, self.root / "receipt.json")
        self.assertEqual(backup.read_bytes(), b"preserve")

    def test_cli_requires_confirmation_before_authentication(self):
        with patch.object(cli, "runtime_settings") as auth, patch("sys.argv", ["apply_hath_metadata.py", "--evidence", str(self.root),
                    "--output", str(self.root / "out"), "--fetch"]), patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(cli.main(), 1)
            auth.assert_not_called()


if __name__ == "__main__":
    unittest.main()
