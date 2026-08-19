"""Entrypoint for the isolated ML worker container."""

import asyncio

from config import logger, settings
from database import init_db
from worker import worker

async def initialise_models() -> None:
    """Load local models only in the worker process."""
    if not settings.load_local_models:
        logger.warning("LOAD_LOCAL_MODELS is false; worker will use configured external providers only")
        return

    import llm
    import translation
    import whisper_init

    loop = asyncio.get_running_loop()
    if whisper_init.whisper_model is None:
        whisper_init.startup_load()
    if translation.nllb_pipeline is None:
        translation.nllb_pipeline = await loop.run_in_executor(None, translation._init_translator)
    if settings.enable_qwen_correction and llm.qwen_model is None:
        llm.qwen_model = await loop.run_in_executor(None, llm._init_qwen)


async def main() -> None:
    await init_db()
    await initialise_models()
    await worker()


if __name__ == "__main__":
    asyncio.run(main())
