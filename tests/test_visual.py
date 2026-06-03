import types
import unittest

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


if __name__ == "__main__":
    unittest.main()
