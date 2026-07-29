"""Application configuration and shared infrastructure.

All runtime configuration comes from environment variables.  A local `.env` file
is supported for development but must never be committed.
"""

from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional convenience for local runs
    load_dotenv = None


PROJECT_ROOT = Path(__file__).resolve().parent
if load_dotenv:
    load_dotenv(PROJECT_ROOT / ".env")


def _bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    data_dir: Path = Path(os.getenv("DATA_DIRECTORY", PROJECT_ROOT / "data"))
    log_level: str = os.getenv("LOG_LEVEL", "INFO")
    host: str = os.getenv("HOST", "0.0.0.0")
    port: int = int(os.getenv("PORT", "5000"))
    max_upload_mb: int = int(os.getenv("MAX_UPLOAD_MB", "500"))
    load_local_models: bool = _bool("LOAD_LOCAL_MODELS")
    whisper_model_size: str = os.getenv("WHISPER_MODEL_SIZE", "medium")
    enable_qwen_correction: bool = _bool("ENABLE_QWEN_CORRECTION")
    enable_telegram_bot: bool = _bool("ENABLE_TELEGRAM_BOT")
    run_embedded_worker: bool = _bool("RUN_EMBEDDED_WORKER", True)
    redis_url: str = os.getenv("REDIS_URL", "")
    # PostgreSQL is required when API, bot and worker run as independent pods.
    # Leave empty only for the legacy local SQLite development mode.
    database_url: str = os.getenv("DATABASE_URL", "")
    telegram_bot_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    telegram_local_server: str = os.getenv("TELEGRAM_LOCAL_SERVER", "")
    webapp_url: str = os.getenv("WEBAPP_URL", "")
    webapp_auth_max_age_seconds: int = int(os.getenv("WEBAPP_AUTH_MAX_AGE_SECONDS", "3600"))
    translation_backend: str = os.getenv("TRANSLATION_BACKEND", "gemini")
    deepl_api_key: str = os.getenv("DEEPL_API_KEY", "")
    gemini_api_key: str = os.getenv("GEMINI_API_KEY", "")
    gemini_model: str = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    gemini_vision_model: str = os.getenv("GEMINI_VISION_MODEL", "gemini-2.5-flash")
    omninet_url: str = os.getenv("OMNINET_URL", "")
    omninet_login: str = os.getenv("OMNINET_LOGIN", "")
    omninet_password: str = os.getenv("OMNINET_PASSWORD", "")
    third_party_url: str = os.getenv("THIRD_PARTY_URL", "")
    third_party_api_key: str = os.getenv("THIRD_PARTY_API_KEY", "")
    internal_tls_verify: bool = _bool("INTERNAL_TLS_VERIFY", True)


settings = Settings()
BASE_PATH = str(settings.data_dir)
TMP_DIR = str(settings.data_dir / "tmp")
os.makedirs(TMP_DIR, exist_ok=True)

logging.basicConfig(
    level=settings.log_level.upper(),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("transcription_service")
