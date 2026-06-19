from __future__ import annotations

import io
import importlib.util
import math
import os
import threading
from typing import Any


SIMPLE_VISUAL_VERSION = "canvas-rgb-8x8-v1"
DINOV2_VISUAL_VERSION = "dinov2-small-cls-v1"
DEFAULT_VISUAL_ENCODER = os.environ.get("EXH_REC_VISUAL_ENCODER", "dinov2")
DINOV2_MODEL_NAME = os.environ.get("EXH_REC_DINOV2_MODEL", "facebook/dinov2-small")
DEFAULT_DINOV2_DEVICE = os.environ.get("EXH_REC_DINOV2_DEVICE", "auto")
_DINO_LOCK = threading.Lock()
_DINO_STATE: dict[str, Any] = {}


class VisualEncoderUnavailable(RuntimeError):
    pass


def normalize_visual_encoder(value: object) -> str:
    encoder = str(value or DEFAULT_VISUAL_ENCODER).strip().lower()
    if encoder in {"dinov2", "simple"}:
        return encoder
    raise ValueError("Visual encoder must be dinov2 or simple")


def normalize_embedding(values: list[object]) -> list[float]:
    floats: list[float] = []
    for value in values:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            raise ValueError("embedding values must be finite numbers") from None
        if math.isnan(parsed) or math.isinf(parsed):
            raise ValueError("embedding values must be finite numbers")
        floats.append(parsed)
    norm = math.sqrt(sum(value * value for value in floats))
    if norm <= 0:
        raise ValueError("embedding must not be all zero")
    return [round(value / norm, 6) for value in floats]


def average_embeddings(embeddings: list[list[float]]) -> list[float]:
    if not embeddings:
        raise ValueError("at least one embedding is required")
    length = len(embeddings[0])
    total = [0.0] * length
    count = 0
    for embedding in embeddings:
        if len(embedding) != length:
            continue
        for index, value in enumerate(embedding):
            total[index] += value
        count += 1
    if not count:
        raise ValueError("embeddings must share one dimension")
    return normalize_embedding([value / count for value in total])


def dinov2_available() -> bool:
    try:
        load_dinov2()
    except VisualEncoderUnavailable:
        return False
    return True


def normalize_dinov2_device(value: object) -> str:
    device = str(value or "auto").strip().lower()
    if not device:
        return "auto"
    if device in {"rocm", "hip"}:
        return "cuda"
    if device.startswith("rocm:") or device.startswith("hip:"):
        suffix = device.split(":", 1)[1]
        if suffix.isdigit():
            return f"cuda:{suffix}"
        raise ValueError("DINOv2 device must be auto, cpu, cuda, cuda:N, rocm, rocm:N, hip, hip:N, or mps")
    if device in {"auto", "cpu", "cuda", "mps"}:
        return device
    if re_fullmatch_cuda_device(device):
        return device
    raise ValueError("DINOv2 device must be auto, cpu, cuda, cuda:N, rocm, rocm:N, hip, hip:N, or mps")


def re_fullmatch_cuda_device(device: str) -> bool:
    if not device.startswith("cuda:"):
        return False
    suffix = device.split(":", 1)[1]
    return suffix.isdigit()


def dinov2_dependency_status(device: str | None = None) -> dict:
    requested_device = normalize_dinov2_device(device or DEFAULT_DINOV2_DEVICE)
    if _DINO_STATE.get("loaded") and _DINO_STATE.get("device_config") == requested_device:
        return {
            "available": True,
            "loaded": True,
            "model": DINOV2_MODEL_NAME,
            "device": _DINO_STATE.get("device"),
            "device_config": requested_device,
            "cuda_available": _DINO_STATE.get("cuda_available", False),
            "cuda_device_count": _DINO_STATE.get("cuda_device_count", 0),
            "cuda_device_name": _DINO_STATE.get("cuda_device_name"),
            "error": None,
        }
    if _DINO_STATE.get("deps_error") and _DINO_STATE.get("device_config") == requested_device:
        return {
            "available": False,
            "loaded": False,
            "model": DINOV2_MODEL_NAME,
            "device_config": requested_device,
            "error": _DINO_STATE["deps_error"],
        }
    missing = [
        name
        for name, module in {
            "torch": "torch",
            "torchvision": "torchvision",
            "transformers": "transformers",
            "Pillow": "PIL",
        }.items()
        if importlib.util.find_spec(module) is None
    ]
    device_status = torch_device_status(requested_device) if not missing and importlib.util.find_spec("torch") else {}
    device_error = device_status.get("error")
    # A prior model download/initialization failure (typically a network issue) is
    # kept only for reporting; it must not flip "available" off or it would prevent
    # the next attempt from retrying once the proxy or network is fixed.
    load_error = _DINO_STATE.get("load_error") if _DINO_STATE.get("device_config") == requested_device else None
    return {
        "available": not missing and not device_error,
        "loaded": False,
        "model": DINOV2_MODEL_NAME,
        "device": device_status.get("device"),
        "device_config": requested_device,
        "cuda_available": device_status.get("cuda_available", False),
        "cuda_device_count": device_status.get("cuda_device_count", 0),
        "cuda_device_name": device_status.get("cuda_device_name"),
        "missing": missing,
        "error": f"Missing optional dependencies: {', '.join(missing)}" if missing else (device_error or load_error),
    }


def torch_device_status(requested_device: str) -> dict:
    try:
        import torch
    except Exception:
        return {}
    cuda_available = bool(torch.cuda.is_available())
    cuda_device_count = int(torch.cuda.device_count()) if hasattr(torch.cuda, "device_count") else 0
    cuda_device_name = None
    if cuda_available and cuda_device_count:
        try:
            cuda_device_name = torch.cuda.get_device_name(0)
        except Exception:
            cuda_device_name = None
    try:
        device = resolve_dinov2_device(torch, requested_device)
        error = None
    except VisualEncoderUnavailable as exc:
        device = None
        error = str(exc)
    return {
        "device": device,
        "device_config": requested_device,
        "cuda_available": cuda_available,
        "cuda_device_count": cuda_device_count,
        "cuda_device_name": cuda_device_name,
        "error": error,
    }


def dinov2_embedding(image_blobs: list[bytes], device: str | None = None) -> list[float]:
    if not image_blobs:
        raise ValueError("at least one image is required")
    processor, model, torch, Image = load_dinov2(device)
    images = []
    for blob in image_blobs:
        image = Image.open(io.BytesIO(blob)).convert("RGB")
        images.append(image)
    inputs = processor(images=images, return_tensors="pt")
    device = next(model.parameters()).device
    inputs = {key: value.to(device) for key, value in inputs.items()}
    with torch.no_grad():
        output = model(**inputs)
    cls_tokens = output.last_hidden_state[:, 0, :]
    vector = cls_tokens.mean(dim=0)
    return normalize_embedding(vector.detach().cpu().tolist())


def load_dinov2(device: str | None = None) -> tuple[Any, Any, Any, Any]:
    requested_device = normalize_dinov2_device(device or DEFAULT_DINOV2_DEVICE)
    if _DINO_STATE.get("loaded") and _DINO_STATE.get("device_config") == requested_device:
        return _DINO_STATE["processor"], _DINO_STATE["model"], _DINO_STATE["torch"], _DINO_STATE["Image"]
    if _DINO_STATE.get("deps_error") and _DINO_STATE.get("device_config") == requested_device:
        raise VisualEncoderUnavailable(_DINO_STATE["deps_error"])
    with _DINO_LOCK:
        if _DINO_STATE.get("loaded") and _DINO_STATE.get("device_config") == requested_device:
            return _DINO_STATE["processor"], _DINO_STATE["model"], _DINO_STATE["torch"], _DINO_STATE["Image"]
        if _DINO_STATE.get("deps_error") and _DINO_STATE.get("device_config") == requested_device:
            raise VisualEncoderUnavailable(_DINO_STATE["deps_error"])
        try:
            import torch
            from PIL import Image
            from transformers import AutoImageProcessor, AutoModel
        except Exception as exc:
            # Missing optional dependencies cannot be fixed without reinstalling, so
            # this is cached as a hard failure that blocks future load attempts.
            _DINO_STATE.clear()
            _DINO_STATE["device_config"] = requested_device
            _DINO_STATE["deps_error"] = f"DINOv2 dependencies are unavailable: {exc}"
            raise VisualEncoderUnavailable(_DINO_STATE["deps_error"]) from exc

        try:
            resolved_device = resolve_dinov2_device(torch, requested_device)
            try:
                processor = AutoImageProcessor.from_pretrained(DINOV2_MODEL_NAME)
                model = AutoModel.from_pretrained(DINOV2_MODEL_NAME)
            except Exception:
                # The first attempt commonly fails because the cache is missing a
                # file (for example preprocessor_config.json after a network hiccup),
                # which transformers reports as the cryptic "Can't load image
                # processor" error. Re-download the model files once and retry so a
                # transient failure self-heals without manual intervention.
                _snapshot_download_dinov2()
                processor = AutoImageProcessor.from_pretrained(DINOV2_MODEL_NAME)
                model = AutoModel.from_pretrained(DINOV2_MODEL_NAME)
            model.to(resolved_device)
            model.eval()
        except Exception as exc:
            # Downloading/initializing the model can fail transiently (for example a
            # blocked network). Record it for status reporting but leave the state
            # retryable so a later call can succeed once the proxy/network is fixed.
            _DINO_STATE.clear()
            _DINO_STATE["device_config"] = requested_device
            _DINO_STATE["load_error"] = f"DINOv2 model is unavailable: {exc}"
            raise VisualEncoderUnavailable(_DINO_STATE["load_error"]) from exc

        cuda_available = bool(torch.cuda.is_available())
        cuda_device_count = int(torch.cuda.device_count()) if hasattr(torch.cuda, "device_count") else 0
        cuda_device_name = None
        if cuda_available and cuda_device_count:
            try:
                cuda_device_name = torch.cuda.get_device_name(0)
            except Exception:
                cuda_device_name = None
        _DINO_STATE.pop("load_error", None)
        _DINO_STATE.update(
            {
                "loaded": True,
                "device_config": requested_device,
                "device": resolved_device,
                "cuda_available": cuda_available,
                "cuda_device_count": cuda_device_count,
                "cuda_device_name": cuda_device_name,
                "processor": processor,
                "model": model,
                "torch": torch,
                "Image": Image,
            }
        )
        return processor, model, torch, Image


def _snapshot_download_dinov2() -> str:
    # Pull the full model repo (config + image-processor config + weights) into the
    # local Hugging Face cache. transformers' from_pretrained downloads the same
    # files lazily, but fetching them up front gives a single, clear failure point
    # instead of the misleading "Can't load image processor" message it raises when
    # an individual file (such as preprocessor_config.json) is missing.
    from huggingface_hub import snapshot_download

    return snapshot_download(
        DINOV2_MODEL_NAME,
        allow_patterns=["*.json", "*.txt", "*.safetensors", "*.bin", "*.model"],
    )


def download_dinov2(device: str | None = None) -> dict:
    """Ensure the DINOv2 model and image-processor files are present in the local
    cache, downloading them through the configured proxy if necessary.

    Returns a status dict on success. Raises VisualEncoderUnavailable with a clear
    message if the dependencies or network are unavailable, while leaving the
    in-memory state retryable so a later attempt can still succeed.
    """
    requested_device = normalize_dinov2_device(device or DEFAULT_DINOV2_DEVICE)
    missing = [
        name
        for name, module in {
            "torch": "torch",
            "transformers": "transformers",
            "huggingface_hub": "huggingface_hub",
            "Pillow": "PIL",
        }.items()
        if importlib.util.find_spec(module) is None
    ]
    if missing:
        message = f"DINOv2 dependencies are unavailable: missing {', '.join(missing)}"
        with _DINO_LOCK:
            _DINO_STATE.clear()
            _DINO_STATE["device_config"] = requested_device
            _DINO_STATE["deps_error"] = message
        raise VisualEncoderUnavailable(message)
    try:
        path = _snapshot_download_dinov2()
    except Exception as exc:
        message = f"DINOv2 model download failed: {exc}"
        if "socksio" in str(exc) or "httpx[socks]" in str(exc):
            # huggingface_hub downloads via httpx, which needs the socksio extra to
            # use a SOCKS proxy; PySocks only covers the app's own requests.
            message += " (install it with: python3 -m pip install socksio)"
        with _DINO_LOCK:
            if _DINO_STATE.get("device_config") != requested_device:
                _DINO_STATE.clear()
                _DINO_STATE["device_config"] = requested_device
            _DINO_STATE["load_error"] = message
        raise VisualEncoderUnavailable(message) from exc
    with _DINO_LOCK:
        if _DINO_STATE.get("device_config") == requested_device:
            _DINO_STATE.pop("load_error", None)
    return {
        "ok": True,
        "model": DINOV2_MODEL_NAME,
        "path": path,
        "device_config": requested_device,
    }


def resolve_dinov2_device(torch: Any, requested_device: str) -> str:
    requested_device = normalize_dinov2_device(requested_device)
    if requested_device == "auto":
        if torch.cuda.is_available():
            return "cuda"
        if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            return "mps"
        return "cpu"
    if requested_device.startswith("cuda"):
        if not torch.cuda.is_available():
            raise VisualEncoderUnavailable("CUDA was requested for DINOv2, but torch.cuda.is_available() is false")
        if ":" in requested_device:
            index = int(requested_device.split(":", 1)[1])
            if index >= int(torch.cuda.device_count()):
                raise VisualEncoderUnavailable(f"CUDA device {requested_device} is not available")
        return requested_device
    if requested_device == "mps":
        if not (hasattr(torch.backends, "mps") and torch.backends.mps.is_available()):
            raise VisualEncoderUnavailable("MPS was requested for DINOv2, but it is not available")
        return "mps"
    return "cpu"


def reset_dinov2_state_for_tests() -> None:
    _DINO_STATE.clear()
