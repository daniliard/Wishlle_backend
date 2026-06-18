"""Публічний доступ до списків побажань — БЕЗ авторизації.

Доступний лише для списків зі статусом visibility='public'.
Дозволяє переглянути список та анонімно зарезервувати подарунок.
Посилання: /share/{list_id}
"""
from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from app.core.config import settings
from app.core.directus import DirectusError, get_directus
from app.modules.public.schemas import PublicItemData, PublicListData

router = APIRouter()


def _rel(value: Any) -> str | None:
    if isinstance(value, dict):
        value = value.get('id')
    return str(value) if value is not None else None


def _list_visibility(item: dict) -> str:
    value = str(item.get('visibility') or '').strip().lower()
    if value in {'public', 'friends', 'private'}:
        return value
    legacy = item.get('is_public')
    if isinstance(legacy, bool):
        return 'public' if legacy else 'private'
    if isinstance(legacy, (int, float)):
        return 'public' if int(legacy) != 0 else 'private'
    if isinstance(legacy, str):
        n = legacy.strip().lower()
        if n in {'1', 'true', 'yes', 'on', 'public'}:
            return 'public'
        if n in {'0', 'false', 'no', 'off', 'private'}:
            return 'private'
    return 'private'  # для публічного доступу за замовчуванням ховаємо


@router.get('/list/{list_id}', response_model=PublicListData)
async def public_list(list_id: str) -> PublicListData:
    """Перегляд публічного списку без авторизації."""
    client = get_directus()
    try:
        wishlist = await client.get_item(settings.directus_wishes_collection, list_id)
        if not wishlist:
            raise HTTPException(status_code=404, detail='Список не знайдено.')

        if _list_visibility(wishlist) != 'public':
            raise HTTPException(status_code=403, detail='Цей список не є публічним.')

        owner_id = _rel(wishlist.get(settings.directus_wishes_owner_field))
        owner = await client.get_item(
            settings.directus_users_collection, owner_id,
            fields=['id', 'display_name', 'username', 'avatar_url'],
        ) if owner_id else None
        owner_name = (owner or {}).get('display_name') or (owner or {}).get('username') or 'Користувач'

        items = await client.get_items(
            settings.directus_wish_items_collection,
            filter_={'wishlist_id': {'_eq': list_id}},
            sort=['-date_created'],
        )

        # Резервування
        item_ids = [str(i['id']) for i in items]
        reserved_ids: set[str] = set()
        if item_ids:
            res_rows = await client.get_items(
                settings.directus_reservations_collection,
                filter_={'item_id': {'_in': item_ids}},
            )
            reserved_ids = {_rel(r.get('item_id')) for r in res_rows if _rel(r.get('item_id'))}

        result_items: list[PublicItemData] = []
        for item in items:
            iid = str(item['id'])
            is_reserved = iid in reserved_ids
            result_items.append(PublicItemData(
                id=iid,
                title=item.get('title') or 'Без назви',
                # Посилання приховуємо для зарезервованих (як у приватному перегляді)
                url=item.get('url') if not is_reserved else None,
                price=item.get('price'),
                image_url=item.get('image_url'),
                notes=item.get('notes'),
                is_reserved=is_reserved,
            ))

        return PublicListData(
            id=list_id,
            title=wishlist.get('title') or 'Список побажань',
            emoji=wishlist.get('emoji') or '🎁',
            owner_name=owner_name,
            owner_avatar=(owner or {}).get('avatar_url'),
            items_count=len(result_items),
            items=result_items,
        )
    except DirectusError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


class AnonReserveRequest(BaseModel):
    item_id: str


@router.post('/reserve', status_code=status.HTTP_201_CREATED)
async def public_reserve(payload: AnonReserveRequest) -> dict:
    """Анонімне резервування товару з публічного списку."""
    client = get_directus()
    try:
        item = await client.get_item(settings.directus_wish_items_collection, payload.item_id)
        if not item:
            raise HTTPException(status_code=404, detail='Бажання не знайдено.')

        wishlist_id = _rel(item.get('wishlist_id'))
        wishlist = await client.get_item(settings.directus_wishes_collection, wishlist_id) if wishlist_id else None
        if not wishlist or _list_visibility(wishlist) != 'public':
            raise HTTPException(status_code=403, detail='Список недоступний.')

        # Перевірка чи вже зарезервовано
        existing = await client.get_items(
            settings.directus_reservations_collection,
            filter_={'item_id': {'_eq': payload.item_id}},
            limit=1,
        )
        if existing:
            raise HTTPException(status_code=409, detail='Цей подарунок уже зарезервований.')

        # reserved_by лишаємо порожнім — анонімне резервування
        await client.create_item(
            settings.directus_reservations_collection,
            {'item_id': payload.item_id},
        )
        await client.update_item(
            settings.directus_wish_items_collection,
            payload.item_id,
            {'status': 'reserved'},
        )
        return {'item_id': payload.item_id, 'status': 'reserved'}
    except DirectusError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
