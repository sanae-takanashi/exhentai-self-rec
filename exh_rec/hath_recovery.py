"""Bounded, read-only identity evidence for existing downloaded-page samples."""

from __future__ import annotations

from collections import Counter
from contextlib import closing, nullcontext
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import stat
import time
import urllib.error
import urllib.parse
import urllib.request

from .exhentai import LinkTitleParser, normalize_cookie_header, valid_cookie_header
from .local_pages import LocalPageLibrary, MAX_INDEX, read_regular
from .net import normalize_proxy_url, patched_socks_create_connection, temporary_ban_detected


BASE = "https://exhentai.org/"
SCHEMA = "hath-recovery-v1"
MAX_RESPONSE = 2 * 1024 * 1024
GID = re.compile(r"[1-9][0-9]{0,19}\Z")
SHA1 = re.compile(r"[0-9a-f]{40}\Z")
GALLERY = re.compile(r"https://(?:exhentai|e-hentai)\.org/g/([1-9][0-9]{0,19})/([0-9a-fA-F]{1,64})/\Z")
URL_TABLES = ("hath_downloads", "feedback", "gallery_marks", "gallery_feature_snapshots", "recommendation_impressions")


class RecoveryError(Exception):
    """Only fixed public reason codes belong in this exception."""


def search_url(kind, value):
    if kind == "gid" and isinstance(value, str) and GID.fullmatch(value):
        return BASE + "?" + urllib.parse.urlencode({"f_search": "gid:" + value})
    if kind == "sha1" and isinstance(value, str) and SHA1.fullmatch(value):
        return BASE + "?" + urllib.parse.urlencode({"f_shash": value})
    raise RecoveryError("invalid_lookup")


def gallery_identity(url):
    match = GALLERY.fullmatch(url) if isinstance(url, str) else None
    if not match:
        raise RecoveryError("invalid_candidate")
    return match.group(1)


class SearchParser(LinkTitleParser):
    def __init__(self):
        super().__init__()
        self.listing = False
        self.has_more = False
        self.blocked = False

    def handle_starttag(self, tag, attrs):
        super().handle_starttag(tag, attrs)
        attributes = dict(attrs)
        classes = set((attributes.get("class") or "").split())
        self.listing |= bool(classes & {"itg", "glink", "gl1t"})
        self.has_more |= attributes.get("id") in {"unext", "dnext"} and bool(attributes.get("href"))
        self.blocked |= attributes.get("name") in {"password", "captcha"} or attributes.get("id") == "challenge-form"


def parse_search(text):
    if temporary_ban_detected(text):
        raise RecoveryError("rate_limited")
    parser = SearchParser()
    parser.feed(text)
    if parser.blocked or re.search(r"sad\s*panda|cf-chl-|verify you are human|you are not logged in", text, re.I):
        raise RecoveryError("access_required")
    candidates = {}
    for href, _, _ in parser.links:
        url = urllib.parse.urljoin(BASE, href)
        if GALLERY.fullmatch(url):
            candidates[url] = {"url": url, "gid": gallery_identity(url)}
    empty = bool(re.search(r"\bNo hits found\b|\bNo unfiltered results found\b", text, re.I))
    if len(candidates) > 100 or (empty and candidates) or (not candidates and not empty) or (candidates and not parser.listing):
        raise RecoveryError("unrecognized_search_page")
    if empty and parser.has_more:
        raise RecoveryError("unrecognized_search_page")
    return {"candidates": list(candidates.values()), "has_more": parser.has_more,
            "outcome": "candidates" if candidates else "no_visible_hits"}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class RecoveryClient:
    def __init__(self, cookie, proxy="", interval=5.0, clock=time.monotonic, sleep=time.sleep):
        self.cookie = normalize_cookie_header(cookie)
        if not valid_cookie_header(self.cookie) or any(char in self.cookie for char in "\r\n"):
            raise RecoveryError("no_valid_saved_cookie")
        self.proxy = normalize_proxy_url(proxy)
        if not math.isfinite(interval):
            raise RecoveryError("invalid_interval")
        self.interval = max(5.0, interval)
        self.clock, self.sleep, self.next_at = clock, sleep, 0.0

    def lookup(self, kind, value):
        url = search_url(kind, value)
        delay = self.next_at - self.clock()
        if delay > 0:
            self.sleep(delay)
        self.next_at = self.clock() + self.interval
        request = urllib.request.Request(url, headers={
            "Cookie": self.cookie,
            "User-Agent": "exhentai-self-recommend/0.1 (+local identity recovery)",
            "Accept": "text/html,application/xhtml+xml", "Accept-Encoding": "identity",
        })
        parsed = urllib.parse.urlsplit(self.proxy)
        context = nullcontext()
        proxy_handler = urllib.request.ProxyHandler({})
        if parsed.scheme in {"http", "https"}:
            proxy_handler = urllib.request.ProxyHandler({"https": self.proxy})
        elif parsed.scheme in {"socks5", "socks5h"}:
            import socks
            context = patched_socks_create_connection(
                socks, parsed.hostname, parsed.port or 1080,
                username=urllib.parse.unquote(parsed.username) if parsed.username else None,
                password=urllib.parse.unquote(parsed.password) if parsed.password else None,
                rdns=True)
        try:
            with context:
                opener = urllib.request.build_opener(proxy_handler, NoRedirect())
                with opener.open(request, timeout=30) as response:
                    if response.geturl() != url:
                        raise RecoveryError("redirect_blocked")
                    if response.headers.get_content_type() not in {"text/html", "application/xhtml+xml"}:
                        raise RecoveryError("access_required")
                    if response.headers.get("Content-Encoding", "identity").lower() != "identity":
                        raise RecoveryError("unsupported_response_encoding")
                    body = response.read(MAX_RESPONSE + 1)
                    if len(body) > MAX_RESPONSE:
                        raise RecoveryError("response_limit")
            return parse_search(body.decode("utf-8", errors="replace"))
        except urllib.error.HTTPError as exc:
            if exc.code in {429, 509}:
                reason = "rate_limited"
            elif 300 <= exc.code < 400:
                reason = "redirect_blocked"
            elif exc.code in {401, 403}:
                reason = "access_required"
            else:
                reason = "http_error"
            exc.close()
            raise RecoveryError(reason) from None
        except RecoveryError:
            raise
        except Exception:
            raise RecoveryError("transport_failed") from None


def connect_read_only(database):
    conn = sqlite3.connect(Path(database).resolve().as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def recovery_plan(library: LocalPageLibrary, database, limit=3):
    if type(limit) is not int or not 1 <= limit <= 100:
        raise RecoveryError("invalid_gallery_limit")
    library.load()
    index_hash = hashlib.sha256(read_regular(library.root / "index.json", MAX_INDEX)).hexdigest()
    rows = []
    with closing(connect_read_only(database)) as conn:
        conn.execute("BEGIN")
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for row in library.rows.values():
            gid = row["gid"]
            if not isinstance(gid, str) or not GID.fullmatch(gid):
                raise RecoveryError("invalid_gallery_identity")
            if conn.execute("SELECT 1 FROM galleries WHERE gid=? LIMIT 1", (gid,)).fetchone():
                continue
            known = set()
            for table in URL_TABLES:
                if table in tables:
                    for found in conn.execute("SELECT DISTINCT gallery_url FROM " + table + " WHERE gallery_url LIKE ? LIMIT 100", ("%/g/" + gid + "/%",)):
                        if isinstance(found[0], str) and GALLERY.fullmatch(found[0]) and gallery_identity(found[0]) == gid:
                            known.add(found[0])
            rows.append({"key": row["key"], "gid": gid, "resolution": row["resolution"], "local_candidates": sorted(known)})
    rows.sort(key=lambda row: (int(row["gid"]), row["key"]))
    # A local file position is not asserted to be a verified site page number.
    for target in rows[:limit]:
        source = library.rows[target["key"]]
        samples, seen = [], set()
        pages = sorted(source["pages"], key=lambda page: (page["position"] == 1, page["position"]))
        for page in pages:
            if page["sha256"] in seen:
                continue
            blob, _ = library.image(page["sha256"])
            samples.append({"sha1": hashlib.sha1(blob).hexdigest(), "sha256": page["sha256"], "position": page["position"]})
            seen.add(page["sha256"])
            if len(samples) == 2:
                break
        target["samples"] = samples
    return {"schema": SCHEMA, "created_at": datetime.now(timezone.utc).isoformat(),
            "library_index_sha256": index_hash, "read_only": True, "missing": len(rows),
            "selected": rows[:limit], "unselected": len(rows[limit:])}


def runtime_settings(database):
    with closing(connect_read_only(database)) as conn:
        settings = dict(conn.execute("SELECT key, value FROM settings WHERE key IN ('cookie_header', 'network_proxy', 'request_interval_seconds')"))
    try:
        interval = float(settings.get("request_interval_seconds", "5"))
        if not math.isfinite(interval) or not 0 <= interval <= 300:
            raise ValueError
    except (ValueError, TypeError):
        raise RecoveryError("invalid_saved_interval") from None
    return settings.get("cookie_header", ""), settings.get("network_proxy", ""), max(5.0, interval)


def summarize_candidate(gid, candidate):
    methods = candidate["evidence"]
    exact = candidate["gid"] == gid
    if exact and "gid" in methods and candidate["sample_matches"]:
        return "exact_gid_and_sample"
    if exact and "gid" in methods:
        return "exact_gid"
    if exact:
        return "original_gid_local_or_sample"
    if "gid" in methods:
        return "possible_update_chain"
    return "shared_sample_other_gid"


def recover(plan, lookup, max_requests=12, record=lambda result: None):
    if type(max_requests) is not int or not 1 <= max_requests <= 300:
        raise RecoveryError("invalid_request_limit")
    cache, items, request_count, stopped = {}, [], 0, None
    for row in plan["selected"]:
        item = {**row, "lookups": [], "candidates": [], "complete": False}
        items.append(item)
        candidates = {}

        def add(url, method, fingerprint=None):
            candidate_gid = gallery_identity(url)
            candidate = candidates.setdefault(url, {"url": url, "gid": candidate_gid, "evidence": [], "sample_matches": []})
            if method not in candidate["evidence"]:
                candidate["evidence"].append(method)
            if fingerprint and fingerprint not in candidate["sample_matches"]:
                candidate["sample_matches"].append(fingerprint)

        for url in row["local_candidates"]:
            add(url, "local_url")
        queries = [("gid", row["gid"])] + [("sha1", sample["sha1"]) for sample in row["samples"]]
        for kind, value in queries:
            search_url(kind, value)
            if (kind, value) not in cache:
                if request_count >= max_requests:
                    stopped = "request_budget"
                    break
                request_count += 1
                try:
                    result = lookup(kind, value)
                    # Persist only the allowlisted response shape, never raw transport data.
                    found = []
                    if not isinstance(result["candidates"], list):
                        raise RecoveryError("invalid_search_evidence")
                    for candidate in result["candidates"]:
                        url = candidate["url"]
                        found.append({"url": url, "gid": gallery_identity(url)})
                    if (len(found) > 100 or len({row["url"] for row in found}) != len(found)
                            or type(result["has_more"]) is not bool
                            or result["outcome"] != ("candidates" if found else "no_visible_hits")
                            or (not found and result["has_more"])):
                        raise RecoveryError("invalid_search_evidence")
                    cache[kind, value] = {"candidates": found, "has_more": result["has_more"], "outcome": result["outcome"]}
                except Exception as exc:
                    reasons = {"rate_limited", "access_required", "redirect_blocked", "unrecognized_search_page", "http_error", "transport_failed", "response_limit", "unsupported_response_encoding", "invalid_search_evidence"}
                    stopped = str(exc) if isinstance(exc, RecoveryError) and str(exc) in reasons else "lookup_failed"
                    record({"kind": kind, "value": value, "status": "stopped", "reason": stopped})
                    break
                # A failed durable write must abort, not turn into search evidence.
                record({"kind": kind, "value": value, "status": "ok", **cache[kind, value]})
            evidence = cache[kind, value]
            item["lookups"].append({"kind": kind, "value": value, **evidence})
            for candidate in evidence["candidates"]:
                add(candidate["url"], kind, value if kind == "sha1" else None)
        item["complete"] = len(item["lookups"]) == len(queries)
        item["candidates"] = [{**candidate, "relationship": summarize_candidate(row["gid"], candidate)} for candidate in candidates.values()]
        if stopped:
            break
    complete = sum(item["complete"] for item in items)
    return {"schema": SCHEMA, "created_at": datetime.now(timezone.utc).isoformat(), "read_only": True,
            "library_index_sha256": plan["library_index_sha256"], "items": items,
            "summary": {"status": "stopped" if stopped and stopped != "request_budget" else "partial" if complete < plan["missing"] else "completed_scope",
                        "stop_reason": stopped, "missing": plan["missing"], "selected": len(plan["selected"]),
                        "completed": complete, "pending": plan["missing"] - complete, "requests": request_count,
                        "with_candidates": sum(bool(item["candidates"]) for item in items),
                        "relationships": dict(Counter(candidate["relationship"] for item in items for candidate in item["candidates"]))},
            "limitations": ["Candidates only; no production writes, model changes, labels, downloads or gallery-detail fetches.",
                            "Gid searches may return the latest chain member, not the original version.",
                            "A matching SHA-1 refers to sampled file bytes; shared images do not establish gallery identity.",
                            "Only the first search page is inspected; has_more marks incomplete candidate enumeration.",
                            "No visible hits does not prove absence: filters, visibility and file indexing may affect results.",
                            "Up to two filename-ordered local samples per gallery; resized copies may not hash-match originals."]}


def write_new(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=True, indent=2)
        stream.write("\n")


def validate_output_directory(path):
    path = Path(path).absolute()
    if str(path).startswith(("\\\\", "//")):
        raise RecoveryError("invalid_output_directory")
    for parent in (path, *path.parents):
        try:
            info = parent.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise RecoveryError("linked_output_directory")
        if parent == path or not stat.S_ISDIR(info.st_mode):
            raise RecoveryError("invalid_output_directory")
    return path


def markdown_report(report):
    summary = report["summary"]
    lines = ["# H@H Recovery Candidates", "", "Read-only evidence. No links were written back to the production database.", "",
             f"Status: {summary['status']}; completed {summary['completed']}/{summary['missing']}; pending {summary['pending']}; requests {summary['requests']}.", "",
             "| Local gid / resolution | Candidate gid | Relationship | Matching samples | Candidate |", "| --- | --- | --- | --- | --- |"]
    for item in report["items"]:
        for candidate in item["candidates"]:
            lines.append(f"| {item['gid']} / {item['resolution']} | {candidate['gid']} | {candidate['relationship']} | {len(candidate['sample_matches'])} | [Inspect]({candidate['url']}) |")
        if not item["candidates"]:
            lines.append(f"| {item['gid']} / {item['resolution']} | - | {'no_visible_candidates' if item['complete'] else 'incomplete'} | - | - |")
    lines += ["", "## Limits", ""] + ["- " + value for value in report["limitations"]]
    return "\n".join(lines) + "\n"
