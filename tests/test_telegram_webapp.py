import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import pytest

from telegram_webapp import TelegramWebAppAuthError, validate_init_data


def _signed_init_data(token: str) -> str:
    fields = {
        "auth_date": str(int(time.time())),
        "query_id": "query",
        "user": json.dumps({"id": 42, "first_name": "Ada"}, separators=(",", ":")),
    }
    check = "\n".join(f"{key}={fields[key]}" for key in sorted(fields))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


def test_validate_telegram_miniapp_data():
    data = validate_init_data(_signed_init_data("token"), "token")
    assert data["user"]["id"] == 42


def test_rejects_tampered_telegram_miniapp_data():
    with pytest.raises(TelegramWebAppAuthError):
        validate_init_data(_signed_init_data("token") + "broken", "token")
