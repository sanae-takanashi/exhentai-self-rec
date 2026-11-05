"""Fixed, secret-free environment probe run by the selected service Python."""
import importlib.metadata
import json
import platform
import re


def probe():
    packages = {}
    for name in ("numpy", "scipy", "scikit-learn", "joblib", "Pillow", "torch", "torchvision", "transformers", "PySocks"):
        try:
            value = importlib.metadata.version(name)
            packages[name] = value if re.fullmatch(r"[A-Za-z0-9.+_-]{1,80}", value) else "installed"
        except importlib.metadata.PackageNotFoundError:
            packages[name] = "missing"
    accelerator = "not-installed"
    if packages["torch"] != "missing":
        try:
            import torch
            accelerator = ("rocm" if torch.version.hip else "cuda") if torch.cuda.is_available() else "cpu"
        except Exception:
            accelerator = "unavailable"
    return {"schema": 1, "python": platform.python_version(), "packages": packages, "accelerator": accelerator}


if __name__ == "__main__":
    print(json.dumps(probe()))
