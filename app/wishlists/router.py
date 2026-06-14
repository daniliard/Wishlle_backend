from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.auth.service import AuthError, decode_access_token
from app.core.config import settings
from app.core.directus import DirectusError, get_directus
from app.wishlists.schemas import (
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


def item_data(item: dict) -> WishItemData:
    return WishItemData(
        id=str(item['id']),
        wishlist_id=relation_id(item.get('wishlist_id')) or '',
        title=item.get('title') or 'Без назви',
        url=item.get('url'),
        price=item.get('price'),
        image_url=item.get('image_url'),
        notes=item.get('notes'),
        status=item.get('status') or 'available',
        date_created=date_value(item.get('date_created')),
    )


def directus_error(exc: DirectusError) -> HTTPException:
    return HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))


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


@router.get('', response_model=list[WishlistData])
async def get_my_wishlists(user_id: str = Depends(current_user_id)) -> list[WishlistData]:
    client = get_directus()
    try:
        wishlists = await client.get_items(
            settings.directus_wishes_collection,
            filter_={settings.directus_wishes_owner_field: {'_eq': user_id}},
            sort=[f'-{settings.directus_wishes_created_field}'],
        )

        result: list[WishlistData] = []
        for wishlist in wishlists:
            list_id = str(wishlist['id'])
            items = await client.get_items(
                settings.directus_wish_items_collection,
                filter_={'wishlist_id': {'_eq': list_id}},
                sort=['-date_created'],
            )
            available = sum(1 for item in items if (item.get('status') or 'available') == 'available')
            reserved = sum(1 for item in items if item.get('status') == 'reserved')
            preview = [
                {
                    'id': str(item['id']),
                    'title': item.get('title') or 'Без назви',
                    'image_url': item.get('image_url'),
                    'price': item.get('price'),
                    'status': item.get('status') or 'available',
                }
                for item in items[:4]
            ]
            result.append(WishlistData(
                id=list_id,
                title=wishlist.get('title') or 'Без назви',
                emoji=wishlist.get('emoji') or '🎁',
                is_public=bool(wishlist.get('is_public', True)),
                date_created=date_value(wishlist.get(settings.directus_wishes_created_field)),
                items_count=len(items),
                available_count=available,
                reserved_count=reserved,
                preview_items=preview,
            ))
        return result
    except DirectusError as exc:
        raise directus_error(exc) from exc


@router.post('', response_model=WishlistData, status_code=status.HTTP_201_CREATED)
async def create_wishlist(
    payload: WishlistCreate,
    user_id: str = Depends(current_user_id),
) -> WishlistData:
    try:
        data = payload.model_dump(mode='json')
        data[settings.directus_wishes_owner_field] = user_id
        created = await get_directus().create_item(settings.directus_wishes_collection, data)
        return WishlistData(
            id=str(created['id']),
            title=created.get('title') or payload.title,
            emoji=created.get('emoji') or payload.emoji,
            is_public=bool(created.get('is_public', payload.is_public)),
            date_created=date_value(created.get(settings.directus_wishes_created_field)),
        )
    except DirectusError as exc:
        raise directus_error(exc) from exc


@router.patch('/{list_id}', response_model=WishlistData)
async def update_wishlist(
    list_id: str,
    payload: WishlistUpdate,
    user_id: str = Depends(current_user_id),
) -> WishlistData:
    try:
        current = await get_owned_list(list_id, user_id)
        update = payload.model_dump(mode='json', exclude_unset=True)
        updated = await get_directus().update_item(settings.directus_wishes_collection, list_id, update) if update else current
        items = await get_directus().get_items(
            settings.directus_wish_items_collection,
            filter_={'wishlist_id': {'_eq': list_id}},
            sort=['-date_created'],
        )
        return WishlistData(
            id=str(updated['id']),
            title=updated.get('title') or current.get('title') or 'Без назви',
            emoji=updated.get('emoji') or current.get('emoji') or '🎁',
            is_public=bool(updated.get('is_public', current.get('is_public', True))),
            date_created=date_value(updated.get(settings.directus_wishes_created_field) or current.get(settings.directus_wishes_created_field)),
            items_count=len(items),
            available_count=sum(1 for item in items if (item.get('status') or 'available') == 'available'),
            reserved_count=sum(1 for item in items if item.get('status') == 'reserved'),
            preview_items=[{
                'id': str(item['id']),
                'title': item.get('title') or 'Без назви',
                'image_url': item.get('image_url'),
                'price': item.get('price'),
                'status': item.get('status') or 'available',
            } for item in items[:4]],
        )
    except DirectusError as exc:
        raise directus_error(exc) from exc


@router.delete('/{list_id}', status_code=status.HTTP_204_NO_CONTENT)
async def delete_wishlist(
    list_id: str,
    user_id: str = Depends(current_user_id),
) -> Response:
    try:
        await get_owned_list(list_id, user_id)
        items = await get_directus().get_items(
            settings.directus_wish_items_collection,
            filter_={'wishlist_id': {'_eq': list_id}},
        )
        for item in items:
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
        items = await get_directus().get_items(
            settings.directus_wish_items_collection,
            filter_={'wishlist_id': {'_eq': list_id}},
            sort=['-date_created'],
        )
        return [item_data(item) for item in items]
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
        updated = await get_directus().update_item(settings.directus_wish_items_collection, item_id, update) if update else current
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
