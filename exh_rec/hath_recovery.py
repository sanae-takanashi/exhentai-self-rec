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
from .local_pages import ASSET, KEY, LocalPageLibrary, MAX_INDEX, read_regular
from .net import normalize_proxy_url, patched_socks_create_connection, temporary_ban_detected


BASE = "https://exhentai.org/"
SCHEMA = "hath-recovery-v1"
MAX_RESPONSE = 2 * 1024 * 1024
GID = re.compile(r"[1-9][0-9]{0,19}\Z")
SHA1 = re.compile(r"[0-9a-f]{40}\Z")
GALLERY = re.compile(r"https://(?:exhentai|e-hentai)\.org/g/([1-9][0-9]{0,19})/([0-9a-fA-F]{1,64})/\Z")
URL_TABLES = ("hath_downloads", "feedback", "gallery_marks", "gallery_feature_snapshots", "recommendation_impressions")
DIAGNOSTIC_FLAGS = frozenset({"listing_marker", "search_form", "login_marker", "challenge_marker",
                              "empty_marker", "rate_limit_marker", "content_warning_marker", "has_more"})
DIAGNOSTIC_COUNTS = frozenset({"response_bytes", "text_chars", "html_tags", "forms", "images",
                               "anchors", "gallery_anchors", "canonical_candidates", "missing_slash_candidates",
                               "http_gallery_candidates"})
STOP_REASONS = frozenset({"rate_limited", "access_required", "redirect_blocked", "empty_search_response",
                          "unrecognized_search_page", "http_error", "transport_failed", "response_limit",
                          "unsupported_response_encoding", "invalid_search_evidence", "lookup_failed"})
MAX_REUSE_AGE_SECONDS = 24 * 60 * 60


def safe_diagnostics(value):
    if not isinstance(value, dict):
        return {}
    result = {key: value[key] for key in DIAGNOSTIC_FLAGS if type(value.get(key)) is bool}
    result.update({key: value[key] for key in DIAGNOSTIC_COUNTS
                   if type(value.get(key)) is int and 0 <= value[key] <= MAX_RESPONSE * 4})
    if type(value.get("http_status")) is int and 100 <= value["http_status"] <= 599:
        result["http_status"] = value["http_status"]
    return result


class RecoveryError(Exception):
    """Only fixed public reason codes belong in this exception."""

    def __init__(self, reason, diagnostics=None):
        super().__init__(reason)
        self.diagnostics = safe_diagnostics(diagnostics)


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
        self.counts = Counter()
        self.search_form = False

    def handle_starttag(self, tag, attrs):
        super().handle_starttag(tag, attrs)
        attributes = dict(attrs)
        if tag in {"html", "form", "img", "a"}:
            self.counts[tag] += 1
        self.search_form |= tag == "input" and attributes.get("name") in {"f_search", "f_shash"}
        classes = set((attributes.get("class") or "").split())
        self.listing |= bool(classes & {"itg", "glink", "gl1t"})
        self.has_more |= attributes.get("id") in {"unext", "dnext"} and bool(attributes.get("href"))
        self.blocked |= attributes.get("name") in {"password", "captcha"} or attributes.get("id") == "challenge-form"


def parse_search(text, transport=None):
    parser = SearchParser()
    parser.feed(text)
    challenge = bool(re.search(r"cf-chl-|verify you are human", text, re.I))
    login = bool(re.search(r"sad\s*panda|you are not logged in|requires? you to log (?:on|in)|must be logged (?:on|in)", text, re.I))
    candidates = {}
    for href, _, _ in parser.links:
        url = urllib.parse.urljoin(BASE, href)
        if GALLERY.fullmatch(url):
            candidates[url] = {"url": url, "gid": gallery_identity(url)}
    empty = bool(re.search(r"\bNo hits found\b|\bNo unfiltered results found\b", text, re.I))
    diagnostics = safe_diagnostics({**safe_diagnostics(transport),
        "text_chars": len(text), "html_tags": parser.counts["html"], "forms": parser.counts["form"],
        "images": parser.counts["img"], "anchors": parser.counts["a"], "gallery_anchors": len(parser.links),
        "canonical_candidates": len(candidates), "listing_marker": parser.listing,
        "search_form": parser.search_form, "login_marker": login, "challenge_marker": challenge or parser.blocked,
        "empty_marker": empty, "rate_limit_marker": temporary_ban_detected(text), "has_more": parser.has_more,
        "content_warning_marker": bool(re.search(r"\bContent Warning\b", text, re.I)),
        "missing_slash_candidates": sum(bool(GALLERY.fullmatch(urllib.parse.urljoin(BASE, href) + "/")) for href, _, _ in parser.links),
        "http_gallery_candidates": sum(bool(GALLERY.fullmatch(urllib.parse.urljoin(BASE, href).replace("http://", "https://", 1))) for href, _, _ in parser.links
                                       if href.startswith("http://"))})
    if not text.strip():
        raise RecoveryError("empty_search_response", diagnostics)
    if diagnostics["rate_limit_marker"]:
        raise RecoveryError("rate_limited", diagnostics)
    if parser.blocked or challenge or login:
        raise RecoveryError("access_required", diagnostics)
    if len(candidates) > 100 or (empty and candidates) or (not candidates and not empty) or (candidates and not parser.listing):
        raise RecoveryError("unrecognized_search_page", diagnostics)
    if empty and parser.has_more:
        raise RecoveryError("unrecognized_search_page", diagnostics)
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
                    status = getattr(response, "status", 200)
            return parse_search(body.decode("utf-8", errors="replace"), {"http_status": status, "response_bytes": len(body)})
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
            raise RecoveryError(reason, {"http_status": exc.code}) from None
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


def query_keys(row):
    return [("gid", row["gid"])] + [("sha1", sample["sha1"]) for sample in row["samples"]]


def search_evidence(result):
    found = []
    if not isinstance(result["candidates"], list):
        raise RecoveryError("invalid_search_evidence")
    for candidate in result["candidates"]:
        url = candidate["url"]
        gid = gallery_identity(url)
        if candidate.get("gid", gid) != gid:
            raise RecoveryError("invalid_search_evidence")
        found.append({"url": url, "gid": gid})
    if (len(found) > 100 or len({row["url"] for row in found}) != len(found)
            or type(result["has_more"]) is not bool
            or result["outcome"] != ("candidates" if found else "no_visible_hits")
            or (not found and result["has_more"])):
        raise RecoveryError("invalid_search_evidence")
    return {"candidates": found, "has_more": result["has_more"], "outcome": result["outcome"]}


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise RecoveryError("invalid_reuse_evidence")
        result[key] = value
    return result


def recent_timestamp(value, now):
    stamp = datetime.fromisoformat(value)
    if stamp.tzinfo is None or not -300 <= (now - stamp).total_seconds() <= MAX_REUSE_AGE_SECONDS:
        raise RecoveryError("stale_reuse_evidence")
    return stamp.isoformat()


def load_reuse(directories, plan, now=None):
    """Rebuild successful lookups, not completion labels, from explicit private journals."""
    if len(directories) > 10:
        raise RecoveryError("invalid_reuse_evidence")
    now = now or datetime.now(timezone.utc)
    current = {row["key"]: row for row in plan["selected"]}
    cache, sources = {}, []
    for directory in directories:
        directory = Path(directory).absolute()
        if str(directory).startswith(("\\\\", "//")):
            raise RecoveryError("invalid_reuse_evidence")
        try:
            plan_bytes = read_regular(directory / "plan.json", MAX_INDEX)
            journal = read_regular(directory / "events.jsonl", MAX_INDEX)
            old = json.loads(plan_bytes, object_pairs_hook=unique_object)
            if (old["schema"] != SCHEMA or old["read_only"] is not True
                    or old["library_index_sha256"] != plan["library_index_sha256"]
                    or not isinstance(old["selected"], list) or not 0 < len(old["selected"]) <= 100):
                raise RecoveryError("invalid_reuse_evidence")
            created_at = recent_timestamp(old["created_at"], now)
            allowed, relevant, seen = set(), set(), set()
            for row in old["selected"]:
                if (not KEY.fullmatch(row["key"]) or row["key"] != f"{row['gid']}-{row['resolution']}"
                        or row["key"] in seen or not isinstance(row["samples"], list)
                        or not 1 <= len(row["samples"]) <= 2):
                    raise RecoveryError("invalid_reuse_evidence")
                seen.add(row["key"])
                for sample in row["samples"]:
                    if (not ASSET.fullmatch(sample["sha256"]) or type(sample["position"]) is not int
                            or sample["position"] <= 0):
                        raise RecoveryError("invalid_reuse_evidence")
                pairs = query_keys(row)
                for kind, value in pairs:
                    search_url(kind, value)
                allowed.update(pairs)
                active = current.get(row["key"])
                if active is not None:
                    if row["samples"] != active["samples"]:
                        raise RecoveryError("changed_reuse_samples")
                    relevant.update(pairs)
            source = hashlib.sha256(journal).hexdigest()
            lines = journal.splitlines()
            if len(lines) > 301 or (journal and not journal.endswith(b"\n")):
                raise RecoveryError("invalid_reuse_evidence")
            events = [json.loads(line, object_pairs_hook=unique_object) for line in lines]
            stopped, reused = False, 0
            for event in events:
                pair = event["kind"], event["value"]
                if stopped or pair not in allowed:
                    raise RecoveryError("invalid_reuse_evidence")
                if event["status"] == "stopped":
                    if event["reason"] not in STOP_REASONS:
                        raise RecoveryError("invalid_reuse_evidence")
                    stopped = True
                    continue
                if event["status"] != "ok":
                    raise RecoveryError("invalid_reuse_evidence")
                evidence = search_evidence(event)
                observed_at = recent_timestamp(event.get("observed_at", created_at), now)
                if pair in relevant:
                    if pair in cache and search_evidence(cache[pair]) != evidence:
                        raise RecoveryError("conflicting_reuse_evidence")
                    cache[pair] = {**evidence, "observed_at": observed_at, "reused_from": source}
                    reused += 1
            sources.append({"plan_sha256": hashlib.sha256(plan_bytes).hexdigest(),
                            "events_sha256": source, "created_at": created_at, "reusable_events": reused})
        except RecoveryError:
            raise
        except Exception:
            raise RecoveryError("invalid_reuse_evidence") from None
    return cache, sources


def recover(plan, lookup, max_requests=12, record=lambda result: None, reuse=None):
    if type(max_requests) is not int or not 1 <= max_requests <= 300:
        raise RecoveryError("invalid_request_limit")
    cache = {pair: {**search_evidence(value), "observed_at": value["observed_at"],
                    "reused_from": value["reused_from"]} for pair, value in (reuse or {}).items()}
    reused_keys, recorded = set(cache), set()
    items, request_count, stopped, used_reuse = [], 0, None, set()
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
        queries = query_keys(row)
        for kind, value in queries:
            search_url(kind, value)
            if (kind, value) not in cache:
                if request_count >= max_requests:
                    stopped = "request_budget"
                    break
                request_count += 1
                try:
                    result = lookup(kind, value)
                    cache[kind, value] = {**search_evidence(result), "observed_at": datetime.now(timezone.utc).isoformat()}
                except Exception as exc:
                    stopped = str(exc) if isinstance(exc, RecoveryError) and str(exc) in STOP_REASONS else "lookup_failed"
                    diagnostics = safe_diagnostics(exc.diagnostics) if isinstance(exc, RecoveryError) else {}
                    if diagnostics:
                        item["stop_diagnostics"] = diagnostics
                    record({"kind": kind, "value": value, "status": "stopped", "reason": stopped, **({"diagnostics": diagnostics} if diagnostics else {})})
                    break
            # Journal reused successes too, so a later run has a self-contained checkpoint.
            # A failed durable write must abort before issuing any later request.
            if (kind, value) not in recorded:
                record({"kind": kind, "value": value, "status": "ok", **cache[kind, value]})
                recorded.add((kind, value))
            evidence = cache[kind, value]
            if (kind, value) in reused_keys:
                used_reuse.add((kind, value))
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
            "reuse_sources": plan.get("reuse_sources", []),
            "summary": {"status": "stopped" if stopped and stopped != "request_budget" else "partial" if complete < plan["missing"] else "completed_scope",
                        "stop_reason": stopped, "missing": plan["missing"], "selected": len(plan["selected"]),
                        "completed": complete, "pending": plan["missing"] - complete, "requests": request_count,
                        "reused_lookups": len(used_reuse),
                        "reused_records": sum(item["complete"] and all(pair in reused_keys for pair in query_keys(item)) for item in items),
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


def repair_preview(report, database):
    """Classify evidence for a later, explicitly confirmed normal metadata fetch."""
    exact_urls = {}
    for item in report["items"]:
        for candidate in item["candidates"]:
            if candidate["gid"] == item["gid"]:
                exact_urls.setdefault(item["gid"], set()).add(candidate["url"])
    rows = []
    with closing(connect_read_only(database)) as conn:
        conn.execute("BEGIN")
        for item in report["items"]:
            known = conn.execute("SELECT COUNT(*) FROM galleries WHERE gid=?", (item["gid"],)).fetchone()[0]
            exact = [candidate for candidate in item["candidates"]
                     if summarize_candidate(item["gid"], candidate) == "exact_gid_and_sample"
                     and len(set(candidate["sample_matches"])) >= 2]
            paginated = any(lookup["has_more"] for lookup in item["lookups"])
            url = None
            if known:
                action, reason = "already_present", "metadata_now_present"
            elif not item["complete"]:
                action, reason = "needs_review", "incomplete_queries"
            elif paginated:
                action, reason = "needs_review", "candidate_enumeration_partial"
            elif len(exact_urls.get(item["gid"], ())) > 1:
                action, reason = "needs_review", "conflicting_original_urls"
            elif len(exact) == 1:
                action, reason = "fetch_metadata_after_confirmation", "same_gid_and_two_distinct_samples"
                url = exact[0]["url"]
            elif item["candidates"]:
                action, reason = "needs_review", "identity_not_sufficiently_corroborated"
            else:
                action, reason = "no_visible_candidates", "not_proof_of_absence"
            rows.append({"key": item["key"], "gid": item["gid"], "resolution": item["resolution"],
                         "action": action, "reason": reason, "candidate_url": url,
                         "relationships": dict(Counter(candidate["relationship"] for candidate in item["candidates"]))})
    return {"schema": "hath-metadata-repair-preview-v1", "created_at": datetime.now(timezone.utc).isoformat(),
            "production_read_only": True, "requires_confirmation": True, "can_apply": False,
            "library_index_sha256": report["library_index_sha256"], "items": rows,
            "summary": {"records": len(rows), "unprocessed": report["summary"]["pending"],
                        "actions": dict(Counter(row["action"] for row in rows)),
                        "unique_ready_galleries": len({row["gid"] for row in rows if row["candidate_url"]})},
            "limitations": ["Preview only: candidate evidence is not gallery metadata or a database patch.",
                            "After confirmation, fetch real metadata through the normal site path; do not fabricate titles or tags.",
                            "Do not relink an original gallery to an update-chain or shared-image candidate.",
                            "Recheck missing metadata, source samples and candidate identity before applying any later change."]}


def markdown_repair_preview(preview):
    lines = ["# Metadata Repair Preview", "", "No production changes. Explicit confirmation is required before metadata fetching or applying changes.", "",
             "| Local gid / resolution | Action | Reason | Candidate |", "| --- | --- | --- | --- |"]
    for row in preview["items"]:
        candidate = f"[Inspect]({row['candidate_url']})" if row["candidate_url"] else "-"
        lines.append(f"| {row['gid']} / {row['resolution']} | {row['action']} | {row['reason']} | {candidate} |")
    lines += ["", "## Limits", ""] + ["- " + value for value in preview["limitations"]]
    return "\n".join(lines) + "\n"


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
             f"Status: {summary['status']}; completed {summary['completed']}/{summary['missing']}; pending {summary['pending']}; new requests {summary['requests']}; reused lookups {summary.get('reused_lookups', 0)}; stop reason: {summary['stop_reason'] or 'none'}.", "",
             "| Local gid / resolution | Candidate gid | Relationship | Matching samples | Candidate |", "| --- | --- | --- | --- | --- |"]
    for item in report["items"]:
        for candidate in item["candidates"]:
            lines.append(f"| {item['gid']} / {item['resolution']} | {candidate['gid']} | {candidate['relationship']} | {len(candidate['sample_matches'])} | [Inspect]({candidate['url']}) |")
        if not item["candidates"]:
            lines.append(f"| {item['gid']} / {item['resolution']} | - | {'no_visible_candidates' if item['complete'] else 'incomplete'} | - | - |")
    diagnostics = [safe_diagnostics(item["stop_diagnostics"]) for item in report["items"] if item.get("stop_diagnostics")]
    if diagnostics:
        lines += ["", "## Stopped Response", "", "```json", json.dumps(diagnostics, ensure_ascii=True, indent=2), "```"]
    lines += ["", "## Limits", ""] + ["- " + value for value in report["limitations"]]
    return "\n".join(lines) + "\n"
