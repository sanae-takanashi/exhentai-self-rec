import sqlite3
import unittest

from exh_rec.fetch_insights import SCHEMA, fetch_insights, finish_listing, known_gallery_urls, start_listing


class FetchInsightsTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(':memory:')
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript('''
            PRAGMA foreign_keys=ON;
            CREATE TABLE fetch_runs(id INTEGER PRIMARY KEY);
            INSERT INTO fetch_runs VALUES(1),(2);
            CREATE TABLE galleries(url TEXT PRIMARY KEY, source_query TEXT);
            CREATE TABLE feedback(id INTEGER PRIMARY KEY AUTOINCREMENT, gallery_url TEXT, vote REAL, reason_code TEXT);
            CREATE TABLE recommendation_impressions(id INTEGER PRIMARY KEY AUTOINCREMENT, gallery_url TEXT, visible_at TEXT);
        ''' + SCHEMA)
        self.addCleanup(self.conn.close)

    def observe(self, query, urls, page=0, returned=None):
        entries = [{'url': url} for url in urls]
        listing = start_listing(self.conn, 1, {'query': query, 'source': 'manual'}, page)
        known = known_gallery_urls(self.conn, entries)
        self.conn.executemany('INSERT OR IGNORE INTO galleries(url) VALUES(?)', [(url,) for url in urls])
        finish_listing(self.conn, listing, entries, existing_urls=known, returned_count=returned)
        return listing

    def outcome(self, url, vote, reason=None):
        return self.conn.execute('INSERT INTO feedback(gallery_url,vote,reason_code) VALUES(?,?,?)',
                                 (url, vote, reason)).lastrowid

    def rows(self):
        return {row['query']: row for row in fetch_insights(self.conn)['items']}

    def test_repeated_and_shared_provenance_retains_first_discovery(self):
        self.observe('a', ['u1', 'u2'], returned=4)
        self.observe('a', ['u1'], page=1)
        self.observe('b', ['u1'])
        self.outcome('u1', 1)
        a, b = self.rows()['a'], self.rows()['b']
        self.assertEqual((a['recorded_galleries'], a['shared_galleries'], a['new_galleries']), (2, 1, 2))
        self.assertEqual((a['returned_occurrences'], a['admitted_occurrences'], a['existing_occurrences']), (5, 3, 1))
        self.assertEqual(a['new_positive_per_completed_operation'], .5)
        self.assertEqual((b['positive'], b['new_positive'], b['existing_galleries']), (1, 0, 1))
        data = fetch_insights(self.conn)
        self.assertEqual((data['recorded_unique_galleries'], data['recorded_query_gallery_pairs']), (2, 3))

    def test_latest_feedback_undo_and_prior_feedback_are_not_new_outcomes(self):
        self.conn.execute("INSERT INTO galleries(url) VALUES('old')")
        self.outcome('old', 1)
        self.observe('a', ['old', 'new'])
        self.assertEqual(self.rows()['a']['unknown'], 2)
        positive = self.outcome('new', 1)
        negative = self.outcome('new', -1)
        self.assertEqual((self.rows()['a']['positive'], self.rows()['a']['negative']), (0, 1))
        # SQL DELETE models the existing feedback undo API on an in-memory fixture.
        self.conn.execute('DELETE FROM feedback WHERE id=?', (negative,))
        self.assertEqual(self.rows()['a']['positive'], 1)
        self.conn.execute('DELETE FROM feedback WHERE id=?', (positive,))
        self.assertEqual(self.rows()['a']['unknown'], 2)
        old_new = self.outcome('old', -1)
        self.assertEqual(self.rows()['a']['negative'], 1)
        self.conn.execute('DELETE FROM feedback WHERE id=?', (old_new,))
        self.assertEqual(self.rows()['a']['unknown'], 2)

    def test_neutral_duplicate_update_and_unseen_are_not_negatives(self):
        self.observe('a', ['good', 'bad', 'neutral', 'duplicate', 'unknown'])
        self.outcome('good', 1)
        self.outcome('bad', -1)
        self.outcome('neutral', 0)
        self.outcome('duplicate', -1, 'duplicate_update')
        a = self.rows()['a']
        self.assertEqual([a[k] for k in ('positive', 'negative', 'neutral', 'duplicate_update', 'unknown')], [1] * 5)
        self.assertEqual(a['positive_rate_explicit'], .5)
        self.assertEqual(a['visible_galleries'], 0)
        self.assertIsNone(a['visible_positive_rate'])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM recommendation_impressions').fetchone()[0], 0)

    def test_visibility_requires_new_real_visible_receipt(self):
        self.conn.execute("INSERT INTO recommendation_impressions(gallery_url,visible_at) VALUES('u1','earlier')")
        self.observe('a', ['u1', 'u2'])
        self.outcome('u1', 1)
        self.conn.execute("INSERT INTO recommendation_impressions(gallery_url) VALUES('u1')")
        self.assertEqual(self.rows()['a']['visible_galleries'], 0)
        self.conn.execute("INSERT INTO recommendation_impressions(gallery_url,visible_at) VALUES('u1','later')")
        self.assertEqual((self.rows()['a']['visible_galleries'], self.rows()['a']['visible_positive_rate']), (1, 1))

    def test_idempotent_completion_and_start(self):
        listing = self.observe('a', ['u1'])
        self.assertEqual(start_listing(self.conn, 1, {'query': 'a'}, 0), listing)
        finish_listing(self.conn, listing, failed=True)
        finish_listing(self.conn, listing, [{'url': 'invalid'}], existing_urls=set())
        self.assertEqual(self.rows()['a']['listing_operations'], 1)
        self.assertEqual(self.rows()['a']['completed_operations'], 1)

    def test_failures_unfinished_and_empty_success_have_honest_denominators(self):
        listing = start_listing(self.conn, 1, {'query': 'a'}, 0)
        finish_listing(self.conn, listing, failed=True)
        start_listing(self.conn, 1, {'query': 'a'}, 1)
        self.observe('a', [], page=2)
        a = self.rows()['a']
        self.assertEqual([a[k] for k in ('listing_operations', 'failed_operations', 'unfinished_operations', 'completed_operations')], [3, 1, 1, 1])
        self.assertIsNone(a['positive_rate_explicit'])
        self.assertEqual(a['new_positive_per_completed_operation'], 0)

    def test_historical_coverage_is_separate_without_invented_cost(self):
        self.conn.executemany('INSERT INTO galleries VALUES(?,?)', [('old', 'legacy'), ('shared', 'legacy')])
        self.outcome('old', 1)
        self.observe('current', ['shared'])
        old = self.rows()['legacy']
        self.assertEqual((old['inferred_historical_galleries'], old['listing_operations'], old['positive']), (1, 0, 0))
        self.assertIsNone(old['new_positive_per_completed_operation'])

    def test_read_only_with_sqlite_query_only_and_no_changes(self):
        self.observe(None, ['u1'])
        self.conn.commit()
        before = self.conn.total_changes
        self.conn.execute('PRAGMA query_only=ON')
        self.assertTrue(fetch_insights(self.conn)['read_only'])
        self.assertEqual(before, self.conn.total_changes)

    def test_invalid_input_leaves_observation_unfinished(self):
        listing = start_listing(self.conn, 1, {'query': 'a'}, 0)
        with self.assertRaises(ValueError):
            finish_listing(self.conn, listing, [{'url': 'unstored'}], existing_urls=set())
        with self.assertRaises(ValueError):
            finish_listing(self.conn, listing)
        with self.assertRaises(ValueError):
            finish_listing(self.conn, listing, [], existing_urls={'other'})
        self.assertEqual(self.rows()['a']['unfinished_operations'], 1)

    def test_large_pages_deduplicate_and_accept_iterators(self):
        urls = [f'u{n}' for n in range(805)]
        self.observe('a', urls)
        entries = ({'url': url} for url in urls + urls[:2])
        self.assertEqual(len(known_gallery_urls(self.conn, entries)), 805)


if __name__ == '__main__':
    unittest.main()
