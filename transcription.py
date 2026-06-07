import os
import asyncio
import traceback

import httpx
import settings

from config import logger
import whisper_init
import llm


# =============================================================================
# ЛОКАЛЬНАЯ ТРАНСКРИБАЦИЯ (Whisper + LLM-коррекция)
# =============================================================================

def run_whisper_sync(audio_path: str) -> str:
    """
    Транскрибирует файл через Whisper, затем прогоняет результат
    через Qwen для исправления ASR-ошибок.
    """
    try:
        logger.info(f"Whisper: начало транскрибации {audio_path}")
        segments, _ = whisper_init.whisper_model.transcribe(
            audio_path,
            beam_size=5,
            language="vi",
            condition_on_previous_text=False,
            vad_filter=True,
        )
        raw = "\n".join(
            f"[{s.start:.2f}s -> {s.end:.2f}s] {s.text.strip()}" for s in segments
        )
        logger.info("Whisper: транскрибация завершена, запускаем LLM-коррекцию")
        corrected = llm.correct_transcript(raw)
        return corrected
    except Exception:
        err = traceback.format_exc()
        logger.error(f"Whisper: ошибка транскрибации\n{err}")
        return f"Whisper Error: {err}"


# =============================================================================
# ВНЕШНИЙ API (Upload → Task → Polling → Result)
# =============================================================================

async def process_external_api(audio_path: str, uid: str) -> str:
    """Загружает файл во внешний сервис, ожидает результат и возвращает текст с таймкодами."""
    headers = {"aik-api-key": settings.THIRD_PARTY_API_KEY}
    base_url = settings.THIRD_PARTY_URL.rstrip('/')

    async with httpx.AsyncClient(verify=False, timeout=120.0) as client:
        try:
            logger.info(f"[{uid}] Ext: Uploading file...")
            with open(audio_path, 'rb') as f:
                up_resp = await client.post(
                    f"{base_url}/upload",
                    files={'file': (os.path.basename(audio_path), f)},
                    headers=headers,
                )
                up_resp.raise_for_status()
                file_link = up_resp.json().get("link")

            logger.info(f"[{uid}] Ext: Creating task for {file_link}")
            task_resp = await client.post(
                f"{base_url}/task",
                json={"type": "transcribe", "data": {"url": file_link}},
                headers=headers,
            )
            task_resp.raise_for_status()
            task_id = task_resp.json()["data"]["id"]

            for i in range(60):
                await asyncio.sleep(10)
                st = await client.get(f"{base_url}/task/{task_id}", headers=headers)
                logger.info(f"[{uid}] Ext Poll #{i + 1} [{st.status_code}]: {st.text}")

                d = st.json().get("data", {})
                if d.get("status") == "complete":
                    segments = d.get("result", {}).get("transcribe", [])
                    res_text = "\n".join(
                        f"[{s.get('start', 0):.2f}s -> {s.get('end', 0):.2f}s] "
                        f"{s.get('speaker')}: {s.get('text', '').strip()}"
                        for s in segments
                    )
                    return res_text or "External: Success, but no text detected."

                if d.get("status") == "fail":
                    return f"Ext Error: {d.get('result', {}).get('message')}"

            return "External Error: Timeout (polling limit reached)"

        except httpx.ConnectError:
            logger.error(f"[{uid}] Ext: Connection failed to {base_url}")
            return "External API Error: Connection failed. Check network/DNS."
        except Exception as e:
            logger.error(f"[{uid}] Ext: Exception: {e}")
            return f"External API Exception: {e}"
