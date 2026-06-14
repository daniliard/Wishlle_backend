from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile

from app.core.config import settings
from app.core.directus import DirectusError, get_directus
from app.profile.router import current_user_id

router = APIRouter()

MAX_COVER_SIZE = 8 * 1024 * 1024
ALLOWED_TYPES = {'image/jpeg', 'image/png', 'image/webp'}
ALLOWED_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp'}


@router.post('/cover')
async def upload_cover(
    file: UploadFile = File(...),
    user_id: str = Depends(current_user_id),
) -> dict[str, str]:
    content_type = file.content_type or ''
    extension = Path(file.filename or '').suffix.lower()
    if content_type not in ALLOWED_TYPES or extension not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=415, detail='Підтримуються лише JPG, PNG та WEBP.')

    content = await file.read(MAX_COVER_SIZE + 1)
    if len(content) > MAX_COVER_SIZE:
        raise HTTPException(status_code=413, detail='Зображення завелике. Максимальний розмір — 8 МБ.')
    if not content:
        raise HTTPException(status_code=400, detail='Файл порожній.')

    try:
        uploaded = await get_directus().upload_file(
            filename=file.filename or f'cover{extension}',
            content=content,
            content_type=content_type,
            title=f'Wishlle cover {user_id}',
        )
        file_id = str(uploaded['id'])
        public_url = f"{settings.app_public_url.rstrip('/')}/backend/api/media/{file_id}"
        return {'id': file_id, 'url': public_url}
    except DirectusError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get('/{file_id}')
async def get_cover(file_id: str) -> Response:
    try:
        content, content_type = await get_directus().get_file(file_id)
        return Response(
            content=content,
            media_type=content_type or 'application/octet-stream',
            headers={'Cache-Control': 'public, max-age=86400'},
        )
    except DirectusError as exc:
        raise HTTPException(status_code=404, detail='Зображення не знайдено.') from exc
