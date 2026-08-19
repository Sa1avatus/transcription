"""Whisper model lifecycle: load, switch, list, download.

The module exposes a small public API consumed by ``routes.py`` and the worker:

* ``whisper_model``        – the currently loaded ``WhisperModel`` instance
* ``get_model_info()``     – metadata about the running model
* ``switch_model(size)``   – hot-swap the active model (unloads old one)
* ``list_models()``        – available + downloaded models
* ``download_model(size)`` – pull a model from HuggingFace Hub
"""

import os
import asyncio
import traceback
import time
from pathlib import Path
from typing import Optional

from config import BASE_PATH, logger, settings

# Lazy imports for ML dependencies (not available in API-only container)
ctranslate2 = None
WhisperModel = None


def _ensure_ml_deps():
    """Import ML dependencies on demand (only in worker container)."""
    global ctranslate2, WhisperModel
    if ctranslate2 is None:
        import ctranslate2 as _ct2
        ctranslate2 = _ct2
    if WhisperModel is None:
        from faster_whisper import WhisperModel as _wm
        WhisperModel = _wm


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# All model sizes supported by faster-whisper (CTranslate2 format).
# The HuggingFace repo pattern is ``Systran/faster-whisper-{size}``.
MODEL_SIZES: list[str] = [
    "tiny",
    "base",
    "small",
    "medium",
    "large-v1",
    "large-v2",
    "large-v3",
    "distil-large-v2",
    "distil-large-v3",
    "distil-medium",
    "distil-small",
]

# Approximate disk sizes for display (GB).
MODEL_DISK_SIZE: dict[str, str] = {
    "tiny": "~75 MB",
    "base": "~150 MB",
    "small": "~500 MB",
    "medium": "~1.5 GB",
    "large-v1": "~3 GB",
    "large-v2": "~3 GB",
    "large-v3": "~3 GB",
    "distil-large-v2": "~1.5 GB",
    "distil-large-v3": "~1.5 GB",
    "distil-medium": "~800 MB",
    "distil-small": "~350 MB",
}

# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

whisper_model = None  # WhisperModel | None (lazy type)
_current_model_size: str | None = None
_current_device: str | None = None
_current_compute_type: str | None = None
_model_load_time: float | None = None
_switch_lock = asyncio.Lock()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _models_dir() -> Path:
    return Path(BASE_PATH) / "models"


def _local_model_path(size: str) -> Path:
    return _models_dir() / size


def _is_downloaded(size: str) -> bool:
    p = _local_model_path(size)
    return p.is_dir() and any(p.iterdir())


def _init_whisper_model(model_size: str | None = None):
    """Load a Whisper model. Called once at startup or on hot-swap."""
    _ensure_ml_deps()
    size = model_size or settings.whisper_model_size
    model_path = _local_model_path(size)
    model_ref = str(model_path) if model_path.is_dir() else f"Systran/faster-whisper-{size}"
    local_only = model_path.is_dir()

    cuda_available = ctranslate2.get_cuda_device_count() > 0
    logger.info(f"Whisper INIT: CTranslate2 CUDA devices = {ctranslate2.get_cuda_device_count()}")

    if cuda_available:
        device, compute_type = "cuda", "float16"
    else:
        device, compute_type = "cpu", "int8"

    logger.info(f"Whisper INIT: загрузка модели '{model_ref}' на {device} ({compute_type})")
    try:
        model = WhisperModel(
            model_ref, device=device, compute_type=compute_type, local_files_only=local_only
        )
        logger.info(f"Whisper INIT: модель успешно загружена на {device.upper()}")
        return model, device, compute_type
    except Exception:
        logger.error(
            f"Whisper INIT: не удалось загрузить на {device}, пробуем CPU\n{traceback.format_exc()}"
        )
        model = WhisperModel(model_ref, device="cpu", compute_type="int8", local_files_only=local_only)
        logger.info("Whisper INIT: модель загружена на CPU (fallback)")
        return model, "cpu", "int8"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_model_info() -> dict:
    """Return metadata about the currently loaded model."""
    global _current_model_size, _current_device, _current_compute_type, _model_load_time
    cuda_count = 0
    try:
        _ensure_ml_deps()
        cuda_count = ctranslate2.get_cuda_device_count()
    except Exception:
        pass
    return {
        "model_size": _current_model_size or settings.whisper_model_size,
        "device": _current_device or "unknown",
        "compute_type": _current_compute_type or "unknown",
        "loaded": whisper_model is not None,
        "load_time_s": round(time.time() - _model_load_time, 2) if _model_load_time else None,
        "cuda_devices": cuda_count,
        "models_dir": str(_models_dir()),
    }


def list_models() -> list[dict]:
    """Return all known models with download status."""
    current = _current_model_size or settings.whisper_model_size
    result = []
    for size in MODEL_SIZES:
        downloaded = _is_downloaded(size)
        result.append({
            "size": size,
            "active": size == current and whisper_model is not None,
            "downloaded": downloaded,
            "disk_size": MODEL_DISK_SIZE.get(size, "unknown"),
            "path": str(_local_model_path(size)) if downloaded else None,
        })
    return result


async def switch_model(new_size: str) -> dict:
    """Hot-swap the active Whisper model. Thread-safe via lock."""
    global whisper_model, _current_model_size, _current_device, _current_compute_type, _model_load_time

    if new_size not in MODEL_SIZES:
        return {"ok": False, "error": f"Unknown model size: {new_size}"}

    async with _switch_lock:
        old_size = _current_model_size
        if new_size == old_size and whisper_model is not None:
            return {"ok": True, "message": f"Model '{new_size}' is already active", "switched": False}

        loop = asyncio.get_running_loop()
        try:
            model, device, compute_type = await loop.run_in_executor(
                None, _init_whisper_model, new_size
            )
            # Swap
            whisper_model = model
            _current_model_size = new_size
            _current_device = device
            _current_compute_type = compute_type
            _model_load_time = time.time()
            logger.info(f"Whisper SWITCH: {old_size} -> {new_size} ({device}/{compute_type})")
            return {
                "ok": True,
                "message": f"Switched from '{old_size}' to '{new_size}'",
                "previous": old_size,
                "current": new_size,
                "device": device,
                "compute_type": compute_type,
                "switched": True,
            }
        except Exception as e:
            logger.error(f"Whisper SWITCH failed: {traceback.format_exc()}")
            return {"ok": False, "error": str(e)}


async def download_model(size: str) -> dict:
    """Download a model from HuggingFace Hub to the local models directory."""
    if size not in MODEL_SIZES:
        return {"ok": False, "error": f"Unknown model size: {size}"}

    if _is_downloaded(size):
        return {"ok": True, "message": f"Model '{size}' is already downloaded", "downloaded": False}

    loop = asyncio.get_running_loop()
    try:
        # Ensure models directory exists
        _models_dir().mkdir(parents=True, exist_ok=True)

        def _do_download():
            _ensure_ml_deps()
            model_ref = f"Systran/faster-whisper-{size}"
            logger.info(f"Whisper DOWNLOAD: starting '{model_ref}'")
            # faster-whisper downloads automatically; we just need to load with local_files_only=False
            WhisperModel(model_ref, device="cpu", compute_type="int8", local_files_only=False)
            logger.info(f"Whisper DOWNLOAD: '{size}' complete")
            return True

        await loop.run_in_executor(None, _do_download)
        return {"ok": True, "message": f"Model '{size}' downloaded successfully", "downloaded": True}
    except Exception as e:
        logger.error(f"Whisper DOWNLOAD failed for '{size}': {traceback.format_exc()}")
        return {"ok": False, "error": str(e)}


def startup_load() -> None:
    """Called once during application startup to load the initial model."""
    global whisper_model, _current_model_size, _current_device, _current_compute_type, _model_load_time

    model, device, compute_type = _init_whisper_model()
    whisper_model = model
    _current_model_size = settings.whisper_model_size
    _current_device = device
    _current_compute_type = compute_type
    _model_load_time = time.time()
