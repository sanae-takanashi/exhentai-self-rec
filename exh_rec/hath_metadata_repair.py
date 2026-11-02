"""Explicit, staged metadata-only repair for corroborated local-page identities."""

from contextlib import closing, nullcontext
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import urllib.error
import urllib.parse
import urllib.request

from .exhentai import EH_API_URL, format_posted, parse_gdata_entry
from .hath_recovery import (
    GALLERY, MAX_RESPONSE, NoRedirect, RecoveryClient, RecoveryError, connect_read_only,
    load_reuse, recent_timestamp, recover, unique_object,
)
from .local_pages import MAX_INDEX, read_regular
from .net import patched_socks_create_connection

SCHEMA = "hath-metadata-stage-v1"
FIELDS = ("gid", "token", "title", "title_jpn", "category", "uploader", "posted",
          "filecount", "rating", "tags", "thumb", "parent_gid", "parent_key", "parent_token")


def document(path):
    return json.loads(read_regular(Path(path), MAX_INDEX), object_pairs_hook=unique_object)


def targets_from_evidence(directory, library, now=None):
    """Reconstruct identities from journals and freshly hashed local bytes, not report labels."""
    directory = Path(directory)
    plan = document(directory / "plan.json")
    library.load()
    index_hash = hashlib.sha256(read_regular(library.root / "index.json", MAX_INDEX)).hexdigest()
    if plan["library_index_sha256"] != index_hash:
        raise RecoveryError("changed_library")
    selected = plan["selected"]
    if not 0 < len(selected) <= 100:
        raise RecoveryError("invalid_repair_scope")
    for row in selected:
        source = library.rows[row["key"]]
        if source["gid"] != row["gid"] or len(row["samples"]) != 2:
            raise RecoveryError("invalid_repair_scope")
        for sample in row["samples"]:
            if not any(page["position"] == sample["position"] and page["sha256"] == sample["sha256"]
                       for page in source["pages"]):
                raise RecoveryError("changed_repair_sample")
            blob, _ = library.image(sample["sha256"])
            if hashlib.sha1(blob).hexdigest() != sample["sha1"]:
                raise RecoveryError("changed_repair_sample")
        if len({sample["sha1"] for sample in row["samples"]}) != 2:
            raise RecoveryError("insufficient_repair_samples")
    reuse, _ = load_reuse([directory], plan, now=now)
    report = recover(plan, None, max_requests=1, reuse=reuse)
    targets = []
    for item in report["items"]:
        exact = [candidate for candidate in item["candidates"] if candidate["gid"] == item["gid"]]
        if (not item["complete"] or any(lookup["has_more"] for lookup in item["lookups"])
                or len(exact) != 1 or exact[0]["relationship"] != "exact_gid_and_sample"
                or len(set(exact[0]["sample_matches"])) != 2):
            raise RecoveryError("insufficient_repair_evidence")
        match = GALLERY.fullmatch(exact[0]["url"])
        targets.append({"gid": item["gid"], "token": match[2], "url": exact[0]["url"], "key": item["key"]})
    if len(targets) != len(selected) or len({row["gid"] for row in targets}) != len(targets):
        raise RecoveryError("duplicate_repair_identity")
    evidence = {"library_index_sha256": index_hash,
                "plan_sha256": hashlib.sha256(read_regular(directory / "plan.json", MAX_INDEX)).hexdigest(),
                "events_sha256": hashlib.sha256(read_regular(directory / "events.jsonl", MAX_INDEX)).hexdigest()}
    return targets, evidence


def validated_entry(entry, target):
    if (not isinstance(entry, dict) or entry.get("error") or type(entry.get("gid")) is not int
            or str(entry["gid"]) != target["gid"] or entry.get("token") != target["token"]):
        raise RecoveryError("metadata_identity_mismatch")
    for field in FIELDS:
        value = entry.get(field)
        if field in {"gid", "tags"}:
            continue
        if value is not None and (isinstance(value, bool) or not isinstance(value, (str, int, float))
                                  or len(str(value)) > 8192):
            raise RecoveryError("invalid_metadata")
    tags = entry.get("tags")
    if not isinstance(tags, list) or len(tags) > 1000 or any(not isinstance(tag, str) or len(tag) > 1000 for tag in tags):
        raise RecoveryError("invalid_metadata")
    if not isinstance(entry.get("title"), str) or not entry["title"].strip():
        raise RecoveryError("missing_metadata_title")
    result = {key: entry[key] for key in FIELDS if key in entry}
    parsed = parse_gdata_entry(result)
    if (parsed["page_count"] is None or not 0 < parsed["page_count"] <= 1000000
            or parsed["rating"] is None or not math.isfinite(parsed["rating"]) or not 0 <= parsed["rating"] <= 5):
        raise RecoveryError("invalid_metadata")
    thumb = urllib.parse.urlsplit(parsed["thumb"] or "")
    if (thumb.scheme != "https" or thumb.username or thumb.password or thumb.port
            or thumb.fragment or not thumb.hostname
            or not any(thumb.hostname == domain or thumb.hostname.endswith("." + domain)
                       for domain in ("ehgt.org", "exhentai.org", "e-hentai.org"))):
        raise RecoveryError("invalid_metadata_thumbnail")
    if parsed["parent_url"] and not GALLERY.fullmatch(parsed["parent_url"]):
        raise RecoveryError("invalid_metadata_parent")
    return result


def parse_response(body, targets):
    try:
        response = json.loads(body, object_pairs_hook=unique_object)
        entries = response["gmetadata"]
        if not isinstance(entries, list) or len(entries) != len(targets):
            raise RecoveryError("incomplete_metadata_batch")
        by_gid = {}
        allowed = {target["gid"]: target for target in targets}
        for entry in entries:
            gid = str(entry.get("gid")) if isinstance(entry, dict) else ""
            if gid not in allowed or gid in by_gid:
                raise RecoveryError("metadata_identity_mismatch")
            by_gid[gid] = validated_entry(entry, allowed[gid])
        return [by_gid[target["gid"]] for target in targets]
    except RecoveryError:
        raise
    except Exception:
        raise RecoveryError("unrecognized_metadata_response") from None


class MetadataClient(RecoveryClient):
    """Same saved transport policy, fixed official API origin; never visits a CDN."""

    def fetch(self, targets):
        if not 0 < len(targets) <= 25:
            raise RecoveryError("invalid_metadata_batch")
        for target in targets:
            match = GALLERY.fullmatch(target["url"])
            if not match or (match[1], match[2]) != (target["gid"], target["token"]):
                raise RecoveryError("invalid_candidate")
        delay = self.next_at - self.clock()
        if delay > 0:
            self.sleep(delay)
        self.next_at = self.clock() + self.interval
        request = urllib.request.Request(EH_API_URL, data=json.dumps({"method": "gdata", "namespace": 1,
            "gidlist": [[int(row["gid"]), row["token"]] for row in targets]}).encode("utf-8"), headers={
                "Cookie": self.cookie, "User-Agent": "exhentai-self-recommend/0.1 (+confirmed metadata repair)",
                "Content-Type": "application/json", "Accept": "application/json", "Accept-Encoding": "identity"})
        parsed = urllib.parse.urlsplit(self.proxy)
        handler, context = urllib.request.ProxyHandler({}), nullcontext()
        if parsed.scheme in {"http", "https"}:
            handler = urllib.request.ProxyHandler({"https": self.proxy})
        elif parsed.scheme in {"socks5", "socks5h"}:
            import socks
            context = patched_socks_create_connection(socks, parsed.hostname, parsed.port or 1080,
                username=urllib.parse.unquote(parsed.username) if parsed.username else None,
                password=urllib.parse.unquote(parsed.password) if parsed.password else None, rdns=True)
        try:
            with context:
                opener = urllib.request.build_opener(handler, NoRedirect())
                with opener.open(request, timeout=30) as response:
                    if response.geturl() != EH_API_URL:
                        raise RecoveryError("redirect_blocked")
                    if response.headers.get_content_type() != "application/json":
                        raise RecoveryError("unrecognized_metadata_response")
                    if response.headers.get("Content-Encoding", "identity").lower() != "identity":
                        raise RecoveryError("unsupported_response_encoding")
                    body = response.read(MAX_RESPONSE + 1)
                    if len(body) > MAX_RESPONSE:
                        raise RecoveryError("response_limit")
            return parse_response(body, targets)
        except urllib.error.HTTPError as exc:
            reason = ("rate_limited" if exc.code in {429, 509} else "redirect_blocked" if 300 <= exc.code < 400
                      else "access_required" if exc.code in {401, 403} else "http_error")
            exc.close()
            raise RecoveryError(reason) from None
        except RecoveryError:
            raise
        except Exception:
            raise RecoveryError("transport_failed") from None


def staged_rows(stages, targets, evidence, now=None):
    allowed = {row["gid"]: row for row in targets}
    rows = {}
    for path in stages:
        stage = document(path)
        if stage["schema"] != SCHEMA or stage["evidence"] != evidence:
            raise RecoveryError("changed_repair_evidence")
        recent_timestamp(stage["created_at"], now or datetime.now(timezone.utc))
        if not isinstance(stage["entries"], list) or not 0 < len(stage["entries"]) <= len(targets):
            raise RecoveryError("invalid_metadata_stage")
        for entry in stage["entries"]:
            gid = str(entry.get("gid"))
            if gid not in allowed or gid in rows:
                raise RecoveryError("duplicate_repair_identity")
            rows[gid] = validated_entry(entry, allowed[gid])
    return [(target, rows[target["gid"]]) for target in targets if target["gid"] in rows]


def missing_targets(database, targets):
    with closing(connect_read_only(database)) as conn:
        return [row for row in targets if not conn.execute(
            "SELECT 1 FROM galleries WHERE gid=? OR url=?", (row["gid"], row["url"])).fetchone()]


def apply_metadata(database, rows, backup, receipt):
    """Insert only; existing gid/url always wins. Never creates feature snapshots."""
    backup, receipt = Path(backup), Path(receipt)
    if backup.exists() or receipt.exists() or backup.resolve() == Path(database).resolve():
        raise RecoveryError("repair_output_exists")
    # Reserve both names before touching production. A failed attempt retains private evidence.
    with backup.open("xb"), receipt.open("x", encoding="utf-8"):
        pass
    with closing(sqlite3.connect(database, timeout=10)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("BEGIN IMMEDIATE")
        try:
            pending = [(target, validated_entry(entry, target)) for target, entry in rows
                       if not conn.execute("SELECT 1 FROM galleries WHERE gid=? OR url=?",
                                           (target["gid"], target["url"])).fetchone()]
            # A separate reader backs up the last committed state while our write lock prevents races.
            with closing(connect_read_only(database)) as source, closing(sqlite3.connect(backup)) as destination:
                source.backup(destination, pages=256)
                if destination.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise RecoveryError("backup_integrity_failed")
            inserted = []
            for target, entry in pending:
                meta = parse_gdata_entry(entry)
                conn.execute("""INSERT INTO galleries
                    (url,gid,token,title,title_jpn,category,uploader,posted_at,thumb_url,rating,
                     tags_json,source_query,parent_url,review_excluded,page_count)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,1,?)""", (
                        target["url"], target["gid"], target["token"], meta["title"], meta["title_jpn"],
                        meta["category"], meta["uploader"], format_posted(meta["posted"]) if meta["posted"] else None,
                        meta["thumb"], meta["rating"], json.dumps(meta["tags"], ensure_ascii=True),
                        "hath-metadata-repair-v1", meta["parent_url"], meta["page_count"]))
                inserted.append(target["gid"])
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
    result = {"ok": True, "inserted": len(inserted), "skipped": len(rows) - len(inserted),
              "inserted_gids": inserted, "backup": str(backup), "review_excluded": True}
    # Receipt is reserved above; a failed write cannot result in a second accidental apply.
    with receipt.open("w", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=True, indent=2)
        stream.flush()
    return result
