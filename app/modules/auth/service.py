import asyncio
import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qsl

import httpx
import jwt
from google.auth.exceptions import GoogleAuthError
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token
from jwt import PyJWKClient

from app.modules.auth.schemas import GoogleUser, TelegramUser
from app.core.config import settings


GOOGLE_ISSUERS    = {"https://accounts.google.com", "accounts.google.com"}
TELEGRAM_AUTH_MAX_AGE = timedelta(hours=24)


class AuthError(Exception):
    pass


def _compute_telegram_hash(parsed: dict, secret_key: bytes) -> str:
    """Обчислює HMAC-SHA256 для набору полів Telegram."""
    data_check_string = "\n".join(
        f"{k}={v}" for k, v in sorted(parsed.items())
    )
    return hmac.new(
        key=secret_key,
        msg=data_check_string.encode(),
        digestmod=hashlib.sha256,
    ).hexdigest()


def verify_telegram_init_data(init_data: str) -> TelegramUser:
    """
    Підтримує два формати:

    1. Mini App initData (Telegram WebApp SDK):
       user={"id":123,"first_name":"Dan",...}&auth_date=...&hash=...

    2. Telegram Login Widget:
       user={"id":123,"first_name":"Dan",...}&auth_date=...&hash=...
       (той самий формат — ми самі пакуємо на фронті)

    Якщо поле `user` є — парсимо його як JSON.
    Якщо ні — поля id/first_name/... лежать прямо в рядку (legacy).
    """
    parsed = dict(parse_qsl(init_data, keep_blank_values=True))

    received_hash = parsed.pop("hash", None)
    if not received_hash:
        raise AuthError("Missing hash in initData")

    secret_key = hmac.new(
        key=b"WebAppData",
        msg=settings.telegram_bot_token.encode(),
        digestmod=hashlib.sha256,
    ).digest()

    computed_hash = _compute_telegram_hash(parsed, secret_key)

    if not hmac.compare_digest(computed_hash, received_hash):
        # Telegram Login Widget підписує іншим ключем — bot_token напряму (без "WebAppData")
        secret_key_widget = hashlib.sha256(settings.telegram_bot_token.encode()).digest()
        computed_hash_widget = _compute_telegram_hash(parsed, secret_key_widget)
        if not hmac.compare_digest(computed_hash_widget, received_hash):
            raise AuthError("Invalid initData signature")

    auth_date_raw = parsed.get("auth_date")
    if not auth_date_raw:
        raise AuthError("Missing auth_date")

    auth_date = datetime.fromtimestamp(int(auth_date_raw), tz=timezone.utc)
    if datetime.now(tz=timezone.utc) - auth_date > TELEGRAM_AUTH_MAX_AGE:
        raise AuthError("initData expired")

    # Поле user може бути як JSON-об'єкт, так і розпаковані поля
    user_raw = parsed.get("user")
    if user_raw:
        try:
            user_data = json.loads(user_raw)
        except json.JSONDecodeError as exc:
            raise AuthError(f"Invalid user JSON: {exc}") from exc
    else:
        # Telegram Login Widget кладе поля напряму
        if "id" not in parsed:
            raise AuthError("Missing user payload")
        user_data = {
            "id":           int(parsed["id"]),
            "first_name":   parsed.get("first_name", ""),
            "last_name":    parsed.get("last_name"),
            "username":     parsed.get("username"),
            "photo_url":    parsed.get("photo_url"),
            "language_code": parsed.get("language_code"),
        }

    return TelegramUser(**user_data)


async def verify_google_id_token(id_token: str) -> GoogleUser:
    """Перевіряє Google ID token офіційною бібліотекою google-auth."""
    if not settings.google_client_id:
        raise AuthError("Google auth is not configured")

    try:
        payload = await asyncio.to_thread(
            google_id_token.verify_oauth2_token,
            id_token,
            google_requests.Request(),
            settings.google_client_id,
        )
    except (ValueError, GoogleAuthError) as exc:
        raise AuthError(f"Invalid Google id_token: {exc}") from exc

    if payload.get("iss") not in GOOGLE_ISSUERS:
        raise AuthError("Invalid Google token issuer")

    google_sub = payload.get("sub")
    if not google_sub:
        raise AuthError("Google token missing subject")

    return GoogleUser(
        sub=str(google_sub),
        email=payload.get("email"),
        email_verified=payload.get("email_verified"),
        name=payload.get("name"),
        picture=payload.get("picture"),
    )


def issue_access_token(user_id: str) -> str:
    now = datetime.now(tz=timezone.utc)
    payload = {
        "sub": user_id,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=settings.jwt_expires_minutes)).timestamp()),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> str:
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
        )
    except jwt.PyJWTError as exc:
        raise AuthError(f"Invalid access token: {exc}") from exc

    user_id = payload.get("sub")
    if not user_id:
        raise AuthError("Token missing subject")
    return user_id


async def verify_telegram_oidc_token(id_token: str) -> TelegramUser:
    """Верифікує OIDC id_token, отриманий від Telegram Web Login."""
    telegram_jwks_url = "https://oauth.telegram.org/.well-known/jwks.json"
    telegram_issuer = "https://oauth.telegram.org"

    try:
        jwks_client = PyJWKClient(telegram_jwks_url)
        signing_key = jwks_client.get_signing_key_from_jwt(id_token).key
        payload = jwt.decode(
            id_token,
            signing_key,
            algorithms=["RS256", "ES256", "EdDSA", "ES256K"],
            audience=str(settings.telegram_client_id),
            issuer=telegram_issuer,
            options={"verify_exp": True},
        )
    except Exception as exc:
        raise AuthError(f"Invalid Telegram OIDC token: {exc}") from exc

    tg_id = payload.get("id") or payload.get("sub")
    if not tg_id:
        raise AuthError("Missing Telegram user id in OIDC token")

    full_name = (payload.get("name") or "").strip()
    name_parts = full_name.split(maxsplit=1)
    first_name = payload.get("given_name") or (name_parts[0] if name_parts else None)
    last_name = payload.get("family_name") or (name_parts[1] if len(name_parts) > 1 else None)

    return TelegramUser(
        id=int(tg_id),
        first_name=first_name,
        last_name=last_name,
        username=payload.get("preferred_username"),
        photo_url=payload.get("picture"),
    )
