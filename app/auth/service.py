import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qsl

import httpx
import jwt
from jwt import PyJWKClient

from app.auth.schemas import GoogleUser, TelegramUser
from app.core.config import settings


GOOGLE_JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"
GOOGLE_ISSUERS = {"https://accounts.google.com", "accounts.google.com"}
TELEGRAM_AUTH_MAX_AGE = timedelta(hours=24)


class AuthError(Exception):
    pass


def verify_telegram_init_data(init_data: str) -> TelegramUser:
    parsed = dict(parse_qsl(init_data, keep_blank_values=True))

    received_hash = parsed.pop("hash", None)
    if not received_hash:
        raise AuthError("Missing hash in initData")

    data_check_string = "\n".join(
        f"{k}={v}" for k, v in sorted(parsed.items())
    )

    secret_key = hmac.new(
        key=b"WebAppData",
        msg=settings.telegram_bot_token.encode(),
        digestmod=hashlib.sha256,
    ).digest()

    computed_hash = hmac.new(
        key=secret_key,
        msg=data_check_string.encode(),
        digestmod=hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(computed_hash, received_hash):
        raise AuthError("Invalid initData signature")

    auth_date_raw = parsed.get("auth_date")
    if not auth_date_raw:
        raise AuthError("Missing auth_date")

    auth_date = datetime.fromtimestamp(int(auth_date_raw), tz=timezone.utc)
    if datetime.now(tz=timezone.utc) - auth_date > TELEGRAM_AUTH_MAX_AGE:
        raise AuthError("initData expired")

    user_raw = parsed.get("user")
    if not user_raw:
        raise AuthError("Missing user payload")

    try:
        user_data = json.loads(user_raw)
    except json.JSONDecodeError as exc:
        raise AuthError(f"Invalid user JSON: {exc}") from exc

    return TelegramUser(**user_data)


async def verify_google_id_token(id_token: str) -> GoogleUser:
    if not settings.google_client_id:
        raise AuthError("Google auth is not configured")

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            jwks_response = await client.get(GOOGLE_JWKS_URL)
            jwks_response.raise_for_status()
    except httpx.HTTPError as exc:
        raise AuthError(f"Failed to fetch Google JWKS: {exc}") from exc

    jwks_client = PyJWKClient(GOOGLE_JWKS_URL)
    try:
        signing_key = jwks_client.get_signing_key_from_jwt(id_token).key
        payload = jwt.decode(
            id_token,
            signing_key,
            algorithms=["RS256"],
            audience=settings.google_client_id,
            options={"verify_exp": True},
        )
    except jwt.PyJWTError as exc:
        raise AuthError(f"Invalid Google id_token: {exc}") from exc

    if payload.get("iss") not in GOOGLE_ISSUERS:
        raise AuthError("Invalid token issuer")

    return GoogleUser(
        sub=payload["sub"],
        email=payload.get("email"),
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
