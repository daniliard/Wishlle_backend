from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.modules.auth.service import AuthError, decode_access_token
from app.core.config import settings
from app.core.directus import DirectusError, get_directus
from app.modules.wishlists.schemas import (
    WishItemCreate,
    WishItemData,
    WishItemUpdate,
    WishlistCreate,
    WishlistData,
    WishlistUpdate,
)

router = APIRouter()
bearer = HTTPBearer(auto_error=False)


def current_user_id(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> str:
    if credentials is None or credentials.scheme.lower() != 'bearer':
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Потрібна авторизація.')
    try:
        return decode_access_token(credentials.credentials)
    except AuthError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc


def relation_id(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, dict):
        value = value.get('id')
    return str(value) if value is not None else None


def date_value(value: Any) -> str | None:
    return str(value) if value is not None else None


def wishlist_visibility(item: dict) -> str:
    value = str(item.get('visibility') or '').strip().lower()
    if value in {'public', 'friends', 'private'}:
        return value
    legacy = item.get('is_public')
    if isinstance(legacy, str):
        return 'public' if legacy.lower() in {'1', 'true', 'yes', 'public'} else 'private'
    if legacy is not None:
        return 'public' if bool(legacy) else 'private'
    return 'public'


def wishlist_emoji(item: dict) -> str:
    # У схемі дипломки ярлик списку зберігається у cover_image. Старе emoji
    # лишається як fallback, щоб не ламати вже створені записи.
    value = item.get('cover_image') or item.get('emoji') or '🎁'
    return str(value).strip() or '🎁'


def wishlist_data(item: dict, items: list[dict] | None = None) -> WishlistData:
    item_rows = items or []
    visibility = wishlist_visibility(item)
    return WishlistData(
        id=str(item['id']),
        title=item.get('title') or 'Без назви',
        emoji=wishlist_emoji(item),
        visibility=visibility,
        is_public=visibility == 'public',
        date_created=date_value(
            item.get(settings.directus_wishes_created_field)
            or item.get('created_at')
            or item.get('date_created')
        ),
        items_count=len(item_rows),
        available_count=sum(1 for row in item_rows if (row.get('status') or 'available') == 'available'),
        reserved_count=sum(1 for row in item_rows if row.get('status') == 'reserved'),
        preview_items=[{
            'id': str(row['id']),
            'title': row.get('title') or 'Без назви',
            'image_url': row.get('image_url'),
            'price': row.get('price'),
            'status': row.get('status') or 'available',
        } for row in item_rows[:4]],
    )


def item_data(item: dict) -> WishItemData:
    return WishItemData(
        id=str(item['id']),
        wishlist_id=relation_id(item.get('wishlist_id')) or '',
        title=item.get('title') or 'Без назви',
        url=item.get('url'),
        price=item.get('price'),
        image_url=item.get('image_url'),
        notes=item.get('notes') or item.get('description'),
        status=item.get('status') or 'available',
        date_created=date_value(item.get('date_created') or item.get('created_at')),
    )


def directus_error(exc: DirectusError) -> HTTPException:
    return HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))


def is_schema_mismatch(exc: DirectusError) -> bool:
    text = str(exc).lower()
    return any(token in text for token in ('field', 'column', 'does not exist', 'unknown'))


def create_payload(payload: WishlistCreate, user_id: str, legacy: bool = False) -> dict:
    base = {
        settings.directus_wishes_owner_field: user_id,
        'title': payload.title,
    }
    if legacy:
        base.update({'emoji': payload.emoji, 'is_public': payload.visibility == 'public'})
    else:
        base.update({'cover_image': payload.emoji, 'visibility': payload.visibility})
    return base


def update_payload(payload: WishlistUpdate, *, legacy: bool) -> dict:
    supplied = payload.model_fields_set
    data: dict[str, Any] = {}
    if 'title' in supplied:
        data['title'] = payload.title
    if 'emoji' in supplied:
        data['emoji' if legacy else 'cover_image'] = payload.emoji
    if 'visibility' in supplied:
        data['is_public' if legacy else 'visibility'] = (
            payload.visibility == 'public' if legacy else payload.visibility
        )
    return data


async def get_owned_list(list_id: str, user_id: str) -> dict:
    wishlist = await get_directus().get_item(settings.directus_wishes_collection, list_id)
    if not wishlist:
        raise HTTPException(status_code=404, detail='Список не знайдено.')
    if relation_id(wishlist.get(settings.directus_wishes_owner_field)) != str(user_id):
        raise HTTPException(status_code=403, detail='Немає доступу до цього списку.')
    return wishlist


async def get_owned_item(item_id: str, user_id: str) -> tuple[dict, dict]:
    item = await get_directus().get_item(settings.directus_wish_items_collection, item_id)
    if not item:
        raise HTTPException(status_code=404, detail='Бажання не знайдено.')
    wishlist_id = relation_id(item.get('wishlist_id'))
    if not wishlist_id:
        raise HTTPException(status_code=409, detail='Бажання не прив’язане до списку.')
    wishlist = await get_owned_list(wishlist_id, user_id)
    return item, wishlist


async def list_items(list_id: str) -> list[dict]:
    client = get_directus()
    for sort_field in ('date_created', 'created_at'):
        try:
            return await client.get_items(
                settings.directus_wish_items_collection,
                filter_={'wishlist_id': {'_eq': list_id}},
                sort=[f'-{sort_field}'],
            )
        except DirectusError as exc:
            if not is_schema_mismatch(exc):
                raise
    return await client.get_items(
        settings.directus_wish_items_collection,
        filter_={'wishlist_id': {'_eq': list_id}},
    )


@router.get('', response_model=list[WishlistData])
async def get_my_wishlists(user_id: str = Depends(current_user_id)) -> list[WishlistData]:
    client = get_directus()
    try:
        try:
            wishlists = await client.get_items(
                settings.directus_wishes_collection,
                filter_={settings.directus_wishes_owner_field: {'_eq': user_id}},
                sort=[f'-{settings.directus_wishes_created_field}'],
            )
        except DirectusError as exc:
            if not is_schema_mismatch(exc):
                raise
            wishlists = await client.get_items(
                settings.directus_wishes_collection,
                filter_={settings.directus_wishes_owner_field: {'_eq': user_id}},
            )
        result: list[WishlistData] = []
        for wishlist in wishlists:
            items = await list_items(str(wishlist['id']))
            result.append(wishlist_data(wishlist, items))
        return result
    except DirectusError as exc:
        raise directus_error(exc) from exc


@router.post('', response_model=WishlistData, status_code=status.HTTP_201_CREATED)
async def create_wishlist(
    payload: WishlistCreate,
    user_id: str = Depends(current_user_id),
) -> WishlistData:
    client = get_directus()
    try:
        try:
            created = await client.create_item(
                settings.directus_wishes_collection,
                create_payload(payload, user_id, legacy=False),
            )
        except DirectusError as exc:
            if not is_schema_mismatch(exc):
                raise
            created = await client.create_item(
                settings.directus_wishes_collection,
                create_payload(payload, user_id, legacy=True),
            )
        return wishlist_data(created)
    except DirectusError as exc:
        raise directus_error(exc) from exc


@router.patch('/{list_id}', response_model=WishlistData)
async def update_wishlist(
    list_id: str,
    payload: WishlistUpdate,
    user_id: str = Depends(current_user_id),
) -> WishlistData:
    client = get_directus()
    try:
        current = await get_owned_list(list_id, user_id)
        uses_legacy = 'visibility' not in current and 'cover_image' not in current
        update = update_payload(payload, legacy=uses_legacy)
        try:
            updated = await client.update_item(
                settings.directus_wishes_collection, list_id, update
            ) if update else current
        except DirectusError as exc:
            if not is_schema_mismatch(exc):
                raise
            # Дозволяє оновити стару інсталяцію з emoji/is_public і нову зі
            # схемою дипломки без ручного переписування даних.
            updated = await client.update_item(
                settings.directus_wishes_collection,
                list_id,
                update_payload(payload, legacy=not uses_legacy),
            )
        return wishlist_data(updated, await list_items(list_id))
    except DirectusError as exc:
        raise directus_error(exc) from exc


@router.delete('/{list_id}', status_code=status.HTTP_204_NO_CONTENT)
async def delete_wishlist(
    list_id: str,
    user_id: str = Depends(current_user_id),
) -> Response:
    try:
        await get_owned_list(list_id, user_id)
        for item in await list_items(list_id):
            await get_directus().delete_item(settings.directus_wish_items_collection, item['id'])
        await get_directus().delete_item(settings.directus_wishes_collection, list_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except DirectusError as exc:
        raise directus_error(exc) from exc


@router.get('/{list_id}/items', response_model=list[WishItemData])
async def get_wishlist_items(
    list_id: str,
    user_id: str = Depends(current_user_id),
) -> list[WishItemData]:
    try:
        await get_owned_list(list_id, user_id)
        return [item_data(item) for item in await list_items(list_id)]
    except DirectusError as exc:
        raise directus_error(exc) from exc


@router.post('/{list_id}/items', response_model=WishItemData, status_code=status.HTTP_201_CREATED)
async def create_wishlist_item(
    list_id: str,
    payload: WishItemCreate,
    user_id: str = Depends(current_user_id),
) -> WishItemData:
    try:
        await get_owned_list(list_id, user_id)
        data = payload.model_dump(mode='json')
        data['wishlist_id'] = list_id
        data['status'] = 'available'
        created = await get_directus().create_item(settings.directus_wish_items_collection, data)
        return item_data(created)
    except DirectusError as exc:
        raise directus_error(exc) from exc


@router.patch('/items/{item_id}', response_model=WishItemData)
async def update_wishlist_item(
    item_id: str,
    payload: WishItemUpdate,
    user_id: str = Depends(current_user_id),
) -> WishItemData:
    try:
        current, _ = await get_owned_item(item_id, user_id)
        update = payload.model_dump(mode='json', exclude_unset=True)
        updated = await get_directus().update_item(
            settings.directus_wish_items_collection, item_id, update
        ) if update else current
        return item_data(updated)
    except DirectusError as exc:
        raise directus_error(exc) from exc


@router.delete('/items/{item_id}', status_code=status.HTTP_204_NO_CONTENT)
async def delete_wishlist_item(
    item_id: str,
    user_id: str = Depends(current_user_id),
) -> Response:
    try:
        await get_owned_item(item_id, user_id)
        await get_directus().delete_item(settings.directus_wish_items_collection, item_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except DirectusError as exc:
        raise directus_error(exc) from exc
