import sqlite3
import unittest
from datetime import date
from unittest.mock import patch

from exh_rec import app, db, recommender
from exh_rec.exhentai import Gallery, parse_gallery_list
from exh_rec.recency import gallery_posted_date, recent_gallery_cutoff


class RecencyTest(unittest.TestCase):
    def test_calendar_month_boundary_and_month_end(self):
        for today, expected in [(date(2026, 11, 3), "2026-08-03"), (date(2026, 5, 31), "2026-02-28"),
                                (date(2024, 5, 31), "2024-02-29"), (date(2026, 1, 1), "2025-10-01")]:
            self.assertEqual(recent_gallery_cutoff(today), expected)

    def test_invalid_dates_fail_closed(self):
        for value in (None, "", "unknown", "2026-02-30", "20260803", "2026-W32-1"):
            self.assertIsNone(gallery_posted_date(value))
        self.assertEqual(gallery_posted_date("2026-08-03 12:34"), "2026-08-03")

    def test_listing_dates_are_bound_to_gallery_id_not_nearby_rows(self):
        page = '''<tr><td><div id="posted_10">2026-11-03 12:34</div></td>
            <td><a class="glink" href="/g/10/abcdef/">One</a></td></tr>
            <div class="gl1t"><a class="glink" href="/g/9/abcdef/">Two</a>
            <div id="posted_9"><span>2026-07-01 01:02</span></div></div>
            <a class="glink" href="/g/8/abcdef/">Undated</a>'''
        rows = parse_gallery_list(page)
        self.assertEqual([g.posted_at for g in rows], ["2026-11-03 12:34", "2026-07-01 01:02", None])

    def test_live_payloads_and_counts_always_supply_boundary(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(db.SCHEMA)
        self.addCleanup(conn.close)
        with patch.object(app, "recent_gallery_cutoff", return_value="2026-08-03"), patch.object(
            app, "recommend_page", return_value={"items": [], "primary_total": 0, "low_interest_total": 0}
        ) as recommend, patch.object(app, "gallery_item_payloads", side_effect=lambda _c, rows: rows), patch.object(
            app, "related_queue_counts", return_value={}
        ):
            app.recommendation_payload(conn, posted_after="2020-01-01", interest_band="primary")
            app.queue_counts_payload(conn)
        self.assertEqual([call.kwargs["min_posted_date"] for call in recommend.call_args_list], ["2026-08-03"] * 2)

    def test_old_rows_cannot_fill_candidate_pool_and_history_stays_available(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(db.SCHEMA)
        self.addCleanup(conn.close)
        recent = Gallery(url="https://exhentai.org/g/1/a/", title="Recent", posted_at="2026-08-03")
        old = [Gallery(url=f"https://exhentai.org/g/{i}/a/", title="Old", posted_at="2026-07-01") for i in range(2, 112)]
        recommender.store_galleries(conn, [recent, *old])
        conn.execute("UPDATE galleries SET last_seen_at='2020-01-01' WHERE url=?", (recent.url,))
        options = dict(limit=40, candidate_limit=100, low_interest_percent=0, exclude_short_repeats=False)
        with patch.object(recommender, "load_personalized_model", return_value={"ready": False, "accepted": False}):
            page = recommender.recommend_page(conn, min_posted_date="2026-08-03", **options)
            unrestricted = recommender.recommend_page(conn, **options)
        self.assertEqual([item["url"] for item in page["items"]], [recent.url])
        self.assertEqual(page["overall_total"], 1)
        self.assertEqual(len(unrestricted["items"]), 40)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM galleries").fetchone()[0], 111)

    def test_recency_does_not_change_sticky_membership_policy_key(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(db.SCHEMA)
        self.addCleanup(conn.close)
        recommender.store_galleries(conn, [Gallery(url=f"https://exhentai.org/g/{i}/a/", title="Recent", posted_at="2026-11-03") for i in range(1, 6)])
        with patch.object(recommender, "low_interest_membership_policy_key", wraps=recommender.low_interest_membership_policy_key) as policy:
            first = recommender.recommend_page(conn, min_posted_date="2026-08-03")
            second = recommender.recommend_page(conn, min_posted_date="2026-08-04")
        self.assertEqual([call.kwargs["posted_after"] for call in policy.call_args_list], ["", ""])
        self.assertEqual(first["low_interest_policy"]["membership_policy_key"], second["low_interest_policy"]["membership_policy_key"])

    def test_manual_date_filter_can_only_narrow_the_window_and_cache_rolls_forward(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(db.SCHEMA)
        self.addCleanup(conn.close)
        recommender.store_galleries(conn, [Gallery(url=f"https://exhentai.org/g/{i}/a/", title="Dated", posted_at=posted)
                                          for i, posted in enumerate(("2026-08-02", "2026-08-03", "2026-08-04", "2026-11-03"), 1)])
        options = dict(low_interest_percent=0, exclude_short_repeats=False)
        loose = recommender.recommend_page(conn, min_posted_date="2026-08-03", posted_after="2020-01-01", **options)
        next_day = recommender.recommend_page(conn, min_posted_date="2026-08-04", **options)
        strict = recommender.recommend_page(conn, min_posted_date="2026-08-03", posted_after="2026-11-01", **options)
        self.assertEqual([loose["total"], next_day["total"], strict["total"]], [3, 2, 1])


if __name__ == "__main__":
    unittest.main()
