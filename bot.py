"""
bot.py — Telegram-бот на aiogram v3.

Поддерживаемые типы сообщений:
  🎤 Голосовое / аудио / документ(audio/*) → транскрибация + предложение перевода
  🖼  Фото / документ(image/*)             → Gemini Vision: OCR или описание + предложение перевода
  💬 Текст                                 → предложение перевода на выбранный язык
"""

import io
import os
import asyncio
import uuid
from typing import Optional

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    MenuButtonWebApp,
    WebAppInfo,
)

from config import TMP_DIR, logger, settings
from storage import task_queue, task_store, store_put
from database import db_insert_task, db_get_task, db_get_translation, db_upsert_translation
from translation import LANGUAGES
from gemini_vision import analyze_image_sync

bot = Bot(token=settings.telegram_bot_token)
logger.info("[BOT] Используем стандартный Bot API (лимит файлов 20 МБ)")
dp = Dispatcher()

# Временное хранилище текстов для перевода (in-memory, не нужна БД)
# { temp_id: {"text": str, "src_lang": str} }
_text_store: dict[str, dict] = {}

MAX_TG_MSG = 4096


# =============================================================================
# КЛАВИАТУРЫ
# =============================================================================

def _translate_keyboard(task_id: str, prefix: str = "translate") -> InlineKeyboardMarkup:
    """Строит inline-клавиатуру с языками перевода. prefix задаёт тип callback."""
    buttons = [
        InlineKeyboardButton(
            text=f"{language.flag} {language.label}",
            callback_data=f"{prefix}:{task_id}:{language_code}",
        )
        for language_code, language in LANGUAGES.items()
    ]
    rows = [buttons[offset: offset + 2] for offset in range(0, len(buttons), 2)]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _stats_keyboard() -> InlineKeyboardMarkup | None:
    """Возвращает кнопку Mini App, если для него настроен публичный HTTPS URL."""
    if not settings.webapp_url:
        return None
    return InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(
                text="📊 Открыть статистику",
                web_app=WebAppInfo(url=settings.webapp_url),
            )
        ]]
    )


# =============================================================================
# ОТПРАВКА
# =============================================================================

async def _send_chunks(chat_id: str, text: str) -> None:
    """Отправляет длинный текст частями, не разрывая строки."""
    lines = text.split("\n")
    chunk = ""
    for line in lines:
        if len(chunk) + len(line) + 1 > MAX_TG_MSG:
            await bot.send_message(chat_id, chunk)
            chunk = ""
        chunk += line + "\n"
    if chunk.strip():
        await bot.send_message(chat_id, chunk)


async def _send_text(chat_id: str, header: str, body: str) -> None:
    """Отправляет сообщение с заголовком (HTML) и телом, разбивая при необходимости."""
    full = header + body
    if len(full) <= MAX_TG_MSG:
        await bot.send_message(chat_id, full, parse_mode="HTML")
    else:
        await bot.send_message(chat_id, header + "Результат разбит на части:", parse_mode="HTML")
        await _send_chunks(chat_id, body)


# =============================================================================
# КОМАНДЫ
# =============================================================================

@dp.message(CommandStart())
async def cmd_start(message: Message) -> None:
    await message.answer(
        "Привет! Я умею:\n\n"
        "🎤 <b>Аудио / голосовое</b> → транскрибация + перевод\n"
        "🖼 <b>Фото / картинка</b> → распознавание текста или описание\n"
        "💬 <b>Текст</b> → перевод на выбранный язык\n\n"
        "Просто отправь файл или напиши что-нибудь!",
        parse_mode="HTML",
        reply_markup=_stats_keyboard(),
    )


@dp.message(Command("stats"))
async def cmd_stats(message: Message) -> None:
    """Показывает Mini App как обычную inline-кнопку в чате."""
    keyboard = _stats_keyboard()
    if keyboard is None:
        await message.answer("Страница статистики пока недоступна. Попробуйте немного позже.")
        return
    await message.answer(
        "📊 Откройте личную статистику транскрибаций, распознавания и переводов:",
        reply_markup=keyboard,
    )


# =============================================================================
# АУДИО
# =============================================================================

# Лимит стандартного Bot API. При использовании локального сервера
# (TELEGRAM_LOCAL_SERVER в settings.py) лимит не применяется.
BOT_API_FILE_LIMIT = 20 * 1024 * 1024   # 20 МБ


async def _handle_audio(message: Message, file_id: str, ext: str, file_size: int = 0) -> None:
    chat_id  = str(message.chat.id)

    # Всегда пытаемся скачать файл. Telegram Bot API технически
    # поддерживает файлы до ~50 МБ через стандартный сервер,
    # хотя документация указывает 20 МБ. Если реальный скачивание
    # не удастся — сообщим пользователю.
    task_id  = uuid.uuid4().hex
    poll_url = f"/task/{task_id}"
    audio_path = os.path.join(TMP_DIR, f"tg_{task_id}{ext}")

    try:
        size_info = f" ({file_size / 1048576:.1f} МБ)" if file_size else ""
        await message.answer(f"⏳ Получил файл{size_info}, ставлю в очередь на транскрибацию...")

        # Try downloading with generous timeout; Bot API often handles
        # files up to ~50 MB even though docs say 20 MB.
        tg_file = await bot.get_file(file_id, request_timeout=300)
        await bot.download_file(tg_file.file_path, destination=audio_path, timeout=300)
        logger.info(f"[BOT] Аудио сохранено: {audio_path}, chat_id={chat_id}")

        await db_insert_task(task_id, uid=None, poll_url=poll_url, chat_id=chat_id)
        store_put(task_id, uid=None, chat_id=chat_id)
        await task_queue.put((task_id, None, audio_path, True))

        await message.answer(
            f"✅ Задача принята.\nID: <code>{task_id}</code>\n"
            "Пришлю результат, как только готово.",
            parse_mode="HTML",
        )
    except Exception:
        logger.exception("[BOT] Ошибка приёма аудио")
        if os.path.exists(audio_path):
            os.remove(audio_path)
        if file_size and file_size > BOT_API_FILE_LIMIT:
            size_mb = file_size / 1048576
            await message.answer(
                f"❌ Не удалось скачать файл ({size_mb:.1f} МБ) из Telegram.\n\n"
                f"Для файлов >20 МБ загрузите через веб-интерфейс:\n"
                f"• Откройте API в браузере (порт 5000)\n"
                f"• Перейдите на вкладку Dashboard\n"
                f"• Перетащите файл в зону загрузки\n\n"
                f"Или настройте локальный Telegram Bot API сервер."
            )
        else:
            await message.answer(
                "❌ Не удалось скачать файл из Telegram. "
                "Попробуйте отправить его ещё раз немного позже."
            )


@dp.message(F.voice)
async def handle_voice(message: Message) -> None:
    await _handle_audio(message, message.voice.file_id, ext=".ogg", file_size=message.voice.file_size or 0)


@dp.message(F.audio)
async def handle_audio(message: Message) -> None:
    ext = os.path.splitext(message.audio.file_name or "")[1] or ".mp3"
    await _handle_audio(message, message.audio.file_id, ext=ext, file_size=message.audio.file_size or 0)


# =============================================================================
# ИЗОБРАЖЕНИЯ
# =============================================================================

async def _handle_image(message: Message, file_id: str, mime_type: str = "image/jpeg") -> None:
    """Скачивает изображение, анализирует через Gemini Vision, предлагает перевод."""
    chat_id = str(message.chat.id)
    caption = (message.caption or "").strip() or None   # подпись из Telegram

    try:
        hint = f" (с подписью: «{caption}»)" if caption else ""
        await message.answer(f"🔍 Анализирую изображение{hint}...")

        tg_file = await bot.get_file(file_id)
        buf = io.BytesIO()
        await bot.download_file(tg_file.file_path, destination=buf)
        image_bytes = buf.getvalue()

        logger.info(f"[BOT] Изображение получено: {len(image_bytes)} байт, caption={caption!r}, chat_id={chat_id}")

        loop   = asyncio.get_running_loop()
        image_analysis = await loop.run_in_executor(None, analyze_image_sync, image_bytes, mime_type, caption)

        header = "🖼 <b>Результат анализа изображения:</b>\n\n"
        await _send_text(chat_id, header, image_analysis)

        # Предлагаем перевод результата
        temp_id = uuid.uuid4().hex
        _text_store[temp_id] = {"text": image_analysis, "src_lang": "auto"}
        await bot.send_message(
            chat_id,
            "🌐 Хочешь перевести результат?",
            reply_markup=_translate_keyboard(temp_id, prefix="translate_plain"),
        )

    except Exception:
        logger.exception("[BOT] Ошибка анализа изображения")
        await message.answer("❌ Не удалось обработать изображение. Попробуйте ещё раз немного позже.")


@dp.message(F.photo)
async def handle_photo(message: Message) -> None:
    # Берём фото наибольшего размера (последнее в массиве)
    photo = message.photo[-1]
    await _handle_image(message, photo.file_id, mime_type="image/jpeg")


# =============================================================================
# ДОКУМЕНТЫ (аудио + изображения)
# =============================================================================

@dp.message(F.document)
async def handle_document(message: Message) -> None:
    mime = message.document.mime_type or ""
    if mime.startswith("audio/"):
        ext = os.path.splitext(message.document.file_name or "")[1] or ".bin"
        await _handle_audio(message, message.document.file_id, ext=ext, file_size=message.document.file_size or 0)
    elif mime.startswith("image/"):
        await _handle_image(message, message.document.file_id, mime_type=mime)
    else:
        await message.answer(
            "⚠️ Поддерживаются аудиофайлы (mp3, wav, ogg...) и изображения (jpg, png, ...).\n"
            "Или отправь голосовое сообщение."
        )


# =============================================================================
# ТЕКСТОВЫЕ СООБЩЕНИЯ → ПЕРЕВОД
# =============================================================================

@dp.message(F.text & ~F.text.startswith("/"))
async def handle_text(message: Message) -> None:
    """Получает текст и предлагает выбор языка для перевода."""
    text = message.text.strip()
    if not text:
        return

    temp_id = uuid.uuid4().hex
    _text_store[temp_id] = {"text": text, "src_lang": "auto"}

    preview = text[:200] + "..." if len(text) > 200 else text
    await message.answer(
        f"💬 Текст получен:\n<i>{preview}</i>\n\n🌐 На какой язык перевести?",
        parse_mode="HTML",
        reply_markup=_translate_keyboard(temp_id, prefix="translate_plain"),
    )


# =============================================================================
# CALLBACK: ПЕРЕВОД ТРАНСКРИБАЦИИ
# =============================================================================

@dp.callback_query(F.data.startswith("translate:"))
async def handle_translate_callback(callback: CallbackQuery) -> None:
    _, task_id, tgt_lang = callback.data.split(":", 2)
    chat_id   = str(callback.message.chat.id)
    lang_info = LANGUAGES.get(tgt_lang)
    lang_label = f"{lang_info.flag} {lang_info.label}" if lang_info else tgt_lang

    await callback.answer(f"Переводим на {lang_label}...")

    # Кэш
    cached = await db_get_translation(task_id, tgt_lang)
    if cached:
        await _send_translation(chat_id, task_id, tgt_lang, cached["translated"], from_cache=True)
        return

    # The bot and worker run in separate containers.  The bot's in-memory
    # cache retains its initial ``pending`` value, while the worker updates
    # the shared SQLite database.  Prefer the database for the authoritative
    # cross-service status.
    entry = await db_get_task(task_id) or task_store.get(task_id)
    if not entry:
        await bot.send_message(chat_id, f"❌ Задача <code>{task_id}</code> не найдена.", parse_mode="HTML")
        return
    if entry["status"] != "done":
        await bot.send_message(
            chat_id,
            f"⚠️ Транскрибация ещё не завершена (статус: {entry['status']}). Попробуй позже.",
        )
        return

    source_text: str = entry.get("result") or ""
    if not source_text:
        await bot.send_message(chat_id, "❌ Текст транскрибации пустой.")
        return

    await bot.send_message(chat_id, f"⏳ Переводим на {lang_label}...")
    await task_queue.put({
        "kind": "translation",
        "task_id": task_id,
        "chat_id": chat_id,
        "source_text": source_text,
        "src_lang": "vie_Latn",
        "tgt_lang": tgt_lang,
    })


# =============================================================================
# CALLBACK: ПЕРЕВОД ТЕКСТА / ИЗОБРАЖЕНИЯ
# =============================================================================

@dp.callback_query(F.data.startswith("translate_plain:"))
async def handle_translate_plain_callback(callback: CallbackQuery) -> None:
    _, temp_id, tgt_lang = callback.data.split(":", 2)
    chat_id   = str(callback.message.chat.id)
    lang_info = LANGUAGES.get(tgt_lang)
    lang_label = f"{lang_info.flag} {lang_info.label}" if lang_info else tgt_lang

    await callback.answer(f"Переводим на {lang_label}...")

    entry = _text_store.get(temp_id)
    if not entry:
        await bot.send_message(chat_id, "❌ Текст не найден. Отправь его заново.")
        return

    source_text = entry["text"]
    src_lang    = "vie_Latn"   # дефолт; Gemini сам определит язык если "auto"

    await bot.send_message(chat_id, f"⏳ Переводим на {lang_label}...")
    await task_queue.put({
        "kind": "translation",
        "chat_id": chat_id,
        "source_text": source_text,
        "src_lang": src_lang,
        "tgt_lang": tgt_lang,
    })


# =============================================================================
# ОТПРАВКА РЕЗУЛЬТАТОВ ТРАНСКРИБАЦИИ (вызывается из worker.py)
# =============================================================================

async def _send_translation(
    chat_id: str, task_id: str, tgt_lang: str, text: str, from_cache: bool
) -> None:
    lang_info  = LANGUAGES.get(tgt_lang)
    label      = f"{lang_info.flag} {lang_info.label}" if lang_info else tgt_lang
    cache_note = " (из кэша)" if from_cache else ""
    header     = f"🌐 <b>Перевод на {label}</b>{cache_note} (<code>{task_id}</code>)\n\n"
    try:
        await _send_text(chat_id, header, text)
        logger.info(f"[BOT] Перевод отправлен: task={task_id}, lang={tgt_lang}, chat={chat_id}")
    except Exception as e:
        logger.error(f"[BOT] Ошибка отправки перевода: {e}")


async def send_transcription_result(chat_id: str, task_id: str, text: str) -> None:
    """Вызывается из worker.py по завершении транскрибации."""
    header = f"📝 <b>Транскрибация готова</b> (<code>{task_id}</code>)\n\n"
    try:
        await _send_text(chat_id, header, text)
        await bot.send_message(
            chat_id,
            "🌐 Хочешь перевести на другой язык?",
            reply_markup=_translate_keyboard(task_id),
        )
        logger.info(f"[BOT] Транскрибация отправлена: task={task_id}, chat={chat_id}")
    except Exception as e:
        logger.error(f"[BOT] Ошибка отправки транскрибации: {e}")


# =============================================================================
# ЗАПУСК
# =============================================================================

async def start_bot() -> None:
    logger.info("[BOT] Запуск Telegram бота...")
    if settings.webapp_url:
        await bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(
                text="Статистика",
                web_app=WebAppInfo(url=settings.webapp_url),
            )
        )
        logger.info("[BOT] Mini App menu button configured: %s", settings.webapp_url)
    else:
        logger.info("[BOT] WEBAPP_URL is empty; Mini App menu button is disabled")
    await dp.start_polling(bot, handle_signals=False)
