import base64

import httpx
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from app.auth.repository import (
    create_user_from_google,
    create_user_from_telegram,
    find_user_by_google_sub,
    find_user_by_telegram_id,
    update_user_from_google,
    update_user_from_telegram,
)
from app.auth.schemas import (
    AuthResponse,
    GoogleAuthRequest,
    TelegramAuthRequest,
    UserData,
)
from app.auth.service import (
    AuthError,
    issue_access_token,
    verify_google_id_token,
    verify_telegram_init_data,
    verify_telegram_oidc_token,
)
from app.core.config import settings
from app.core.directus import get_directus

router = APIRouter()


def _to_user_data(user: dict) -> UserData:
    return UserData(
        id=str(user["id"]),
        display_name=user.get("display_name"),
        username=user.get("username"),
        avatar_url=user.get("avatar_url"),
        auth_provider=user.get("auth_provider"),
        birth_date=user.get("birth_date"),
        language=user.get("language") or "uk",
    )


# ── Telegram Mini App (initData) ──────────────────────────────────────────
@router.post("/telegram", response_model=AuthResponse)
async def telegram_login(payload: TelegramAuthRequest) -> AuthResponse:
    try:
        tg_user = verify_telegram_init_data(payload.init_data)
    except AuthError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc))

    client = get_directus()
    user = await find_user_by_telegram_id(client, tg_user.id)
    if user is None:
        user = await create_user_from_telegram(client, tg_user)
    else:
        user = await update_user_from_telegram(client, str(user["id"]), tg_user)

    user_id = str(user["id"])
    return AuthResponse(
        access_token=issue_access_token(user_id),
        user_id=user_id,
        user=_to_user_data(user),
    )


# ── Telegram OIDC — обмін code на токен ──────────────────────────────────
class TelegramCallbackRequest(BaseModel):
    code: str
    code_verifier: str


@router.post("/telegram/callback", response_model=AuthResponse)
async def telegram_callback(payload: TelegramCallbackRequest) -> AuthResponse:
    """
    Фронт передає code і code_verifier.
    Бекенд обмінює їх на id_token через oauth.telegram.org/token.
    """
    credentials = base64.b64encode(
        f"{settings.telegram_client_id}:{settings.telegram_client_secret}".encode()
    ).decode()

    try:
        async with httpx.AsyncClient(timeout=10.0) as http:
            resp = await http.post(
                "https://oauth.telegram.org/token",
                headers={
                    "Authorization": f"Basic {credentials}",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                data={
                    "grant_type":    "authorization_code",
                    "code":          payload.code,
                    "redirect_uri":  settings.telegram_redirect_uri,
                    "client_id":     settings.telegram_client_id,
                    "code_verifier": payload.code_verifier,
                },
            )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Telegram token request failed: {exc}")

    if resp.status_code != 200:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Telegram token error: {resp.text}",
        )

    token_data = resp.json()
    id_token = token_data.get("id_token")
    if not id_token:
        raise HTTPException(status_code=502, detail="No id_token in Telegram response")

    try:
        tg_user = await verify_telegram_oidc_token(id_token)
    except AuthError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc))

    client = get_directus()
    user = await find_user_by_telegram_id(client, tg_user.id)
    if user is None:
        user = await create_user_from_telegram(client, tg_user)
    else:
        user = await update_user_from_telegram(client, str(user["id"]), tg_user)

    user_id = str(user["id"])
    return AuthResponse(
        access_token=issue_access_token(user_id),
        user_id=user_id,
        user=_to_user_data(user),
    )


# ── Google ────────────────────────────────────────────────────────────────
@router.post("/google", response_model=AuthResponse)
async def google_login(payload: GoogleAuthRequest) -> AuthResponse:
    try:
        google_user = await verify_google_id_token(payload.id_token)
    except AuthError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc))

    client = get_directus()
    user = await find_user_by_google_sub(client, google_user.sub)
    if user is None:
        user = await create_user_from_google(client, google_user)
    else:
        user = await update_user_from_google(client, str(user["id"]), google_user)

    user_id = str(user["id"])
    return AuthResponse(
        access_token=issue_access_token(user_id),
        user_id=user_id,
        user=_to_user_data(user),
    )
