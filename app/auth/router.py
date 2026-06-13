from fastapi import APIRouter, HTTPException, status

from app.auth.repository import (
    create_user_from_google,
    create_user_from_telegram,
    find_user_by_google_sub,
    find_user_by_telegram_id,
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
)
from app.core.directus import get_directus


router = APIRouter()


def _to_user_data(user: dict) -> UserData:
    return UserData(
        id=str(user["id"]),
        display_name=user.get("display_name"),
        username=user.get("username"),
        avatar_url=user.get("avatar_url"),
        auth_provider=user.get("auth_provider"),
    )


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

    user_id = str(user["id"])
    return AuthResponse(
        access_token=issue_access_token(user_id),
        user_id=user_id,
        user=_to_user_data(user),
    )


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

    user_id = str(user["id"])
    return AuthResponse(
        access_token=issue_access_token(user_id),
        user_id=user_id,
        user=_to_user_data(user),
    )
