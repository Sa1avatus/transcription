"""
translation.py — перевод с поддержкой трёх бэкендов.

Бэкенд выбирается через settings.TRANSLATION_BACKEND:
  "nllb_600m"   — facebook/nllb-200-distilled-600M  (локально, ~1.2 ГБ VRAM)
  "nllb_1300m"  — facebook/nllb-200-1.3B            (локально, ~2.5 ГБ VRAM)
  "deepl"       — DeepL API (требует settings.DEEPL_API_KEY, 500к символов/мес бесплатно)

Добавить в settings.py:
  TRANSLATION_BACKEND = "nllb_600m"   # или "nllb_1300m" / "deepl"
  DEEPL_API_KEY       = "xxxxxxxx-...-...-...-xxxxxxxxxxxx:fx"  # только для deepl
"""

import os
import re
import traceback
from abc import ABC, abstractmethod
from dataclasses import dataclass

import torch
import httpx
import settings

from config import BASE_PATH, logger

# =============================================================================
# КОНСТАНТЫ
# =============================================================================

MAX_CHUNK_TOKENS = 512
MAX_NEW_TOKENS   = 1024
LINES_PER_GROUP  = 6      # строк в одном запросе к NLLB (контекст между репликами)

NLLB_MODELS = {
    "nllb_600m":  "facebook/nllb-200-distilled-600M",
    "nllb_1300m": "facebook/nllb-200-1.3B",
}

# Глобальная ссылка на активный бэкенд — заполняется в startup()
nllb_pipeline = None   # на самом деле BaseTranslator, название сохранено для совместимости


# =============================================================================
# СПРАВОЧНИК ЯЗЫКОВ
# =============================================================================

@dataclass
class LangInfo:
    code:      str    # NLLB-код
    deepl_src: str    # код DeepL для источника (None = не поддерживается как источник)
    deepl_tgt: str    # код DeepL для цели
    label:     str
    flag:      str

LANGUAGES: dict[str, LangInfo] = {
    "eng_Latn": LangInfo("eng_Latn", "EN",   "EN-GB", "English",    "🇬🇧"),
    "rus_Cyrl": LangInfo("rus_Cyrl", "RU",   "RU",    "Русский",    "🇷🇺"),
    "vie_Latn": LangInfo("vie_Latn", "VI",   "VI",    "Tiếng Việt", "🇻🇳"),
    "zho_Hans": LangInfo("zho_Hans", "ZH",   "ZH",    "中文",        "🇨🇳"),
    "fra_Latn": LangInfo("fra_Latn", "FR",   "FR",    "Français",   "🇫🇷"),
    "deu_Latn": LangInfo("deu_Latn", "DE",   "DE",    "Deutsch",    "🇩🇪"),
    "kor_Hang": LangInfo("kor_Hang", "KO",   "KO",    "한국어",       "🇰🇷"),
    "jpn_Jpan": LangInfo("jpn_Jpan", "JA",   "JA",    "日本語",      "🇯🇵"),
}

DEFAULT_SRC_LANG = "vie_Latn"


# =============================================================================
# АБСТРАКТНЫЙ ИНТЕРФЕЙС
# =============================================================================

class BaseTranslator(ABC):
    @abstractmethod
    def translate_text(self, text: str, src_lang: str, tgt_lang: str) -> str:
        """Переводит один текстовый блок. Синхронный вызов."""

    def translate_lines(self, lines: list[str], src_lang: str, tgt_lang: str) -> list[str]:
        """
        Переводит список строк группами по LINES_PER_GROUP.
        Строки внутри группы объединяются через ' | ' — модель видит контекст соседних реплик.
        """
        SEP = " | "
        translated: list[str] = []

        for i in range(0, len(lines), LINES_PER_GROUP):
            group = lines[i: i + LINES_PER_GROUP]
            joined = SEP.join(group)

            out   = self.translate_text(joined, src_lang, tgt_lang)
            parts = [p.strip() for p in out.split(SEP)]

            if len(parts) == len(group):
                translated.extend(parts)
            else:
                # Разделитель потерялся — отдаём блок целиком
                translated.append(out.strip())

        return translated


# =============================================================================
# БЭКЕНД 1 и 2: NLLB (600M и 1.3B)
# =============================================================================

class NllbTranslator(BaseTranslator):
    """Локальный перевод через facebook/nllb-200-* без использования pipeline."""

    def __init__(self, model, tokenizer, device: torch.device):
        self.model     = model
        self.tokenizer = tokenizer
        self.device    = device

    def translate_text(self, text: str, src_lang: str, tgt_lang: str) -> str:
        self.tokenizer.src_lang = src_lang
        inputs = self.tokenizer(
            text,
            return_tensors="pt",
            truncation=True,
            max_length=MAX_CHUNK_TOKENS,
        )
        inputs = {k: v.to(self.device) for k, v in inputs.items()}
        forced_bos = self.tokenizer.convert_tokens_to_ids(tgt_lang)
        with torch.no_grad():
            out = self.model.generate(
                **inputs,
                forced_bos_token_id=forced_bos,
                max_new_tokens=MAX_NEW_TOKENS,
                num_beams=4,
                repetition_penalty=1.3,
                no_repeat_ngram_size=3,
                early_stopping=True,
            )
        return self.tokenizer.decode(out[0], skip_special_tokens=True)


def _init_nllb(backend: str) -> NllbTranslator:
    from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

    model_name  = NLLB_MODELS[backend]
    local_path  = os.path.join(BASE_PATH, "models", model_name.split("/")[-1])
    use_cuda    = torch.cuda.is_available()
    device      = torch.device("cuda" if use_cuda else "cpu")
    dtype       = torch.float16 if use_cuda else torch.float32

    local_ok = (
        os.path.isdir(local_path)
        and any(f.endswith((".bin", ".safetensors")) for f in os.listdir(local_path))
    )

    source      = local_path if local_ok else model_name
    extra       = {"local_files_only": True} if local_ok else {"cache_dir": local_path}

    if local_ok:
        logger.info(f"NLLB INIT [{backend}]: локальная модель найдена в {local_path}")
    else:
        os.makedirs(local_path, exist_ok=True)
        logger.info(f"NLLB INIT [{backend}]: скачиваем '{model_name}' → {local_path} ...")

    tokenizer = AutoTokenizer.from_pretrained(source, **extra)
    model = (
        AutoModelForSeq2SeqLM
        .from_pretrained(source, dtype=dtype, **extra)
        .to(device)
    )
    model.eval()
    model.generation_config.max_length = None   # убираем конфликт с max_new_tokens

    if not local_ok:
        logger.info(f"NLLB INIT [{backend}]: сохраняем модель локально ...")
        tokenizer.save_pretrained(local_path)
        model.save_pretrained(local_path)

    logger.info(f"NLLB INIT [{backend}]: готово на {'GPU' if use_cuda else 'CPU'}")
    return NllbTranslator(model, tokenizer, device)


# =============================================================================
# БЭКЕНД 3: DeepL
# =============================================================================

DEEPL_FREE_URL = "https://api-free.deepl.com/v2/translate"
DEEPL_PAID_URL = "https://api.deepl.com/v2/translate"


class DeepLTranslator(BaseTranslator):
    """Перевод через DeepL API. Бесплатный план: 500к символов/мес."""

    def __init__(self, api_key: str):
        self.api_key = api_key
        # Ключи бесплатного плана заканчиваются на :fx
        self.url = DEEPL_FREE_URL if api_key.endswith(":fx") else DEEPL_PAID_URL
        logger.info(f"DeepL INIT: {'free' if api_key.endswith(':fx') else 'paid'} plan, url={self.url}")

    def _nllb_to_deepl(self, nllb_code: str, as_target: bool = False) -> str:
        info = LANGUAGES.get(nllb_code)
        if not info:
            raise ValueError(f"Неизвестный язык: {nllb_code}")
        return info.deepl_tgt if as_target else info.deepl_src

    def translate_text(self, text: str, src_lang: str, tgt_lang: str) -> str:
        src = self._nllb_to_deepl(src_lang, as_target=False)
        tgt = self._nllb_to_deepl(tgt_lang, as_target=True)

        resp = httpx.post(
            self.url,
            headers={"Authorization": f"DeepL-Auth-Key {self.api_key}"},
            json={
                "text":        [text],
                "source_lang": src,
                "target_lang": tgt,
            },
            timeout=30.0,
            verify=False,
        )
        resp.raise_for_status()
        return resp.json()["translations"][0]["text"]

    def translate_lines(self, lines: list[str], src_lang: str, tgt_lang: str) -> list[str]:
        """
        DeepL поддерживает массив текстов в одном запросе — используем это
        вместо join/split: каждая строка отдельным элементом массива.
        """
        src = self._nllb_to_deepl(src_lang, as_target=False)
        tgt = self._nllb_to_deepl(tgt_lang, as_target=True)

        # DeepL принимает до 50 строк за раз
        results: list[str] = []
        BATCH = 50
        for i in range(0, len(lines), BATCH):
            batch = lines[i: i + BATCH]
            resp = httpx.post(
                self.url,
                headers={"Authorization": f"DeepL-Auth-Key {self.api_key}"},
                json={
                    "text":        batch,
                    "source_lang": src,
                    "target_lang": tgt,
                },
                timeout=60.0,
                verify=False,
            )
            resp.raise_for_status()
            results.extend(t["text"] for t in resp.json()["translations"])

        return results


def _init_deepl() -> DeepLTranslator:
    api_key = getattr(settings, "DEEPL_API_KEY", "")
    if not api_key:
        raise ValueError("DEEPL_API_KEY не задан в settings.py")
    return DeepLTranslator(api_key)


# =============================================================================
# БЭКЕНД 4: Google Gemini (AI Studio)
# =============================================================================

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
GEMINI_DEFAULT_MODEL = "gemini-2.0-flash"


class GeminiTranslator(BaseTranslator):
    """
    Перевод через Google AI Studio (Gemini API).
    Бесплатный лимит: 15 запросов/мин, 1500 запросов/день, 1М токенов/мин.
    Ключ: https://aistudio.google.com/apikey
    """

    def __init__(self, api_key: str, model: str = GEMINI_DEFAULT_MODEL):
        self.api_key = api_key
        self.url     = GEMINI_URL.format(model=model)
        logger.info(f"Gemini INIT: модель={model}")

    def _lang_label(self, nllb_code: str) -> str:
        info = LANGUAGES.get(nllb_code)
        return info.label if info else nllb_code

    def _call(self, prompt: str) -> str:
        import time
        delays = [5, 15, 30]   # секунд между попытками при 429 или обрыве
        for attempt, delay in enumerate(delays + [None], 1):
            try:
                resp = httpx.post(
                    self.url,
                    params={"key": self.api_key},
                    json={"contents": [{"parts": [{"text": prompt}]}]},
                    timeout=60.0,
                    verify=False,
                )
                if resp.status_code == 429:
                    if delay is None:
                        resp.raise_for_status()
                    logger.warning(f"Gemini 429: попытка {attempt}, ждём {delay}с...")
                    time.sleep(delay)
                    continue
                resp.raise_for_status()
                return resp.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
            except (httpx.ConnectError, httpx.ReadError, httpx.RemoteProtocolError) as e:
                if delay is None:
                    raise
                logger.warning(f"Gemini сетевая ошибка ({e.__class__.__name__}): попытка {attempt}, ждём {delay}с...")
                time.sleep(delay)
        raise RuntimeError("Gemini: превышен лимит попыток")

    def translate_text(self, text: str, src_lang: str, tgt_lang: str) -> str:
        src_label = self._lang_label(src_lang)
        tgt_label = self._lang_label(tgt_lang)
        prompt = (
            f"Translate the following {src_label} text to {tgt_label}.\n"
            f"Return ONLY the translated text, no explanations or notes.\n\n"
            f"{text}"
        )
        return self._call(prompt)

    def translate_lines(self, lines: list[str], src_lang: str, tgt_lang: str) -> list[str]:
        """
        Отправляет все строки одним запросом — Gemini хорошо держит контекст диалога.
        Строки нумеруются чтобы модель вернула их в том же порядке.
        """
        src_label = self._lang_label(src_lang)
        tgt_label = self._lang_label(tgt_lang)

        numbered = "\n".join(f"{i+1}. {line}" for i, line in enumerate(lines))
        prompt = (
            f"You are translating a phone call transcript from {src_label} to {tgt_label}.\n"
            f"Translate each numbered line. Keep the same numbering. "
            f"Return ONLY the translated numbered lines, nothing else.\n\n"
            f"{numbered}"
        )

        raw = self._call(prompt)

        # Парсим пронумерованный ответ
        result: list[str] = []
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            # Убираем номер "1. ", "1) ", "1- " и т.п.
            cleaned = re.sub(r'^\d+[.)\-]\s*', '', line)
            if cleaned:
                result.append(cleaned)

        # Если количество не совпало — возвращаем как есть
        if len(result) != len(lines):
            logger.warning(
                f"Gemini: ожидалось {len(lines)} строк, получено {len(result)} — возвращаем блоком"
            )
            return [raw]

        return result


def _init_gemini() -> GeminiTranslator:
    api_key = getattr(settings, "GEMINI_API_KEY", "")
    if not api_key:
        raise ValueError("GEMINI_API_KEY не задан в settings.py")
    model = getattr(settings, "GEMINI_MODEL", GEMINI_DEFAULT_MODEL)
    return GeminiTranslator(api_key, model)


# =============================================================================
# ФАБРИКА — вызывается из app.py
# =============================================================================

def _init_translator() -> BaseTranslator:
    backend = getattr(settings, "TRANSLATION_BACKEND", "nllb_600m").lower()
    logger.info(f"Translation INIT: бэкенд = '{backend}'")

    if backend in ("nllb_600m", "nllb_1300m"):
        return _init_nllb(backend)
    elif backend == "deepl":
        return _init_deepl()
    elif backend == "gemini":
        return _init_gemini()
    else:
        raise ValueError(
            f"Неизвестный TRANSLATION_BACKEND='{backend}'. "
            f"Допустимые значения: nllb_600m, nllb_1300m, deepl, gemini"
        )


# =============================================================================
# ВСПОМОГАТЕЛЬНЫЕ
# =============================================================================

def _strip_timestamps(text: str) -> list[str]:
    """Убирает таймкоды '[12.40s -> 15.80s] [vi]', возвращает список строк."""
    lines = []
    for line in text.splitlines():
        cleaned = re.sub(r'\[\d+\.\d+s\s*->\s*\d+\.\d+s\]\s*', '', line)
        cleaned = re.sub(r'\[[a-z]{2,3}(_[A-Za-z]{4})?\]\s*', '', cleaned).strip()
        if cleaned:
            lines.append(cleaned)
    return lines


# =============================================================================
# ПУБЛИЧНАЯ ФУНКЦИЯ
# =============================================================================

def run_translate_sync(text: str, src_lang: str, tgt_lang: str) -> str:
    """Синхронный перевод (запускается через run_in_executor)."""
    if not src_lang or src_lang == tgt_lang:
        src_lang = DEFAULT_SRC_LANG
    try:
        backend_name = getattr(settings, "TRANSLATION_BACKEND", "nllb_600m")
        logger.info(f"[{backend_name}] перевод {src_lang} → {tgt_lang}, символов={len(text)}")

        lines = _strip_timestamps(text)
        if not lines:
            return "Нет текста для перевода."

        translated = nllb_pipeline.translate_lines(lines, src_lang, tgt_lang)
        result = "\n".join(translated)

        logger.info(f"[{backend_name}] перевод завершён, строк={len(translated)}")
        return result

    except Exception:
        err = traceback.format_exc()
        logger.error(f"Translation error:\n{err}")
        return f"Translation Error: {err}"
