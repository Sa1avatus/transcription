import os
import traceback
import ctranslate2
from faster_whisper import WhisperModel

from config import BASE_PATH, logger, settings

# Глобальная ссылка — заполняется в startup()
whisper_model: WhisperModel | None = None


def _init_whisper_model() -> WhisperModel:
    """Загружает модель Whisper (вызывается один раз при старте в executor)."""
    model_path = os.path.join(BASE_PATH, "models", settings.whisper_model_size)
    model_ref = model_path if os.path.isdir(model_path) else settings.whisper_model_size
    local_only = os.path.isdir(model_path)

    # faster-whisper runs on CTranslate2 and does not require PyTorch.
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
        return model
    except Exception:
        logger.error(
            f"Whisper INIT: не удалось загрузить на {device}, пробуем CPU\n{traceback.format_exc()}"
        )
        model = WhisperModel(model_ref, device="cpu", compute_type="int8", local_files_only=local_only)
        logger.info("Whisper INIT: модель загружена на CPU (fallback)")
        return model
