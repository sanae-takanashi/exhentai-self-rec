"""Bounded, restart-safe Review media preparation; never records impressions."""
from __future__ import annotations

import hashlib
import io
import json
import sqlite3
import time
from types import SimpleNamespace

from . import db
from .visual import (DINOV2_MODEL_NAME, DINOV2_VISUAL_VERSION, SIMPLE_VISUAL_VERSION,
                     average_embeddings, dinov2_image_embeddings, normalize_embedding)


BATCH_SIZE = 3
STATUS = {"checked": 0, "ready": 0, "pending": 0, "cooling": 0, "last_error_code": ""}
_SERVICE = None


def bind_service(namespace):
    global _SERVICE
    # Both python -m and runpy entrypoints can execute app outside its import name.
    # Reuse their live queue/locks instead of importing a second application instance.
    _SERVICE = SimpleNamespace(**namespace)


def service():
    if _SERVICE is not None:
        return _SERVICE
    from . import app
    return app


def settings(conn):
    try:
        window = max(40, min(400, int(db.get_setting(conn, "review_prewarm_window", "120"))))
    except (TypeError, ValueError):
        window = 120
    return {"enabled": db.get_setting(conn, "review_prewarm_enabled", "1") == "1",
            "window": window, "batch_size": BATCH_SIZE, **STATUS}


def encoder_config(conn):
    app = service()
    encoder = app.configured_visual_encoder(conn)
    return (encoder, app.configured_dinov2_device(conn),
            DINOV2_VISUAL_VERSION if encoder == "dinov2" else SIMPLE_VISUAL_VERSION)


def entries(item):
    app = service()
    samples = item.get("samples")
    if samples is None:
        samples = app.safe_json_list(item.get("samples_json"))
    cover = app.normalize_thumbnail_url(str(item.get("thumb_url") or ""))
    return ([cover] if cover else []) + [entry for entry in samples if app.sample_entry_url(entry)]


def cache_key(entry):
    app = service()
    if not isinstance(entry, dict):
        return app.normalize_thumbnail_url(str(entry))
    url = app.normalize_thumbnail_url(app.sample_entry_url(entry))
    box = app.sprite_crop_box(entry)
    return f"{url}#{box[0]},{box[1]},{box[2]},{box[3]}" if box else url


def fingerprint(item, config):
    keys = [cache_key(entry) for entry in entries(item)]
    identity = [keys, config[2], DINOV2_MODEL_NAME if config[0] == "dinov2" else "rgb-8x8"]
    return hashlib.sha256(json.dumps(identity, ensure_ascii=True).encode("ascii")).hexdigest()


def media_cached(item):
    app = service()
    if not item.get("detail_fetched_at") or not item.get("samples"):
        return False
    return bool(entries(item)) and all(app.read_cached_thumbnail(*app.thumbnail_cache_paths(cache_key(entry)),
                                                               cache_key(entry)) for entry in entries(item))


def analysis_current(item, state, config):
    return bool(state and state["fingerprint"] == fingerprint(item, config)
                and state["embedding_digest"] and state["embedding_digest"] == item.get("visual_embedding_digest")
                and item.get("visual_embedding_version") == config[2])


def candidates():
    app = service()
    with db.connect() as conn:
        # Enforce a truly read-only scan, including membership/audit selection.
        conn.execute("PRAGMA query_only=ON")
        config = encoder_config(conn)
        options = settings(conn)
        page = app.recommendation_payload(conn, limit=options["window"], interest_band="primary",
                    require_bootstrap_match=app.configured_review_require_bootstrap_match(conn),
                    selection_read_only=True)
        urls = [item["url"] for item in page["items"]]
        placeholders = ",".join("?" for _ in urls)
        states = {row["gallery_url"]: dict(row) for row in conn.execute(
            f"SELECT * FROM review_media_prewarm WHERE gallery_url IN ({placeholders})", urls)} if urls else {}
    due = []
    counts = {"checked": len(page["items"]), "ready": 0, "pending": 0, "cooling": 0}
    now = time.time()
    for item in page["items"]:
        state = states.get(item["url"])
        if analysis_current(item, state, config) and state["state"] == "ready" and media_cached(item):
            counts["ready"] += 1
        elif state and state["fingerprint"] == fingerprint(item, config) and state["retry_after"] > now:
            counts["cooling"] += 1
        else:
            counts["pending"] += 1
            due.append(item)
    STATUS.update(counts)
    return due


def load_item(url):
    app = service()
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM galleries WHERE url=?", (url,)).fetchone()
        return app.gallery_db_row_payload(conn, row) if row else None


def record_attempt(item, config):
    identity = fingerprint(item, config)
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM review_media_prewarm WHERE gallery_url=?", (item["url"],)).fetchone()
        previous = dict(row) if row else None
        attempts = previous["attempts"] + 1 if previous and previous["fingerprint"] == identity else 1
        conn.execute("""INSERT INTO review_media_prewarm(gallery_url,fingerprint,state,retry_after,attempts)
            VALUES (?,?,'partial',?,?) ON CONFLICT(gallery_url) DO UPDATE SET
            embedding_digest=CASE WHEN fingerprint=excluded.fingerprint THEN embedding_digest ELSE '' END,
            fingerprint=excluded.fingerprint, state='partial', retry_after=excluded.retry_after,
            attempts=excluded.attempts, updated_at=CURRENT_TIMESTAMP""",
            (item["url"], identity, time.time() + min(21600, 300 * 2 ** min(attempts, 6)), attempts))
    return previous


def refresh_detail(item, checkpoint):
    app = service()
    if not app.FETCH_LOCK.acquire(blocking=False):
        raise RuntimeError("metadata_busy")
    try:
        with db.connect() as conn:
            cookie = db.get_setting(conn, "cookie_header", "")
            proxy = app.network_proxy(conn)
            extra = app.sample_extra_pages(conn)
        if not cookie:
            raise RuntimeError("authentication_required")
        checkpoint("detail")
        gallery = app.gallery_from_item(item)
        detailed = app.fetch_gallery_detail(cookie, gallery, delay=0, proxy_url=proxy)
        app.ensure_api_cover(cookie, gallery, detailed, proxy_url=proxy)
        samples = app.collect_gallery_samples(cookie, detailed, extra, delay=0, proxy_url=proxy)
        with db.connect() as conn:
            app.store_galleries(conn, [detailed], detail_fetched=True)
            app.store_gallery_samples(conn, detailed.url, detailed.page_count, samples)
    finally:
        app.FETCH_LOCK.release()
    return load_item(item["url"])


def simple_embeddings(blobs):
    from PIL import Image
    vectors = []
    for blob in blobs:
        with Image.open(io.BytesIO(blob)) as image:
            image = image.convert("RGB").resize((8, 8), Image.Resampling.BILINEAR)
            vectors.append(normalize_embedding([value / 255 for pixel in image.getdata() for value in pixel]))
    return vectors


def prepare_gallery(item, config, previous, checkpoint):
    app = service()
    if not item.get("detail_fetched_at") or not item.get("samples"):
        item = refresh_detail(item, checkpoint)
    for attempt in range(2):
        blobs, keys = [], []
        seen = set()
        limit = 11 if item.get("thumb_url") else 10
        analysis_keys = {cache_key(entry) for entry in entries(item)[:limit]}
        try:
            for entry in entries(item):
                checkpoint("media")
                key = cache_key(entry)
                if key in seen:
                    continue
                blob, _ = app.render_sample_entry(entry, item["url"])
                # Serving may fall back to the whole sprite sheet; that is not a prepared sample.
                if isinstance(entry, dict) and app.sprite_crop_box(entry) and not app.read_cached_thumbnail(
                        *app.thumbnail_cache_paths(key), key):
                    raise app.SpriteCropUnavailable("sample crop unavailable")
                seen.add(key)
                if key in analysis_keys:
                    blobs.append(blob)
                    keys.append(key)
            break
        except app.ApiError as exc:
            if attempt or not app.stale_thumbnail_error(exc):
                raise
            item = refresh_detail(item, checkpoint)
    if not media_cached(item):
        raise RuntimeError("media_incomplete")
    identity = fingerprint(item, config)
    analyzed = not analysis_current(item, previous, config)
    if analyzed:
        checkpoint("visual")
        # The UI analyzes the cover plus ten samples. Keep the same bounded image set.
        vectors = (dinov2_image_embeddings(blobs, device=config[1], local_files_only=True)
                   if config[0] == "dinov2" else simple_embeddings(blobs))
        embedding = average_embeddings(vectors)
    checkpoint("finalizing")
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM galleries WHERE url=?", (item["url"],)).fetchone()
        current = app.gallery_db_row_payload(conn, row) if row else None
        if not current or encoder_config(conn) != config or fingerprint(current, config) != identity:
            raise RuntimeError("media_changed")
        if not analyzed and not analysis_current(current, previous, config):
            raise RuntimeError("analysis_changed")
        if analyzed:
            app.store_visual_embedding(conn, item["url"], embedding, version=config[2])
            app.store_visual_image_embeddings(conn, item["url"], [
                {"image_url": key, "embedding": vector} for key, vector in zip(keys, vectors)], version=config[2])
            app.mark_model_retrain_pending(conn, increment=False)
        digest = conn.execute("SELECT visual_embedding_digest FROM galleries WHERE url=?", (item["url"],)).fetchone()[0]
        conn.execute("""UPDATE review_media_prewarm SET fingerprint=?, embedding_digest=?, state='ready',
            retry_after=0, attempts=0, updated_at=CURRENT_TIMESTAMP WHERE gallery_url=?""",
            (identity, digest, item["url"]))
    if analyzed:
        app.MODEL_RETRAIN_WAKE.set()
    return len(seen), int(analyzed)


def run_batch(checkpoint=lambda stage: None, *, automatic=False):
    result = {"checked": 0, "ready": 0, "partial": 0, "images": 0, "analyzed": 0, "errors": []}
    for candidate in candidates()[:BATCH_SIZE]:
        checkpoint("starting")
        with db.connect() as conn:
            if automatic and not settings(conn)["enabled"]:
                break
            config = encoder_config(conn)
        item = load_item(candidate["url"])
        if not item:
            continue
        previous = record_attempt(item, config)
        result["checked"] += 1
        try:
            images, analyzed = prepare_gallery(item, config, previous, checkpoint)
            result["images"] += images
            result["analyzed"] += analyzed
            result["ready"] += 1
        except sqlite3.Error:
            raise
        except Exception:
            # Only fixed counters enter durable history; no auth or remote exception text.
            result["partial"] += 1
            result["errors"].append("media_not_ready")
            # Details may have refreshed the manifest. Carry cooldown to that new manifest.
            current = load_item(item["url"])
            if current:
                with db.connect() as conn:
                    identity = fingerprint(current, config)
                    conn.execute("""UPDATE review_media_prewarm SET
                        embedding_digest=CASE WHEN fingerprint=? THEN embedding_digest ELSE '' END,
                        fingerprint=? WHERE gallery_url=?""", (identity, identity, item["url"]))
    if result["partial"]:
        result["status"] = "partial"
    return result


def schedule_once():
    app = service()
    with db.connect() as conn:
        if not settings(conn)["enabled"] or not db.get_setting(conn, "cookie_header", "") or not app.JOBS.running:
            return False
        rows = conn.execute("SELECT steps_json FROM jobs WHERE state IN ('queued','held','running','pause_requested','paused')").fetchall()
    if any(any(step["kind"] == "review_prewarm" for step in json.loads(row[0])) for row in rows):
        return True
    if not candidates():
        return False
    app.JOBS.submit([{"kind": "review_prewarm"}], source="background", coalesce=True)
    return True


def background(stop):
    if stop.wait(30):
        return
    while not stop.is_set():
        try:
            submitted = schedule_once()
            STATUS["last_error_code"] = ""
        except Exception:
            STATUS["last_error_code"] = "scheduler_failed"
            submitted = True
        if stop.wait(30 if submitted else 300):
            return
