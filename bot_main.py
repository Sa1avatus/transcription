"""Entrypoint for the optional Telegram adapter container."""

import asyncio

from config import settings
from database import init_db


async def main() -> None:
    if not settings.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is required for the bot container")
    await init_db()
    from bot import start_bot
    await start_bot()


if __name__ == "__main__":
    asyncio.run(main())
