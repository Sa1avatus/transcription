"""Validation of signed Telegram Mini App launch data."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl


class TelegramWebAppAuthError(ValueError):
    """The Mini App launch payload is malformed, expired or forged."""


def validate_init_data(init_data: str, bot_token: str, max_age_seconds: int = 3600) -> dict:
    """Validate Telegram's HMAC signature and return trusted launch data."""
    if not init_data or not bot_token:
        raise TelegramWebAppAuthError("Missing launch data or bot token")

    values = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = values.pop("hash", "")
    if not received_hash:
        raise TelegramWebAppAuthError("Missing Telegram signature")

    data_check_string = "\n".join(f"{key}={values[key]}" for key in sorted(values))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    expected_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected_hash, received_hash):
        raise TelegramWebAppAuthError("Invalid Telegram signature")

    try:
        auth_date = int(values["auth_date"])
        user = json.loads(values["user"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise TelegramWebAppAuthError("Invalid Telegram launch data") from exc

    if time.time() - auth_date > max_age_seconds:
        raise TelegramWebAppAuthError("Expired Telegram launch data")
    if not user.get("id"):
        raise TelegramWebAppAuthError("Telegram user is missing")

    values["user"] = user
    return values
