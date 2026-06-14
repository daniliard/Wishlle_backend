"""Резервування подарунків із чужих списків побажань.

Логіка згідно дипломної роботи:
- Reservation: id, item_id, reserved_by, created_at
- Один товар має щонайбільше одне активне резервування.
- available -> reserved при створенні, reserved -> available при скасуванні.
- Скасувати може той, хто зарезервував, АБО власник списку.
- Власник бачить статус reserved, але не бачить хто саме (приватність).
"""
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response, status

from app.core.config import settings
from app.core.directus import DirectusError, get_directus
from app.profile.router import current_user_id
from app.reservations.schemas import (
    FriendListView,
    ReservationCreate,
    ReservationData,
    ReservedItemData,
)

router = APIRouter()


def _relation_id(value: Any) -> str | None:
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
    return 'public'


async def _friendship_exists(user_id: str, owner_id: str) -> bool:
    rows = await get_directus().get_items(
        settings.directus_friendships_collection,
        filter_={'_and': [
            {'user_id': {'_eq': user_id}},
            {'friend_id': {'_eq': owner_id}},
        ]},
        limit=1,
    )
    return bool(rows)


async def _active_reservation(item_id: str) -> dict | None:
    rows = await get_directus().get_items(
        settings.directus_reservations_collection,
        filter_={'item_id': {'_eq': item_id}},
        limit=1,
    )
    return rows[0] if rows else None


# ── Перегляд повного чужого списку з товарами ──────────────────────────────
@router.get('/list/{list_id}', response_model=FriendListView)
async def view_friend_list(
    list_id: str,
    user_id: str = Depends(current_user_id),
) -> FriendListView:
    client = get_directus()
    try:
        wishlist = await client.get_item(settings.directus_wishes_collection, list_id)
        if not wishlist:
            raise HTTPException(status_code=404, detail='Список не знайдено.')

        owner_id = _relation_id(wishlist.get(settings.directus_wishes_owner_field))
        if not owner_id:
            raise HTTPException(status_code=404, detail='Список без власника.')

        is_owner = owner_id == user_id
        visibility = _list_visibility(wishlist)

        # Перевірка доступу
        if not is_owner:
            if visibility == 'private':
                raise HTTPException(status_code=403, detail='Список приватний.')
            if visibility == 'friends' and not await _friendship_exists(user_id, owner_id):
                raise HTTPException(status_code=403, detail='Список доступний лише друзям.')

        owner = await client.get_item(
            settings.directus_users_collection, owner_id,
            fields=['id', 'display_name', 'username'],
        )
        owner_name = (owner or {}).get('display_name') or (owner or {}).get('username') or 'Користувач'

        items = await client.get_items(
            settings.directus_wish_items_collection,
            filter_={'wishlist_id': {'_eq': list_id}},
            sort=['-date_created'],
        )

        # Збираємо резервування одним запитом
        item_ids = [str(i['id']) for i in items]
        reservations: dict[str, dict] = {}
        if item_ids:
            res_rows = await client.get_items(
                settings.directus_reservations_collection,
                filter_={'item_id': {'_in': item_ids}},
            )
            for r in res_rows:
                rid = _relation_id(r.get('item_id'))
                if rid:
                    reservations[rid] = r

        result_items: list[ReservedItemData] = []
        for item in items:
            iid = str(item['id'])
            res = reservations.get(iid)
            is_reserved = res is not None
            reserved_by_me = bool(res and _relation_id(res.get('reserved_by')) == user_id)

            result_items.append(ReservedItemData(
                id=iid,
                title=item.get('title') or 'Без назви',
                url=item.get('url'),
                price=item.get('price'),
                image_url=item.get('image_url'),
                notes=item.get('notes'),
                status='reserved' if is_reserved else 'available',
                reserved_by_me=reserved_by_me,
                is_reserved=is_reserved,
            ))

        return FriendListView(
            id=list_id,
            title=wishlist.get('title') or 'Список побажань',
            emoji=wishlist.get('emoji') or '🎁',
            visibility=visibility,
            owner_id=owner_id,
            owner_name=owner_name,
            items_count=len(result_items),
            items=result_items,
        )
    except DirectusError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


# ── Зарезервувати товар ────────────────────────────────────────────────────
@router.post('', response_model=ReservationData, status_code=status.HTTP_201_CREATED)
async def reserve_item(
    payload: ReservationCreate,
    user_id: str = Depends(current_user_id),
) -> ReservationData:
    client = get_directus()
    try:
        item = await client.get_item(settings.directus_wish_items_collection, payload.item_id)
        if not item:
            raise HTTPException(status_code=404, detail='Бажання не знайдено.')

        wishlist_id = _relation_id(item.get('wishlist_id'))
        if not wishlist_id:
            raise HTTPException(status_code=409, detail='Бажання не прив’язане до списку.')

        wishlist = await client.get_item(settings.directus_wishes_collection, wishlist_id)
        owner_id = _relation_id(wishlist.get(settings.directus_wishes_owner_field)) if wishlist else None

        # Не можна резервувати власні товари
        if owner_id == user_id:
            raise HTTPException(status_code=400, detail='Не можна резервувати власні бажання.')

        # Перевірка доступу до списку
        visibility = _list_visibility(wishlist or {})
        if visibility == 'private':
            raise HTTPException(status_code=403, detail='Список приватний.')
        if visibility == 'friends' and owner_id and not await _friendship_exists(user_id, owner_id):
            raise HTTPException(status_code=403, detail='Список доступний лише друзям.')

        # Один товар — одне активне резервування
        if await _active_reservation(payload.item_id):
            raise HTTPException(status_code=409, detail='Цей подарунок уже зарезервований.')

        created = await client.create_item(
            settings.directus_reservations_collection,
            {'item_id': payload.item_id, 'reserved_by': user_id},
        )
        # Оновлюємо статус товару
        await client.update_item(
            settings.directus_wish_items_collection,
            payload.item_id,
            {'status': 'reserved'},
        )

        return ReservationData(
            id=str(created['id']),
            item_id=payload.item_id,
            reserved_by=user_id,
            created_at=created.get('created_at') or created.get('date_created'),
        )
    except DirectusError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


# ── Скасувати резервування за item_id ──────────────────────────────────────
@router.delete('/item/{item_id}', status_code=status.HTTP_204_NO_CONTENT)
async def cancel_reservation(
    item_id: str,
    user_id: str = Depends(current_user_id),
) -> Response:
    client = get_directus()
    try:
        reservation = await _active_reservation(item_id)
        if not reservation:
            raise HTTPException(status_code=404, detail='Резервування не знайдено.')

        reserved_by = _relation_id(reservation.get('reserved_by'))

        # Власник списку теж може скасувати
        item = await client.get_item(settings.directus_wish_items_collection, item_id)
        wishlist_id = _relation_id(item.get('wishlist_id')) if item else None
        owner_id = None
        if wishlist_id:
            wishlist = await client.get_item(settings.directus_wishes_collection, wishlist_id)
            owner_id = _relation_id(wishlist.get(settings.directus_wishes_owner_field)) if wishlist else None

        if reserved_by != user_id and owner_id != user_id:
            raise HTTPException(status_code=403, detail='Можна скасувати лише власне резервування.')

        await client.delete_item(settings.directus_reservations_collection, reservation['id'])
        await client.update_item(
            settings.directus_wish_items_collection,
            item_id,
            {'status': 'available'},
        )
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except DirectusError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


# ── Мої резервування (товари які я зарезервував) ───────────────────────────
@router.get('/mine', response_model=list[ReservedItemData])
async def my_reservations(
    user_id: str = Depends(current_user_id),
) -> list[ReservedItemData]:
    client = get_directus()
    try:
        rows = await client.get_items(
            settings.directus_reservations_collection,
            filter_={'reserved_by': {'_eq': user_id}},
        )
        result: list[ReservedItemData] = []
        for r in rows:
            item_id = _relation_id(r.get('item_id'))
            if not item_id:
                continue
            item = await client.get_item(settings.directus_wish_items_collection, item_id)
            if not item:
                continue
            result.append(ReservedItemData(
                id=item_id,
                title=item.get('title') or 'Без назви',
                url=item.get('url'),
                price=item.get('price'),
                image_url=item.get('image_url'),
                notes=item.get('notes'),
                status='reserved',
                reserved_by_me=True,
                is_reserved=True,
            ))
        return result
    except DirectusError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
