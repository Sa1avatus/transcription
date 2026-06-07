"""
gemini_vision.py — анализ изображений через Gemini Vision API.

Используется тот же ключ что и для перевода (settings.GEMINI_API_KEY).
Gemini 2.0 Flash поддерживает изображения нативно.
"""

import base64
import traceback
import httpx
import settings
from config import logger

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
GEMINI_DEFAULT_VISION_MODEL = "gemini-2.5-flash"

ANALYZE_PROMPT = """Analyze this image carefully. Do the following:

1. If the image contains text (document, screenshot, sign, handwriting, etc.) — extract and output ALL text exactly as it appears, preserving formatting.
2. If there is no text or the text is minimal — describe what is shown in the image in detail.
3. If both text and visual content are present — first extract the text, then briefly describe the visual context.

Respond in the same language as the text in the image. If there is no text, respond in English."""

ANALYZE_PROMPT_WITH_CAPTION = """The user sent this image with the following comment: "{caption}"

Taking that comment into account, analyze the image:
1. If the comment asks a specific question — answer it based on what you see.
2. If the image contains text relevant to the comment — extract it.
3. Otherwise describe what is shown, keeping the user's comment in mind.

Respond in the same language as the user's comment."""


def analyze_image_sync(
    image_bytes: bytes,
    mime_type: str = "image/jpeg",
    caption: str | None = None,
) -> str:
    """
    Синхронный анализ изображения через Gemini Vision.
    Запускается через run_in_executor.
    caption — подпись пользователя из Telegram, учитывается в промпте.
    """
    api_key = getattr(settings, "GEMINI_API_KEY", "")
    if not api_key:
        return "❌ GEMINI_API_KEY не задан в settings.py"

    model = getattr(settings, "GEMINI_VISION_MODEL", None) or getattr(settings, "GEMINI_MODEL", GEMINI_DEFAULT_VISION_MODEL)
    url   = GEMINI_URL.format(model=model)
    b64   = base64.b64encode(image_bytes).decode("utf-8")
    prompt = ANALYZE_PROMPT_WITH_CAPTION.format(caption=caption) if caption else ANALYZE_PROMPT

    payload = {
        "contents": [{
            "parts": [
                {"text": prompt},
                {"inline_data": {"mime_type": mime_type, "data": b64}},
            ]
        }]
    }

    import time
    delays = [5, 15, 30]
    for attempt, delay in enumerate(delays + [None], 1):
        try:
            resp = httpx.post(
                url,
                params={"key": api_key},
                json=payload,
                timeout=60.0,
                verify=False,
            )
            if resp.status_code == 429:
                if delay is None:
                    return f"❌ Gemini: слишком много запросов (429). Попробуйте позже."
                logger.warning(f"Gemini Vision 429: попытка {attempt}, ждём {delay}с...")
                time.sleep(delay)
                continue

            resp.raise_for_status()
            return resp.json()["candidates"][0]["content"]["parts"][0]["text"].strip()

        except httpx.HTTPStatusError as e:
            logger.error(f"Gemini Vision HTTP error: {e}")
            return f"❌ Ошибка Gemini API: {e.response.status_code}"
        except Exception:
            logger.error(f"Gemini Vision error:\n{traceback.format_exc()}")
            return "❌ Ошибка при анализе изображения"

    return "❌ Gemini: превышен лимит попыток"
