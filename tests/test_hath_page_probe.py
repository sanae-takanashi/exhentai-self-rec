import ast
from contextlib import closing
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import stat
import tempfile
import unittest
from unittest.mock import MagicMock, patch
import zipfile

from PIL import Image

from hath_observer.core import parse_download_directory_name
from scripts.probe_hath_pages import (
    collect_batches, collect_remote, copy_cached_previews, fetch_remote, join_metadata, main,
    read_bounded, unpack_verified,
)
from scripts.compare_hath_pages import compare, cosine, reduced_image, verified_blob, verify_experiment
from scripts.plan_hath_metadata import plan


def fixture():
    # Retained deliberately: these checks never permanently delete files.
    return Path(tempfile.mkdtemp(prefix="hath-pages-fixture-"))


def picture(color="red", size=(600, 900)):
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format="PNG")
    return buffer.getvalue()


def gallery(root, gid="123", count=5):
    path = root / ("Private title [%s-1280x]" % gid)
    path.mkdir()
    (path / "galleryinfo.txt").write_text("https://exhentai.org/g/%s/abcdef/" % gid, encoding="utf-8")
    for number in range(1, count + 1):
        (path / ("%d-page.png" % number)).write_bytes(picture())
    return path


def archive_mutation(wire, transform):
    with zipfile.ZipFile(io.BytesIO(wire)) as source:
        entries = {name: source.read(name) for name in source.namelist()}
    transform(entries)
    target = io.BytesIO()
    with zipfile.ZipFile(target, "w") as archive:
        for name, blob in entries.items():
            archive.writestr(name, blob)
    return target.getvalue()


class HathPageProbeTests(unittest.TestCase):
    def test_collection_is_bounded_numeric_ordered_and_sanitized(self):
        root = fixture()
        gallery(root, count=12)
        incomplete = root / "Incomplete [124-1280x]"
        incomplete.mkdir()
        wire = collect_remote(root, parse_download_directory_name)
        manifest = unpack_verified(wire, root / "dataset")
        row = manifest["galleries"][0]
        self.assertEqual([page["position"] for page in row["pages"]], [1, 7, 12])
        self.assertEqual(row["ordering"], "numeric_filename_prefix")
        self.assertTrue(row["marker_has_matching_gallery_url"])
        self.assertEqual(manifest["skipped"]["incomplete_gallery"], 1)
        self.assertNotIn("Private title", json.dumps(manifest))
        self.assertNotIn("abcdef", json.dumps(manifest))
        self.assertEqual(len(list((root / "dataset/images/123-1280").iterdir())), 3)

    def test_missing_marker_and_linked_images_are_excluded(self):
        root = fixture()
        path = gallery(root)
        with patch.object(Path, "is_symlink", lambda path: path.name == "1-page.png"):
            wire = collect_remote(root, parse_download_directory_name)
        result = unpack_verified(wire, root / "dataset")
        self.assertEqual(result["galleries"][0]["image_count"], 4)

    def test_byte_budget_skips_without_transferring_extra_bytes(self):
        root = fixture()
        gallery(root)
        with patch("scripts.probe_hath_pages.TOTAL_LIMIT", 1):
            wire = collect_remote(root, parse_download_directory_name)
        result = unpack_verified(wire, root / "dataset")
        self.assertEqual(result["sample_bytes"], 0)
        self.assertEqual(result["skipped"]["byte_limit"], 3)

    def test_duplicate_gallery_identity_is_not_arbitrarily_chosen(self):
        root = fixture()
        gallery(root)
        other = root / "Other [123-1280x]"
        other.mkdir()
        (other / "galleryinfo.txt").write_text("fixture", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "ambiguous_gallery"):
            collect_remote(root, parse_download_directory_name)

    def test_oversized_and_linked_files_are_not_read(self):
        root = fixture()
        path = root / "file"
        path.write_bytes(b"fixture")
        with self.assertRaisesRegex(ValueError, "oversized"):
            read_bounded(path, 1)
        mode = path.lstat()
        with patch.object(Path, "lstat") as lstat:
            lstat.return_value = type("FakeStat", (), {"st_mode": stat.S_IFLNK, "st_size": mode.st_size})()
            with self.assertRaises(ValueError):
                read_bounded(path, 100)

    def test_traversal_and_unlisted_archive_members_fail_before_writing(self):
        root = fixture()
        gallery(root)
        wire = collect_remote(root, parse_download_directory_name)
        for name in ("../outside.png", "images/123-1280/999999.png"):
            altered = archive_mutation(wire, lambda entries: entries.update({name: b"fake"}))
            output = root / "dataset"
            with self.assertRaises(ValueError):
                unpack_verified(altered, output)
            self.assertFalse(output.exists())

    def test_archive_digest_mismatch_fails_before_writing(self):
        root = fixture()
        gallery(root)
        wire = collect_remote(root, parse_download_directory_name)
        def corrupt(entries):
            name = next(name for name in entries if name != "manifest.json")
            entries[name] = b"corrupt"
        altered = archive_mutation(wire, corrupt)
        with self.assertRaisesRegex(ValueError, "digest_mismatch"):
            unpack_verified(altered, root / "dataset")
        self.assertFalse((root / "dataset").exists())

    def test_existing_output_is_preserved_before_ssh(self):
        root = fixture()
        with patch("sys.argv", ["probe", "--output", str(root)]), patch("scripts.probe_hath_pages.fetch_remote") as fetch:
            with self.assertRaisesRegex(ValueError, "new_output"):
                main()
            fetch.assert_not_called()

    def test_invalid_host_is_rejected_without_process(self):
        with patch("scripts.probe_hath_pages.subprocess.Popen") as process:
            with self.assertRaisesRegex(ValueError, "invalid_ssh_host"):
                fetch_remote({"hath_ssh_host": "-oUnsafe=yes"})
            process.assert_not_called()

    def test_transport_is_bounded_and_reports_only_public_facts(self):
        root = fixture()
        process = MagicMock()
        process.__enter__.return_value = process
        process.stdout = io.BytesIO(b"fixture transport")
        process.wait.return_value = 0
        process.poll.return_value = 0
        process.returncode = 0
        config = {"hath_ssh_host": "fixture-host", "hath_ssh_config": "private-config-path"}
        with patch("scripts.probe_hath_pages.subprocess.Popen", return_value=process) as run:
            wire = fetch_remote(config, root / "transport.json")
        self.assertEqual(wire, b"fixture transport")
        command = run.call_args.args[0]
        self.assertIn("StrictHostKeyChecking=yes", command)
        self.assertIn("ClearAllForwardings=yes", command)
        self.assertEqual(command[-4:], ["/usr/bin/python3", "-B", "-", "--remote-probe"])
        report = json.loads((root / "transport.json").read_text(encoding="utf-8"))
        self.assertEqual(report["bytes_received"], len(wire))
        self.assertFalse(report["timed_out"])
        self.assertNotIn("private-config-path", json.dumps(report))
        self.assertNotIn("fixture transport", json.dumps(report))

    def test_transport_limit_kills_process_and_does_not_accept_partial_archive(self):
        process = MagicMock()
        process.__enter__.return_value = process
        process.stdout = io.BytesIO(b"too much fixture data")
        process.poll.return_value = 1
        with patch("scripts.probe_hath_pages.subprocess.Popen", return_value=process), patch("scripts.probe_hath_pages.WIRE_LIMIT", 4):
            with self.assertRaisesRegex(ValueError, "remote_output_limit"):
                fetch_remote({"hath_ssh_host": "fixture-host", "hath_ssh_config": "unused"})
        process.kill.assert_called_once()

    def test_invalid_selection_is_rejected_before_remote_shell(self):
        with patch("scripts.probe_hath_pages.subprocess.Popen") as process:
            with self.assertRaisesRegex(ValueError, "invalid_gallery_selection"):
                fetch_remote({"hath_ssh_host": "fixture", "hath_ssh_config": "unused"}, selected_keys=["123;unsafe"])
            process.assert_not_called()

    def test_batches_resume_verified_checkpoints_without_network(self):
        root = fixture()
        for gid in ("123", "124", "125"):
            gallery(root, gid)
        output = root / "dataset"
        def fetch(config, transport_report=None, selected_keys=None, byte_budget=None):
            return collect_remote(root, parse_download_directory_name, selected_keys, byte_budget)
        with patch("scripts.probe_hath_pages.fetch_remote", side_effect=fetch) as request, patch("scripts.probe_hath_pages.BATCH_SIZE", 1):
            result = collect_batches({}, output)
            self.assertEqual(request.call_count, 4)
        self.assertEqual(len(result["galleries"]), 3)
        self.assertEqual(sum(len(row["pages"]) for row in result["galleries"]), 9)
        with patch("scripts.probe_hath_pages.fetch_remote", side_effect=AssertionError("network forbidden")), patch("scripts.probe_hath_pages.BATCH_SIZE", 1):
            resumed = collect_batches({}, output, resume=True)
        self.assertEqual(result, resumed)
        page = resumed["galleries"][0]["pages"][0]
        (output / page["path"]).write_bytes(b"corrupt fixture")
        with patch("scripts.probe_hath_pages.BATCH_SIZE", 1):
            with self.assertRaisesRegex(ValueError, "checkpoint_digest_mismatch"):
                collect_batches({}, output, resume=True)

    def test_inventory_selection_transfers_no_image_bytes(self):
        root = fixture()
        gallery(root)
        result = unpack_verified(collect_remote(root, parse_download_directory_name, []), root / "dataset")
        self.assertEqual(result["sample_bytes"], 0)
        self.assertEqual(len(result["galleries"]), 1)
        self.assertEqual(result["galleries"][0]["pages"], [])

    def test_interrupted_batch_run_resumes_only_missing_batches(self):
        root = fixture()
        for gid in ("123", "124", "125"):
            gallery(root, gid)
        output = root / "dataset"
        calls = []
        def interrupted(config, transport_report=None, selected_keys=None, byte_budget=None):
            calls.append(selected_keys)
            if len(calls) == 3:
                raise ValueError("fixture_transport_interruption")
            return collect_remote(root, parse_download_directory_name, selected_keys, byte_budget)
        with patch("scripts.probe_hath_pages.fetch_remote", side_effect=interrupted), patch("scripts.probe_hath_pages.BATCH_SIZE", 1):
            with self.assertRaisesRegex(ValueError, "fixture_transport_interruption"):
                collect_batches({}, output)
        self.assertTrue((output / "batch-000/source.json").exists())
        self.assertFalse((output / "batch-001").exists())
        def complete(config, transport_report=None, selected_keys=None, byte_budget=None):
            return collect_remote(root, parse_download_directory_name, selected_keys, byte_budget)
        with patch("scripts.probe_hath_pages.fetch_remote", side_effect=complete) as request, patch("scripts.probe_hath_pages.BATCH_SIZE", 1):
            resumed = collect_batches({}, output, resume=True)
        self.assertEqual(request.call_count, 2)
        self.assertEqual(sum(len(row["pages"]) for row in resumed["galleries"]), 9)

    def test_total_image_budget_is_enforced_across_batches(self):
        root = fixture()
        for gid in ("123", "124", "125"):
            gallery(root, gid)
        budget = len(picture()) * 4
        def fetch(config, transport_report=None, selected_keys=None, byte_budget=None):
            return collect_remote(root, parse_download_directory_name, selected_keys, byte_budget)
        with patch("scripts.probe_hath_pages.fetch_remote", side_effect=fetch), patch("scripts.probe_hath_pages.BATCH_SIZE", 1), patch("scripts.probe_hath_pages.TOTAL_LIMIT", budget):
            result = collect_batches({}, root / "dataset")
        self.assertEqual(result["sample_bytes"], budget)
        self.assertEqual(sum(len(row["pages"]) for row in result["galleries"]), 4)
        self.assertEqual(result["skipped"]["byte_limit"], 5)

    def test_changed_completed_gallery_does_not_silently_mix_snapshots(self):
        root = fixture()
        path = gallery(root)
        def fetch(config, transport_report=None, selected_keys=None, byte_budget=None):
            if selected_keys:
                (path / "6-page.png").write_bytes(picture())
            return collect_remote(root, parse_download_directory_name, selected_keys, byte_budget)
        with patch("scripts.probe_hath_pages.fetch_remote", side_effect=fetch):
            with self.assertRaisesRegex(ValueError, "gallery_changed_between_batches"):
                collect_batches({}, root / "dataset")
        self.assertFalse((root / "dataset/manifest.json").exists())

    def test_partial_batch_directory_is_preserved_not_deleted(self):
        root = fixture()
        gallery(root)
        output = root / "dataset"
        inventory = unpack_verified(collect_remote(root, parse_download_directory_name, []), output / "inventory")
        (output / "inventory.json").write_text(json.dumps(inventory), encoding="utf-8")
        partial = output / "batch-000"
        partial.mkdir()
        retained = partial / "retained-fixture.txt"
        retained.write_text("incomplete fixture", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "partial_batch_requires_new_output"):
            collect_batches({}, output, resume=True)
        self.assertEqual(retained.read_text(encoding="utf-8"), "incomplete fixture")

    def test_remote_code_remains_python36_compatible(self):
        source = Path(__file__).resolve().parents[1] / "scripts/probe_hath_pages.py"
        ast.parse(source.read_text(encoding="utf-8"), feature_version=(3, 6))

    def test_metadata_gap_reasons_read_only_and_no_private_fields(self):
        root = fixture()
        database = root / "db.sqlite3"
        with closing(sqlite3.connect(database)) as conn:
            conn.executescript("""
                CREATE TABLE galleries(gid, url, thumb_url, samples_json, page_count, detail_fetched_at, last_seen_at);
                CREATE TABLE hath_downloads(gid, resolution, status, gallery_url);
                CREATE TABLE settings(key, value);
                INSERT INTO settings VALUES ('cookie_header', 'NEVER_EXPORT_THIS');
                INSERT INTO galleries VALUES ('123', 'private-url', 'private-thumb', '[{"url":"private-sample","x":0,"y":0,"w":20,"h":20}]', 5, '2026', '2026');
                INSERT INTO hath_downloads VALUES ('123', '1280', 'completed', 'private-url');
                INSERT INTO hath_downloads VALUES ('124', '1280', 'completed', NULL);
                INSERT INTO hath_downloads VALUES ('125', '1280', 'completed', 'private-unknown-url');
            """)
        before = hashlib.sha256(database.read_bytes()).hexdigest()
        rows = [{"gid": gid, "resolution": "1280", "image_count": 5} for gid in ("123", "124", "125", "126")]
        internal = join_metadata(database, rows)
        self.assertEqual(rows[0]["thumbnail_sample_entries"], 1)
        self.assertTrue(rows[0]["metadata_image_count_matches"])
        self.assertEqual([row["gap_reason"] for row in rows], [None, "completion_has_no_gallery_url", "linked_url_missing_from_metadata", "no_completion_record"])
        self.assertEqual(internal[("123", "1280")]["cover"], "private-thumb")
        self.assertEqual(before, hashlib.sha256(database.read_bytes()).hexdigest())
        for private in ("private-url", "private-thumb", "private-sample", "NEVER_EXPORT_THIS"):
            self.assertNotIn(private, json.dumps(rows))

    def test_missing_database_is_not_created(self):
        path = fixture() / "missing.sqlite3"
        with self.assertRaises(sqlite3.OperationalError):
            join_metadata(path, [])
        self.assertFalse(path.exists())

    def test_recovery_plan_checks_only_known_url_columns_and_exports_no_urls(self):
        root = fixture()
        database = root / "db.sqlite3"
        with closing(sqlite3.connect(database)) as conn:
            conn.executescript("""
                CREATE TABLE galleries(gid, url, thumb_url, samples_json, page_count, detail_fetched_at, last_seen_at);
                CREATE TABLE hath_downloads(gid, resolution, status, gallery_url);
                CREATE TABLE feedback(gallery_url);
                CREATE TABLE settings(key, value);
                INSERT INTO settings VALUES ('cookie_header', 'NEVER_EXPORT_THIS');
                INSERT INTO hath_downloads VALUES ('123', '1280', 'completed', NULL);
                INSERT INTO hath_downloads VALUES ('124', '1280', 'completed', NULL);
                INSERT INTO galleries VALUES ('125', 'https://exhentai.org/g/125/abcdef/', NULL, '[]', 5, '2026', '2026');
                INSERT INTO feedback VALUES ('https://exhentai.org/g/123/abcdef/');
                INSERT INTO feedback VALUES ('https://example.invalid/g/124/abcdef/');
            """)
        before = hashlib.sha256(database.read_bytes()).hexdigest()
        source = {"galleries": [{"gid": gid, "resolution": "1280", "image_count": 5} for gid in ("123", "124", "125")]}
        result = plan(database, source)
        self.assertEqual(result["summary"]["metadata_missing"], 2)
        self.assertEqual(result["summary"]["missing_metadata_with_known_url"], 1)
        self.assertEqual(result["summary"]["thumbnail_sample_entries_missing"], 1)
        self.assertEqual(result["items"][0]["known_url_evidence_tables"], ["feedback"])
        self.assertEqual(result["items"][1]["next_action"], "gallery_url_not_found_in_checked_sources")
        self.assertEqual(before, hashlib.sha256(database.read_bytes()).hexdigest())
        for value in ("abcdef", "https://", "NEVER_EXPORT_THIS"):
            self.assertNotIn(value, json.dumps(result))
        self.assertNotIn("metadata_status", source["galleries"][0])

    def test_cached_sprite_is_cropped_without_network_or_metadata_copy(self):
        root = fixture()
        cache = root / "cache"
        cache.mkdir()
        url = "https://example.invalid/private-sheet"
        (cache / (hashlib.sha256(url.encode()).hexdigest() + ".bin")).write_bytes(picture(size=(400, 200)))
        rows = [{"gid": "123", "resolution": "1280"}]
        internal = {("123", "1280"): {"samples": [{"url": url, "x": 100, "y": 0, "w": 100, "h": 200}], "cover": None}}
        with patch("urllib.request.urlopen", side_effect=AssertionError("network forbidden")):
            failures = copy_cached_previews(cache, root / "dataset", rows, internal)
        self.assertFalse(failures)
        blob = verified_blob(root / "dataset", rows[0]["previews"][0])
        with Image.open(io.BytesIO(blob)) as image:
            self.assertEqual(image.size, (100, 200))
        self.assertNotIn(url, json.dumps(rows))

    def test_missing_cache_never_triggers_fetch(self):
        root = fixture()
        rows = [{"gid": "123", "resolution": "1280"}]
        with patch("urllib.request.urlopen", side_effect=AssertionError("network forbidden")):
            failures = copy_cached_previews(root, root / "dataset", rows,
                                           {("123", "1280"): {"cover": "https://example.invalid/missing"}})
        self.assertEqual(failures, {"not_cached": 1})
        self.assertEqual(rows[0]["previews"], [])

    def test_existing_cropped_cache_is_used_without_sprite_sheet(self):
        root = fixture()
        url = "https://example.invalid/missing-sheet"
        key = url + "#0,0,100,200"
        (root / (hashlib.sha256(key.encode()).hexdigest() + ".bin")).write_bytes(picture(size=(100, 200)))
        rows = [{"gid": "123", "resolution": "1280"}]
        failures = copy_cached_previews(root, root / "dataset", rows,
                                       {("123", "1280"): {"samples": [{"url": url, "x": 0, "y": 0, "w": 100, "h": 200}]}})
        self.assertFalse(failures)
        self.assertEqual(len(rows[0]["previews"]), 1)

    def test_identical_cover_and_sample_are_not_weighted_twice(self):
        root = fixture()
        url = "https://example.invalid/cover"
        (root / (hashlib.sha256(url.encode()).hexdigest() + ".bin")).write_bytes(picture())
        rows = [{"gid": "123", "resolution": "1280"}]
        failures = copy_cached_previews(root, root / "dataset", rows,
                                       {("123", "1280"): {"cover": url, "samples": [url, url]}})
        self.assertFalse(failures)
        self.assertEqual([item["kind"] for item in rows[0]["previews"]], ["cover"])

    def test_preview_budget_does_not_write_extra_files(self):
        root = fixture()
        url = "https://example.invalid/cached"
        (root / (hashlib.sha256(url.encode()).hexdigest() + ".bin")).write_bytes(picture())
        rows = [{"gid": "123", "resolution": "1280"}]
        with patch("scripts.probe_hath_pages.PREVIEW_TOTAL_LIMIT", 1):
            failures = copy_cached_previews(root, root / "dataset", rows, {("123", "1280"): {"cover": url}})
        self.assertEqual(failures, {"preview_byte_limit": 1})
        self.assertFalse((root / "dataset").exists())

    def test_sprite_out_of_bounds_does_not_generate_black_padding(self):
        root = fixture()
        url = "https://example.invalid/sheet"
        (root / (hashlib.sha256(url.encode()).hexdigest() + ".bin")).write_bytes(picture(size=(20, 20)))
        rows = [{"gid": "123", "resolution": "1280"}]
        failures = copy_cached_previews(root, root / "dataset", rows,
                                       {("123", "1280"): {"samples": [{"url": url, "x": 10, "y": 10, "w": 20, "h": 20}]}})
        self.assertEqual(failures, {"invalid_or_changed_cache_image": 1})
        self.assertFalse((root / "dataset").exists())

    def test_comparison_runs_offline_and_reports_no_recommendation_claim(self):
        root = fixture()
        gallery(root)
        manifest = unpack_verified(collect_remote(root, parse_download_directory_name), root / "dataset")
        row = manifest["galleries"][0]
        row["metadata_status"] = "missing"
        result = compare(root / "dataset", manifest, lambda blobs: [[1.0, 0.0] for blob in blobs])
        self.assertEqual(result["metrics"]["pages_encoded"], 3)
        self.assertEqual(result["metrics"]["paired_galleries"], 0)
        self.assertEqual(result["metrics"]["mean_same_page_full_vs_reduced_cosine"], 1.0)
        self.assertIsNone(result["metrics"]["mean_neighbor_overlap"])
        self.assertIsNone(result["galleries"][0]["preview_embedding"])

    def test_paired_neighbors_use_identical_candidate_sets(self):
        root = fixture()
        for gid in ("123", "124", "125", "126", "127"):
            gallery(root, gid)
        manifest = unpack_verified(collect_remote(root, parse_download_directory_name), root / "dataset")
        for row in manifest["galleries"]:
            row["metadata_status"] = "present"
            row["previews"] = [{**row["pages"][0], "kind": "cover"}]
        result = compare(root / "dataset", manifest, lambda blobs: [[1.0, 0.0] for blob in blobs])
        self.assertEqual(result["metrics"]["paired_galleries"], 5)
        self.assertEqual(result["metrics"]["neighbor_k"], 3)
        self.assertTrue(result["metrics"]["neighbor_overlap_informative"])
        self.assertEqual(result["metrics"]["mean_neighbor_overlap"], 1.0)
        self.assertEqual(result["metrics"]["mean_same_page_resize_neighbor_overlap"], 1.0)
        self.assertEqual(result["metrics"]["mean_full_vs_reduced_gallery_cosine"], 1.0)
        self.assertEqual(result["metrics"]["cross_gallery_exact_sample_pairs"], 90)

    def test_neighbor_overlap_is_not_reported_when_all_candidates_are_selected(self):
        root = fixture()
        for gid in ("123", "124"):
            gallery(root, gid, count=1)
        manifest = unpack_verified(collect_remote(root, parse_download_directory_name), root / "dataset")
        for row in manifest["galleries"]:
            row["metadata_status"] = "present"
            row["previews"] = [{**row["pages"][0], "kind": "cover"}]
        result = compare(root / "dataset", manifest, lambda blobs: [[1.0, 0.0] for blob in blobs])
        self.assertFalse(result["metrics"]["neighbor_overlap_informative"])
        self.assertIsNone(result["metrics"]["mean_neighbor_overlap"])

    def test_reduced_image_and_invalid_vectors(self):
        with Image.open(io.BytesIO(reduced_image(picture(size=(600, 900))))) as image:
            self.assertEqual(max(image.size), 250)
        for right in ([0], [0, 0], [float("nan"), 1]):
            with self.assertRaises(ValueError):
                cosine([1, 0], right)

    def test_dataset_path_traversal_fails(self):
        root = fixture()
        with self.assertRaisesRegex(ValueError, "outside_dataset"):
            verified_blob(root, {"path": "../outside", "bytes": 0, "sha256": ""})

    def test_independent_verifier_checks_bytes_dimensions_and_coverage(self):
        root = fixture()
        gallery(root)
        dataset = root / "dataset"
        manifest = unpack_verified(collect_remote(root, parse_download_directory_name), dataset)
        manifest["galleries"][0].update({"metadata_status": "missing", "previews": []})
        manifest_path = dataset / "manifest.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        report = compare(dataset, manifest, lambda blobs: [[1.0, 0.0] for blob in blobs])
        report.update({"schema": "hath-page-comparison-v1", "production_read_only": True,
                       "dataset_manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest()})
        report_path = root / "comparison.json"
        report_path.write_text(json.dumps(report), encoding="utf-8")
        result = verify_experiment(dataset, report_path)
        self.assertEqual(result["pages_verified"], 3)
        self.assertTrue(result["source_coverage_verified"])
        report["galleries"][0]["pages"][0]["width"] = 1
        report_path.write_text(json.dumps(report), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "dimensions_mismatch"):
            verify_experiment(dataset, report_path)

    def test_changed_dataset_digest_is_rejected(self):
        root = fixture()
        dataset = root / "dataset"
        dataset.mkdir()
        (dataset / "manifest.json").write_text("{}", encoding="utf-8")
        report = {"schema": "hath-page-comparison-v1", "production_read_only": True,
                  "dataset_manifest_sha256": "stale"}
        report_path = root / "report.json"
        report_path.write_text(json.dumps(report), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "manifest_changed"):
            verify_experiment(dataset, report_path)


if __name__ == "__main__":
    unittest.main()
