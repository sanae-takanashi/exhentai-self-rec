from __future__ import annotations

import io
import importlib.util
import math
import os
import threading
from typing import Any


SIMPLE_VISUAL_VERSION = "canvas-rgb-8x8-v1"
DINOV2_VISUAL_VERSION = "dinov2-small-cls-v1"
DEFAULT_VISUAL_ENCODER = "dinov2"
DINOV2_MODEL_NAME = os.environ.get("EXH_REC_DINOV2_MODEL", "facebook/dinov2-small")
_DINO_LOCK = threading.Lock()
_DINO_STATE: dict[str, Any] = {}


class VisualEncoderUnavailable(RuntimeError):
    pass


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


def dinov2_dependency_status() -> dict:
    if _DINO_STATE.get("loaded"):
        return {"available": True, "loaded": True, "model": DINOV2_MODEL_NAME, "error": None}
    if _DINO_STATE.get("error"):
        return {"available": False, "loaded": False, "model": DINOV2_MODEL_NAME, "error": _DINO_STATE["error"]}
    missing = [
        name
        for name, module in {
            "torch": "torch",
            "transformers": "transformers",
            "Pillow": "PIL",
        }.items()
        if importlib.util.find_spec(module) is None
    ]
    return {
        "available": not missing,
        "loaded": False,
        "model": DINOV2_MODEL_NAME,
        "missing": missing,
        "error": None if not missing else f"Missing optional dependencies: {', '.join(missing)}",
    }


def dinov2_embedding(image_blobs: list[bytes]) -> list[float]:
    if not image_blobs:
        raise ValueError("at least one image is required")
    processor, model, torch, Image = load_dinov2()
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


def load_dinov2() -> tuple[Any, Any, Any, Any]:
    if _DINO_STATE.get("loaded"):
        return _DINO_STATE["processor"], _DINO_STATE["model"], _DINO_STATE["torch"], _DINO_STATE["Image"]
    if _DINO_STATE.get("error"):
        raise VisualEncoderUnavailable(_DINO_STATE["error"])
    with _DINO_LOCK:
        if _DINO_STATE.get("loaded"):
            return _DINO_STATE["processor"], _DINO_STATE["model"], _DINO_STATE["torch"], _DINO_STATE["Image"]
        try:
            import torch
            from PIL import Image
            from transformers import AutoImageProcessor, AutoModel
        except Exception as exc:
            _DINO_STATE["error"] = f"DINOv2 dependencies are unavailable: {exc}"
            raise VisualEncoderUnavailable(_DINO_STATE["error"]) from exc

        try:
            processor = AutoImageProcessor.from_pretrained(DINOV2_MODEL_NAME)
            model = AutoModel.from_pretrained(DINOV2_MODEL_NAME)
            device = "cuda" if torch.cuda.is_available() else "cpu"
            model.to(device)
            model.eval()
        except Exception as exc:
            _DINO_STATE["error"] = f"DINOv2 model is unavailable: {exc}"
            raise VisualEncoderUnavailable(_DINO_STATE["error"]) from exc

        _DINO_STATE.update(
            {
                "loaded": True,
                "processor": processor,
                "model": model,
                "torch": torch,
                "Image": Image,
            }
        )
        return processor, model, torch, Image


def reset_dinov2_state_for_tests() -> None:
    _DINO_STATE.clear()
