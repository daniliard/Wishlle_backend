import json
from collections import defaultdict
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response, status

from app.core.config import settings
from app.core.directus import DirectusError, get_directus
from app.profile.router import current_user_id
from app.friends.schemas import (
    FriendCreate,
    FriendDetailsData,
    FriendshipData,
    FriendUpdate,
    FriendUserData,
    FriendWishlistData,
    SearchUserData,
)

router = APIRouter()


def _relation_id(value: Any) -> str | None:
    if isinstance(value, dict):
        value = value.get('id')
    if value is None:
        return None
    return str(value)


def _tags(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(tag).strip() for tag in value if str(tag).strip()]
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                return [str(tag).strip() for tag in parsed if str(tag).strip()]
        except (TypeError, ValueError):
            pass
        return [part.strip() for part in text.split(',') if part.strip()]
    return []


def _visible_user(user: dict, *, added: bool = True) -> FriendUserData:
    # Схема дипломної роботи не містить окремих серверних налаштувань
    # приватності. Тому показуємо базові поля профілю, а доступ до списків
    # контролюємо через wish_lists.visibility.
    return FriendUserData(
        id=str(user['id']),
        display_name=user.get('display_name') or user.get('username') or 'Користувач',
        username=user.get('username'),
        avatar_url=user.get('avatar_url'),
        birth_date=user.get('birth_date'),
    )


def _list_visibility(item: dict) -> str:
    # 1) Явне текстове поле visibility (public / friends / private)
    value = str(item.get('visibility') or '').strip().lower()
    if value in {'public', 'friends', 'private'}:
        return value

    # 2) Старе поле is_public у будь-якому форматі:
    #    bool True/False, число 1/0, рядок "true"/"false"/"1"/"0"/"yes"/"no"
    legacy = item.get('is_public')
    if isinstance(legacy, bool):
        return 'public' if legacy else 'private'
    if isinstance(legacy, (int, float)):
        return 'public' if int(legacy) != 0 else 'private'
    if isinstance(legacy, str):
        normalized = legacy.strip().lower()
        if normalized in {'1', 'true', 'yes', 'on', 'public'}:
            return 'public'
        if normalized in {'0', 'false', 'no', 'off', 'private'}:
            return 'private'

    # 3) Нічого не вказано — за замовчуванням список ПУБЛІЧНИЙ.
    #    (У вішліст-застосунку списки створюються щоб ними ділитися,
    #    тому відсутність прапорця не повинна ховати список від друзів.)
    return 'public'


def _can_view_wishlist(item: dict, owner: dict, *, added: bool) -> bool:
    visibility = _list_visibility(item)
    if visibility == 'private':
        return False
    if visibility == 'friends' and not added:
        return False
    return True


def _directus_error(exc: DirectusError) -> HTTPException:
    text = str(exc)
    lowered = text.lower()
    if 'friendships' in lowered and any(word in lowered for word in ('field', 'collection', 'column', 'unknown')):
        return HTTPException(
            status_code=500,
            detail=(
                'Перевір колекцію friendships у Directus. Вона повинна містити поля '
                'user_id, friend_id, nickname, tags та created_at/date_created.'
            ),
        )
    return HTTPException(status_code=502, detail=text)


async def _friendship_between(user_id: str, friend_id: str) -> dict | None:
    rows = await get_directus().get_items(
        settings.directus_friendships_collection,
        filter_={
            '_and': [
                {'user_id': {'_eq': user_id}},
                {'friend_id': {'_eq': friend_id}},
            ]
        },
        limit=1,
    )
    return rows[0] if rows else None


async def _owned_friendship(friendship_id: str, user_id: str) -> dict:
    row = await get_directus().get_item(settings.directus_friendships_collection, friendship_id)
    if not row or _relation_id(row.get('user_id')) != user_id:
        raise HTTPException(status_code=404, detail='Запис друга не знайдено.')
    return row


async def _users_by_ids(ids: list[str]) -> dict[str, dict]:
    if not ids:
        return {}
    users = await get_directus().get_items(
        settings.directus_users_collection,
        fields=[
            'id', 'display_name', 'username', 'avatar_url', 'birth_date',
        ],
        filter_={'id': {'_in': ids}},
    )
    return {str(user['id']): user for user in users}


async def _owner_wishlists(owner_id: str) -> list[dict]:
    """Load one user's lists without requiring owner_id in the response."""
    client = get_directus()
    filter_ = {settings.directus_wishes_owner_field: {'_eq': owner_id}}

    sort_candidates = [
        settings.directus_wishes_created_field,
        'created_at',
        'date_created',
    ]
    tried: set[str] = set()
    for field in sort_candidates:
        if not field or field in tried:
            continue
        tried.add(field)
        try:
            return await client.get_items(
                settings.directus_wishes_collection,
                filter_=filter_,
                sort=[f'-{field}'],
            )
        except DirectusError as exc:
            lowered = str(exc).lower()
            if not any(token in lowered for token in ('field', 'column', 'forbidden', 'permission')):
                raise

    rows = await client.get_items(
        settings.directus_wishes_collection,
        filter_=filter_,
    )
    rows.sort(
        key=lambda item: str(
            item.get(settings.directus_wishes_created_field)
            or item.get('created_at')
            or item.get('date_created')
            or ''
        ),
        reverse=True,
    )
    return rows


async def _wishlist_items(list_id: str) -> list[dict]:
    client = get_directus()
    filter_ = {'wishlist_id': {'_eq': list_id}}
    for field in ('date_created', 'created_at'):
        try:
            return await client.get_items(
                settings.directus_wish_items_collection,
                filter_=filter_,
                sort=[f'-{field}'],
            )
        except DirectusError as exc:
            lowered = str(exc).lower()
            if not any(token in lowered for token in ('field', 'column', 'forbidden', 'permission')):
                raise
    return await client.get_items(
        settings.directus_wish_items_collection,
        filter_=filter_,
    )


async def _accessible_lists_by_owner(owner_ids: list[str], users: dict[str, dict]) -> dict[str, list[dict]]:
    result: dict[str, list[dict]] = defaultdict(list)
    for owner_id in owner_ids:
        owner = users.get(owner_id)
        if not owner:
            continue
        lists = await _owner_wishlists(owner_id)
        result[owner_id] = [
            item
            for item in lists
            if _can_view_wishlist(item, owner, added=True)
        ]
    return result


@router.get('', response_model=list[FriendshipData])
async def list_friends(user_id: str = Depends(current_user_id)) -> list[FriendshipData]:
    try:
        rows = await get_directus().get_items(
            settings.directus_friendships_collection,
            filter_={'user_id': {'_eq': user_id}},
        )
        friend_ids = [value for row in rows if (value := _relation_id(row.get('friend_id')))]
        users = await _users_by_ids(friend_ids)
        available_lists = await _accessible_lists_by_owner(friend_ids, users)

        rows.sort(key=lambda row: str(row.get('created_at') or row.get('date_created') or ''), reverse=True)
        result: list[FriendshipData] = []
        for row in rows:
            friend_id = _relation_id(row.get('friend_id'))
            friend = users.get(friend_id or '')
            if not friend or not friend_id:
                continue
            result.append(
                FriendshipData(
                    id=str(row['id']),
                    friend_id=friend_id,
                    nickname=row.get('nickname'),
                    tags=_tags(row.get('tags')),
                    created_at=row.get('created_at') or row.get('date_created'),
                    accessible_lists_count=len(available_lists.get(friend_id, [])),
                    user=_visible_user(friend, added=True),
                )
            )
        return result
    except DirectusError as exc:
        raise _directus_error(exc) from exc


@router.get('/search', response_model=list[SearchUserData])
async def search_users(
    q: str,
    limit: int = 12,
    user_id: str = Depends(current_user_id),
) -> list[SearchUserData]:
    query = q.strip().lstrip('@')
    if len(query) < 2:
        return []
    limit = max(1, min(limit, 20))

    try:
        existing = await get_directus().get_items(
            settings.directus_friendships_collection,
            fields=['friend_id'],
            filter_={'user_id': {'_eq': user_id}},
        )
        existing_ids = {
            value for row in existing if (value := _relation_id(row.get('friend_id')))
        }

        candidates = await get_directus().get_items(
            settings.directus_users_collection,
            fields=[
                'id', 'display_name', 'username', 'avatar_url', 'birth_date',
            ],
            filter_={
                '_and': [
                    {'id': {'_neq': user_id}},
                    {
                        '_or': [
                            {'username': {'_icontains': query}},
                            {'display_name': {'_icontains': query}},
                        ]
                    },
                ]
            },
            limit=max(limit * 3, 30),
        )

        result: list[SearchUserData] = []
        for candidate in candidates:
            candidate_id = str(candidate['id'])
            visible = _visible_user(candidate, added=candidate_id in existing_ids)
            result.append(
                SearchUserData(
                    **visible.model_dump(),
                    already_added=candidate_id in existing_ids,
                    can_add=True,
                )
            )
            if len(result) >= limit:
                break
        return result
    except DirectusError as exc:
        raise _directus_error(exc) from exc


@router.post('', response_model=FriendshipData, status_code=status.HTTP_201_CREATED)
async def add_friend(
    payload: FriendCreate,
    user_id: str = Depends(current_user_id),
) -> FriendshipData:
    if payload.friend_id == user_id:
        raise HTTPException(status_code=400, detail='Не можна додати самого себе.')

    client = get_directus()
    try:
        target = await client.get_item(
            settings.directus_users_collection,
            payload.friend_id,
            fields=[
                'id', 'display_name', 'username', 'avatar_url', 'birth_date',
            ],
        )
        if not target:
            raise HTTPException(status_code=404, detail='Користувача не знайдено.')
        if await _friendship_between(user_id, payload.friend_id):
            raise HTTPException(status_code=409, detail='Цей користувач уже є у твоєму списку друзів.')

        created = await client.create_item(
            settings.directus_friendships_collection,
            {
                'user_id': user_id,
                'friend_id': payload.friend_id,
                'nickname': None,
                'tags': [],
            },
        )
        available = await _accessible_lists_by_owner([payload.friend_id], {payload.friend_id: target})
        return FriendshipData(
            id=str(created['id']),
            friend_id=payload.friend_id,
            nickname=created.get('nickname'),
            tags=_tags(created.get('tags')),
            created_at=created.get('created_at') or created.get('date_created'),
            accessible_lists_count=len(available.get(payload.friend_id, [])),
            user=_visible_user(target, added=True),
        )
    except DirectusError as exc:
        raise _directus_error(exc) from exc


@router.patch('/{friendship_id}', response_model=FriendshipData)
async def update_friend(
    friendship_id: str,
    payload: FriendUpdate,
    user_id: str = Depends(current_user_id),
) -> FriendshipData:
    client = get_directus()
    try:
        row = await _owned_friendship(friendship_id, user_id)
        update = payload.model_dump(exclude_unset=True)
        if update:
            row = await client.update_item(settings.directus_friendships_collection, friendship_id, update)

        friend_id = _relation_id(row.get('friend_id'))
        if not friend_id:
            raise HTTPException(status_code=404, detail='Користувача не знайдено.')
        target = await client.get_item(
            settings.directus_users_collection,
            friend_id,
            fields=[
                'id', 'display_name', 'username', 'avatar_url', 'birth_date',
            ],
        )
        if not target:
            raise HTTPException(status_code=404, detail='Користувача не знайдено.')
        available = await _accessible_lists_by_owner([friend_id], {friend_id: target})
        return FriendshipData(
            id=str(row['id']),
            friend_id=friend_id,
            nickname=row.get('nickname'),
            tags=_tags(row.get('tags')),
            created_at=row.get('created_at') or row.get('date_created'),
            accessible_lists_count=len(available.get(friend_id, [])),
            user=_visible_user(target, added=True),
        )
    except DirectusError as exc:
        raise _directus_error(exc) from exc


@router.delete('/{friendship_id}', status_code=status.HTTP_204_NO_CONTENT)
async def delete_friend(
    friendship_id: str,
    user_id: str = Depends(current_user_id),
) -> Response:
    try:
        await _owned_friendship(friendship_id, user_id)
        await get_directus().delete_item(settings.directus_friendships_collection, friendship_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except DirectusError as exc:
        raise _directus_error(exc) from exc


@router.get('/{friend_id}/details', response_model=FriendDetailsData)
async def friend_details(
    friend_id: str,
    user_id: str = Depends(current_user_id),
) -> FriendDetailsData:
    client = get_directus()
    try:
        friendship = await _friendship_between(user_id, friend_id)
        if not friendship:
            raise HTTPException(status_code=404, detail='Користувач не знаходиться у твоєму списку друзів.')

        target = await client.get_item(
            settings.directus_users_collection,
            friend_id,
            fields=[
                'id', 'display_name', 'username', 'avatar_url', 'birth_date',
            ],
        )
        if not target:
            raise HTTPException(status_code=404, detail='Користувача не знайдено.')

        available = await _accessible_lists_by_owner([friend_id], {friend_id: target})
        wishlists: list[FriendWishlistData] = []
        for wishlist in available.get(friend_id, []):
            list_id = str(wishlist['id'])
            items = await _wishlist_items(list_id)
            wishlists.append(
                FriendWishlistData(
                    id=list_id,
                    title=wishlist.get('title') or 'Список побажань',
                    emoji=wishlist.get('emoji') or '🎁',
                    visibility=_list_visibility(wishlist),
                    date_created=wishlist.get(settings.directus_wishes_created_field) or wishlist.get('date_created'),
                    items_count=len(items),
                    preview_items=[
                        {
                            'id': str(item.get('id')),
                            'title': item.get('title'),
                            'image_url': item.get('image_url'),
                            'status': item.get('status') or 'available',
                        }
                        for item in items[:4]
                    ],
                )
            )

        return FriendDetailsData(
            friendship_id=str(friendship['id']),
            nickname=friendship.get('nickname'),
            tags=_tags(friendship.get('tags')),
            user=_visible_user(target, added=True),
            wishlists=wishlists,
        )
    except DirectusError as exc:
        raise _directus_error(exc) from exc


@router.get('/{friend_id}/debug')
async def friend_debug(
    friend_id: str,
    user_id: str = Depends(current_user_id),
) -> dict:
    """ТИМЧАСОВИЙ діагностичний ендпоінт. Показує сирі дані зі списків друга.
    Видали після того як розберешся з видимістю."""
    client = get_directus()
    raw_lists = await _owner_wishlists(friend_id)
    target = await client.get_item(
        settings.directus_users_collection,
        friend_id,
        fields=['id', 'display_name', 'username'],
    )
    return {
        'friend_id': friend_id,
        'owner_field_used': settings.directus_wishes_owner_field,
        'raw_lists_count': len(raw_lists),
        'raw_lists': [
            {
                'id': str(item.get('id')),
                'title': item.get('title'),
                'is_public_raw': item.get('is_public'),
                'is_public_type': type(item.get('is_public')).__name__,
                'visibility_raw': item.get('visibility'),
                'computed_visibility': _list_visibility(item),
                'can_view': _can_view_wishlist(item, target or {}, added=True),
                'all_keys': list(item.keys()),
            }
            for item in raw_lists
        ],
    }
