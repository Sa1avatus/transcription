"""Application configuration and shared infrastructure.

All runtime configuration comes from environment variables.  A local `.env` file
is supported for development but must never be committed.
"""

from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass, fields, replace
from pathlib import Path
from typing import Any

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
    models_dir: Path = Path(os.getenv("MODELS_DIRECTORY", os.getenv("DATA_DIRECTORY", str(PROJECT_ROOT / "data")) + "/models"))
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


# =============================================================================
# Settings management (for web UI)
# =============================================================================

# Fields that can be edited via the web UI
EDITABLE_FIELDS: dict[str, dict[str, Any]] = {
    "whisper_model_size": {"label": "Whisper Model Size", "type": "select", "options": [
        "tiny", "base", "small", "medium", "large-v1", "large-v2", "large-v3",
        "distil-large-v2", "distil-large-v3", "distil-medium", "distil-small",
    ]},
    "translation_backend": {"label": "Translation Backend", "type": "select", "options": [
        "gemini", "deepl", "nllb_600m", "nllb_1300m",
    ]},
    "gemini_api_key": {"label": "Gemini API Key", "type": "password"},
    "gemini_model": {"label": "Gemini Model", "type": "text"},
    "gemini_vision_model": {"label": "Gemini Vision Model", "type": "text"},
    "deepl_api_key": {"label": "DeepL API Key", "type": "password"},
    "telegram_bot_token": {"label": "Telegram Bot Token", "type": "password"},
    "webapp_url": {"label": "WebApp URL", "type": "text"},
    "omninet_url": {"label": "Omninet URL", "type": "text"},
    "omninet_login": {"label": "Omninet Login", "type": "text"},
    "omninet_password": {"label": "Omninet Password", "type": "password"},
    "third_party_url": {"label": "Third Party URL", "type": "text"},
    "third_party_api_key": {"label": "Third Party API Key", "type": "password"},
    "load_local_models": {"label": "Load Local Models", "type": "bool"},
    "enable_qwen_correction": {"label": "Enable Qwen Correction", "type": "bool"},
    "enable_telegram_bot": {"label": "Enable Telegram Bot", "type": "bool"},
    "run_embedded_worker": {"label": "Run Embedded Worker", "type": "bool"},
    "internal_tls_verify": {"label": "Internal TLS Verify", "type": "bool"},
}


def get_settings_dict() -> dict[str, Any]:
    """Return current settings as a dict (sensitive fields masked)."""
    result = {}
    for f in fields(settings):
        if f.name == "data_dir":
            continue
        value = getattr(settings, f.name)
        if f.name in ("gemini_api_key", "deepl_api_key", "telegram_bot_token",
                       "omninet_password", "third_party_api_key"):
            # Mask sensitive fields
            result[f.name] = "***" if value else ""
        else:
            result[f.name] = value
    return result


def _env_path() -> Path:
    return PROJECT_ROOT / ".env"


def _read_env() -> dict[str, str]:
    """Read .env file into a dict."""
    env_path = _env_path()
    if not env_path.exists():
        return {}
    result = {}
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, _, value = line.partition("=")
            result[key.strip()] = value.strip()
    return result


def _write_env(data: dict[str, str]) -> None:
    """Write dict back to .env file, preserving comments and ordering."""
    env_path = _env_path()
    lines: list[str] = []

    if env_path.exists():
        existing_lines = env_path.read_text(encoding="utf-8").splitlines()
        written_keys: set[str] = set()
        for line in existing_lines:
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                key = stripped.partition("=")[0].strip()
                if key in data:
                    lines.append(f"{key}={data[key]}")
                    written_keys.add(key)
                else:
                    lines.append(line)
            else:
                lines.append(line)
        # Append new keys not in existing file
        for key, value in data.items():
            if key not in written_keys:
                lines.append(f"{key}={value}")
    else:
        for key, value in data.items():
            lines.append(f"{key}={value}")

    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def update_settings(updates: dict[str, Any]) -> dict[str, Any]:
    """Update settings in memory and persist to .env.

    Returns the updated settings dict.
    """
    global settings

    # Convert bool fields
    bool_fields = {f.name for f in fields(settings) if f.type == "bool"}
    int_fields = {f.name for f in fields(settings) if f.type == "int"}

    kwargs: dict[str, Any] = {}
    for key, value in updates.items():
        if key in bool_fields:
            kwargs[key] = str(value).lower() in ("1", "true", "yes", "on")
        elif key in int_fields:
            kwargs[key] = int(value)
        else:
            kwargs[key] = value

    # Update in-memory settings
    settings = replace(settings, **kwargs)

    # Persist to .env
    env = _read_env()
    env.update({k: str(v) for k, v in kwargs.items()})
    _write_env(env)

    logger.info(f"Settings updated: {list(kwargs.keys())}")
    return get_settings_dict()
