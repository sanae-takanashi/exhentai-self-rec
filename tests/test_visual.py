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
    def test_siglip2_image_embeddings_uses_model_image_features(self):
        class FakeTensor:
            def __init__(self, values=None):
                self.values = values

            def to(self, _device):
                return self

            def detach(self):
                return self

            def float(self):
                return self

            def cpu(self):
                return self

            def tolist(self):
                return self.values

        class FakeModel:
            def parameters(self):
                yield types.SimpleNamespace(device="cpu")

            def get_image_features(self, **inputs):
                self.inputs = inputs
                return types.SimpleNamespace(pooler_output=FakeTensor([[3.0, 4.0]]))

        class NoGrad:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        class FakeImage:
            def convert(self, _mode):
                return self

        fake_image_module = types.SimpleNamespace(open=lambda _stream: FakeImage())

        model = FakeModel()
        processor = lambda **_kwargs: {"pixel_values": FakeTensor()}
        fake_torch = types.SimpleNamespace(no_grad=lambda: NoGrad())
        original = visual.load_siglip2
        visual.load_siglip2 = lambda _device=None: (processor, model, fake_torch, fake_image_module)
        self.addCleanup(lambda: setattr(visual, "load_siglip2", original))

        embeddings = visual.siglip2_image_embeddings([b"fake-image"], device="cpu")

        self.assertEqual(embeddings, [[0.6, 0.8]])
        self.assertIn("pixel_values", model.inputs)

    def test_normalize_dinov2_device_accepts_cpu_auto_and_cuda_index(self):
        self.assertEqual(normalize_dinov2_device(""), "auto")
        self.assertEqual(normalize_dinov2_device("AUTO"), "auto")
        self.assertEqual(normalize_dinov2_device("cuda:1"), "cuda:1")
        self.assertEqual(normalize_dinov2_device("rocm"), "cuda")
        self.assertEqual(normalize_dinov2_device("HIP:0"), "cuda:0")

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


    def test_download_dinov2_reports_missing_dependencies(self):
        # Without the optional ML stack installed, the download must fail fast with a
        # clear dependency message instead of attempting a network call.
        visual.reset_dinov2_state_for_tests()
        self.addCleanup(visual.reset_dinov2_state_for_tests)

        def fake_find_spec(name):
            return None if name in {"torch", "transformers", "huggingface_hub", "PIL"} else object()

        original = visual.importlib.util.find_spec
        visual.importlib.util.find_spec = fake_find_spec
        self.addCleanup(lambda: setattr(visual.importlib.util, "find_spec", original))

        with self.assertRaises(VisualEncoderUnavailable) as ctx:
            visual.download_dinov2("cpu")

        self.assertIn("dependencies are unavailable", str(ctx.exception))
        self.assertIn("deps_error", visual._DINO_STATE)

    def test_download_dinov2_records_retryable_download_failure(self):
        # A network failure during download is reported but left retryable so a later
        # attempt can still succeed once connectivity is restored.
        visual.reset_dinov2_state_for_tests()
        self.addCleanup(visual.reset_dinov2_state_for_tests)

        original_find_spec = visual.importlib.util.find_spec
        visual.importlib.util.find_spec = lambda name: object()
        self.addCleanup(lambda: setattr(visual.importlib.util, "find_spec", original_find_spec))

        def boom():
            raise RuntimeError("network down")

        original_download = visual._snapshot_download_dinov2
        visual._snapshot_download_dinov2 = boom
        self.addCleanup(lambda: setattr(visual, "_snapshot_download_dinov2", original_download))

        with self.assertRaises(VisualEncoderUnavailable) as ctx:
            visual.download_dinov2("cpu")

        self.assertIn("download failed", str(ctx.exception))
        self.assertIn("network down", str(ctx.exception))
        self.assertEqual(visual._DINO_STATE.get("device_config"), "cpu")
        self.assertIn("load_error", visual._DINO_STATE)
        self.assertNotIn("deps_error", visual._DINO_STATE)


if __name__ == "__main__":
    unittest.main()
