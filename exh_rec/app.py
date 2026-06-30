from __future__ import annotations

import hashlib
import io
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
    apply_gallery_metadata,
    check_access,
    fetch_galleries,
    fetch_gallery_detail,
    fetch_gallery_metadata,
    fetch_gallery_sample_pages,
    normalize_cookie_header,
    sample_entry_url,
    usable_thumb,
    valid_cookie_header,
)
from .net import (
    apply_proxy_environment,
    configure_request_rate_limit,
    default_proxy_url,
    normalize_proxy_url,
    open_url_with_retry,
    pause_after_temporary_ban,
    proxy_preview,
    temporary_ban_detected,
)
from .recommender import (
    clear_feedback,
    clear_gallery_mark,
    export_preferences,
    feedback_history,
    feedback_signal,
    get_bootstrap_tags,
    gallery_matches_filter,
    import_preferences,
    learned_query_tags,
    marked_gallery_page,
    model_snapshot,
    normalize_gallery_url,
    normalize_language_filter,
    normalize_model_mode,
    normalize_posted_after,
    parse_bootstrap_tags,
    reaction_history_page,
    recommend_page,
    record_gallery_mark,
    record_feedback,
    reset_library,
    retrain_model,
    clear_shared_thumbnail_metadata,
    score_gallery,
    short_repeat_page,
    store_galleries,
    store_gallery_samples,
    store_visual_embedding,
    tag_corpus_strengths,
    upsert_bootstrap_tags,
    visual_preference_model,
)
from .visual import (
    DEFAULT_VISUAL_ENCODER,
    DEFAULT_DINOV2_DEVICE,
    DINOV2_MODEL_NAME,
    DINOV2_VISUAL_VERSION,
    SIMPLE_VISUAL_VERSION,
    VisualEncoderUnavailable,
    dinov2_dependency_status,
    dinov2_embedding,
    download_dinov2,
    normalize_dinov2_device,
    normalize_visual_encoder,
)


HOST = os.environ.get("EXH_REC_HOST", "0.0.0.0")
PORT = int(os.environ.get("EXH_REC_PORT", "8787"))
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
FETCH_LOCK = threading.Lock()
FETCH_STATE: dict[str, Any] = {"running": False}
PARENT_UPDATE_STATE: dict[str, Any] = {"running": False, "logs": []}
REFRESH_STATE: dict[str, Any] = {"last_checked_at": None, "next_check_at": None, "last_error": None}
REFRESH_WAKE = threading.Event()
COMMON_EXHENTAI_COOKIE_KEYS = ("ipb_member_id", "ipb_pass_hash", "igneous")
ALLOWED_THUMB_HOSTS = {"s.exhentai.org", "ehgt.org"}
THUMB_MAX_BYTES = 5 * 1024 * 1024
PARENT_UPDATE_LOG_LIMIT = 120
PARENT_UPDATE_FETCH_RETRIES = 2
PARENT_UPDATE_RETRY_BACKOFF_SECONDS = 1.0


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
                if "sample" in query:
                    sample_index = query_int(query, "sample", default=0, lower=0, upper=10000)
                    self.serve_gallery_sample(gallery_url, sample_index)
                else:
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
                freshness_weight = query_float(query, "freshness_weight", default=1.0, lower=0.0, upper=50.0)
                posted_after = query.get("posted_after", [""])[0]
                bootstrap_explore_count = query_int(query, "bootstrap_explore_count", default=0, lower=0, upper=20)
                explore_seed = str(query.get("explore_seed", [""])[0])[:80]
                language_filter = query.get("language_filter", [None])[0]
                model_mode = query.get("model_mode", [None])[0]
                require_bootstrap_match = parse_bool(query.get("require_bootstrap_match", ["0"])[0])
                filter_text = query.get("filter", query.get("filter_text", [""]))[0]
                with db.connect() as conn:
                    self.send_json(
                        recommendation_payload(
                            conn,
                            limit=limit,
                            include_rated=include_rated,
                            offset=offset,
                            filter_text=filter_text,
                            freshness_weight=freshness_weight,
                            posted_after=posted_after,
                            bootstrap_explore_count=bootstrap_explore_count,
                            explore_seed=explore_seed,
                            language_filter=language_filter,
                            model_mode=model_mode,
                            require_bootstrap_match=require_bootstrap_match,
                        )
                    )
            elif path == "/api/reactions":
                query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                limit = query_int(query, "limit", default=40, lower=1, upper=100)
                offset = query_int(query, "offset", default=0, lower=0, upper=10000)
                filter_text = query.get("filter", query.get("filter_text", [""]))[0]
                with db.connect() as conn:
                    self.send_json(reaction_history_payload(conn, limit=limit, offset=offset, filter_text=filter_text))
            elif path == "/api/short-repeats":
                query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                limit = query_int(query, "limit", default=40, lower=1, upper=100)
                offset = query_int(query, "offset", default=0, lower=0, upper=10000)
                filter_text = query.get("filter", query.get("filter_text", [""]))[0]
                with db.connect() as conn:
                    self.send_json(short_repeat_payload(conn, limit=limit, offset=offset, filter_text=filter_text))
            elif path == "/api/marks":
                query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                limit = query_int(query, "limit", default=40, lower=1, upper=100)
                offset = query_int(query, "offset", default=0, lower=0, upper=10000)
                kind = parse_mark_kind(query.get("kind", ["favorite"])[0])
                filter_text = query.get("filter", query.get("filter_text", [""]))[0]
                with db.connect() as conn:
                    self.send_json(marked_gallery_payload(conn, kind=kind, limit=limit, offset=offset, filter_text=filter_text))
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
            elif path == "/api/refresh-thumbs":
                payload = self.read_json()
                result = refresh_thumbnails(
                    payload.get("gallery_urls"),
                    include_rated=parse_bool(payload.get("include_rated")),
                    filter_text=payload.get("filter_text"),
                )
                self.send_json(result)
            elif path == "/api/feedback":
                payload = self.read_json()
                gallery_url = str(payload.get("gallery_url") or "")
                if not gallery_url:
                    raise ApiError(HTTPStatus.BAD_REQUEST, "gallery_url is required")
                vote, score = parse_feedback_request(payload)
                signal = feedback_signal(vote=vote, score=score)
                require_bootstrap_match = parse_bool(payload.get("require_bootstrap_match"))
                with db.connect() as conn:
                    ensure_gallery_exists(conn, gallery_url)
                    before_model = model_snapshot(conn)
                    before_signature = model_signature(conn)
                    update_started = time.perf_counter()
                    log_feedback_received("record", gallery_url, vote=vote, score=score)
                    record_feedback(conn, gallery_url, vote=vote, score=score, note=payload.get("note"))
                    elapsed_ms = round((time.perf_counter() - update_started) * 1000, 2)
                    after_model = model_snapshot(conn)
                    after_signature = model_signature(conn)
                    feedback_update = feedback_update_summary(
                        conn,
                        action="record",
                        gallery_url=gallery_url,
                        vote=vote,
                        score=score,
                        before_model=before_model,
                        before_signature=before_signature,
                        after_model=after_model,
                        after_signature=after_signature,
                        retrained=signal != 0 or before_signature != after_signature,
                        elapsed_ms=elapsed_ms,
                    )
                    log_feedback_update(feedback_update)
                feedback_enrichment = feedback_enrichment_plan(signal, payload)
                with db.connect() as conn:
                    page = response_page_payload(conn, payload, require_bootstrap_match=require_bootstrap_match)
                self.send_json({"ok": True, "feedback_update": feedback_update, "feedback_enrichment": feedback_enrichment, **page})
            elif path == "/api/mark":
                payload = self.read_json()
                gallery_url = str(payload.get("gallery_url") or "")
                if not gallery_url:
                    raise ApiError(HTTPStatus.BAD_REQUEST, "gallery_url is required")
                kind = parse_mark_kind(payload.get("kind"))
                require_bootstrap_match = parse_bool(payload.get("require_bootstrap_match"))
                with db.connect() as conn:
                    ensure_gallery_exists(conn, gallery_url)
                    before_model = model_snapshot(conn)
                    before_signature = model_signature(conn)
                    update_started = time.perf_counter()
                    record_gallery_mark(conn, gallery_url, kind=kind, note=payload.get("note"))
                    elapsed_ms = round((time.perf_counter() - update_started) * 1000, 2)
                    after_model = model_snapshot(conn)
                    after_signature = model_signature(conn)
                    mark_update = mark_update_summary(
                        conn,
                        action="record",
                        gallery_url=gallery_url,
                        kind=kind,
                        before_model=before_model,
                        before_signature=before_signature,
                        after_model=after_model,
                        after_signature=after_signature,
                        elapsed_ms=elapsed_ms,
                    )
                    page = response_page_payload(conn, payload, require_bootstrap_match=require_bootstrap_match)
                self.send_json({"ok": True, "mark_update": mark_update, **page})
            elif path == "/api/mark/clear":
                payload = self.read_json()
                gallery_url = str(payload.get("gallery_url") or "")
                if not gallery_url:
                    raise ApiError(HTTPStatus.BAD_REQUEST, "gallery_url is required")
                require_bootstrap_match = parse_bool(payload.get("require_bootstrap_match"))
                with db.connect() as conn:
                    ensure_gallery_exists(conn, gallery_url)
                    before_model = model_snapshot(conn)
                    before_signature = model_signature(conn)
                    update_started = time.perf_counter()
                    removed = clear_gallery_mark(conn, gallery_url)
                    elapsed_ms = round((time.perf_counter() - update_started) * 1000, 2)
                    after_model = model_snapshot(conn)
                    after_signature = model_signature(conn)
                    mark_update = mark_update_summary(
                        conn,
                        action="clear",
                        gallery_url=gallery_url,
                        kind=None,
                        before_model=before_model,
                        before_signature=before_signature,
                        after_model=after_model,
                        after_signature=after_signature,
                        removed=removed,
                        elapsed_ms=elapsed_ms,
                    )
                    page = response_page_payload(conn, payload, require_bootstrap_match=require_bootstrap_match)
                self.send_json({"ok": True, "removed": removed, "mark_update": mark_update, **page})
            elif path == "/api/feedback/clear":
                payload = self.read_json()
                gallery_url = str(payload.get("gallery_url") or "")
                if not gallery_url:
                    raise ApiError(HTTPStatus.BAD_REQUEST, "gallery_url is required")
                require_bootstrap_match = parse_bool(payload.get("require_bootstrap_match"))
                with db.connect() as conn:
                    ensure_gallery_exists(conn, gallery_url)
                    before_model = model_snapshot(conn)
                    before_signature = model_signature(conn)
                    update_started = time.perf_counter()
                    log_feedback_received("clear", gallery_url)
                    removed = clear_feedback(conn, gallery_url)
                    elapsed_ms = round((time.perf_counter() - update_started) * 1000, 2)
                    after_model = model_snapshot(conn)
                    feedback_update = feedback_update_summary(
                        conn,
                        action="clear",
                        gallery_url=gallery_url,
                        vote=None,
                        score=None,
                        before_model=before_model,
                        before_signature=before_signature,
                        after_model=after_model,
                        after_signature=model_signature(conn),
                        removed=removed,
                        elapsed_ms=elapsed_ms,
                    )
                    log_feedback_update(feedback_update)
                    page = response_page_payload(conn, payload, require_bootstrap_match=require_bootstrap_match)
                self.send_json({"ok": True, "removed": removed, "feedback_update": feedback_update, **page})
            elif path == "/api/short-repeats/recalculate":
                payload = self.read_json()
                with db.connect() as conn:
                    page = short_repeat_payload(conn, limit=40, filter_text=payload.get("filter_text"))
                self.send_json({"ok": True, "recalculated_at": current_timestamp(), **page})
            elif path == "/api/reactions/backfill-parents":
                payload = self.read_json()
                result = backfill_parent_metadata(
                    scope=payload.get("scope") or "history",
                    limit=payload.get("limit") or 100,
                    filter_text=payload.get("filter_text"),
                )
                self.send_json(result)
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
            elif path == "/api/visual/download":
                self.read_json()
                self.send_json(download_dinov2_payload())
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

    def serve_gallery_sample(self, gallery_url: str, sample_index: int) -> None:
        data, content_type = cached_gallery_sample(gallery_url, sample_index)
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
        proxy_url = network_proxy(conn)
        dinov2_device = configured_dinov2_device(conn)
        visual_encoder = configured_visual_encoder(conn)
        return {
            "has_cookie": bool(cookie),
            "cookie_preview": preview_cookie(cookie),
            "cookie_missing_keys": missing_common_cookie_keys(cookie),
            "auto_refresh": db.get_setting(conn, "auto_refresh", "1") == "1",
            "refresh_interval_minutes": refresh_interval_minutes(conn),
            "fetch_pages": fetch_pages(conn),
            "stale_fetch_extra_pages": stale_fetch_extra_pages(conn),
            "detail_fetch_limit": detail_fetch_limit(conn),
            "learned_query_limit": learned_query_limit(conn),
            "request_interval_seconds": request_interval_seconds(conn),
            "temporary_ban_pause_seconds": temporary_ban_pause_seconds(conn),
            "recommend_candidate_limit": recommend_candidate_limit(conn),
            "recommend_language_filter": configured_language_filter(conn),
            "recommend_model_mode": configured_model_mode(conn),
            "preview_freshness_weight": preview_freshness_weight(conn),
            "preview_posted_after": preview_posted_after(conn),
            "review_require_bootstrap_match": configured_review_require_bootstrap_match(conn),
            "sample_extra_pages": sample_extra_pages(conn),
            "network_proxy": proxy_url,
            "network_proxy_preview": proxy_preview(proxy_url),
            "visual_encoder": visual_encoder,
            "dinov2_device": dinov2_device,
            "visual": visual_settings(visual_encoder, dinov2_device),
            "last_access_check": get_access_check(conn),
            "bootstrap_tags": get_bootstrap_tags(conn),
        }


def get_status() -> dict:
    with db.connect() as conn:
        proxy_url = network_proxy(conn)
        dinov2_device = configured_dinov2_device(conn)
        visual_encoder = configured_visual_encoder(conn)
        return {
            "fetch": dict(FETCH_STATE),
            "parent_update": parent_update_status(),
            "last_fetch": last_fetch_run(conn),
            "fetch_history": fetch_runs(conn, limit=5),
            "plan": plan_fetch_from_conn(conn),
            "refresh": refresh_summary(conn),
            "settings": {
                "auto_refresh": db.get_setting(conn, "auto_refresh", "1") == "1",
                "refresh_interval_minutes": refresh_interval_minutes(conn),
                "fetch_pages": fetch_pages(conn),
                "stale_fetch_extra_pages": stale_fetch_extra_pages(conn),
                "detail_fetch_limit": detail_fetch_limit(conn),
                "learned_query_limit": learned_query_limit(conn),
                "request_interval_seconds": request_interval_seconds(conn),
                "temporary_ban_pause_seconds": temporary_ban_pause_seconds(conn),
                "recommend_candidate_limit": recommend_candidate_limit(conn),
                "recommend_language_filter": configured_language_filter(conn),
                "recommend_model_mode": configured_model_mode(conn),
                "preview_freshness_weight": preview_freshness_weight(conn),
                "preview_posted_after": preview_posted_after(conn),
                "review_require_bootstrap_match": configured_review_require_bootstrap_match(conn),
                "has_cookie": bool(db.get_setting(conn, "cookie_header", "")),
                "network_proxy": proxy_url,
                "network_proxy_preview": proxy_preview(proxy_url),
                "visual_encoder": visual_encoder,
                "dinov2_device": dinov2_device,
                "last_access_check": get_access_check(conn),
            },
            "visual": visual_settings(visual_encoder, dinov2_device),
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
        if "stale_fetch_extra_pages" in payload:
            pages = bounded_int(payload["stale_fetch_extra_pages"], default=20, lower=0, upper=50)
            db.set_setting(conn, "stale_fetch_extra_pages", str(pages))
            refresh_relevant_change = True
        if "detail_fetch_limit" in payload:
            limit = bounded_int(payload["detail_fetch_limit"], default=8, lower=0, upper=50)
            db.set_setting(conn, "detail_fetch_limit", str(limit))
            refresh_relevant_change = True
        if "learned_query_limit" in payload:
            limit = bounded_int(payload["learned_query_limit"], default=6, lower=0, upper=20)
            db.set_setting(conn, "learned_query_limit", str(limit))
            refresh_relevant_change = True
        if "request_interval_seconds" in payload:
            seconds = bounded_float(payload["request_interval_seconds"], default=3.0, lower=0.0, upper=30.0)
            db.set_setting(conn, "request_interval_seconds", str(seconds))
            refresh_relevant_change = True
        if "temporary_ban_pause_seconds" in payload:
            seconds = bounded_float(payload["temporary_ban_pause_seconds"], default=90.0, lower=0.0, upper=600.0)
            db.set_setting(conn, "temporary_ban_pause_seconds", str(seconds))
            refresh_relevant_change = True
        if "recommend_candidate_limit" in payload:
            limit = bounded_int(payload["recommend_candidate_limit"], default=2000, lower=100, upper=10000)
            db.set_setting(conn, "recommend_candidate_limit", str(limit))
        if "recommend_language_filter" in payload:
            languages = normalize_language_filter(str(payload["recommend_language_filter"]))
            db.set_setting(conn, "recommend_language_filter", ",".join(sorted(languages)))
        if "recommend_model_mode" in payload:
            db.set_setting(conn, "recommend_model_mode", normalize_model_mode(payload["recommend_model_mode"]))
        if "preview_freshness_weight" in payload:
            weight = bounded_float(payload["preview_freshness_weight"], default=8.0, lower=0.0, upper=50.0)
            db.set_setting(conn, "preview_freshness_weight", str(weight))
        if "preview_posted_after" in payload:
            db.set_setting(conn, "preview_posted_after", normalize_posted_after(str(payload["preview_posted_after"])))
        if "review_require_bootstrap_match" in payload:
            db.set_setting(conn, "review_require_bootstrap_match", "1" if parse_bool(payload["review_require_bootstrap_match"]) else "0")
        if "sample_extra_pages" in payload:
            extra = bounded_int(payload["sample_extra_pages"], default=2, lower=0, upper=10)
            db.set_setting(conn, "sample_extra_pages", str(extra))
            refresh_relevant_change = True
        if "network_proxy" in payload:
            try:
                proxy_url = normalize_proxy_url(payload["network_proxy"])
            except ValueError as exc:
                raise ApiError(HTTPStatus.BAD_REQUEST, str(exc)) from exc
            db.set_setting(conn, "network_proxy", proxy_url)
            apply_proxy_environment(proxy_url)
            refresh_relevant_change = True
        if "visual_encoder" in payload:
            try:
                encoder = normalize_visual_encoder(payload["visual_encoder"])
            except ValueError as exc:
                raise ApiError(HTTPStatus.BAD_REQUEST, str(exc)) from exc
            db.set_setting(conn, "visual_encoder", encoder)
        if "dinov2_device" in payload:
            try:
                device = normalize_dinov2_device(payload["dinov2_device"])
            except ValueError as exc:
                raise ApiError(HTTPStatus.BAD_REQUEST, str(exc)) from exc
            db.set_setting(conn, "dinov2_device", device)
        if "bootstrap_tags_raw" in payload:
            upsert_bootstrap_tags(conn, parse_bootstrap_tags(str(payload["bootstrap_tags_raw"])))
            refresh_relevant_change = True
        configure_request_rate_limit_from_conn(conn)
    if refresh_relevant_change:
        wake_background_refresh()


def wake_background_refresh() -> None:
    REFRESH_WAKE.set()


def check_saved_access() -> dict:
    with db.connect() as conn:
        cookie = db.get_setting(conn, "cookie_header", "")
        proxy_url = network_proxy(conn)
    if not cookie:
        raise ApiError(HTTPStatus.BAD_REQUEST, "Save your ExHentai cookie first")
    try:
        result = check_access(cookie, proxy_url=proxy_url)
    except Exception as exc:
        result = {"ok": False, "gallery_count": 0, "message": str(exc)}
    result["checked_at"] = current_timestamp()
    with db.connect() as conn:
        db.set_setting(conn, "last_access_check", json.dumps(result, ensure_ascii=True))
    return result


def network_proxy(conn) -> str:
    raw = db.get_setting(conn, "network_proxy", "")
    try:
        return normalize_proxy_url(raw or default_proxy_url())
    except ValueError:
        return ""


def configured_dinov2_device(conn) -> str:
    try:
        return normalize_dinov2_device(db.get_setting(conn, "dinov2_device", DEFAULT_DINOV2_DEVICE))
    except ValueError:
        return normalize_dinov2_device(DEFAULT_DINOV2_DEVICE)


def configured_visual_encoder(conn) -> str:
    try:
        return normalize_visual_encoder(db.get_setting(conn, "visual_encoder", DEFAULT_VISUAL_ENCODER))
    except ValueError:
        return normalize_visual_encoder(DEFAULT_VISUAL_ENCODER)


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
    with db.connect() as conn:
        configured_encoder = configured_visual_encoder(conn)
    default_encoder = "simple" if embedding is not None else configured_encoder
    encoder = str(payload.get("encoder") or default_encoder).strip().lower()
    if embedding is None and encoder == "dinov2":
        return save_dinov2_visual_embedding(gallery_url, payload)
    version = str(payload.get("version") or "").strip() or SIMPLE_VISUAL_VERSION
    try:
        with db.connect() as conn:
            store_visual_embedding(conn, gallery_url, embedding, version=version)
    except ValueError as exc:
        raise ApiError(HTTPStatus.BAD_REQUEST, str(exc)) from exc
    return {"ok": True, "gallery_url": gallery_url, "encoder": encoder, "version": version, "visual_ready": True}


def save_dinov2_visual_embedding(gallery_url: str, payload: dict[str, Any]) -> dict:
    image_urls = payload.get("image_urls") or []
    if not isinstance(image_urls, list):
        raise ApiError(HTTPStatus.BAD_REQUEST, "image_urls must be a list")
    with db.connect() as conn:
        proxy_url = network_proxy(conn)
        dinov2_device = configured_dinov2_device(conn)
        visual_encoder = configured_visual_encoder(conn)
    apply_proxy_environment(proxy_url)
    if visual_encoder != "dinov2":
        return {
            "ok": False,
            "gallery_url": gallery_url,
            "encoder": "dinov2",
            "fallback_required": True,
            "fallback_encoder": "simple",
            "reason": "DINOv2 is disabled by visual_encoder setting",
        }
    dinov2_status = dinov2_dependency_status(dinov2_device)
    if not dinov2_status.get("available"):
        return {
            "ok": False,
            "gallery_url": gallery_url,
            "encoder": "dinov2",
            "fallback_required": True,
            "fallback_encoder": "simple",
            "reason": dinov2_status.get("error") or "DINOv2 is unavailable",
        }
    blobs = []
    errors = []
    for raw_url in image_urls[:12]:
        try:
            data, _ = cached_thumbnail(str(raw_url), gallery_url)
            blobs.append(data)
        except Exception as exc:
            errors.append(str(exc))
    if not blobs:
        raise ApiError(HTTPStatus.BAD_REQUEST, "no usable visual images")
    try:
        embedding = dinov2_embedding(blobs, device=dinov2_device)
    except VisualEncoderUnavailable as exc:
        return {
            "ok": False,
            "gallery_url": gallery_url,
            "encoder": "dinov2",
            "fallback_required": True,
            "fallback_encoder": "simple",
            "reason": str(exc),
        }
    try:
        with db.connect() as conn:
            store_visual_embedding(conn, gallery_url, embedding, version=DINOV2_VISUAL_VERSION)
    except ValueError as exc:
        raise ApiError(HTTPStatus.BAD_REQUEST, str(exc)) from exc
    return {
        "ok": True,
        "gallery_url": gallery_url,
        "encoder": "dinov2",
        "version": DINOV2_VISUAL_VERSION,
        "visual_ready": True,
        "image_count": len(blobs),
        "errors": errors,
    }


def download_dinov2_payload() -> dict:
    with db.connect() as conn:
        proxy_url = network_proxy(conn)
        dinov2_device = configured_dinov2_device(conn)
        visual_encoder = configured_visual_encoder(conn)
    apply_proxy_environment(proxy_url)
    ok = True
    reason = None
    path = None
    try:
        result = download_dinov2(dinov2_device)
        path = result.get("path")
    except VisualEncoderUnavailable as exc:
        ok = False
        reason = str(exc)
    return {
        "ok": ok,
        "model": DINOV2_MODEL_NAME,
        "path": path,
        "reason": reason,
        "visual": visual_settings(visual_encoder, dinov2_device),
    }


def visual_settings(encoder: str | None = None, device: str | None = None) -> dict:
    encoder = normalize_visual_encoder(encoder or DEFAULT_VISUAL_ENCODER)
    device = normalize_dinov2_device(device or DEFAULT_DINOV2_DEVICE)
    return {
        "default_encoder": encoder,
        "default_version": DINOV2_VISUAL_VERSION if encoder == "dinov2" else SIMPLE_VISUAL_VERSION,
        "fallback_encoder": "simple",
        "fallback_version": SIMPLE_VISUAL_VERSION,
        "dinov2": dinov2_dependency_status(device),
    }


def model_signature(conn) -> dict:
    feature_rows = [
        (
            str(row["feature"]),
            round(float(row["weight"]), 8),
            int(row["positive_count"]),
            int(row["negative_count"]),
        )
        for row in conn.execute(
            """
            SELECT feature, weight, positive_count, negative_count
            FROM feature_weights
            ORDER BY feature
            """
        )
    ]
    visual_model = visual_preference_model(conn)
    visual_signature = None
    if visual_model:
        visual_signature = {
            "version": visual_model.get("version"),
            "positive_count": visual_model.get("positive_count"),
            "negative_count": visual_model.get("negative_count"),
            "total_weight": round(float(visual_model.get("total_weight") or 0), 8),
            "vector_head": [round(float(value), 6) for value in (visual_model.get("vector") or [])[:12]],
        }
    return {"features": feature_rows, "visual": visual_signature}


def feedback_update_summary(
    conn,
    action: str,
    gallery_url: str,
    vote: int | None,
    score: int | None,
    before_model: dict,
    before_signature: dict,
    after_model: dict,
    after_signature: dict,
    removed: int | None = None,
    retrained: bool = True,
    elapsed_ms: float | None = None,
) -> dict:
    latest = feedback_history(conn, gallery_url, limit=1)
    signal = feedback_signal(vote=vote, score=score) if action == "record" else None
    before_counts = before_model.get("counts", {})
    after_counts = after_model.get("counts", {})
    before_visual = before_model.get("visual", {})
    after_visual = after_model.get("visual", {})
    return {
        "action": action,
        "gallery_url": gallery_url,
        "vote": vote,
        "score": score,
        "signal": signal,
        "removed": removed,
        "latest_feedback_id": latest[0]["id"] if latest else None,
        "feedback_events_before": before_counts.get("feedback_events", 0),
        "feedback_events_after": after_counts.get("feedback_events", 0),
        "rated_galleries_before": before_counts.get("rated_galleries", 0),
        "rated_galleries_after": after_counts.get("rated_galleries", 0),
        "model_features_before": before_counts.get("model_features", 0),
        "model_features_after": after_counts.get("model_features", 0),
        "visual_ready_before": bool(before_visual.get("ready")),
        "visual_ready_after": bool(after_visual.get("ready")),
        "visual_rated_before": before_visual.get("rated_embedded_galleries", 0),
        "visual_rated_after": after_visual.get("rated_embedded_galleries", 0),
        "model_changed": before_signature != after_signature,
        "retrained": retrained,
        "elapsed_ms": elapsed_ms,
    }


def mark_update_summary(
    conn,
    action: str,
    gallery_url: str,
    kind: str | None,
    before_model: dict,
    before_signature: dict,
    after_model: dict,
    after_signature: dict,
    removed: int | None = None,
    elapsed_ms: float | None = None,
) -> dict:
    before_counts = before_model.get("counts", {})
    after_counts = after_model.get("counts", {})
    row = conn.execute(
        """
        SELECT kind, created_at, updated_at
        FROM gallery_marks
        WHERE gallery_url = ?
        """,
        (gallery_url,),
    ).fetchone()
    return {
        "action": action,
        "gallery_url": gallery_url,
        "kind": kind,
        "removed": removed,
        "current_kind": row["kind"] if row else None,
        "marked_at": row["created_at"] if row else None,
        "updated_at": row["updated_at"] if row else None,
        "marked_galleries_before": before_counts.get("marked_galleries", 0),
        "marked_galleries_after": after_counts.get("marked_galleries", 0),
        "favorite_galleries_before": before_counts.get("favorite_galleries", 0),
        "favorite_galleries_after": after_counts.get("favorite_galleries", 0),
        "banned_galleries_before": before_counts.get("banned_galleries", 0),
        "banned_galleries_after": after_counts.get("banned_galleries", 0),
        "model_features_before": before_counts.get("model_features", 0),
        "model_features_after": after_counts.get("model_features", 0),
        "model_changed": before_signature != after_signature,
        "retrained": True if action != "clear" else removed != 0,
        "elapsed_ms": elapsed_ms,
    }


def log_feedback_received(action: str, gallery_url: str, vote: int | None = None, score: int | None = None) -> None:
    print(
        "[feedback] "
        f"received action={action} "
        f"url={gallery_url} "
        f"vote={vote} "
        f"score={score}",
        flush=True,
    )


def log_feedback_update(summary: dict) -> None:
    print(
        "[feedback] "
        f"action={summary.get('action')} "
        f"url={summary.get('gallery_url')} "
        f"vote={summary.get('vote')} "
        f"score={summary.get('score')} "
        f"signal={summary.get('signal')} "
        f"events={summary.get('feedback_events_before')}->{summary.get('feedback_events_after')} "
        f"rated={summary.get('rated_galleries_before')}->{summary.get('rated_galleries_after')} "
        f"features={summary.get('model_features_before')}->{summary.get('model_features_after')} "
        f"visual_ready={summary.get('visual_ready_before')}->{summary.get('visual_ready_after')} "
        f"visual_rated={summary.get('visual_rated_before')}->{summary.get('visual_rated_after')} "
        f"model_changed={summary.get('model_changed')} "
        f"elapsed_ms={summary.get('elapsed_ms')}",
        flush=True,
    )


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
    extra_pages = stale_fetch_extra_pages(conn)
    detail_limit = detail_fetch_limit(conn)
    learned_limit = learned_query_limit(conn)
    tags = get_bootstrap_tags(conn)
    learned_tags = learned_query_tags(conn, learned_limit)
    entries = build_query_plan(tags, learned_tags, force_query=force_query)
    return {
        "queries": [entry["query"] for entry in entries],
        "entries": entries,
        "pages": pages,
        "stale_fetch_extra_pages": extra_pages,
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


def update_fetch_progress(message: str, **fields: Any) -> None:
    fields["message"] = message
    fields["updated_at"] = current_timestamp()
    FETCH_STATE.update(fields)
    log_fields = dict(fields)
    log_fields.pop("message", None)
    fetch_log(message, **log_fields)


def fetch_log(message: str, **fields: Any) -> None:
    details = " ".join(f"{key}={format_log_value(value)}" for key, value in fields.items())
    line = f"[fetch] {message}"
    if details:
        line = f"{line} {details}"
    print(line, flush=True)


def format_log_value(value: Any) -> str:
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=True)
    if isinstance(value, (list, tuple, dict)):
        return json.dumps(value, ensure_ascii=True)
    return str(value)


def parent_update_status() -> dict:
    state = dict(PARENT_UPDATE_STATE)
    state["logs"] = list(PARENT_UPDATE_STATE.get("logs") or [])
    return state


def reset_parent_update_progress(scope: str, limit: int, filter_text: str | None) -> None:
    PARENT_UPDATE_STATE.clear()
    PARENT_UPDATE_STATE.update(
        {
            "running": True,
            "stage": "starting",
            "scope": scope,
            "limit": limit,
            "filter_text": filter_text or "",
            "checked": 0,
            "total": 0,
            "detail_checked": 0,
            "detail_total": 0,
            "detail_done": 0,
            "persisted": 0,
            "updated": 0,
            "parent_updated": 0,
            "title_jpn_updated": 0,
            "errors": [],
            "logs": [],
        }
    )


def update_parent_progress(message: str, **fields: Any) -> None:
    timestamp = current_timestamp()
    fields["message"] = message
    fields["updated_at"] = timestamp
    PARENT_UPDATE_STATE.update(fields)
    log_fields = dict(fields)
    log_fields.pop("message", None)
    parent_update_log(message, **log_fields)
    entry_fields = {key: value for key, value in log_fields.items() if key not in {"updated_at"}}
    logs = list(PARENT_UPDATE_STATE.get("logs") or [])
    logs.append({"at": timestamp, "message": message, "fields": entry_fields})
    PARENT_UPDATE_STATE["logs"] = logs[-PARENT_UPDATE_LOG_LIMIT:]


def parent_update_log(message: str, **fields: Any) -> None:
    details = " ".join(f"{key}={format_log_value(value)}" for key, value in fields.items())
    line = f"[parent-update] {message}"
    if details:
        line = f"{line} {details}"
    print(line, flush=True)


def parent_update_fetch_with_retries(action: str, fetcher, **progress_fields: Any) -> Any:
    attempts = PARENT_UPDATE_FETCH_RETRIES + 1
    for attempt in range(1, attempts + 1):
        try:
            return fetcher()
        except Exception as exc:
            if attempt >= attempts:
                raise
            update_parent_progress(
                f"{action} retry",
                retry_attempt=attempt,
                retry_remaining=attempts - attempt,
                retry_limit=PARENT_UPDATE_FETCH_RETRIES,
                error=str(exc),
                **progress_fields,
            )
            time.sleep(PARENT_UPDATE_RETRY_BACKOFF_SECONDS * attempt)
    raise RuntimeError(f"{action} exhausted retries")


def display_query(query: str | None) -> str:
    return query or "recent"


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
        stale_extra_pages = stale_fetch_extra_pages(conn)
        detail_limit = detail_fetch_limit(conn)
        learned_limit = learned_query_limit(conn)
        sample_pages = sample_extra_pages(conn)
        proxy_url = network_proxy(conn)
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
    update_fetch_progress(
        "fetch started",
        running=True,
        trigger=trigger,
        stage="starting",
        queries=queries,
        query_total=len(queries),
        page_count=pages,
        stale_extra_pages=stale_extra_pages,
        detail_limit=detail_limit,
        sample_extra_pages=sample_pages,
        started_at=current_timestamp(),
        fetched=0,
        stored=0,
        enriched=0,
        errors=[],
    )
    try:
        with db.connect() as conn:
            cursor = conn.execute(
                "INSERT INTO fetch_runs(trigger, status, queries_json) VALUES (?, ?, ?)",
                (trigger, "running", json.dumps(queries, ensure_ascii=True)),
            )
            run_id = int(cursor.lastrowid)
        update_fetch_progress("fetch run recorded", stage="running", run_id=run_id)

        for query_index, query in enumerate(queries, start=1):
            try:
                start_page = 0
                batch_pages = pages
                remaining_extra_pages = stale_extra_pages if pages >= 5 else 0
                update_fetch_progress(
                    "query started",
                    stage="fetching_pages",
                    current_query=display_query(query),
                    query_index=query_index,
                    query_total=len(queries),
                    page_start=start_page,
                    page_count=batch_pages,
                    remaining_extra_pages=remaining_extra_pages,
                )
                while True:
                    update_fetch_progress(
                        "fetching page batch",
                        stage="fetching_pages",
                        current_query=display_query(query),
                        query_index=query_index,
                        query_total=len(queries),
                        page_start=start_page,
                        page_count=batch_pages,
                        remaining_extra_pages=remaining_extra_pages,
                    )
                    galleries = fetch_galleries(
                        cookie,
                        query=query,
                        pages=batch_pages,
                        start_page=start_page,
                        proxy_url=proxy_url,
                    )
                    fetched += len(galleries)
                    update_fetch_progress(
                        "page batch fetched",
                        stage="fetching_pages",
                        current_query=display_query(query),
                        fetched_batch=len(galleries),
                        fetched=fetched,
                    )
                    try:
                        cover_updated = enrich_covers_via_api(cookie, galleries, proxy_url=proxy_url)
                        update_fetch_progress(
                            "cover metadata checked",
                            stage="cover_metadata",
                            current_query=display_query(query),
                            cover_updated=cover_updated,
                        )
                    except Exception as exc:
                        errors.append(f"covers {query or 'recent'}: {exc}")
                        update_fetch_progress(
                            "cover metadata failed",
                            stage="cover_metadata",
                            current_query=display_query(query),
                            error=str(exc),
                            errors=list(errors),
                        )
                    with db.connect() as conn:
                        batch_stored = store_galleries(conn, galleries)
                        stored += batch_stored
                        candidates = select_detail_candidates(conn, galleries, detail_limit - len(selected_for_detail))
                    for gallery in candidates:
                        if gallery.url not in selected_urls:
                            selected_for_detail.append(gallery)
                            selected_urls.add(gallery.url)
                    update_fetch_progress(
                        "page batch stored",
                        stage="storing",
                        current_query=display_query(query),
                        fetched=fetched,
                        stored=stored,
                        stored_batch=batch_stored,
                        detail_selected=len(selected_for_detail),
                    )
                    if batch_stored > 0 or not galleries or remaining_extra_pages <= 0:
                        break
                    update_fetch_progress(
                        "page batch stale; fetching deeper",
                        stage="fetching_pages",
                        current_query=display_query(query),
                        next_page_start=start_page + batch_pages,
                        next_page_count=min(5, remaining_extra_pages),
                    )
                    start_page += batch_pages
                    batch_pages = min(5, remaining_extra_pages)
                    remaining_extra_pages -= batch_pages
            except Exception as exc:
                errors.append(str(exc))
                update_fetch_progress(
                    "query failed",
                    stage="error",
                    current_query=display_query(query),
                    error=str(exc),
                    errors=list(errors),
                )

        update_fetch_progress(
            "detail enrichment selected",
            stage="enriching_details",
            detail_total=len(selected_for_detail),
            detail_done=0,
        )
        for detail_index, gallery in enumerate(selected_for_detail, start=1):
            try:
                update_fetch_progress(
                    "detail fetch started",
                    stage="enriching_details",
                    detail_index=detail_index,
                    detail_total=len(selected_for_detail),
                    current_gallery_url=gallery.url,
                    current_gallery_title=gallery.title,
                )
                detailed = fetch_gallery_detail(cookie, gallery, proxy_url=proxy_url)
                ensure_api_cover(cookie, gallery, detailed, proxy_url=proxy_url)
                samples = collect_gallery_samples(cookie, detailed, sample_pages, proxy_url=proxy_url)
                cache_sample_thumbnails(detailed.url, samples)
                with db.connect() as conn:
                    store_galleries(conn, [detailed], detail_fetched=True)
                    store_gallery_samples(conn, detailed.url, detailed.page_count, samples)
                enriched += 1
                update_fetch_progress(
                    "detail fetch finished",
                    stage="enriching_details",
                    detail_index=detail_index,
                    detail_total=len(selected_for_detail),
                    detail_done=detail_index,
                    current_gallery_url=gallery.url,
                    enriched=enriched,
                    sample_count=len(samples),
                )
            except Exception as exc:
                errors.append(f"detail {gallery.url}: {exc}")
                update_fetch_progress(
                    "detail fetch failed",
                    stage="enriching_details",
                    detail_index=detail_index,
                    detail_total=len(selected_for_detail),
                    current_gallery_url=gallery.url,
                    error=str(exc),
                    errors=list(errors),
                )

        if fetched == 0 and not errors:
            errors.append(empty_fetch_error(queries))
            update_fetch_progress("fetch returned no galleries", stage="failed", errors=list(errors))

        status = "failed" if errors and fetched == 0 else "partial" if errors else "success"
        with db.connect() as conn:
            clear_shared_thumbnail_metadata(conn)
            if enriched:
                update_fetch_progress("retraining model", stage="retraining", enriched=enriched)
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
        update_fetch_progress(
            "fetch finished",
            stage="finished",
            status=status,
            fetched=fetched,
            stored=stored,
            enriched=enriched,
            model_retrained=model_retrained,
            errors=list(errors),
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
            update_fetch_progress("fetch failed", stage="failed", error=str(exc), errors=list(errors))
            with db.connect() as conn:
                finish_running_fetch_run(conn, run_id, "failed", fetched, stored, enriched, errors)
        raise
    finally:
        if run_id is not None:
            with db.connect() as conn:
                last = last_fetch_run(conn)
        else:
            last = None
        FETCH_STATE.update({"running": False, "last_fetch": last, "updated_at": current_timestamp()})
        FETCH_LOCK.release()


def enrich_recommendations(include_rated: bool = False, filter_text: str | None = None, limit: Any = None) -> dict:
    if not FETCH_LOCK.acquire(blocking=False):
        raise ApiError(HTTPStatus.CONFLICT, "A fetch or enrichment is already running")
    with db.connect() as conn:
        cookie = db.get_setting(conn, "cookie_header", "")
        detail_limit = detail_fetch_limit(conn)
        extra_pages = sample_extra_pages(conn)
        proxy_url = network_proxy(conn)
    if not cookie:
        FETCH_LOCK.release()
        raise ApiError(HTTPStatus.BAD_REQUEST, "Save your ExHentai cookie first")

    requested_limit = detail_limit if limit is None else bounded_int(limit, default=detail_limit, lower=0, upper=50)
    run_id: int | None = None
    enriched = 0
    model_retrained = False
    errors: list[str] = []
    update_fetch_progress(
        "enrichment started",
        running=True,
        trigger="enrich",
        stage="selecting_details",
        queries=["recommendation details"],
        detail_limit=requested_limit,
        started_at=current_timestamp(),
        fetched=0,
        stored=0,
        enriched=0,
        errors=[],
    )
    try:
        with db.connect() as conn:
            cursor = conn.execute(
                "INSERT INTO fetch_runs(trigger, status, queries_json) VALUES (?, ?, ?)",
                ("enrich", "running", json.dumps(["recommendation details"], ensure_ascii=True)),
            )
            run_id = int(cursor.lastrowid)
        update_fetch_progress("enrichment run recorded", stage="selecting_details", run_id=run_id)

        with db.connect() as conn:
            candidates = select_recommendation_detail_candidates(
                conn,
                limit=requested_limit,
                include_rated=include_rated,
                filter_text=filter_text,
            )
        update_fetch_progress(
            "detail enrichment selected",
            stage="enriching_details",
            detail_total=len(candidates),
            detail_done=0,
        )

        for detail_index, gallery in enumerate(candidates, start=1):
            try:
                update_fetch_progress(
                    "detail fetch started",
                    stage="enriching_details",
                    detail_index=detail_index,
                    detail_total=len(candidates),
                    current_gallery_url=gallery.url,
                    current_gallery_title=gallery.title,
                )
                detailed = fetch_gallery_detail(cookie, gallery, proxy_url=proxy_url)
                ensure_api_cover(cookie, gallery, detailed, proxy_url=proxy_url)
                samples = collect_gallery_samples(cookie, detailed, extra_pages, proxy_url=proxy_url)
                cache_sample_thumbnails(detailed.url, samples)
                with db.connect() as conn:
                    store_galleries(conn, [detailed], detail_fetched=True)
                    store_gallery_samples(conn, detailed.url, detailed.page_count, samples)
                enriched += 1
                update_fetch_progress(
                    "detail fetch finished",
                    stage="enriching_details",
                    detail_index=detail_index,
                    detail_total=len(candidates),
                    detail_done=detail_index,
                    current_gallery_url=gallery.url,
                    enriched=enriched,
                    sample_count=len(samples),
                )
            except Exception as exc:
                errors.append(f"detail {gallery.url}: {exc}")
                update_fetch_progress(
                    "detail fetch failed",
                    stage="enriching_details",
                    detail_index=detail_index,
                    detail_total=len(candidates),
                    current_gallery_url=gallery.url,
                    error=str(exc),
                    errors=list(errors),
                )

        status = "failed" if errors and enriched == 0 else "partial" if errors else "success"
        with db.connect() as conn:
            clear_shared_thumbnail_metadata(conn)
            if enriched:
                update_fetch_progress("retraining model", stage="retraining", enriched=enriched)
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
        update_fetch_progress(
            "enrichment finished",
            stage="finished",
            status=status,
            enriched=enriched,
            model_retrained=model_retrained,
            errors=list(errors),
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
            update_fetch_progress("enrichment failed", stage="failed", error=str(exc), errors=list(errors))
            with db.connect() as conn:
                finish_running_fetch_run(conn, run_id, "failed", 0, 0, enriched, errors)
        raise
    finally:
        if run_id is not None:
            with db.connect() as conn:
                last = last_fetch_run(conn)
        else:
            last = None
        FETCH_STATE.update({"running": False, "last_fetch": last, "updated_at": current_timestamp()})
        FETCH_LOCK.release()


REFRESH_THUMBS_MAX = 60
PARENT_METADATA_BACKFILL_MAX = 200


def enrich_covers_via_api(cookie: str, galleries: list[Gallery], proxy_url: str = "") -> int:
    """Fill cover thumbnails/metadata on ``galleries`` from the gdata API.

    Returns the number of covers set/changed. Prefers the stable ehgt.org cover;
    galleries the API does not return keep their existing HTML-scraped cover.
    """
    pairs = [(gallery.gid, gallery.token) for gallery in galleries if gallery.gid and gallery.token]
    if not pairs:
        return 0
    metadata = fetch_gallery_metadata(cookie, pairs, proxy_url=proxy_url)
    return apply_gallery_metadata(galleries, metadata)


def history_parent_metadata_candidates(
    conn,
    scope: str = "history",
    limit: int = 100,
    filter_text: str | None = None,
) -> list[Gallery]:
    limit = bounded_int(limit, default=100, lower=1, upper=PARENT_METADATA_BACKFILL_MAX)
    scope = str(scope or "history").strip().lower()
    if scope not in {"history", "all"}:
        raise ApiError(HTTPStatus.BAD_REQUEST, "scope must be history or all")
    filter_text = (filter_text or "").strip().lower()
    fetch_limit = 10000 if filter_text else limit
    history_filter = "AND (f.feedback_id IS NOT NULL OR m.kind IS NOT NULL)" if scope == "history" else ""
    rows = conn.execute(
        f"""
        SELECT g.*, f.feedback_id, m.kind AS user_mark_kind
        FROM galleries g
        LEFT JOIN (
            SELECT feedback.id AS feedback_id, feedback.gallery_url
            FROM feedback
            JOIN (
                SELECT gallery_url, MAX(id) AS latest_id
                FROM feedback
                GROUP BY gallery_url
            ) latest ON latest.gallery_url = feedback.gallery_url AND latest.latest_id = feedback.id
        ) f ON f.gallery_url = g.url
        LEFT JOIN gallery_marks m ON m.gallery_url = g.url
        WHERE g.gid IS NOT NULL AND g.gid != ''
          AND g.token IS NOT NULL AND g.token != ''
          AND (g.parent_url IS NULL OR g.parent_url = '' OR g.title_jpn IS NULL OR g.title_jpn = '')
          {history_filter}
        ORDER BY COALESCE(f.feedback_id, 0) DESC, m.updated_at DESC, g.last_seen_at DESC
        LIMIT ?
        """,
        (fetch_limit,),
    ).fetchall()
    candidates: list[Gallery] = []
    for row in rows:
        item = db.row_to_dict(row)
        if filter_text and not gallery_matches_filter(item, filter_text):
            continue
        candidates.append(gallery_from_item(item))
        if len(candidates) >= limit:
            break
    return candidates


def persist_gallery_metadata(conn, gallery: Gallery) -> dict[str, int]:
    row = conn.execute(
        """
        SELECT title, title_jpn, category, uploader, posted_at, thumb_url, rating,
               tags_json, tag_weights_json, parent_url, page_count
        FROM galleries
        WHERE url = ?
        """,
        (gallery.url,),
    ).fetchone()
    if not row:
        return {"updated": 0, "parent_updated": 0, "title_jpn_updated": 0}

    updates: dict[str, object] = {}
    text_fields = {
        "title": gallery.title if gallery.title and not gallery.title.startswith("Gallery ") else None,
        "title_jpn": gallery.title_jpn,
        "category": gallery.category,
        "uploader": gallery.uploader,
        "posted_at": gallery.posted_at,
        "thumb_url": gallery.thumb_url,
        "parent_url": gallery.parent_url,
    }
    for field, value in text_fields.items():
        value = str(value or "").strip()
        if value and value != (row[field] or ""):
            updates[field] = value
    if gallery.rating is not None and gallery.rating != row["rating"]:
        updates["rating"] = gallery.rating
    if gallery.page_count is not None and gallery.page_count != row["page_count"]:
        updates["page_count"] = gallery.page_count
    tags_json = json.dumps(gallery.tags, ensure_ascii=True)
    if gallery.tags and tags_json != (row["tags_json"] or "[]"):
        updates["tags_json"] = tags_json
    tag_weights_json = json.dumps(normalize_gallery_tag_weights(gallery), ensure_ascii=True)
    if gallery.tag_weights and tag_weights_json != (row["tag_weights_json"] or "{}"):
        updates["tag_weights_json"] = tag_weights_json

    if not updates:
        return {"updated": 0, "parent_updated": 0, "title_jpn_updated": 0}
    assignments = ", ".join(f"{field} = ?" for field in updates)
    conn.execute(f"UPDATE galleries SET {assignments} WHERE url = ?", (*updates.values(), gallery.url))
    return {
        "updated": 1,
        "parent_updated": 1 if "parent_url" in updates else 0,
        "title_jpn_updated": 1 if "title_jpn" in updates else 0,
    }


def normalize_gallery_tag_weights(gallery: Gallery) -> dict[str, float]:
    normalized: dict[str, float] = {}
    for tag, value in gallery.tag_weights.items():
        try:
            normalized[str(tag).strip().lower()] = float(value)
        except (TypeError, ValueError):
            continue
    return normalized


def backfill_parent_metadata(
    scope: str = "history",
    limit: int = 100,
    filter_text: str | None = None,
) -> dict:
    scope = str(scope or "history").strip().lower()
    if scope not in {"history", "all"}:
        raise ApiError(HTTPStatus.BAD_REQUEST, "scope must be history or all")
    limit = bounded_int(limit, default=100, lower=1, upper=PARENT_METADATA_BACKFILL_MAX)
    filter_text = (filter_text or "").strip()
    if not FETCH_LOCK.acquire(blocking=False):
        raise ApiError(HTTPStatus.CONFLICT, "A fetch or enrichment is already running")
    errors: list[str] = []
    try:
        reset_parent_update_progress(scope, limit, filter_text)
        update_parent_progress("parent update started", stage="starting", running=True)
        with db.connect() as conn:
            cookie = db.get_setting(conn, "cookie_header", "")
            proxy_url = network_proxy(conn)
            update_parent_progress("candidate scan started", stage="selecting")
            galleries = history_parent_metadata_candidates(conn, scope=scope, limit=limit, filter_text=filter_text)
        update_parent_progress(
            "candidate scan finished",
            stage="selected",
            checked=len(galleries),
            total=len(galleries),
        )
        if not cookie:
            update_parent_progress(
                "parent update failed",
                stage="failed",
                running=False,
                error="Save your ExHentai cookie first",
                errors=["Save your ExHentai cookie first"],
            )
            raise ApiError(HTTPStatus.BAD_REQUEST, "Save your ExHentai cookie first")
        if not galleries:
            with db.connect() as conn:
                page = reaction_history_payload(conn, limit=40, filter_text=filter_text)
            update_parent_progress(
                "no parent metadata candidates",
                stage="finished",
                running=False,
                checked=0,
                total=0,
                detail_checked=0,
                detail_total=0,
                detail_done=0,
                updated=0,
                parent_updated=0,
                title_jpn_updated=0,
                errors=[],
            )
            return {
                "ok": True,
                "checked": 0,
                "detail_checked": 0,
                "updated": 0,
                "parent_updated": 0,
                "title_jpn_updated": 0,
                "errors": errors,
                **page,
            }

        try:
            pairs = [(gallery.gid, gallery.token) for gallery in galleries if gallery.gid and gallery.token]
            update_parent_progress("gdata metadata fetch started", stage="gdata", gdata_total=len(pairs))
            metadata = parent_update_fetch_with_retries(
                "gdata metadata fetch",
                lambda: fetch_gallery_metadata(
                    cookie,
                    pairs,
                    proxy_url=proxy_url,
                ),
                stage="gdata",
                gdata_total=len(pairs),
            )
            apply_gallery_metadata(galleries, metadata)
            update_parent_progress(
                "gdata metadata applied",
                stage="gdata",
                metadata_count=len(metadata),
                gdata_parent_count=sum(1 for meta in metadata.values() if meta.get("parent_url")),
                detail_total=sum(1 for gallery in galleries if not str(gallery.parent_url or "").strip()),
            )
        except Exception as exc:
            errors.append(f"gdata API: {exc}")
            update_parent_progress(
                "gdata metadata failed",
                stage="gdata",
                error=str(exc),
                errors=list(errors),
            )

        detail_checked = 0
        detail_candidates = [
            (index, gallery) for index, gallery in enumerate(galleries) if not str(gallery.parent_url or "").strip()
        ]
        update_parent_progress(
            "detail fallback selected",
            stage="details",
            detail_total=len(detail_candidates),
            detail_done=0,
            detail_checked=0,
        )
        for detail_index, (index, gallery) in enumerate(detail_candidates, start=1):
            update_parent_progress(
                "detail fetch started",
                stage="details",
                detail_index=detail_index,
                detail_total=len(detail_candidates),
                detail_done=detail_index - 1,
                current_gallery_url=gallery.url,
                current_gallery_title=gallery.title,
            )
            try:
                detailed = parent_update_fetch_with_retries(
                    "detail fetch",
                    lambda: fetch_gallery_detail(cookie, gallery, delay=0, proxy_url=proxy_url),
                    stage="details",
                    detail_index=detail_index,
                    detail_total=len(detail_candidates),
                    detail_done=detail_index - 1,
                    current_gallery_url=gallery.url,
                    current_gallery_title=gallery.title,
                )
                galleries[index] = detailed
                detail_checked += 1
                update_parent_progress(
                    "detail fetch finished",
                    stage="details",
                    detail_index=detail_index,
                    detail_total=len(detail_candidates),
                    detail_done=detail_index,
                    detail_checked=detail_checked,
                    current_gallery_url=gallery.url,
                    current_gallery_title=detailed.title,
                    current_parent_url=detailed.parent_url or "",
                    detail_parent_found=bool(detailed.parent_url),
                )
            except Exception as exc:
                errors.append(f"{gallery.url}: {exc}")
                update_parent_progress(
                    "detail fetch failed",
                    stage="details",
                    detail_index=detail_index,
                    detail_total=len(detail_candidates),
                    detail_done=detail_index,
                    detail_checked=detail_checked,
                    current_gallery_url=gallery.url,
                    current_gallery_title=gallery.title,
                    error=str(exc),
                    errors=list(errors),
                )

        updated = 0
        parent_updated = 0
        title_jpn_updated = 0
        update_parent_progress("database update started", stage="persisting", persisted=0)
        with db.connect() as conn:
            for persisted, gallery in enumerate(galleries, start=1):
                stats = persist_gallery_metadata(conn, gallery)
                updated += stats["updated"]
                parent_updated += stats["parent_updated"]
                title_jpn_updated += stats["title_jpn_updated"]
                update_parent_progress(
                    "database row checked",
                    stage="persisting",
                    persisted=persisted,
                    total=len(galleries),
                    updated=updated,
                    parent_updated=parent_updated,
                    title_jpn_updated=title_jpn_updated,
                    current_gallery_url=gallery.url,
                    current_gallery_title=gallery.title,
                )
            page = reaction_history_payload(conn, limit=40, filter_text=filter_text)
        update_parent_progress(
            "parent update finished" if not errors else "parent update finished with errors",
            stage="finished",
            running=False,
            checked=len(galleries),
            total=len(galleries),
            detail_checked=detail_checked,
            detail_total=len(detail_candidates),
            detail_done=len(detail_candidates),
            persisted=len(galleries),
            updated=updated,
            parent_updated=parent_updated,
            title_jpn_updated=title_jpn_updated,
            errors=list(errors),
        )
        return {
            "ok": not errors,
            "checked": len(galleries),
            "detail_checked": detail_checked,
            "updated": updated,
            "parent_updated": parent_updated,
            "title_jpn_updated": title_jpn_updated,
            "errors": errors,
            **page,
        }
    except Exception as exc:
        if PARENT_UPDATE_STATE.get("running"):
            logged_errors = list(errors) or [str(exc)]
            update_parent_progress(
                "parent update failed",
                stage="failed",
                running=False,
                error=str(exc),
                errors=logged_errors,
            )
        raise
    finally:
        FETCH_LOCK.release()


def ensure_api_cover(cookie: str, base: Gallery, detailed: Gallery, proxy_url: str = "") -> None:
    """Keep a stable ehgt.org cover on a freshly detail-fetched gallery.

    A detail page yields an expiring ``s.exhentai.org`` cover that the detail
    store would otherwise persist over a good ehgt cover. Reuse an ehgt cover the
    caller already has; otherwise ask the gdata API once (best effort).
    """
    if base.thumb_url and "ehgt.org" in base.thumb_url:
        detailed.thumb_url = base.thumb_url
        return
    if detailed.thumb_url and "ehgt.org" in detailed.thumb_url:
        return
    try:
        enrich_covers_via_api(cookie, [detailed], proxy_url=proxy_url)
    except Exception:
        pass


def refresh_thumbnails(
    gallery_urls: Any,
    include_rated: bool = False,
    filter_text: str | None = None,
) -> dict:
    """Re-fetch cover thumbnails for the given galleries (the current page) and backfill any that are missing."""
    if not isinstance(gallery_urls, list):
        raise ApiError(HTTPStatus.BAD_REQUEST, "gallery_urls must be a list")
    urls: list[str] = []
    seen: set[str] = set()
    for raw in gallery_urls:
        url = str(raw or "").strip()
        if url and url not in seen:
            seen.add(url)
            urls.append(url)
    urls = urls[:REFRESH_THUMBS_MAX]
    if not urls:
        raise ApiError(HTTPStatus.BAD_REQUEST, "no galleries to refresh")
    if not FETCH_LOCK.acquire(blocking=False):
        raise ApiError(HTTPStatus.CONFLICT, "A fetch or enrichment is already running")
    try:
        with db.connect() as conn:
            cookie = db.get_setting(conn, "cookie_header", "")
            proxy_url = network_proxy(conn)
        if not cookie:
            raise ApiError(HTTPStatus.BAD_REQUEST, "Save your ExHentai cookie first")

        galleries: list[Gallery] = []
        for url in urls:
            with db.connect() as conn:
                row = conn.execute("SELECT * FROM galleries WHERE url = ?", (url,)).fetchone()
            if row:
                galleries.append(gallery_from_item(db.row_to_dict(row)))

        updated = 0
        errors: list[str] = []
        # Prefer the gdata API: one batched call yields stable ehgt.org covers for
        # the whole page instead of N brittle HTML scrapes of expiring cover URLs.
        api_thumbs: dict[str, str] = {}
        try:
            metadata = fetch_gallery_metadata(
                cookie,
                [(g.gid, g.token) for g in galleries if g.gid and g.token],
                proxy_url=proxy_url,
            )
            for gallery in galleries:
                thumb = usable_thumb((metadata.get(gallery.url) or {}).get("thumb"))
                if thumb:
                    api_thumbs[gallery.url] = thumb
        except Exception as exc:
            errors.append(f"gdata API: {exc}")

        for gallery in galleries:
            thumb = api_thumbs.get(gallery.url)
            if not thumb:
                # The API had no cover for this gallery; fall back to HTML scraping.
                try:
                    detailed = fetch_gallery_detail(cookie, gallery, delay=0, proxy_url=proxy_url)
                except Exception as exc:
                    errors.append(f"{gallery.url}: {exc}")
                    continue
                thumb = usable_thumb(detailed.thumb_url)
            if not thumb:
                continue
            with db.connect() as conn:
                conn.execute("UPDATE galleries SET thumb_url = ? WHERE url = ?", (thumb, gallery.url))
            updated += 1

        with db.connect() as conn:
            clear_shared_thumbnail_metadata(conn)
            page = recommendation_payload(
                conn,
                limit=40,
                include_rated=include_rated,
                filter_text=filter_text,
            )
        return {"ok": not errors, "updated": updated, "errors": errors, **page}
    finally:
        FETCH_LOCK.release()


SAMPLE_BASE_COUNT = 5


def sample_count_for(page_count: int | None) -> int:
    return SAMPLE_BASE_COUNT + (page_count or 0) // 100


def collect_gallery_samples(
    cookie: str,
    detailed: Gallery,
    extra_pages: int,
    delay: float = 1.0,
    proxy_url: str = "",
) -> list:
    """Pick page previews for ``detailed`` while preserving the first page preview.

    Each entry is a URL string or a sprite-frame dict, so dedupe is keyed on the
    underlying image URL rather than the (unhashable) entry itself.
    """
    pool: list = []
    seen: set[str] = set()

    def add(entry) -> None:
        key = sample_entry_url(entry)
        if key and key not in seen:
            seen.add(key)
            pool.append(entry)

    for entry in detailed.sample_thumbs:
        add(entry)
    count = sample_count_for(detailed.page_count)
    if len(pool) < count and extra_pages > 0:
        for entry in fetch_gallery_sample_pages(cookie, detailed, extra_pages, delay=delay, proxy_url=proxy_url):
            add(entry)
    if len(pool) <= count:
        return pool
    first_thumb = pool[0]
    return [first_thumb, *random.sample(pool[1:], count - 1)]


def enrich_feedback_gallery(gallery_url: str) -> dict:
    with db.connect() as conn:
        cookie = db.get_setting(conn, "cookie_header", "")
        extra_pages = sample_extra_pages(conn)
        proxy_url = network_proxy(conn)
        row = conn.execute("SELECT * FROM galleries WHERE url = ?", (gallery_url,)).fetchone()
        if not row:
            return {"status": "skipped", "reason": "gallery not found"}
        item = db.row_to_dict(row)
        if item.get("detail_fetched_at") and has_visible_images(item):
            return {"status": "skipped", "reason": "already enriched"}
        if not cookie:
            return {"status": "skipped", "reason": "no cookie"}
        gallery = gallery_from_item(item)

    try:
        detailed = fetch_gallery_detail(cookie, gallery, delay=0, proxy_url=proxy_url)
        ensure_api_cover(cookie, gallery, detailed, proxy_url=proxy_url)
        samples = collect_gallery_samples(cookie, detailed, extra_pages, delay=0, proxy_url=proxy_url)
        cache_sample_thumbnails(detailed.url, samples)
    except Exception as exc:
        return {"status": "failed", "reason": str(exc)}

    with db.connect() as conn:
        store_galleries(conn, [detailed], detail_fetched=True)
        store_gallery_samples(conn, detailed.url, detailed.page_count, samples)
        retrain_model(conn)
    return {"status": "success", "gallery_url": gallery_url}


def feedback_enrichment_plan(signal: float, payload: dict[str, Any]) -> dict:
    if signal == 0:
        return {"status": "skipped", "reason": "neutral feedback"}
    if not parse_bool(payload.get("enrich_feedback")):
        return {"status": "deferred", "reason": "review feedback does not fetch remote detail before responding"}
    return enrich_feedback_gallery(str(payload.get("gallery_url") or ""))


def ensure_gallery_exists(conn, gallery_url: str) -> None:
    exists = conn.execute("SELECT 1 FROM galleries WHERE url = ?", (gallery_url,)).fetchone()
    if not exists:
        raise ApiError(HTTPStatus.NOT_FOUND, "Gallery not found")


def select_detail_candidates(conn, galleries, remaining_limit: int) -> list:
    if remaining_limit <= 0:
        return []
    bootstrap = {row["tag"]: row["weight"] for row in conn.execute("SELECT tag, weight FROM bootstrap_tags")}
    weights = {row["feature"]: row["weight"] for row in conn.execute("SELECT feature, weight FROM feature_weights")}
    tag_strengths = tag_corpus_strengths(conn)
    candidates = []
    seen: set[str] = set()
    for order, gallery in enumerate(galleries):
        if gallery.url in seen:
            continue
        seen.add(gallery.url)
        row = conn.execute("SELECT detail_fetched_at, thumb_url, samples_json FROM galleries WHERE url = ?", (gallery.url,)).fetchone()
        if row and row["detail_fetched_at"] and has_visible_images(dict(row)):
            continue
        score, _ = score_gallery(
            {
                "title": gallery.title,
                "category": gallery.category,
                "uploader": gallery.uploader,
                "rating": gallery.rating,
                "tags": gallery.tags,
                "tag_weights": gallery.tag_weights,
            },
            bootstrap,
            weights,
            tag_strengths=tag_strengths,
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
        if item.get("detail_fetched_at") and has_visible_images(item):
            continue
        candidates.append(gallery_from_item(item))
        if len(candidates) >= limit:
            break
    return candidates


def has_visible_images(item: dict) -> bool:
    if item.get("thumb_url"):
        return True
    samples = item.get("samples")
    if samples is None and "samples_json" in item:
        try:
            samples = json.loads(item.get("samples_json") or "[]")
        except json.JSONDecodeError:
            samples = []
    return bool(samples)


def gallery_from_item(item: dict) -> Gallery:
    return Gallery(
        url=item["url"],
        gid=item.get("gid"),
        token=item.get("token"),
        title=item.get("title") or "Gallery",
        title_jpn=item.get("title_jpn"),
        parent_url=item.get("parent_url"),
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


def query_float(query: dict[str, list[str]], key: str, default: float, lower: float, upper: float) -> float:
    return bounded_float((query.get(key) or [default])[0], default=default, lower=lower, upper=upper)


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


def parse_mark_kind(value: Any) -> str:
    kind = str(value or "").strip().lower()
    if kind not in {"favorite", "ban"}:
        raise ApiError(HTTPStatus.BAD_REQUEST, "kind must be favorite or ban")
    return kind


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


def bounded_float(value: Any, default: float, lower: float, upper: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        parsed = default
    return max(lower, min(upper, parsed))


def fetch_pages(conn) -> int:
    return bounded_int(db.get_setting(conn, "fetch_pages", "1"), default=1, lower=1, upper=5)


def stale_fetch_extra_pages(conn) -> int:
    return bounded_int(db.get_setting(conn, "stale_fetch_extra_pages", "20"), default=20, lower=0, upper=50)


def detail_fetch_limit(conn) -> int:
    return bounded_int(db.get_setting(conn, "detail_fetch_limit", "8"), default=8, lower=0, upper=50)


def learned_query_limit(conn) -> int:
    return bounded_int(db.get_setting(conn, "learned_query_limit", "6"), default=6, lower=0, upper=20)


def request_interval_seconds(conn) -> float:
    return bounded_float(db.get_setting(conn, "request_interval_seconds", "3.0"), default=3.0, lower=0.0, upper=30.0)


def temporary_ban_pause_seconds(conn) -> float:
    return bounded_float(db.get_setting(conn, "temporary_ban_pause_seconds", "90.0"), default=90.0, lower=0.0, upper=600.0)


def configure_request_rate_limit_from_conn(conn) -> None:
    configure_request_rate_limit(
        interval_seconds=request_interval_seconds(conn),
        temporary_ban_pause_seconds=temporary_ban_pause_seconds(conn),
    )


def refresh_interval_minutes(conn) -> int:
    return bounded_int(db.get_setting(conn, "refresh_interval_minutes", "30"), default=30, lower=5, upper=240)


def recommend_candidate_limit(conn) -> int:
    return bounded_int(db.get_setting(conn, "recommend_candidate_limit", "2000"), default=2000, lower=100, upper=10000)


def configured_language_filter(conn) -> str:
    languages = normalize_language_filter(db.get_setting(conn, "recommend_language_filter", "japanese,chinese"))
    return ",".join(sorted(languages))


def configured_model_mode(conn) -> str:
    return normalize_model_mode(db.get_setting(conn, "recommend_model_mode", "hybrid"))


def preview_freshness_weight(conn) -> float:
    return bounded_float(db.get_setting(conn, "preview_freshness_weight", "8.0"), default=8.0, lower=0.0, upper=50.0)


def preview_posted_after(conn) -> str:
    return normalize_posted_after(db.get_setting(conn, "preview_posted_after", ""))


def configured_review_require_bootstrap_match(conn) -> bool:
    return db.get_setting(conn, "review_require_bootstrap_match", "1") == "1"


def sample_extra_pages(conn) -> int:
    return bounded_int(db.get_setting(conn, "sample_extra_pages", "2"), default=2, lower=0, upper=10)


def recommendation_payload(
    conn,
    limit: int = 40,
    include_rated: bool = False,
    offset: int = 0,
    filter_text: str | None = None,
    freshness_weight: float = 1.0,
    bootstrap_explore_count: int = 0,
    explore_seed: str | None = None,
    language_filter: str | None = None,
    model_mode: str | None = None,
    require_bootstrap_match: bool = False,
    posted_after: str | None = None,
) -> dict:
    if language_filter is None:
        language_filter = configured_language_filter(conn)
    if model_mode is None:
        model_mode = configured_model_mode(conn)
    page = recommend_page(
        conn,
        limit=limit,
        include_rated=include_rated,
        offset=offset,
        filter_text=filter_text,
        candidate_limit=recommend_candidate_limit(conn),
        freshness_weight=freshness_weight,
        bootstrap_explore_count=bootstrap_explore_count,
        explore_seed=explore_seed,
        language_filter=language_filter,
        model_mode=model_mode,
        require_bootstrap_match=require_bootstrap_match,
        posted_after=posted_after,
    )
    page["items"] = gallery_item_payloads(conn, page["items"])
    return {**page, "last_fetch": last_fetch_run(conn)}


def reaction_history_payload(
    conn,
    limit: int = 40,
    offset: int = 0,
    filter_text: str | None = None,
) -> dict:
    page = reaction_history_page(conn, limit=limit, offset=offset, filter_text=filter_text)
    page["items"] = gallery_item_payloads(conn, page["items"])
    return {**page, "last_fetch": last_fetch_run(conn)}


def marked_gallery_payload(
    conn,
    kind: str,
    limit: int = 40,
    offset: int = 0,
    filter_text: str | None = None,
) -> dict:
    page = marked_gallery_page(conn, kind=kind, limit=limit, offset=offset, filter_text=filter_text)
    page["items"] = gallery_item_payloads(conn, page["items"])
    return {**page, "last_fetch": last_fetch_run(conn)}


def short_repeat_payload(
    conn,
    limit: int = 40,
    offset: int = 0,
    filter_text: str | None = None,
) -> dict:
    page = short_repeat_page(
        conn,
        limit=limit,
        offset=offset,
        filter_text=filter_text,
        candidate_limit=recommend_candidate_limit(conn),
    )
    page["items"] = gallery_item_payloads(conn, page["items"])
    return {**page, "last_fetch": last_fetch_run(conn)}


def response_page_payload(conn, payload: dict[str, Any], require_bootstrap_match: bool = False) -> dict:
    view = str(payload.get("view") or "").strip().lower()
    if view in {"favorite", "ban"}:
        return marked_gallery_payload(
            conn,
            kind=view,
            limit=40,
            filter_text=payload.get("filter_text"),
        )
    if view == "history":
        return reaction_history_payload(conn, limit=40, filter_text=payload.get("filter_text"))
    if view == "short-repeats":
        return short_repeat_payload(conn, limit=40, filter_text=payload.get("filter_text"))
    return recommendation_payload(
        conn,
        limit=40,
        include_rated=parse_bool(payload.get("include_rated")),
        filter_text=payload.get("filter_text"),
        require_bootstrap_match=require_bootstrap_match,
    )


def gallery_item_payloads(conn, items: list[dict]) -> list[dict]:
    return [gallery_item_payload(conn, item) for item in items]


def gallery_item_payload(conn, item: dict) -> dict:
    updated = recommendation_item_with_image_fallback(item)
    parent_chain = gallery_parent_chain(conn, updated)
    if parent_chain:
        updated = dict(updated)
        updated["parent_chain"] = parent_chain
    return updated


def gallery_parent_chain(conn, item: dict, limit: int = 12) -> list[dict]:
    parent_url = normalize_gallery_url(item.get("parent_url"))
    gallery_url = normalize_gallery_url(item.get("url"))
    if not parent_url or parent_url == gallery_url:
        return []

    chain: list[dict] = []
    seen = {gallery_url} if gallery_url else set()
    current_url = parent_url
    for _ in range(limit):
        if not current_url or current_url in seen:
            break
        seen.add(current_url)
        row = conn.execute(
            """
            SELECT url, title, title_jpn, parent_url, page_count
            FROM galleries
            WHERE url = ?
            """,
            (current_url,),
        ).fetchone()
        if not row:
            chain.append({"url": current_url, "title": current_url, "known": False})
            break
        chain.append(
            {
                "url": row["url"],
                "title": row["title"] or row["url"],
                "title_jpn": row["title_jpn"],
                "page_count": row["page_count"],
                "known": True,
            }
        )
        current_url = normalize_gallery_url(row["parent_url"])
    return chain


def recommendation_item_with_image_fallback(item: dict) -> dict:
    if item.get("thumb_url"):
        return item
    samples = item.get("samples") or []
    # Only a standalone image URL works as a cover; a sprite-frame dict cannot be
    # used directly (it is served cropped via the sample-index route instead).
    first_url = next((sample for sample in samples if isinstance(sample, str) and sample), None)
    if not first_url:
        return item
    updated = dict(item)
    updated["thumb_url"] = first_url
    return updated


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
        proxy_url = network_proxy(conn)
    if not cookie:
        raise ApiError(HTTPStatus.BAD_REQUEST, "Save your ExHentai cookie first")

    data, content_type = fetch_thumbnail_bytes(cookie, thumb_url, thumbnail_referer(gallery_url), proxy_url=proxy_url)
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


def cached_gallery_sample(gallery_url: str, sample_index: int) -> tuple[bytes, str]:
    if thumbnail_referer(gallery_url) != gallery_url.strip():
        raise ApiError(HTTPStatus.BAD_REQUEST, "valid gallery_url is required")
    entry = gallery_sample_entry(gallery_url, sample_index)
    try:
        return render_sample_entry(entry, gallery_url)
    except ApiError as exc:
        if not stale_thumbnail_error(exc):
            raise

    refresh_gallery_sample_metadata(gallery_url)
    entry = gallery_sample_entry(gallery_url, sample_index, fallback_first=True)
    return render_sample_entry(entry, gallery_url)


def gallery_sample_entry(gallery_url: str, sample_index: int, fallback_first: bool = False):
    """Return the sample entry (URL string or sprite-frame dict) at ``sample_index``."""
    with db.connect() as conn:
        row = conn.execute("SELECT samples_json FROM galleries WHERE url = ?", (gallery_url,)).fetchone()
    if not row:
        raise ApiError(HTTPStatus.NOT_FOUND, "Gallery not found")
    try:
        samples = json.loads(row["samples_json"] or "[]")
    except json.JSONDecodeError as exc:
        raise ApiError(HTTPStatus.NOT_FOUND, "Gallery has no sample images") from exc
    samples = [sample for sample in samples if sample_entry_url(sample).strip()]
    if not samples:
        raise ApiError(HTTPStatus.NOT_FOUND, "Gallery has no sample images")
    if 0 <= sample_index < len(samples):
        return samples[sample_index]
    if fallback_first:
        return samples[0]
    raise ApiError(HTTPStatus.NOT_FOUND, "Sample image not found")


def render_sample_entry(entry, gallery_url: str) -> tuple[bytes, str]:
    """Fetch (and crop, for sprite frames) the image bytes for one sample entry."""
    if not isinstance(entry, dict):
        return cached_thumbnail(str(entry), gallery_url)
    sheet_url = str(entry.get("url") or "")
    sheet_bytes, sheet_type = cached_thumbnail(sheet_url, gallery_url)
    box = sprite_crop_box(entry)
    if not box:
        return sheet_bytes, sheet_type
    cache_key = f"{sheet_url}#{box[0]},{box[1]},{box[2]},{box[3]}"
    data_path, meta_path = thumbnail_cache_paths(cache_key)
    cached = read_cached_thumbnail(data_path, meta_path, cache_key)
    if cached:
        return cached
    try:
        data, content_type = crop_sprite_bytes(sheet_bytes, *box)
    except SpriteCropUnavailable:
        # Pillow missing or the sheet could not be decoded: serve the whole sheet
        # so the page still shows an image instead of a broken thumbnail.
        return sheet_bytes, sheet_type
    data_path.parent.mkdir(parents=True, exist_ok=True)
    data_path.write_bytes(data)
    meta_path.write_text(
        json.dumps(
            {"source_url": cache_key, "content_type": content_type, "fetched_at": current_timestamp()},
            ensure_ascii=True,
        ),
        encoding="utf-8",
    )
    return data, content_type


def sprite_crop_box(entry: dict) -> tuple[int, int, int, int] | None:
    try:
        x, y, w, h = int(entry.get("x") or 0), int(entry.get("y") or 0), int(entry.get("w") or 0), int(entry.get("h") or 0)
    except (TypeError, ValueError):
        return None
    if w <= 0 or h <= 0:
        return None
    return max(0, x), max(0, y), w, h


class SpriteCropUnavailable(RuntimeError):
    pass


def crop_sprite_bytes(data: bytes, x: int, y: int, w: int, h: int) -> tuple[bytes, str]:
    """Crop one frame out of a sprite sheet with Pillow; return PNG bytes."""
    try:
        from PIL import Image
    except ImportError as exc:
        raise SpriteCropUnavailable(f"Pillow is required to crop sprite previews: {exc}") from exc
    try:
        with Image.open(io.BytesIO(data)) as image:
            frame = image.convert("RGB").crop((x, y, x + w, y + h))
            buffer = io.BytesIO()
            frame.save(buffer, format="PNG")
    except Exception as exc:
        raise SpriteCropUnavailable(f"Could not crop sprite frame: {exc}") from exc
    return buffer.getvalue(), "image/png"


def refresh_gallery_sample_metadata(gallery_url: str) -> None:
    with db.connect() as conn:
        cookie = db.get_setting(conn, "cookie_header", "")
        extra_pages = sample_extra_pages(conn)
        proxy_url = network_proxy(conn)
        row = conn.execute("SELECT * FROM galleries WHERE url = ?", (gallery_url,)).fetchone()
    if not row:
        raise ApiError(HTTPStatus.NOT_FOUND, "Gallery not found")
    if not cookie:
        raise ApiError(HTTPStatus.BAD_REQUEST, "Save your ExHentai cookie first")
    try:
        gallery = gallery_from_item(db.row_to_dict(row))
        detailed = fetch_gallery_detail(cookie, gallery, delay=0, proxy_url=proxy_url)
        ensure_api_cover(cookie, gallery, detailed, proxy_url=proxy_url)
        samples = collect_gallery_samples(cookie, detailed, extra_pages, delay=0, proxy_url=proxy_url)
        cache_sample_thumbnails(detailed.url, samples)
    except ApiError:
        raise
    except Exception as exc:
        raise ApiError(HTTPStatus.BAD_GATEWAY, f"Sample refresh failed: {exc}") from exc
    with db.connect() as conn:
        store_galleries(conn, [detailed], detail_fetched=True)
        store_gallery_samples(conn, detailed.url, detailed.page_count, samples)


def cache_sample_thumbnails(gallery_url: str, samples: list) -> int:
    cached = 0
    seen: set[str] = set()
    for sample in samples:
        # Pre-warm by the underlying image URL; a sprite sheet is fetched once and
        # reused across its frames, which are cropped lazily when served.
        url = sample_entry_url(sample)
        if not url or url in seen:
            continue
        seen.add(url)
        try:
            cached_thumbnail(url, gallery_url)
            cached += 1
        except ApiError:
            continue
    return cached


def stale_thumbnail_error(exc: ApiError) -> bool:
    return exc.status == HTTPStatus.BAD_GATEWAY and (
        "HTTP 403" in exc.message or "HTTP 404" in exc.message
    )


def normalize_thumbnail_url(thumb_url: str) -> str:
    thumb_url = thumb_url.strip()
    if thumb_url.startswith("//"):
        return f"https:{thumb_url}"
    return thumb_url


def is_allowed_thumbnail_url(thumb_url: str) -> bool:
    parsed = urllib.parse.urlparse(thumb_url)
    hostname = (parsed.hostname or "").lower()
    return parsed.scheme == "https" and (
        hostname in ALLOWED_THUMB_HOSTS or hostname.endswith(".hath.network") or hostname.endswith(".ehgt.org")
    )


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


def fetch_thumbnail_bytes(
    cookie_header: str,
    thumb_url: str,
    referer: str,
    timeout: int = 20,
    proxy_url: str = "",
) -> tuple[bytes, str]:
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
        with open_url_with_retry(request, timeout=timeout, proxy_url=proxy_url) as response:
            content_type = response_content_type(response.headers)
            data = response.read(THUMB_MAX_BYTES + 1)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="ignore")
        if temporary_ban_detected(body):
            pause_after_temporary_ban(body, sleep_now=False)
            raise ApiError(HTTPStatus.BAD_GATEWAY, "Temporary ExHentai request-rate ban detected") from exc
        raise ApiError(HTTPStatus.BAD_GATEWAY, f"Thumbnail fetch failed with HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise ApiError(HTTPStatus.BAD_GATEWAY, f"Thumbnail fetch failed: {exc.reason}") from exc

    if len(data) > THUMB_MAX_BYTES:
        raise ApiError(HTTPStatus.BAD_GATEWAY, "Thumbnail is too large")
    decoded_data = data.decode("utf-8", errors="ignore")
    if temporary_ban_detected(decoded_data):
        pause_after_temporary_ban(decoded_data, sleep_now=False)
        raise ApiError(HTTPStatus.BAD_GATEWAY, "Temporary ExHentai request-rate ban detected")
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


def finish_interrupted_fetch_runs(conn) -> None:
    conn.execute(
        """
        UPDATE fetch_runs
        SET status = 'failed',
            errors_json = ?,
            finished_at = CURRENT_TIMESTAMP
        WHERE status = 'running'
        """,
        (json.dumps(["interrupted before completion"], ensure_ascii=True),),
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
        apply_proxy_environment(network_proxy(conn))
        configure_request_rate_limit_from_conn(conn)
        finish_interrupted_fetch_runs(conn)
        clear_shared_thumbnail_metadata(conn)
        retrain_model(conn)
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    stop = threading.Event()
    worker = threading.Thread(target=background_refresh, args=(stop,), daemon=True)
    worker.start()
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
