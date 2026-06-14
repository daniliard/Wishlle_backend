from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.auth.service import AuthError, decode_access_token
from app.core.config import settings
from app.core.directus import DirectusError, get_directus
from app.profile.schemas import ProfileData, ProfileUpdate

router = APIRouter()
bearer = HTTPBearer(auto_error=False)

MAX_AVATAR_SIZE = 5 * 1024 * 1024
ALLOWED_TYPES = {'image/jpeg', 'image/png', 'image/webp'}
ALLOWED_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp'}


def _profile(user: dict) -> ProfileData:
    return ProfileData(
        id=str(user['id']),
        display_name=user.get('display_name'),
        username=user.get('username'),
        birth_date=user.get('birth_date'),
        avatar_url=user.get('avatar_url'),
        auth_provider=user.get('auth_provider'),
        language=user.get('language') or 'uk',
    )


def current_user_id(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> str:
    if credentials is None or credentials.scheme.lower() != 'bearer':
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Потрібна авторизація.')
    try:
        return decode_access_token(credentials.credentials)
    except AuthError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc


async def _get_user(user_id: str) -> dict:
    user = await get_directus().get_item(settings.directus_users_collection, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Користувача не знайдено.')
    return user


@router.get('/me', response_model=ProfileData)
async def get_profile(user_id: str = Depends(current_user_id)) -> ProfileData:
    try:
        return _profile(await _get_user(user_id))
    except DirectusError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.patch('/me', response_model=ProfileData)
async def update_profile(
    payload: ProfileUpdate,
    user_id: str = Depends(current_user_id),
) -> ProfileData:
    client = get_directus()
    data = payload.model_dump(mode='json')

    if payload.username:
        matches = await client.get_items(
            settings.directus_users_collection,
            filter_={
                '_and': [
                    {'username': {'_eq': payload.username}},
                    {'id': {'_neq': user_id}},
                ]
            },
            limit=1,
        )
        if matches:
            raise HTTPException(status_code=409, detail='Цей нікнейм уже зайнятий.')

    try:
        updated = await client.update_item(settings.directus_users_collection, user_id, data)
        return _profile(updated)
    except DirectusError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post('/avatar', response_model=ProfileData)
async def upload_avatar(
    file: UploadFile = File(...),
    user_id: str = Depends(current_user_id),
) -> ProfileData:
    content_type = file.content_type or ''
    extension = Path(file.filename or '').suffix.lower()
    if content_type not in ALLOWED_TYPES or extension not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=415, detail='Підтримуються лише JPG, PNG та WEBP.')

    content = await file.read(MAX_AVATAR_SIZE + 1)
    if len(content) > MAX_AVATAR_SIZE:
        raise HTTPException(status_code=413, detail='Фото завелике. Максимальний розмір — 5 МБ.')
    if not content:
        raise HTTPException(status_code=400, detail='Файл порожній.')

    client = get_directus()
    try:
        uploaded = await client.upload_file(
            filename=file.filename or f'avatar{extension}',
            content=content,
            content_type=content_type,
            title=f'Wishlle avatar {user_id}',
        )
        file_id = str(uploaded['id'])
        public_url = f"{settings.app_public_url.rstrip('/')}/backend/api/profile/avatar/{file_id}"
        updated = await client.update_item(
            settings.directus_users_collection,
            user_id,
            {'avatar_url': public_url},
        )
        return _profile(updated)
    except DirectusError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.delete('/avatar', response_model=ProfileData)
async def delete_avatar(user_id: str = Depends(current_user_id)) -> ProfileData:
    try:
        updated = await get_directus().update_item(
            settings.directus_users_collection,
            user_id,
            {'avatar_url': None},
        )
        return _profile(updated)
    except DirectusError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get('/avatar/{file_id}')
async def serve_avatar(file_id: str) -> Response:
    client = get_directus()
    try:
        owners = await client.get_items(
            settings.directus_users_collection,
            filter_={'avatar_url': {'_contains': file_id}},
            limit=1,
        )
        if not owners:
            raise HTTPException(status_code=404, detail='Фото не знайдено.')

        content, content_type = await client.get_file(file_id)
        return Response(
            content=content,
            media_type=content_type or 'application/octet-stream',
            headers={'Cache-Control': 'public, max-age=86400'},
        )
    except DirectusError as exc:
        raise HTTPException(status_code=404, detail='Фото не знайдено.') from exc
