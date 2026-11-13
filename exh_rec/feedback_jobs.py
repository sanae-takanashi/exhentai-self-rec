"""Explicit feedback enrichment intents, committed before any remote work."""
import json
import sqlite3

from . import db, review_prewarm


BATCH_SIZE = 3


def request(conn, url):
    # Do not turn repeated feedback into automatic replay of ambiguous work.
    conn.execute("""INSERT INTO feedback_enrichment_requests(gallery_url) VALUES(?)
        ON CONFLICT(gallery_url) DO NOTHING""", (url,))


def schedule_once():
    app = review_prewarm.service()
    if not app.JOBS.running:
        return False
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        active = conn.execute("""SELECT steps_json FROM jobs WHERE state IN
            ('queued','held','running','pause_requested','paused','cancel_requested')""").fetchall()
        if any(any(step["kind"] == "feedback_enrich" for step in json.loads(row[0])) for row in active):
            return False
        rows = conn.execute("SELECT gallery_url FROM feedback_enrichment_requests WHERE state='pending' ORDER BY updated_at, gallery_url LIMIT ?",
                            (BATCH_SIZE,)).fetchall()
        if not rows:
            return False
        job = app.JOBS.submit([{"kind": "feedback_enrich"}], source="background", connection=conn)
        conn.executemany("UPDATE feedback_enrichment_requests SET state='assigned', job_id=?, updated_at=CURRENT_TIMESTAMP WHERE gallery_url=?",
                         [(job["id"], row[0]) for row in rows])
    return True


def run_batch(context):
    app = review_prewarm.service()
    # Retry may itself have been cancelled before starting. Follow its ancestry.
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        ancestors, current = [], context.job_id
        for _ in range(100):
            if not current or current in ancestors:
                break
            ancestors.append(current)
            row = conn.execute("SELECT retry_of FROM jobs WHERE id=?", (current,)).fetchone()
            current = row[0] if row else None
        marks = ','.join('?' for _ in ancestors)
        rows = conn.execute(f"SELECT gallery_url FROM feedback_enrichment_requests WHERE job_id IN ({marks}) AND state!='done' LIMIT ?",
                            (*ancestors, BATCH_SIZE)).fetchall()
        conn.executemany("UPDATE feedback_enrichment_requests SET job_id=?, state='assigned', error_code='' WHERE gallery_url=?",
                         [(context.job_id, row[0]) for row in rows])
    result = {"checked": 0, "enriched": 0, "partial": 0, "parent_enriched": 0}
    for row in rows:
        context.checkpoint("starting")
        url = row[0]
        with db.connect() as conn:
            conn.execute("UPDATE feedback_enrichment_requests SET attempts=attempts+1, updated_at=CURRENT_TIMESTAMP WHERE gallery_url=?", (url,))
            config = review_prewarm.encoder_config(conn)
        result["checked"] += 1
        try:
            item = review_prewarm.load_item(url)
            if item is None:
                raise ValueError("gallery_missing")
            previous = review_prewarm.record_attempt(item, config)
            review_prewarm.prepare_gallery(item, config, previous, context.checkpoint)
            context.checkpoint("detail")
            with db.connect() as conn:
                cookie = db.get_setting(conn, "cookie_header", "")
                proxy = app.network_proxy(conn)
            if not cookie:
                raise ValueError("authentication_required")
            # prepare_gallery takes this lock itself; never nest the acquisition.
            if not app.FETCH_LOCK.acquire(blocking=False):
                raise ValueError("metadata_busy")
            try:
                parent = app.fetch_parent_chain_metadata(cookie, app.gallery_from_item(review_prewarm.load_item(url)), proxy_url=proxy)
            finally:
                app.FETCH_LOCK.release()
            context.checkpoint('finalizing')
            result["parent_enriched"] += parent
        except sqlite3.Error:
            raise
        except Exception:
            with db.connect() as conn:
                conn.execute("UPDATE feedback_enrichment_requests SET state='held', error_code='enrichment_incomplete', updated_at=CURRENT_TIMESTAMP WHERE gallery_url=?", (url,))
            result["partial"] += 1
        else:
            with db.connect() as conn:
                conn.execute("UPDATE feedback_enrichment_requests SET state='done', error_code='', updated_at=CURRENT_TIMESTAMP WHERE gallery_url=?", (url,))
            result["enriched"] += 1
    if result["partial"]:
        result["status"] = "partial"
    return result
