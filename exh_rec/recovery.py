"""Offline restore drill. Run only in a fresh child, never against live paths."""
from contextlib import closing
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import sys
import uuid


def digest(path):
    value = hashlib.sha256()
    with path.open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def deny_network(*args, **kwargs):
    raise OSError('restore_drill_network_disabled')


def drill(manifest_path):
    from scripts.launcher_ops import local_target, regular
    manifest_path = local_target(manifest_path)
    if not regular(manifest_path):
        raise ValueError('invalid_manifest_file')
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    if manifest.get('schema') != 1 or not isinstance(manifest.get('files'), dict):
        raise ValueError('invalid_manifest')
    files = manifest['files']
    if 'database' not in files or set(files) - {'database', 'model', 'evaluation', 'classifier'}:
        raise ValueError('invalid_bundle')
    directory = local_target(manifest_path.parent / ('restore-drill-' + uuid.uuid4().hex))
    directory.mkdir()
    targets = {'database': 'recommender.sqlite3', 'model': 'model.joblib',
               'evaluation': 'evaluation.json', 'classifier': 'classifier.joblib'}
    for kind, entry in files.items():
        name = entry['name']
        if not isinstance(name, str) or Path(name).name != name or '/' in name or '\\' in name:
            raise ValueError('invalid_bundle_path')
        source = local_target(manifest_path.parent / name)
        if not regular(source):
            raise ValueError('invalid_bundle_file')
        if digest(source) != entry['sha256']:
            raise ValueError('bundle_hash_mismatch')
        target = directory / targets[kind]
        with source.open('rb') as reader, target.open('xb') as writer:
            shutil.copyfileobj(reader, writer)
        if digest(target) != entry['sha256']:
            raise ValueError('restore_hash_mismatch')
    os.environ.update(EXH_REC_DATA_DIR=str(directory), EXH_REC_MODEL_PATH=str(directory / 'model.joblib'),
                      EXH_REC_EVALUATION_PATH=str(directory / 'evaluation.json'),
                      EXH_REC_CLASSIFIER_PATH=str(directory / 'classifier.joblib'))
    socket.socket.connect = deny_network
    socket.socket.connect_ex = deny_network
    socket.create_connection = deny_network
    from . import personalized
    personalized._train_personalized_model = deny_network
    with closing(sqlite3.connect((directory / targets['database']).as_uri() + '?mode=ro', uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA query_only=ON')
        if [tuple(row) for row in conn.execute('PRAGMA quick_check')] != [('ok',)]:
            raise ValueError('restore_database_invalid')
        for table in ('galleries', 'feedback', 'gallery_marks', 'settings'):
            conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()
        row = conn.execute("SELECT value FROM settings WHERE key='model_retrain_active_signature'").fetchone()
        signature = row[0] if row else ''
        if bool(signature) != ('model' in files):
            raise ValueError('restore_model_missing')
        if signature:
            artifact = personalized.load_personalized_model(conn, train_if_needed=False, allow_stale=True)
            if artifact.get('signature') != signature or artifact.get('schema') != personalized.MODEL_SCHEMA:
                raise ValueError('restore_model_invalid')
            if artifact.get('ready'):
                row = conn.execute('SELECT * FROM galleries ORDER BY url LIMIT 1').fetchone()
                if row:
                    scores, _ = personalized.score_personalized_galleries(conn, [dict(row)], artifact=artifact, train_if_needed=False)
                    if len(scores) != 1 or not math.isfinite(scores[0]['rank_score']):
                        raise ValueError('restore_model_scoring_failed')
    return {'verified': True, 'database': True, 'model': bool(signature), 'network_disabled': True}


def main():
    # No exception text, paths, model identities or database contents cross stdout.
    try:
        result = drill(Path(sys.argv[1]))
    except Exception:
        result = {'verified': False}
    print(json.dumps(result))
    return 0 if result['verified'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
