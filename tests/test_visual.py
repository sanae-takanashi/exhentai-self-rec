import importlib.util
import types
import unittest

from exh_rec import visual
from exh_rec.visual import (
    VisualEncoderUnavailable,
    normalize_dinov2_device,
    resolve_dinov2_device,
)


class VisualTest(unittest.TestCase):
    def test_normalize_dinov2_device_accepts_cpu_auto_and_cuda_index(self):
        self.assertEqual(normalize_dinov2_device(""), "auto")
        self.assertEqual(normalize_dinov2_device("AUTO"), "auto")
        self.assertEqual(normalize_dinov2_device("cuda:1"), "cuda:1")

    def test_normalize_dinov2_device_rejects_unknown_device(self):
        with self.assertRaises(ValueError):
            normalize_dinov2_device("gpu")

    def test_resolve_dinov2_device_uses_cuda_for_auto_when_available(self):
        torch = types.SimpleNamespace(
            cuda=types.SimpleNamespace(is_available=lambda: True, device_count=lambda: 2),
            backends=types.SimpleNamespace(),
        )

        self.assertEqual(resolve_dinov2_device(torch, "auto"), "cuda")
        self.assertEqual(resolve_dinov2_device(torch, "cuda:1"), "cuda:1")

    def test_resolve_dinov2_device_rejects_cuda_when_unavailable(self):
        torch = types.SimpleNamespace(
            cuda=types.SimpleNamespace(is_available=lambda: False, device_count=lambda: 0),
            backends=types.SimpleNamespace(),
        )

        with self.assertRaises(VisualEncoderUnavailable):
            resolve_dinov2_device(torch, "cuda")

    def test_load_dinov2_caches_dependency_errors_permanently(self):
        # Missing dependencies cannot be fixed at runtime, so the load must keep
        # failing fast with the cached message without attempting another import.
        visual.reset_dinov2_state_for_tests()
        self.addCleanup(visual.reset_dinov2_state_for_tests)
        visual._DINO_STATE.update({"device_config": "cpu", "deps_error": "missing torch"})

        with self.assertRaises(VisualEncoderUnavailable) as ctx:
            visual.load_dinov2("cpu")

        self.assertEqual(str(ctx.exception), "missing torch")
        self.assertEqual(visual._DINO_STATE.get("deps_error"), "missing torch")

    @unittest.skipIf(
        importlib.util.find_spec("transformers") is not None,
        "retry path would attempt a real model download when transformers is installed",
    )
    def test_load_dinov2_retries_after_transient_load_error(self):
        # A prior model-download failure (typically a network issue) must not be
        # cached as a permanent block; the next call should attempt to load again.
        visual.reset_dinov2_state_for_tests()
        self.addCleanup(visual.reset_dinov2_state_for_tests)
        visual._DINO_STATE.update({"device_config": "cpu", "load_error": "network down"})

        with self.assertRaises(VisualEncoderUnavailable):
            visual.load_dinov2("cpu")

        # The retry actually ran: without transformers the import fails, replacing the
        # transient load_error with a dependency error rather than echoing it back.
        self.assertIsNone(visual._DINO_STATE.get("load_error"))
        self.assertIn("deps_error", visual._DINO_STATE)


if __name__ == "__main__":
    unittest.main()
