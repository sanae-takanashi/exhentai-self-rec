"""Synthetic process-death fixture. No network, service, GUI or credentials."""
from contextlib import contextmanager
from pathlib import Path
import sqlite3
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from exh_rec.jobs import JobQueue


@contextmanager
def connect():
    connection = sqlite3.connect(Path(sys.argv[1]), timeout=5)
    connection.row_factory = sqlite3.Row
    try:
        yield connection
        connection.commit()
    finally:
        connection.close()


jobs = JobQueue(connect)


def execute(kind, params, context, source):
    if kind == "fetch":
        jobs.action(context.job_id, "pause")
        context.checkpoint("query_page")
    return {"stored": 1}


jobs.start(execute)
while True:
    time.sleep(0.1)
