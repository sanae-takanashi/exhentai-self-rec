"""Run feedback/audit regressions with retained fixtures and no production DB."""
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
fixtures = Path(tempfile.mkdtemp(prefix="review-checks-"))
os.environ["EXH_REC_DATA_DIR"] = str(fixtures)


class RetainedTemporaryDirectory(tempfile.TemporaryDirectory):
    def cleanup(self):
        self._finalizer.detach()

    @classmethod
    def _cleanup(cls, name, **kwargs):
        pass


tempfile.TemporaryDirectory = RetainedTemporaryDirectory

from tests.test_app import AppTest

names = [name for name in unittest.defaultTestLoader.getTestCaseNames(AppTest)
         if name.startswith(("test_feedback_", "test_parse_feedback_", "test_mark_", "test_clear_mark_"))]
suite = unittest.TestSuite(AppTest(name) for name in names)
suite.addTests(unittest.defaultTestLoader.loadTestsFromNames(["tests.test_review_mode", "tests.test_audit"]))
result = unittest.TextTestRunner(verbosity=1).run(suite)
print(f"Retained synthetic fixture root: {fixtures}")
sys.exit(0 if result.wasSuccessful() else 1)
