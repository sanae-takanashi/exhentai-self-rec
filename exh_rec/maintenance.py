"""Low-priority recovery backups and an independent read-only health sampler."""
from contextlib import closing
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import threading
import time

from . import db, feedback_jobs, review_prewarm
from .jobs import JobError, timestamp


HEALTH = {'state': 'starting', 'checked_at': None, 'warnings': []}
HEALTH_LOCK = threading.Lock()
WAKE = threading.Event()
MIN_FREE = 1024 ** 3


def state(conn):
    row = conn.execute("SELECT value FROM maintenance_state WHERE key='backup'").fetchone()
    return json.loads(row[0]) if row else {}


def save_state(conn, value):
    conn.execute("INSERT INTO maintenance_state(key,value) VALUES('backup',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                 (json.dumps(value, ensure_ascii=True),))


def settings(conn):
    try:
        hours = max(6, min(168, int(db.get_setting(conn, 'backup_interval_hours', '24'))))
    except (TypeError, ValueError):
        hours = 24
    backup = state(conn)
    error = backup.get('error_code', '')
    if error in {'backup_pending', 'backup_incomplete'}:
        row = conn.execute('SELECT state FROM jobs WHERE id=?', (backup.get('attempt_job'),)).fetchone()
        error = '' if row and row[0] in {'queued', 'held', 'running', 'paused', 'pause_requested', 'cancel_requested'} else 'backup_attention'
    return {'enabled': db.get_setting(conn, 'backup_enabled', '1') == '1', 'interval_hours': hours,
            'last_completed_at': backup.get('completed_at'), 'verified': backup.get('verified', False),
            'restore_verified': backup.get('restore_verified', False), 'error_code': error}


def revision(conn):
    return list(db.cache_revision(conn, db.CACHE_TABLES) or [])


def space_available():
    from . import personalized, classification
    size = db.DB_PATH.stat().st_size * 3
    for path in (personalized.MODEL_PATH, personalized.EVALUATION_PATH, classification.MODEL_PATH):
        if path.is_file():
            size += path.stat().st_size * 3
    return shutil.disk_usage(db.DB_PATH.parent).free >= max(MIN_FREE, size + 256 * 1024 ** 2)


def restore_check(manifest):
    env = {key: value for key, value in os.environ.items() if key.upper() in
           {'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP', 'SYSTEMDRIVE', 'COMSPEC', 'PATH'}}
    child = subprocess.run([sys.executable, '-B', '-m', 'exh_rec.recovery', str(manifest)],
                           cwd=db.APP_DIR, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                           stderr=subprocess.DEVNULL, timeout=120,
                           creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if child.returncode or len(child.stdout) > 4096:
        return False
    return json.loads(child.stdout).get('verified') is True


def backup(context):
    from . import personalized, classification
    from scripts.launcher_ops import backup_sqlite_database, read_database, local_target, regular
    from scripts.launcher_core import atomic_json
    from .recovery import digest
    context.checkpoint('backup')
    previous = {}
    with db.connect() as conn:
        previous = state(conn)
        save_state(conn, {**previous, 'attempt_at': time.time(), 'attempt_job': context.job_id,
                          'error_code': 'backup_incomplete'})
    try:
        if not space_available():
            raise JobError('backup_disk_low')
        result = backup_sqlite_database(db.DB_PATH)
        database = Path(result['path'])
        with closing(read_database(database)) as conn:
            conn.row_factory = sqlite3.Row
            signature = db.get_setting(conn, 'model_retrain_active_signature', '')
            snapshot_revision = revision(conn)
        files = {'database': {'name': database.name, 'sha256': digest(database)}}
        sources = {'evaluation': personalized.EVALUATION_PATH, 'classifier': classification.MODEL_PATH}
        if signature:
            candidates = (personalized._versioned_model_path(signature), personalized.MODEL_PATH)
            sources['model'] = next((path for path in candidates if path.is_file()), candidates[0])
        for kind, path in sources.items():
            if kind != 'model' and not path.is_file():
                continue
            path = local_target(path)
            if not regular(path):
                raise JobError('backup_artifact_invalid')
            target = local_target(database.with_suffix('.' + kind + path.suffix))
            with path.open('rb') as reader, target.open('xb') as writer:
                shutil.copyfileobj(reader, writer)
            files[kind] = {'name': target.name, 'sha256': digest(target)}
        manifest = database.with_suffix('.bundle.json')
        payload = {'schema': 1, 'files': files, 'at': timestamp()}
        atomic_json(manifest, payload)
        if json.loads(manifest.read_text(encoding='utf-8')) != payload:
            raise JobError('backup_readback_failed')
        context.checkpoint('backup')
        restored = restore_check(manifest)
        if not restored:
            raise JobError('restore_check_failed')
        context.checkpoint('finalizing')
        with db.connect() as conn:
            save_state(conn, {'attempt_at': time.time(), 'attempt_job': context.job_id,
                              'completed_at': timestamp(), 'verified': True, 'restore_verified': True,
                              'manifest': str(manifest), 'revision': snapshot_revision, 'error_code': ''})
        return {'verified': True, 'checked': 1, 'bytes': database.stat().st_size}
    except Exception:
        with db.connect() as conn:
            save_state(conn, {**previous, 'attempt_at': time.time(), 'attempt_job': context.job_id,
                              'error_code': 'backup_or_restore_failed'})
        raise JobError('Backup or restore check failed; inspect local recovery files') from None


def schedule_backup():
    app = review_prewarm.service()
    if not app.JOBS.running:
        return False
    # Both the eligibility check and submission use one short writer transaction.
    with db.connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        options, saved = settings(conn), state(conn)
        if not options['enabled'] or conn.execute("SELECT 1 FROM jobs WHERE state IN ('queued','held','running','paused','pause_requested','cancel_requested') LIMIT 1").fetchone():
            return False
        if saved.get('error_code') or time.time() - saved.get('attempt_at', 0) < options['interval_hours'] * 3600:
            return False
        current = list(conn.execute('SELECT name,revision FROM cache_revisions ORDER BY name'))
        # cache_revision intentionally rejects open transactions. Read a stable revision directly.
        versions = dict(current)
        identity = [versions[name] for name in ('__identity__', *db.CACHE_TABLES)]
        if identity == saved.get('revision'):
            return False
        if not space_available():
            return False
        job = app.JOBS.submit([{'kind': 'backup'}], source='background', coalesce=True, connection=conn)
        save_state(conn, {**saved, 'attempt_at': time.time(), 'attempt_job': job['id'], 'error_code': 'backup_pending'})
    return True


def health_sample():
    app = review_prewarm.service()
    warnings = []
    result = {'state': 'ok', 'checked_at': timestamp(), 'worker': app.JOBS.running,
              'warnings': warnings}
    if not result['worker']:
        warnings.append('worker_unavailable')
    deadline = time.monotonic() + 0.5
    try:
        with closing(sqlite3.connect(db.DB_PATH.resolve().as_uri() + '?mode=ro', uri=True, timeout=0.15)) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute('PRAGMA query_only=ON')
            conn.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
            result['queued'] = conn.execute("SELECT COUNT(*) FROM jobs WHERE state='queued'").fetchone()[0]
            result['feedback_pending'] = conn.execute("SELECT COUNT(*) FROM feedback_enrichment_requests WHERE state='pending'").fetchone()[0]
            result['feedback_held'] = conn.execute("""SELECT COUNT(*) FROM feedback_enrichment_requests r JOIN jobs j ON r.job_id=j.id
                WHERE r.state!='done' AND j.state IN ('failed','partial','cancelled','interrupted')""").fetchone()[0]
            if result['feedback_held']:
                warnings.append('feedback_attention')
            for row in conn.execute("SELECT updated_at FROM jobs WHERE state IN ('running','pause_requested','cancel_requested')"):
                if (datetime.now(timezone.utc) - datetime.fromisoformat(row[0])).total_seconds() > 3600:
                    warnings.append('long_running_job')
                    break
            saved = state(conn)
            result['backup_verified'] = saved.get('verified', False)
            result['restore_verified'] = saved.get('restore_verified', False)
            if settings(conn)['error_code']:
                warnings.append('backup_attention')
            completed = saved.get('completed_at')
            if not completed or (datetime.now(timezone.utc) - datetime.fromisoformat(completed)).total_seconds() > 7 * 86400:
                warnings.append('backup_old')
            result['prewarm_failures'] = conn.execute("SELECT COUNT(*) FROM review_media_prewarm WHERE state!='ready' AND attempts>=3").fetchone()[0]
            if result['prewarm_failures']:
                warnings.append('prewarm_attention')
        if not space_available():
            warnings.append('disk_low')
    except Exception:
        warnings.append('read_probe_unavailable')
    result['state'] = 'warning' if warnings else 'ok'
    with HEALTH_LOCK:
        HEALTH.clear()
        HEALTH.update(result)
    return result


def snapshot():
    with HEALTH_LOCK:
        value = {**HEALTH, 'warnings': list(HEALTH['warnings'])}
    if value['checked_at'] and (datetime.now(timezone.utc) - datetime.fromisoformat(value['checked_at'])).total_seconds() > 180:
        value['state'] = 'stale'
    return value


def health_background(stop):
    while not stop.is_set():
        health_sample()
        if stop.wait(60):
            return


def scheduler_background(stop):
    while not stop.is_set():
        try:
            feedback_jobs.schedule_once()
            schedule_backup()
        except Exception:
            pass
        WAKE.wait(15)
        WAKE.clear()
