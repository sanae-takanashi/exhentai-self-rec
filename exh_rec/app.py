from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import random
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from . import db
from .exhentai import (
    Gallery,
    check_access,
    fetch_galleries,
    fetch_gallery_detail,
    fetch_gallery_sample_pages,
    normalize_cookie_header,
    valid_cookie_header,
)
from .recommender import (
    clear_feedback,
    export_preferences,
    feedback_history,
    get_bootstrap_tags,
    import_preferences,
    learned_query_tags,
    model_snapshot,
    parse_bootstrap_tags,
    recommend_page,
    record_feedback,
    reset_library,
    retrain_model,
    score_gallery,
    store_galleries,
    store_gallery_samples,
    store_visual_embedding,
    upsert_bootstrap_tags,
)


HOST = os.environ.get("EXH_REC_HOST", "0.0.0.0")
PORT = int(os.environ.get("EXH_REC_PORT", "8787"))
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
FETCH_LOCK = threading.Lock()
FETCH_STATE: dict[str, Any] = {"running": False}
REFRESH_STATE: dict[str, Any] = {"last_checked_at": None, "next_check_at": None, "last_error": None}
REFRESH_WAKE = threading.Event()
COMMON_EXHENTAI_COOKIE_KEYS = ("ipb_member_id", "ipb_pass_hash", "igneous")
ALLOWED_THUMB_HOSTS = {"s.exhentai.org"}
THUMB_MAX_BYTES = 5 * 1024 * 1024


class ApiError(Exception):
    def __init__(self, status: HTTPStatus, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


class Handler(BaseHTTPRequestHandler):
    server_version = "ExhRec/0.1"

    def do_HEAD(self) -> None:
        try:
            path = urllib.parse.urlparse(self.path).path
            if path == "/":
                self.serve_static("index.html", body=False)
            elif path.startswith("/static/"):
                self.serve_static(path.removeprefix("/static/"), body=False)
            else:
                raise ApiError(HTTPStatus.NOT_FOUND, "Not found")
        except Exception as exc:
            self.handle_error(exc)

    def do_GET(self) -> None:
        try:
            path = urllib.parse.urlparse(self.path).path
            if path == "/":
                self.serve_static("index.html")
            elif path.startswith("/static/"):
                self.serve_static(path.removeprefix("/static/"))
            elif path == "/thumb":
                query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                thumb_url = str(query.get("url", [""])[0])
                gallery_url = str(query.get("gallery_url", [""])[0])
                self.serve_thumbnail(thumb_url, gallery_url)
            elif path == "/api/settings":
                self.send_json(get_settings())
            elif path == "/api/status":
                self.send_json(get_status())
            elif path == "/api/fetch-runs":
                query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                limit = query_int(query, "limit", default=10, lower=1, upper=100)
                with db.connect() as conn:
                    self.send_json({"items": fetch_runs(conn, limit=limit)})
            elif path == "/api/plan":
                query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                force_query = query.get("query", [None])[0]
                self.send_json(plan_fetch(force_query=force_query))
            elif path == "/api/recommendations":
                query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                limit = query_int(query, "limit", default=40, lower=1, upper=100)
                offset = query_int(query, "offset", default=0, lower=0, upper=10000)
                include_rated = parse_bool(query.get("include_rated", ["0"])[0])
                filter_text = query.get("filter", query.get("filter_text", [""]))[0]
                with db.connect() as conn:
                    self.send_json(
                        recommendation_payload(
                            conn,
                            limit=limit,
                            include_rated=include_rated,
                            offset=offset,
                            filter_text=filter_text,
                        )
                    )
            elif path == "/api/feedback":
                query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                gallery_url = str(query.get("gallery_url", [""])[0])
                limit = query_int(query, "limit", default=25, lower=1, upper=100)
                if not gallery_url:
                    raise ApiError(HTTPStatus.BAD_REQUEST, "gallery_url is required")
                with db.connect() as conn:
                    self.send_json(feedback_history_payload(conn, gallery_url, limit=limit))
            elif path == "/api/model":
                with db.connect() as conn:
                    self.send_json(model_snapshot(conn))
            elif path == "/api/export":
                with db.connect() as conn:
                    self.send_json(export_preferences(conn))
            else:
                raise ApiError(HTTPStatus.NOT_FOUND, "Not found")
        except Exception as exc:
            self.handle_error(exc)

    def do_POST(self) -> None:
        try:
            path = urllib.parse.urlparse(self.path).path
            if path == "/api/settings":
                payload = self.read_json()
                save_settings(payload)
                self.send_json(get_settings())
            elif path == "/api/fetch":
                payload = self.read_json()
                result = fetch_and_store(
                    force_query=payload.get("query"),
                    include_rated=parse_bool(payload.get("include_rated")),
                    filter_text=payload.get("filter_text"),
                )
                self.send_json(result)
            elif path == "/api/enrich":
                payload = self.read_json()
                result = enrich_recommendations(
                    include_rated=parse_bool(payload.get("include_rated")),
                    filter_text=payload.get("filter_text"),
                    limit=payload.get("limit"),
                )
                self.send_json(result)
            elif path == "/api/feedback":
                payload = self.read_json()
                gallery_url = str(payload.get("gallery_url") or "")
                if not gallery_url:
                    raise ApiError(HTTPStatus.BAD_REQUEST, "gallery_url is required")
                vote, score = parse_feedback_request(payload)
                with db.connect() as conn:
                    ensure_gallery_exists(conn, gallery_url)
                    record_feedback(conn, gallery_url, vote=vote, score=score, note=payload.get("note"))
                feedback_enrichment = enrich_feedback_gallery(gallery_url)
                with db.connect() as conn:
                    page = recommendation_payload(
                        conn,
                        limit=40,
                        include_rated=parse_bool(payload.get("include_rated")),
                        filter_text=payload.get("filter_text"),
                    )
                self.send_json({"ok": True, "feedback_enrichment": feedback_enrichment, **page})
            elif path == "/api/feedback/clear":
                payload = self.read_json()
                gallery_url = str(payload.get("gallery_url") or "")
                if not gallery_url:
                    raise ApiError(HTTPStatus.BAD_REQUEST, "gallery_url is required")
                with db.connect() as conn:
                    ensure_gallery_exists(conn, gallery_url)
                    removed = clear_feedback(conn, gallery_url)
                    page = recommendation_payload(
                        conn,
                        limit=40,
                        include_rated=parse_bool(payload.get("include_rated")),
                        filter_text=payload.get("filter_text"),
                    )
                self.send_json({"ok": True, "removed": removed, **page})
            elif path == "/api/retrain":
                payload = self.read_json()
                with db.connect() as conn:
                    retrain_model(conn)
                    self.send_json(
                        {
                            "ok": True,
                            "model": model_snapshot(conn),
                            **recommendation_payload(
                                conn,
                                limit=40,
                                include_rated=parse_bool(payload.get("include_rated")),
                                filter_text=payload.get("filter_text"),
                            ),
                        }
                    )
            elif path == "/api/check":
                result = check_saved_access()
                self.send_json(result)
            elif path == "/api/import":
                payload = self.read_json()
                replace = parse_bool(payload.get("replace"))
                data = payload.get("data")
                if not isinstance(data, dict):
                    raise ApiError(HTTPStatus.BAD_REQUEST, "data must be an exported preferences object")
                self.send_json(import_preferences_payload(data, replace=replace))
            elif path == "/api/visual":
                payload = self.read_json()
                self.send_json(save_visual_embedding_payload(payload))
            elif path == "/api/reset":
                self.read_json()
                self.send_json(reset_library_payload())
            else:
                raise ApiError(HTTPStatus.NOT_FOUND, "Not found")
        except Exception as exc:
            self.handle_error(exc)

    def read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode("utf-8") if length else "{}"
        try:
            return json.loads(raw or "{}")
        except json.JSONDecodeError as exc:
            raise ApiError(HTTPStatus.BAD_REQUEST, "Invalid JSON") from exc

    def send_json(self, payload: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        data = json.dumps(payload, ensure_ascii=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_bytes(
        self,
        data: bytes,
        content_type: str,
        status: HTTPStatus = HTTPStatus.OK,
        cache_control: str | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        if cache_control:
            self.send_header("Cache-Control", cache_control)
        self.end_headers()
        self.wfile.write(data)

    def serve_thumbnail(self, thumb_url: str, gallery_url: str) -> None:
        data, content_type = cached_thumbnail(thumb_url, gallery_url)
        self.send_bytes(data, content_type, cache_control="private, max-age=86400")

    def serve_static(self, name: str, body: bool = True) -> None:
        path = (STATIC_DIR / name).resolve()
        if not str(path).startswith(str(STATIC_DIR.resolve())) or not path.exists() or not path.is_file():
            raise ApiError(HTTPStatus.NOT_FOUND, "Not found")
        data = path.read_bytes()
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if body:
            self.wfile.write(data)

    def handle_error(self, exc: Exception) -> None:
        if isinstance(exc, ApiError):
            self.send_json({"error": exc.message}, status=exc.status)
            return
        self.send_json({"error": str(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def log_message(self, format: str, *args: Any) -> None:
        print(f"{self.address_string()} - {format % args}")


def get_settings() -> dict:
    with db.connect() as conn:
        cookie = db.get_setting(conn, "cookie_header", "")
        return {
            "has_cookie": bool(cookie),
            "cookie_preview": preview_cookie(cookie),
            "cookie_missing_keys": missing_common_cookie_keys(cookie),
            "auto_refresh": db.get_setting(conn, "auto_refresh", "1") == "1",
            "refresh_interval_minutes": refresh_interval_minutes(conn),
            "fetch_pages": fetch_pages(conn),
            "detail_fetch_limit": detail_fetch_limit(conn),
            "learned_query_limit": learned_query_limit(conn),
            "recommend_candidate_limit": recommend_candidate_limit(conn),
            "sample_extra_pages": sample_extra_pages(conn),
            "last_access_check": get_access_check(conn),
            "bootstrap_tags": get_bootstrap_tags(conn),
        }


def get_status() -> dict:
    with db.connect() as conn:
        return {
            "fetch": dict(FETCH_STATE),
            "last_fetch": last_fetch_run(conn),
            "fetch_history": fetch_runs(conn, limit=5),
            "plan": plan_fetch_from_conn(conn),
            "refresh": refresh_summary(conn),
            "settings": {
                "auto_refresh": db.get_setting(conn, "auto_refresh", "1") == "1",
                "refresh_interval_minutes": refresh_interval_minutes(conn),
                "fetch_pages": fetch_pages(conn),
                "detail_fetch_limit": detail_fetch_limit(conn),
                "learned_query_limit": learned_query_limit(conn),
                "recommend_candidate_limit": recommend_candidate_limit(conn),
                "has_cookie": bool(db.get_setting(conn, "cookie_header", "")),
                "last_access_check": get_access_check(conn),
            },
        }


def save_settings(payload: dict[str, Any]) -> None:
    refresh_relevant_change = False
    with db.connect() as conn:
        if parse_bool(payload.get("clear_cookie")):
            db.set_setting(conn, "cookie_header", "")
            db.set_setting(conn, "last_access_check", "")
            refresh_relevant_change = True
        if "cookie_header" in payload:
            raw_cookie_header = str(payload["cookie_header"])
            cookie_header = normalize_cookie_header(raw_cookie_header)
            if raw_cookie_header.strip() and not valid_cookie_header(cookie_header):
                raise ApiError(HTTPStatus.BAD_REQUEST, "Cookie input must contain name=value pairs")
            if cookie_header:
                db.set_setting(conn, "cookie_header", cookie_header)
                db.set_setting(conn, "last_access_check", "")
                refresh_relevant_change = True
        if "auto_refresh" in payload:
            db.set_setting(conn, "auto_refresh", "1" if parse_bool(payload["auto_refresh"]) else "0")
            refresh_relevant_change = True
        if "refresh_interval_minutes" in payload:
            minutes = bounded_int(payload["refresh_interval_minutes"], default=30, lower=5, upper=240)
            db.set_setting(conn, "refresh_interval_minutes", str(minutes))
            refresh_relevant_change = True
        if "fetch_pages" in payload:
            pages = bounded_int(payload["fetch_pages"], default=1, lower=1, upper=5)
            db.set_setting(conn, "fetch_pages", str(pages))
            refresh_relevant_change = True
        if "detail_fetch_limit" in payload:
            limit = bounded_int(payload["detail_fetch_limit"], default=8, lower=0, upper=50)
            db.set_setting(conn, "detail_fetch_limit", str(limit))
            refresh_relevant_change = True
        if "learned_query_limit" in payload:
            limit = bounded_int(payload["learned_query_limit"], default=6, lower=0, upper=20)
            db.set_setting(conn, "learned_query_limit", str(limit))
            refresh_relevant_change = True
        if "recommend_candidate_limit" in payload:
            limit = bounded_int(payload["recommend_candidate_limit"], default=2000, lower=100, upper=10000)
            db.set_setting(conn, "recommend_candidate_limit", str(limit))
        if "sample_extra_pages" in payload:
            extra = bounded_int(payload["sample_extra_pages"], default=2, lower=0, upper=10)
            db.set_setting(conn, "sample_extra_pages", str(extra))
            refresh_relevant_change = True
        if "bootstrap_tags_raw" in payload:
            upsert_bootstrap_tags(conn, parse_bootstrap_tags(str(payload["bootstrap_tags_raw"])))
            refresh_relevant_change = True
    if refresh_relevant_change:
        wake_background_refresh()


def wake_background_refresh() -> None:
    REFRESH_WAKE.set()


def check_saved_access() -> dict:
    with db.connect() as conn:
        cookie = db.get_setting(conn, "cookie_header", "")
    if not cookie:
        raise ApiError(HTTPStatus.BAD_REQUEST, "Save your ExHentai cookie first")
    try:
        result = check_access(cookie)
    except Exception as exc:
        result = {"ok": False, "gallery_count": 0, "message": str(exc)}
    result["checked_at"] = current_timestamp()
    with db.connect() as conn:
        db.set_setting(conn, "last_access_check", json.dumps(result, ensure_ascii=True))
    return result


def import_preferences_payload(data: dict, replace: bool = False) -> dict:
    try:
        with db.connect() as conn:
            result = import_preferences(conn, data, replace=replace)
            return {"ok": True, "imported": result, "model": model_snapshot(conn)}
    except ValueError as exc:
        raise ApiError(HTTPStatus.BAD_REQUEST, str(exc)) from exc


def save_visual_embedding_payload(payload: dict[str, Any]) -> dict:
    gallery_url = str(payload.get("gallery_url") or "").strip()
    if not gallery_url:
        raise ApiError(HTTPStatus.BAD_REQUEST, "gallery_url is required")
    embedding = payload.get("embedding")
    version = str(payload.get("version") or "").strip() or "unknown"
    try:
        with db.connect() as conn:
            store_visual_embedding(conn, gallery_url, embedding, version=version)
    except ValueError as exc:
        raise ApiError(HTTPStatus.BAD_REQUEST, str(exc)) from exc
    return {"ok": True, "gallery_url": gallery_url, "visual_ready": True}


def reset_library_payload() -> dict:
    if not FETCH_LOCK.acquire(blocking=False):
        raise ApiError(HTTPStatus.CONFLICT, "A fetch or enrichment is running; try again once it finishes")
    try:
        with db.connect() as conn:
            removed = reset_library(conn)
            page = recommendation_payload(conn, limit=40)
            model = model_snapshot(conn)
    finally:
        FETCH_LOCK.release()
    return {"ok": True, "removed": removed, "model": model, **page}


def plan_fetch(force_query: str | None = None) -> dict:
    with db.connect() as conn:
        return plan_fetch_from_conn(conn, force_query=force_query)


def plan_fetch_from_conn(conn, force_query: str | None = None) -> dict:
    pages = fetch_pages(conn)
    detail_limit = detail_fetch_limit(conn)
    learned_limit = learned_query_limit(conn)
    tags = get_bootstrap_tags(conn)
    learned_tags = learned_query_tags(conn, learned_limit)
    entries = build_query_plan(tags, learned_tags, force_query=force_query)
    return {
        "queries": [entry["query"] for entry in entries],
        "entries": entries,
        "pages": pages,
        "detail_fetch_limit": detail_limit,
        "learned_query_limit": learned_limit,
        "recommend_candidate_limit": recommend_candidate_limit(conn),
        "has_cookie": bool(db.get_setting(conn, "cookie_header", "")),
    }


def refresh_summary(conn) -> dict:
    enabled = db.get_setting(conn, "auto_refresh", "1") == "1"
    interval = refresh_interval_minutes(conn)
    has_cookie = bool(db.get_setting(conn, "cookie_header", ""))
    if not enabled:
        message = "Auto refresh disabled"
    elif not has_cookie:
        message = "Auto refresh waiting for a saved cookie"
    else:
        message = f"Auto refresh every {interval} minutes"
    return {
        "enabled": enabled,
        "has_cookie": has_cookie,
        "ready": enabled and has_cookie,
        "interval_minutes": interval,
        "message": message,
        "last_checked_at": REFRESH_STATE.get("last_checked_at"),
        "next_check_at": REFRESH_STATE.get("next_check_at") if enabled else None,
        "last_error": REFRESH_STATE.get("last_error"),
    }


def get_access_check(conn) -> dict | None:
    raw = db.get_setting(conn, "last_access_check", "")
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def fetch_and_store(
    force_query: str | None = None,
    trigger: str = "manual",
    include_rated: bool = False,
    filter_text: str | None = None,
) -> dict:
    if not FETCH_LOCK.acquire(blocking=False):
        raise ApiError(HTTPStatus.CONFLICT, "A fetch is already running")
    with db.connect() as conn:
        cookie = db.get_setting(conn, "cookie_header", "")
        pages = fetch_pages(conn)
        detail_limit = detail_fetch_limit(conn)
        learned_limit = learned_query_limit(conn)
        extra_pages = sample_extra_pages(conn)
        tags = get_bootstrap_tags(conn)
        learned_tags = learned_query_tags(conn, learned_limit)
    if not cookie:
        FETCH_LOCK.release()
        raise ApiError(HTTPStatus.BAD_REQUEST, "Save your ExHentai cookie first")

    queries = build_queries(tags, learned_tags, force_query)
    run_id: int | None = None
    fetched = 0
    stored = 0
    enriched = 0
    model_retrained = False
    selected_for_detail = []
    selected_urls: set[str] = set()
    errors: list[str] = []
    FETCH_STATE.update(
        {
            "running": True,
            "trigger": trigger,
            "queries": queries,
            "started_at": current_timestamp(),
            "fetched": 0,
            "stored": 0,
            "enriched": 0,
            "errors": [],
        }
    )
    try:
        with db.connect() as conn:
            cursor = conn.execute(
                "INSERT INTO fetch_runs(trigger, status, queries_json) VALUES (?, ?, ?)",
                (trigger, "running", json.dumps(queries, ensure_ascii=True)),
            )
            run_id = int(cursor.lastrowid)

        for query in queries:
            try:
                galleries = fetch_galleries(cookie, query=query, pages=pages)
                fetched += len(galleries)
                with db.connect() as conn:
                    stored += store_galleries(conn, galleries)
                    candidates = select_detail_candidates(conn, galleries, detail_limit - len(selected_for_detail))
                for gallery in candidates:
                    if gallery.url not in selected_urls:
                        selected_for_detail.append(gallery)
                        selected_urls.add(gallery.url)
                FETCH_STATE.update({"fetched": fetched, "stored": stored})
            except Exception as exc:
                errors.append(str(exc))
                FETCH_STATE["errors"] = list(errors)

        for gallery in selected_for_detail:
            try:
                detailed = fetch_gallery_detail(cookie, gallery)
                samples = collect_gallery_samples(cookie, detailed, extra_pages)
                with db.connect() as conn:
                    store_galleries(conn, [detailed], detail_fetched=True)
                    store_gallery_samples(conn, detailed.url, detailed.page_count, samples)
                enriched += 1
                FETCH_STATE.update({"enriched": enriched})
            except Exception as exc:
                errors.append(f"detail {gallery.url}: {exc}")
                FETCH_STATE["errors"] = list(errors)

        if fetched == 0 and not errors:
            errors.append(empty_fetch_error(queries))
            FETCH_STATE["errors"] = list(errors)

        status = "failed" if errors and fetched == 0 else "partial" if errors else "success"
        with db.connect() as conn:
            if enriched:
                retrain_model(conn)
                model_retrained = True
            conn.execute(
                """
                UPDATE fetch_runs
                SET status = ?, fetched_count = ?, stored_count = ?, enriched_count = ?, errors_json = ?, finished_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (status, fetched, stored, enriched, json.dumps(errors, ensure_ascii=True), run_id),
            )
            last_fetch = last_fetch_run(conn)
            page = recommend_page(
                conn,
                limit=40,
                include_rated=include_rated,
                filter_text=filter_text,
                candidate_limit=recommend_candidate_limit(conn),
            )
        return {
            "ok": not errors,
            "status": status,
            "queries": queries,
            "fetched": fetched,
            "stored": stored,
            "enriched": enriched,
            "model_retrained": model_retrained,
            "errors": errors,
            "last_fetch": last_fetch,
            **page,
        }
    except Exception as exc:
        if run_id is not None:
            errors.append(f"internal: {exc}")
            FETCH_STATE["errors"] = list(errors)
            with db.connect() as conn:
                finish_running_fetch_run(conn, run_id, "failed", fetched, stored, enriched, errors)
        raise
    finally:
        if run_id is not None:
            with db.connect() as conn:
                last = last_fetch_run(conn)
        else:
            last = None
        FETCH_STATE.update({"running": False, "last_fetch": last})
        FETCH_LOCK.release()


def enrich_recommendations(include_rated: bool = False, filter_text: str | None = None, limit: Any = None) -> dict:
    if not FETCH_LOCK.acquire(blocking=False):
        raise ApiError(HTTPStatus.CONFLICT, "A fetch or enrichment is already running")
    with db.connect() as conn:
        cookie = db.get_setting(conn, "cookie_header", "")
        detail_limit = detail_fetch_limit(conn)
        extra_pages = sample_extra_pages(conn)
    if not cookie:
        FETCH_LOCK.release()
        raise ApiError(HTTPStatus.BAD_REQUEST, "Save your ExHentai cookie first")

    requested_limit = detail_limit if limit is None else bounded_int(limit, default=detail_limit, lower=0, upper=50)
    run_id: int | None = None
    enriched = 0
    model_retrained = False
    errors: list[str] = []
    FETCH_STATE.update(
        {
            "running": True,
            "trigger": "enrich",
            "queries": ["recommendation details"],
            "started_at": current_timestamp(),
            "fetched": 0,
            "stored": 0,
            "enriched": 0,
            "errors": [],
        }
    )
    try:
        with db.connect() as conn:
            cursor = conn.execute(
                "INSERT INTO fetch_runs(trigger, status, queries_json) VALUES (?, ?, ?)",
                ("enrich", "running", json.dumps(["recommendation details"], ensure_ascii=True)),
            )
            run_id = int(cursor.lastrowid)

        with db.connect() as conn:
            candidates = select_recommendation_detail_candidates(
                conn,
                limit=requested_limit,
                include_rated=include_rated,
                filter_text=filter_text,
            )

        for gallery in candidates:
            try:
                detailed = fetch_gallery_detail(cookie, gallery)
                samples = collect_gallery_samples(cookie, detailed, extra_pages)
                with db.connect() as conn:
                    store_galleries(conn, [detailed], detail_fetched=True)
                    store_gallery_samples(conn, detailed.url, detailed.page_count, samples)
                enriched += 1
                FETCH_STATE.update({"enriched": enriched})
            except Exception as exc:
                errors.append(f"detail {gallery.url}: {exc}")
                FETCH_STATE["errors"] = list(errors)

        status = "failed" if errors and enriched == 0 else "partial" if errors else "success"
        with db.connect() as conn:
            if enriched:
                retrain_model(conn)
                model_retrained = True
            conn.execute(
                """
                UPDATE fetch_runs
                SET status = ?, enriched_count = ?, errors_json = ?, finished_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (status, enriched, json.dumps(errors, ensure_ascii=True), run_id),
            )
            last_fetch = last_fetch_run(conn)
            page = recommend_page(
                conn,
                limit=40,
                include_rated=include_rated,
                filter_text=filter_text,
                candidate_limit=recommend_candidate_limit(conn),
            )
        return {
            "ok": not errors,
            "status": status,
            "enriched": enriched,
            "model_retrained": model_retrained,
            "errors": errors,
            "last_fetch": last_fetch,
            **page,
        }
    except Exception as exc:
        if run_id is not None:
            errors.append(f"internal: {exc}")
            FETCH_STATE["errors"] = list(errors)
            with db.connect() as conn:
                finish_running_fetch_run(conn, run_id, "failed", 0, 0, enriched, errors)
        raise
    finally:
        if run_id is not None:
            with db.connect() as conn:
                last = last_fetch_run(conn)
        else:
            last = None
        FETCH_STATE.update({"running": False, "last_fetch": last})
        FETCH_LOCK.release()


SAMPLE_BASE_COUNT = 5


def sample_count_for(page_count: int | None) -> int:
    return SAMPLE_BASE_COUNT + (page_count or 0) // 100


def collect_gallery_samples(
    cookie: str,
    detailed: Gallery,
    extra_pages: int,
    delay: float = 1.0,
) -> list[str]:
    """Pick a random spread of page thumbnails for ``detailed`` (5 + pages//100)."""
    pool = list(dict.fromkeys(detailed.sample_thumbs))
    count = sample_count_for(detailed.page_count)
    if len(pool) < count and extra_pages > 0:
        for thumb in fetch_gallery_sample_pages(cookie, detailed, extra_pages, delay=delay):
            if thumb not in pool:
                pool.append(thumb)
    if len(pool) <= count:
        return pool
    return random.sample(pool, count)


def enrich_feedback_gallery(gallery_url: str) -> dict:
    with db.connect() as conn:
        cookie = db.get_setting(conn, "cookie_header", "")
        extra_pages = sample_extra_pages(conn)
        row = conn.execute("SELECT * FROM galleries WHERE url = ?", (gallery_url,)).fetchone()
        if not row:
            return {"status": "skipped", "reason": "gallery not found"}
        item = db.row_to_dict(row)
        if item.get("detail_fetched_at"):
            return {"status": "skipped", "reason": "already enriched"}
        if not cookie:
            return {"status": "skipped", "reason": "no cookie"}
        gallery = gallery_from_item(item)

    try:
        detailed = fetch_gallery_detail(cookie, gallery, delay=0)
        samples = collect_gallery_samples(cookie, detailed, extra_pages, delay=0)
    except Exception as exc:
        return {"status": "failed", "reason": str(exc)}

    with db.connect() as conn:
        store_galleries(conn, [detailed], detail_fetched=True)
        store_gallery_samples(conn, detailed.url, detailed.page_count, samples)
        retrain_model(conn)
    return {"status": "success", "gallery_url": gallery_url}


def ensure_gallery_exists(conn, gallery_url: str) -> None:
    exists = conn.execute("SELECT 1 FROM galleries WHERE url = ?", (gallery_url,)).fetchone()
    if not exists:
        raise ApiError(HTTPStatus.NOT_FOUND, "Gallery not found")


def select_detail_candidates(conn, galleries, remaining_limit: int) -> list:
    if remaining_limit <= 0:
        return []
    bootstrap = {row["tag"]: row["weight"] for row in conn.execute("SELECT tag, weight FROM bootstrap_tags")}
    weights = {row["feature"]: row["weight"] for row in conn.execute("SELECT feature, weight FROM feature_weights")}
    candidates = []
    seen: set[str] = set()
    for order, gallery in enumerate(galleries):
        if gallery.url in seen:
            continue
        seen.add(gallery.url)
        row = conn.execute("SELECT detail_fetched_at FROM galleries WHERE url = ?", (gallery.url,)).fetchone()
        if row and row["detail_fetched_at"]:
            continue
        score, _ = score_gallery(
            {
                "title": gallery.title,
                "category": gallery.category,
                "uploader": gallery.uploader,
                "rating": gallery.rating,
                "tags": gallery.tags,
            },
            bootstrap,
            weights,
        )
        candidates.append((score, order, gallery))
    candidates.sort(key=lambda item: (-item[0], item[1]))
    return [gallery for _, _, gallery in candidates[:remaining_limit]]


def select_recommendation_detail_candidates(
    conn,
    limit: int,
    include_rated: bool = False,
    filter_text: str | None = None,
) -> list[Gallery]:
    if limit <= 0:
        return []
    page = recommend_page(
        conn,
        limit=100,
        include_rated=include_rated,
        filter_text=filter_text,
        candidate_limit=recommend_candidate_limit(conn),
    )
    candidates: list[Gallery] = []
    for item in page["items"]:
        if item.get("detail_fetched_at"):
            continue
        candidates.append(gallery_from_item(item))
        if len(candidates) >= limit:
            break
    return candidates


def gallery_from_item(item: dict) -> Gallery:
    return Gallery(
        url=item["url"],
        gid=item.get("gid"),
        token=item.get("token"),
        title=item.get("title") or "Gallery",
        category=item.get("category"),
        uploader=item.get("uploader"),
        posted_at=item.get("posted_at"),
        thumb_url=item.get("thumb_url"),
        rating=item.get("rating"),
        tags=list(item.get("tags") or []),
        source_query=item.get("source_query"),
    )


def build_queries(
    tags: list[dict],
    learned_tags: list[str] | None = None,
    force_query: str | None = None,
) -> list[str | None]:
    return [entry["query"] for entry in build_query_plan(tags, learned_tags, force_query=force_query)]


def build_query_plan(
    tags: list[dict],
    learned_tags: list[str] | None = None,
    force_query: str | None = None,
) -> list[dict]:
    manual_query = force_query.strip() if isinstance(force_query, str) else ""
    if manual_query:
        return [{"query": manual_query, "source": "manual", "label": manual_query}]
    candidates: list[dict] = [{"query": None, "source": "recent", "label": "Recent galleries"}]
    bootstrap_tags = [
        item
        for item in tags
        if float(item["weight"]) > 0 and is_remote_search_preference(item["tag"])
    ]
    bootstrap_tags.sort(key=lambda item: (-float(item["weight"]), str(item["tag"])))
    for item in bootstrap_tags[:6]:
        candidates.append(
            {
                "query": format_generated_query(item["tag"]),
                "source": "bootstrap",
                "label": item["tag"],
                "weight": item["weight"],
            }
        )
    blocked_learned_tags = negative_remote_preferences(tags)
    for tag in (learned_tags or [])[:20]:
        normalized_tag = str(tag).strip().lower()
        if normalized_tag in blocked_learned_tags:
            continue
        candidates.append({"query": format_generated_query(tag), "source": "learned", "label": tag})
    entries: list[dict] = []
    seen: set[str | None] = set()
    for candidate in candidates:
        query = candidate["query"]
        normalized = query.strip() if isinstance(query, str) else query
        if normalized == "":
            continue
        if normalized in seen:
            continue
        candidate["query"] = normalized
        entries.append(candidate)
        seen.add(normalized)
    return entries


def negative_remote_preferences(tags: list[dict]) -> set[str]:
    return {
        str(item["tag"]).strip().lower()
        for item in tags
        if float(item["weight"]) < 0 and is_remote_search_preference(item["tag"])
    }


def is_remote_search_preference(tag: str) -> bool:
    value = str(tag).strip().lower()
    if not value:
        return False
    namespace = value.split(":", 1)[0] if ":" in value else ""
    return namespace not in {"category", "uploader"}


def format_generated_query(tag: str) -> str:
    value = str(tag).strip()
    if not value or '"' in value:
        return value
    if ":" in value:
        namespace, tag_value = value.split(":", 1)
        tag_value = tag_value.strip()
        if " " in tag_value:
            return f'{namespace.strip()}:"{tag_value}"'
        return value
    if " " in value:
        return f'"{value}"'
    return value


def parse_bool(value: str | int | bool | None) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def query_int(query: dict[str, list[str]], key: str, default: int, lower: int, upper: int) -> int:
    return bounded_int((query.get(key) or [default])[0], default=default, lower=lower, upper=upper)


def parse_feedback_request(payload: dict[str, Any]) -> tuple[int | None, int | None]:
    score = payload.get("score")
    vote = payload.get("vote")
    if score is not None:
        parsed_score = strict_int(score)
        if parsed_score is None or parsed_score < 1 or parsed_score > 5:
            raise ApiError(HTTPStatus.BAD_REQUEST, "score must be between 1 and 5")
        return None, parsed_score
    if vote is not None:
        parsed_vote = strict_int(vote)
        if parsed_vote not in (-1, 1):
            raise ApiError(HTTPStatus.BAD_REQUEST, "vote must be 1 or -1")
        return parsed_vote, None
    raise ApiError(HTTPStatus.BAD_REQUEST, "vote or score is required")


def strict_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, float) and not value.is_integer():
        return None
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None


def bounded_int(value: Any, default: int, lower: int, upper: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        parsed = default
    return max(lower, min(upper, parsed))


def fetch_pages(conn) -> int:
    return bounded_int(db.get_setting(conn, "fetch_pages", "1"), default=1, lower=1, upper=5)


def detail_fetch_limit(conn) -> int:
    return bounded_int(db.get_setting(conn, "detail_fetch_limit", "8"), default=8, lower=0, upper=50)


def learned_query_limit(conn) -> int:
    return bounded_int(db.get_setting(conn, "learned_query_limit", "6"), default=6, lower=0, upper=20)


def refresh_interval_minutes(conn) -> int:
    return bounded_int(db.get_setting(conn, "refresh_interval_minutes", "30"), default=30, lower=5, upper=240)


def recommend_candidate_limit(conn) -> int:
    return bounded_int(db.get_setting(conn, "recommend_candidate_limit", "2000"), default=2000, lower=100, upper=10000)


def sample_extra_pages(conn) -> int:
    return bounded_int(db.get_setting(conn, "sample_extra_pages", "2"), default=2, lower=0, upper=10)


def recommendation_payload(
    conn,
    limit: int = 40,
    include_rated: bool = False,
    offset: int = 0,
    filter_text: str | None = None,
) -> dict:
    return {
        **recommend_page(
            conn,
            limit=limit,
            include_rated=include_rated,
            offset=offset,
            filter_text=filter_text,
            candidate_limit=recommend_candidate_limit(conn),
        ),
        "last_fetch": last_fetch_run(conn),
    }


def feedback_history_payload(conn, gallery_url: str, limit: int = 25) -> dict:
    row = conn.execute(
        """
        SELECT url, title, category, uploader, thumb_url
        FROM galleries
        WHERE url = ?
        """,
        (gallery_url,),
    ).fetchone()
    if not row:
        raise ApiError(HTTPStatus.NOT_FOUND, "Gallery not found")
    items = feedback_history(conn, gallery_url, limit=limit)
    return {
        "gallery": dict(row),
        "items": items,
        "latest": items[0] if items else None,
    }


def cached_thumbnail(thumb_url: str, gallery_url: str = "") -> tuple[bytes, str]:
    thumb_url = normalize_thumbnail_url(thumb_url)
    if not thumb_url:
        raise ApiError(HTTPStatus.BAD_REQUEST, "thumbnail url is required")
    if not is_allowed_thumbnail_url(thumb_url):
        raise ApiError(HTTPStatus.BAD_REQUEST, "unsupported thumbnail host")

    data_path, meta_path = thumbnail_cache_paths(thumb_url)
    cached = read_cached_thumbnail(data_path, meta_path, thumb_url)
    if cached:
        return cached

    with db.connect() as conn:
        cookie = db.get_setting(conn, "cookie_header", "")
    if not cookie:
        raise ApiError(HTTPStatus.BAD_REQUEST, "Save your ExHentai cookie first")

    data, content_type = fetch_thumbnail_bytes(cookie, thumb_url, thumbnail_referer(gallery_url))
    data_path.parent.mkdir(parents=True, exist_ok=True)
    data_path.write_bytes(data)
    meta_path.write_text(
        json.dumps(
            {
                "source_url": thumb_url,
                "content_type": content_type,
                "fetched_at": current_timestamp(),
            },
            ensure_ascii=True,
        ),
        encoding="utf-8",
    )
    return data, content_type


def normalize_thumbnail_url(thumb_url: str) -> str:
    thumb_url = thumb_url.strip()
    if thumb_url.startswith("//"):
        return f"https:{thumb_url}"
    return thumb_url


def is_allowed_thumbnail_url(thumb_url: str) -> bool:
    parsed = urllib.parse.urlparse(thumb_url)
    hostname = (parsed.hostname or "").lower()
    return parsed.scheme == "https" and hostname in ALLOWED_THUMB_HOSTS


def thumbnail_cache_paths(thumb_url: str) -> tuple[Path, Path]:
    digest = hashlib.sha256(thumb_url.encode("utf-8")).hexdigest()
    cache_dir = db.DATA_DIR / "thumbs"
    return cache_dir / f"{digest}.bin", cache_dir / f"{digest}.json"


def read_cached_thumbnail(data_path: Path, meta_path: Path, thumb_url: str) -> tuple[bytes, str] | None:
    if not data_path.exists():
        return None
    content_type = ""
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            content_type = str(meta.get("content_type") or "")
        except (OSError, json.JSONDecodeError):
            content_type = ""
    if not content_type.startswith("image/"):
        content_type = mimetypes.guess_type(urllib.parse.urlparse(thumb_url).path)[0] or "application/octet-stream"
    return data_path.read_bytes(), content_type


def thumbnail_referer(gallery_url: str) -> str:
    parsed = urllib.parse.urlparse(gallery_url.strip())
    if parsed.scheme == "https" and (parsed.hostname or "").lower() == "exhentai.org" and parsed.path.startswith("/g/"):
        return gallery_url.strip()
    return "https://exhentai.org/"


def fetch_thumbnail_bytes(cookie_header: str, thumb_url: str, referer: str, timeout: int = 20) -> tuple[bytes, str]:
    request = urllib.request.Request(
        thumb_url,
        headers={
            "Cookie": normalize_cookie_header(cookie_header),
            "User-Agent": "exhentai-self-recommend/0.1 (+local personal recommender)",
            "Accept": "image/avif,image/webp,image/*,*/*",
            "Referer": referer,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            content_type = response_content_type(response.headers)
            data = response.read(THUMB_MAX_BYTES + 1)
    except urllib.error.HTTPError as exc:
        raise ApiError(HTTPStatus.BAD_GATEWAY, f"Thumbnail fetch failed with HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise ApiError(HTTPStatus.BAD_GATEWAY, f"Thumbnail fetch failed: {exc.reason}") from exc

    if len(data) > THUMB_MAX_BYTES:
        raise ApiError(HTTPStatus.BAD_GATEWAY, "Thumbnail is too large")
    if not content_type.startswith("image/"):
        raise ApiError(HTTPStatus.BAD_GATEWAY, "Thumbnail response was not an image")
    return data, content_type


def response_content_type(headers: Any) -> str:
    if hasattr(headers, "get_content_type"):
        return str(headers.get_content_type())
    return str(headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()


def preview_cookie(cookie: str) -> str:
    names = cookie_key_names(cookie)
    return ", ".join(names[:8])


def cookie_key_names(cookie: str) -> list[str]:
    if not cookie:
        return []
    names = []
    for part in cookie.split(";"):
        name = part.strip().split("=", 1)[0].strip()
        if name:
            names.append(name)
    return names


def missing_common_cookie_keys(cookie: str) -> list[str]:
    names = {name.lower() for name in cookie_key_names(cookie)}
    return [name for name in COMMON_EXHENTAI_COOKIE_KEYS if name not in names]


def current_timestamp() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())


def empty_fetch_error(queries: list[str | None]) -> str:
    if any(query for query in queries):
        return "No galleries found; check the saved cookie, access, or search terms"
    return "No galleries found; check the saved cookie or ExHentai access"


def timestamp_after(seconds: int | float) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(time.time() + max(0, seconds)))


def last_fetch_run(conn) -> dict | None:
    runs = fetch_runs(conn, limit=1)
    return runs[0] if runs else None


def finish_running_fetch_run(
    conn,
    run_id: int,
    status: str,
    fetched: int,
    stored: int,
    enriched: int,
    errors: list[str],
) -> None:
    conn.execute(
        """
        UPDATE fetch_runs
        SET status = ?, fetched_count = ?, stored_count = ?, enriched_count = ?, errors_json = ?, finished_at = CURRENT_TIMESTAMP
        WHERE id = ? AND status = 'running'
        """,
        (status, fetched, stored, enriched, json.dumps(errors, ensure_ascii=True), run_id),
    )


def fetch_runs(conn, limit: int = 10) -> list[dict]:
    limit = max(1, min(100, int(limit)))
    rows = conn.execute(
        """
        SELECT id, trigger, status, queries_json, fetched_count, stored_count, enriched_count, errors_json, started_at, finished_at
        FROM fetch_runs
        ORDER BY started_at DESC, id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    history = []
    for row in rows:
        data = dict(row)
        data["queries"] = safe_json_list(data.pop("queries_json"))
        data["errors"] = safe_json_list(data.pop("errors_json"))
        history.append(data)
    return history


def safe_json_list(raw: str | None) -> list:
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return value if isinstance(value, list) else []


def background_refresh(stop: threading.Event) -> None:
    while not stop.is_set():
        try:
            REFRESH_STATE.update({"last_checked_at": current_timestamp(), "last_error": None})
            with db.connect() as conn:
                enabled = db.get_setting(conn, "auto_refresh", "1") == "1"
                interval = refresh_interval_minutes(conn)
                has_cookie = bool(db.get_setting(conn, "cookie_header", ""))
            if enabled and has_cookie:
                fetch_and_store(trigger="background")
            wait_for_refresh_wake(stop, max(300, interval * 60))
        except Exception as exc:
            print(f"background refresh failed: {exc}")
            REFRESH_STATE.update({"last_error": str(exc)})
            wait_for_refresh_wake(stop, 300)


def wait_for_refresh_wake(stop: threading.Event, timeout: int) -> None:
    REFRESH_STATE["next_check_at"] = timestamp_after(timeout)
    deadline = time.monotonic() + max(0, timeout)
    while not stop.is_set():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            REFRESH_STATE["next_check_at"] = None
            return
        if REFRESH_WAKE.wait(min(remaining, 1.0)):
            REFRESH_WAKE.clear()
            REFRESH_STATE["next_check_at"] = None
            return
    REFRESH_STATE["next_check_at"] = None


def server_display_url(host: str, port: int) -> str:
    display_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
    return f"http://{display_host}:{port}"


def main() -> None:
    db.init_db()
    with db.connect() as conn:
        retrain_model(conn)
    stop = threading.Event()
    worker = threading.Thread(target=background_refresh, args=(stop,), daemon=True)
    worker.start()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Serving ExHentai recommender at {server_display_url(HOST, PORT)}")
    if HOST == "0.0.0.0":
        print(f"Remote clients can use http://<server-ip>:{PORT}")
    print(f"SQLite data: {db.DB_PATH}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        server.server_close()


if __name__ == "__main__":
    main()
