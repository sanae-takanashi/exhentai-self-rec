"""Run isolated Windows launcher checks; retain all fixture files."""
import os
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
fixtures = Path(tempfile.mkdtemp(prefix="launcher-checks-"))
os.environ["EXH_LAUNCHER_TEST_ROOT"] = str(fixtures)
os.environ["EXH_REC_DATA_DIR"] = str(fixtures / "import-only")


class RetainedTemporaryDirectory(tempfile.TemporaryDirectory):
    def cleanup(self):
        self._finalizer.detach()

    @classmethod
    def _cleanup(cls, name, **kwargs):
        pass


tempfile.TemporaryDirectory = RetainedTemporaryDirectory
suite = unittest.defaultTestLoader.loadTestsFromNames([
    "tests.test_launcher", "tests.test_launcher_logs", "tests.test_hath_launcher",
    "tests.test_fetch_activity", "tests.test_fetch_pagination",
])
result = unittest.TextTestRunner(verbosity=2, buffer=True).run(suite)
print(f"Retained synthetic fixture root: {fixtures}")
sys.exit(0 if result.wasSuccessful() else 1)
