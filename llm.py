"""
llm.py — локальная LLM Qwen2.5-0.5B-Instruct (GGUF) через llama-cpp-python.

Две задачи:
  1. correct_transcript()  — исправление ошибок ASR после Whisper
  2. refine_translation()  — улучшение машинного перевода после NLLB

Модель загружается один раз при старте в глобальный qwen_model.
"""

from __future__ import annotations

import os
import traceback
from typing import Optional

from config import BASE_PATH, logger

MODEL_PATH = os.path.join(
    BASE_PATH, "models", "qwen2.5-1.5b-instruct-q4_k_m.gguf"
)

# Контекст: 0.5B модель держит 4096 токенов комфортно
N_CTX        = 4096
# Слоёв на GPU: -1 = все; 0 = только CPU
N_GPU_LAYERS = -1 if True else 0   # поменяй на 0 если нет CUDA

# Максимум токенов в ответе модели
MAX_TOKENS_CORRECTION  = 1024
MAX_TOKENS_TRANSLATION = 1024

# Размер одного чанка текста перед отправкой в LLM (в символах).
# При 0.5B модели большие тексты дают деградацию качества — делим на куски.
CHUNK_CHARS = 1500

# Глобальная ссылка — заполняется в startup()
qwen_model: Optional[object] = None


# =============================================================================
# ИНИЦИАЛИЗАЦИЯ
# =============================================================================

def _init_qwen():
    from llama_cpp import Llama
    if not os.path.isfile(MODEL_PATH):
        raise FileNotFoundError(
            f"Модель Qwen не найдена: {MODEL_PATH}\n"
            f"Скачайте файл с https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct-GGUF "
            f"и положите в {MODEL_PATH}"
        )

    logger.info(f"Qwen INIT: загрузка {MODEL_PATH} ...")
    model = Llama(
        model_path=MODEL_PATH,
        n_ctx=N_CTX,
        n_gpu_layers=N_GPU_LAYERS,
        verbose=False,
    )
    logger.info("Qwen INIT: модель готова")
    return model


# =============================================================================
# ВСПОМОГАТЕЛЬНОЕ
# =============================================================================

def _chat(system: str, user: str, max_tokens: int) -> str:
    """Отправляет один запрос в модель, возвращает текст ответа."""
    response = qwen_model.create_chat_completion(
        messages=[
            {"role": "system", "content": system},
            {"role": "user",   "content": user},
        ],
        max_tokens=max_tokens,
        temperature=0.1,      # низкая температура = стабильность, без фантазий
        top_p=0.9,
    )
    return response["choices"][0]["message"]["content"].strip()


def _split_chunks(text: str, chunk_chars: int) -> list[str]:
    """
    Делит текст на чанки по chunk_chars символов, стараясь не разрывать строки.
    """
    lines  = text.splitlines(keepends=True)
    chunks: list[str] = []
    current = ""

    for line in lines:
        if len(current) + len(line) > chunk_chars and current:
            chunks.append(current)
            current = ""
        current += line

    if current:
        chunks.append(current)

    return chunks


# =============================================================================
# 1. ИСПРАВЛЕНИЕ ОШИБОК ТРАНСКРИБАЦИИ
# =============================================================================

SYSTEM_CORRECTION = (
    "You are an expert transcription editor. "
    "Your task is to fix automatic speech recognition (ASR) errors in the provided text: "
    "correct misspellings, restore punctuation, fix grammar, and remove filler words. "
    "Keep timestamps like '[12.40s -> 15.80s] [vi]' unchanged. "
    "Return ONLY the corrected text, no explanations."
)


def correct_transcript(text: str) -> str:
    """
    Прогоняет транскрибацию через LLM для исправления ASR-ошибок.
    Обрабатывает текст чанками, чтобы не выходить за контекст модели.
    """
    if qwen_model is None:
        logger.warning("Qwen: модель не загружена, пропускаем коррекцию")
        return text

    try:
        logger.info(f"Qwen correction: символов={len(text)}")
        chunks   = _split_chunks(text, CHUNK_CHARS)
        corrected_chunks: list[str] = []

        for chunk_number, chunk in enumerate(chunks, 1):
            logger.info(f"Qwen correction: чанк {chunk_number}/{len(chunks)}")
            corrected = _chat(
                system=SYSTEM_CORRECTION,
                user=f"Fix ASR errors in the following transcription:\n\n{chunk}",
                max_tokens=MAX_TOKENS_CORRECTION,
            )
            corrected_chunks.append(corrected)

        corrected_text = "\n".join(corrected_chunks)
        logger.info("Qwen correction: завершено")
        return corrected_text

    except Exception:
        logger.error(f"Qwen correction error:\n{traceback.format_exc()}")
        return text   # возвращаем оригинал при ошибке
