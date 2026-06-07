import os
import traceback
import torch
from faster_whisper import WhisperModel

from config import BASE_PATH, logger

# Глобальная ссылка — заполняется в startup()
whisper_model: WhisperModel | None = None


def _init_whisper_model() -> WhisperModel:
    """Загружает модель Whisper (вызывается один раз при старте в executor)."""
    model_path = os.path.join(BASE_PATH, "models", "medium")
    model_ref = model_path if os.path.isdir(model_path) else "medium"
    local_only = os.path.isdir(model_path)

    # Диагностика torch / CUDA
    logger.info(f"Whisper INIT: torch version = {torch.__version__}")
    logger.info(f"Whisper INIT: CUDA available = {torch.cuda.is_available()}")
    if torch.cuda.is_available():
        logger.info(f"Whisper INIT: GPU = {torch.cuda.get_device_name(0)}")
        logger.info(
            f"Whisper INIT: VRAM = {torch.cuda.get_device_properties(0).total_memory // 1024 ** 2} MB"
        )

    if torch.cuda.is_available():
        device, compute_type = "cuda", "float16"
    else:
        device, compute_type = "cpu", "int8"

    logger.info(f"Whisper INIT: загрузка модели '{model_ref}' на {device} ({compute_type})")
    try:
        model = WhisperModel(
            model_ref, device=device, compute_type=compute_type, local_files_only=local_only
        )
        logger.info(f"Whisper INIT: модель успешно загружена на {device.upper()}")
        return model
    except Exception:
        logger.error(
            f"Whisper INIT: не удалось загрузить на {device}, пробуем CPU\n{traceback.format_exc()}"
        )
        model = WhisperModel(model_ref, device="cpu", compute_type="int8", local_files_only=local_only)
        logger.info("Whisper INIT: модель загружена на CPU (fallback)")
        return model
