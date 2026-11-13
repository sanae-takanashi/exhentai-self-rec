"""Retained synthetic recovery, durable intent and out-of-band health checks."""
from contextlib import ExitStack
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from exh_rec import app, db, feedback_jobs, maintenance, personalized, classification, review_prewarm
from exh_rec.exhentai import Gallery
from exh_rec.jobs import JobContext, JobError, JobHalt, JobQueue
from exh_rec.recommender import record_feedback, store_galleries
from scripts.launcher_jobs import validate_snapshot


class MaintenanceTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='maintenance-test-'))
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(db, 'DATA_DIR', self.root))
        self.stack.enter_context(patch.object(db, 'DB_PATH', self.root / 'recommender.sqlite3'))
        self.stack.enter_context(patch.object(personalized, 'MODEL_PATH', self.root / 'model.joblib'))
        self.stack.enter_context(patch.object(personalized, 'EVALUATION_PATH', self.root / 'evaluation.json'))
        self.stack.enter_context(patch.object(classification, 'MODEL_PATH', self.root / 'classifier.joblib'))
        self.stack.enter_context(patch.object(review_prewarm, '_SERVICE', None))
        db.init_db()
        self.jobs = JobQueue(db.connect)
        self.stack.enter_context(patch.object(app, 'JOBS', self.jobs))
        self.stack.enter_context(patch.object(JobQueue, 'running', new_callable=lambda: property(lambda _: True)))
        self.stack.enter_context(patch.object(maintenance, 'space_available', return_value=True))
        self.context = Mock(job_id='0' * 32)
        self.url = 'https://exhentai.org/g/1/abc/'
        with db.connect() as conn:
            store_galleries(conn, [Gallery(url=self.url, gid='1', token='abc', title='Synthetic')])
            db.set_setting(conn, 'cookie_header', 'FAKE_COOKIE')

    def request(self):
        with db.connect() as conn:
            feedback_jobs.request(conn, self.url)

    def row(self):
        with db.connect() as conn:
            return dict(conn.execute('SELECT * FROM feedback_enrichment_requests').fetchone())

    def claim(self):
        job = self.jobs._claim()
        return job, JobContext(self.jobs, job['id'])

    def test_feedback_intent_rolls_back_with_rating(self):
        with self.assertRaises(RuntimeError):
            with db.connect() as conn:
                record_feedback(conn, self.url, vote=1, retrain=False)
                feedback_jobs.request(conn, self.url)
                raise RuntimeError('synthetic_rollback')
        with db.connect() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM feedback').fetchone()[0], 0)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM feedback_enrichment_requests').fetchone()[0], 0)

    def test_restart_dispatch_is_bounded_atomic_and_deduplicated(self):
        for index in range(1, 6):
            with db.connect() as conn:
                url = f'https://exhentai.org/g/{index}/abc/'
                store_galleries(conn, [Gallery(url=url, gid=str(index), token='abc', title='Synthetic')])
                feedback_jobs.request(conn, url)
        self.jobs = JobQueue(db.connect)
        with patch.object(app, 'JOBS', self.jobs):
            self.assertTrue(feedback_jobs.schedule_once())
            self.assertFalse(feedback_jobs.schedule_once())
        with db.connect() as conn:
            self.assertEqual(dict(conn.execute('SELECT state,COUNT(*) FROM feedback_enrichment_requests GROUP BY state')),
                             {'assigned': 3, 'pending': 2})
        public = json.dumps(self.jobs.snapshot())
        self.assertNotIn('https://', public)
        validate_snapshot(self.jobs.snapshot())

    def test_dispatch_failure_rolls_back_job_and_assignment(self):
        self.request()
        original = self.jobs.submit
        def fail(*args, **kwargs):
            original(*args, **kwargs)
            raise RuntimeError('synthetic_failure')
        with patch.object(self.jobs, 'submit', side_effect=fail), self.assertRaises(RuntimeError):
            feedback_jobs.schedule_once()
        self.assertEqual(self.jobs.snapshot()['jobs'], [])
        self.assertEqual(self.row()['state'], 'pending')

    def test_cancel_and_interruption_are_not_replayed_retry_chain_recovers(self):
        self.request()
        feedback_jobs.schedule_once()
        first = self.jobs.snapshot()['jobs'][0]
        self.jobs.action(first['id'], 'cancel')
        self.assertFalse(feedback_jobs.schedule_once())
        second = self.jobs.action(first['id'], 'retry')
        self.jobs.action(second['id'], 'cancel')
        third = self.jobs.action(second['id'], 'retry')
        job, context = self.claim()
        self.assertEqual(job['id'], third['id'])
        with patch.object(review_prewarm, 'prepare_gallery'), patch.object(app, 'fetch_parent_chain_metadata', return_value=0):
            result = feedback_jobs.run_batch(context)
        self.assertEqual(result['enriched'], 1)
        self.assertEqual(self.row()['state'], 'done')
        self.assertEqual(self.row()['job_id'], third['id'])

    def test_failure_sanitized_no_network_under_writer_and_no_nested_fetch_lock(self):
        self.request()
        feedback_jobs.schedule_once()
        job, context = self.claim()
        def external(*args, **kwargs):
            with db.connect() as conn:
                conn.execute('BEGIN IMMEDIATE')
            self.assertTrue(app.FETCH_LOCK.locked())
            raise RuntimeError('FAKE_SECRET')
        with patch.object(review_prewarm, 'prepare_gallery'), patch.object(app, 'fetch_parent_chain_metadata', side_effect=external):
            result = feedback_jobs.run_batch(context)
        self.assertEqual(result['status'], 'partial')
        self.assertNotIn('FAKE_SECRET', json.dumps(self.row()))
        self.assertFalse(app.FETCH_LOCK.locked())
        self.jobs._finish(job['id'], 'partial')
        self.assertFalse(feedback_jobs.schedule_once())
        self.assertEqual(maintenance.health_sample()['feedback_held'], 1)

    def test_halt_preserves_assigned_intent_and_recover_does_not_requeue(self):
        self.request()
        feedback_jobs.schedule_once()
        job, context = self.claim()
        with patch.object(review_prewarm, 'prepare_gallery', side_effect=JobHalt('interrupted')):
            with self.assertRaises(JobHalt):
                feedback_jobs.run_batch(context)
        self.jobs.recover()
        self.assertEqual(self.jobs.get(job['id'])['state'], 'interrupted')
        self.assertFalse(feedback_jobs.schedule_once())
        self.assertEqual(self.row()['state'], 'assigned')

    def test_cancel_during_parent_fetch_takes_effect_after_committed_parent_work(self):
        self.request()
        feedback_jobs.schedule_once()
        job, context = self.claim()
        def parent(*args, **kwargs):
            self.jobs.action(job['id'], 'cancel')
            return 0
        with patch.object(review_prewarm, 'prepare_gallery'), patch.object(app, 'fetch_parent_chain_metadata', side_effect=parent):
            with self.assertRaises(JobHalt):
                feedback_jobs.run_batch(context)
        self.assertFalse(app.FETCH_LOCK.locked())
        self.assertEqual(self.row()['state'], 'assigned')

    def test_job_submission_joins_transaction_and_rolls_back(self):
        with db.connect() as conn:
            with self.assertRaises(JobError):
                self.jobs.submit([{'kind': 'backup'}], connection=conn)
        with self.assertRaises(RuntimeError):
            with db.connect() as conn:
                conn.execute('BEGIN IMMEDIATE')
                self.jobs.submit([{'kind': 'backup'}], connection=conn)
                raise RuntimeError('synthetic_rollback')
        self.assertEqual(self.jobs.snapshot()['jobs'], [])

    def test_priority_foreground_feedback_backup_prewarm(self):
        for kind, source in [('review_prewarm', 'background'), ('backup', 'background'),
                             ('feedback_enrich', 'background'), ('fetch', 'web')]:
            self.jobs.submit([{'kind': kind}], source=source)
        self.assertEqual([row['steps'][0]['kind'] for row in self.jobs.snapshot()['jobs']],
                         ['fetch', 'feedback_enrich', 'backup', 'review_prewarm'])

    def test_backup_and_actual_offline_restore_of_empty_model(self):
        result = maintenance.backup(self.context)
        self.assertTrue(result['verified'])
        with db.connect() as conn:
            saved = maintenance.state(conn)
        manifest = Path(saved['manifest'])
        self.assertTrue(manifest.is_file())
        self.assertTrue(saved['restore_verified'])
        self.assertTrue(list(manifest.parent.glob('restore-drill-*')))
        self.assertEqual(len(list(manifest.parent.glob('*.sqlite3'))), 1)

    def test_backup_restores_db_selected_version_not_unpublished_model(self):
        import joblib
        with db.connect() as conn:
            db.set_setting(conn, 'model_retrain_active_signature', 'synthetic-active')
        artifact = {'schema': personalized.MODEL_SCHEMA, 'signature': 'synthetic-active', 'ready': False}
        target = personalized._versioned_model_path('synthetic-active')
        target.parent.mkdir(parents=True)
        joblib.dump(artifact, target)
        joblib.dump({**artifact, 'signature': 'unpublished'}, personalized.MODEL_PATH)
        self.assertTrue(maintenance.backup(self.context)['verified'])

    def test_missing_or_mismatched_active_model_fails_closed(self):
        with db.connect() as conn:
            db.set_setting(conn, 'model_retrain_active_signature', 'synthetic-missing')
        with self.assertRaises(JobError):
            maintenance.backup(self.context)
        with db.connect() as conn:
            self.assertEqual(maintenance.state(conn)['error_code'], 'backup_or_restore_failed')
        self.assertFalse(maintenance.schedule_backup())

    def test_ready_but_broken_model_does_not_pass_restore_check(self):
        import joblib
        with db.connect() as conn:
            db.set_setting(conn, 'model_retrain_active_signature', 'synthetic-broken')
        joblib.dump({'schema': personalized.MODEL_SCHEMA, 'signature': 'synthetic-broken', 'ready': True}, personalized.MODEL_PATH)
        with self.assertRaises(JobError):
            maintenance.backup(self.context)

    def test_corrupt_backup_and_traversal_fail_offline_child(self):
        maintenance.backup(self.context)
        with db.connect() as conn:
            manifest = Path(maintenance.state(conn)['manifest'])
        payload = json.loads(manifest.read_text())
        payload['files']['database']['name'] = '../recommender.sqlite3'
        bad = manifest.with_name('bad-path.bundle.json')
        bad.write_text(json.dumps(payload), encoding='utf-8')
        self.assertFalse(maintenance.restore_check(bad))
        payload['files']['database']['name'] = json.loads(manifest.read_text())['files']['database']['name']
        payload['files']['database']['sha256'] = '0' * 64
        bad_hash = manifest.with_name('bad-hash.bundle.json')
        bad_hash.write_text(json.dumps(payload), encoding='utf-8')
        self.assertFalse(maintenance.restore_check(bad_hash))

    def test_schedule_idle_cadence_unchanged_and_low_disk(self):
        with patch.object(maintenance, 'space_available', return_value=False):
            self.assertFalse(maintenance.schedule_backup())
        self.assertTrue(maintenance.schedule_backup())
        self.assertFalse(maintenance.schedule_backup())
        job = self.jobs.snapshot()['jobs'][0]
        self.jobs._finish(job['id'], 'succeeded')
        maintenance.backup(self.context)
        self.assertFalse(maintenance.schedule_backup())
        with db.connect() as conn:
            saved = maintenance.state(conn)
            saved['attempt_at'] = time.time() - 10 * 86400
            maintenance.save_state(conn, saved)
        self.assertFalse(maintenance.schedule_backup())
        with db.connect() as conn:
            db.set_setting(conn, 'synthetic_changed', '1')
        self.assertTrue(maintenance.schedule_backup())

    def test_cancelled_scheduled_backup_is_not_automatically_replayed(self):
        self.assertTrue(maintenance.schedule_backup())
        job = self.jobs.snapshot()['jobs'][0]
        self.jobs.action(job['id'], 'cancel')
        self.assertFalse(maintenance.schedule_backup())
        self.assertIn('backup_attention', maintenance.health_sample()['warnings'])

    def test_health_is_readonly_independent_of_worker_and_bounded_when_writer_busy(self):
        with patch.object(JobQueue, 'running', new_callable=lambda: property(lambda _: False)):
            with db.connect() as conn:
                conn.execute('BEGIN IMMEDIATE')
                before = conn.total_changes
                start = time.monotonic()
                health = maintenance.health_sample()
                self.assertLess(time.monotonic() - start, 2)
                self.assertEqual(conn.total_changes, before)
        self.assertFalse(health['worker'])
        self.assertIn('worker_unavailable', health['warnings'])
        self.assertNotIn('read_probe_unavailable', health['warnings'])
        payload = {**self.jobs.snapshot(), 'maintenance': health}
        self.assertEqual(validate_snapshot(payload)['maintenance']['state'], 'warning')

    def test_health_does_not_call_paused_job_stuck_or_change_lifecycle(self):
        job = self.jobs.submit([{'kind': 'backup'}])
        with db.connect() as conn:
            conn.execute("UPDATE jobs SET state='paused',updated_at='2000-01-01T00:00:00+00:00' WHERE id=?", (job['id'],))
        self.assertNotIn('long_running_job', maintenance.health_sample()['warnings'])
        self.assertEqual(self.jobs.get(job['id'])['state'], 'paused')
        with db.connect() as conn:
            conn.execute("UPDATE jobs SET state='running' WHERE id=?", (job['id'],))
        self.assertIn('long_running_job', maintenance.health_sample()['warnings'])


if __name__ == '__main__':
    unittest.main()
