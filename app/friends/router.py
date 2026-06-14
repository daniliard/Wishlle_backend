import json
from collections import defaultdict
from html import escape
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response, status

from app.core.config import settings
from app.core.directus import DirectusError, get_directus
from app.notifications.service import create_notification
from app.profile.router import current_user_id
from app.friends.schemas import (
    FriendCreate,
    FriendDetailsData,
    FriendRequestData,
    FriendRequestSentData,
    FriendshipData,
    FriendUpdate,
    FriendUserData,
    FriendWishlistData,
    SearchUserData,
)

router = APIRouter()

NOTIF_USER_FIELD = settings.directus_notifications_user_field
NOTIF_TYPE_FIELD = settings.directus_notifications_days_field
NOTIF_RELATED_FIELD = settings.directus_notifications_event_field


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
    return FriendUserData(
        id=str(user['id']),
        display_name=user.get('display_name') or user.get('username') or 'Користувач',
        username=user.get('username'),
        avatar_url=user.get('avatar_url'),
        birth_date=user.get('birth_date'),
    )


def _user_name(user: dict | None) -> str:
    if not user:
        return 'Користувач'
    return str(user.get('display_name') or user.get('username') or 'Користувач')


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
        normalized = legacy.strip().lower()
        if normalized in {'1', 'true', 'yes', 'on', 'public'}:
            return 'public'
        if normalized in {'0', 'false', 'no', 'off', 'private'}:
            return 'private'
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
    if 'notifications' in lowered:
        return HTTPException(
            status_code=500,
            detail=(
                'Перевір права службового користувача Directus на колекцію notifications: '
                'потрібні Read, Create, Update і Delete для полів recipient_id, type, '
                'related_id, sent_at, delivered та error_message.'
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


async def _ensure_friendship(user_id: str, friend_id: str) -> dict:
    existing = await _friendship_between(user_id, friend_id)
    if existing:
        return existing
    client = get_directus()
    try:
        return await client.create_item(
            settings.directus_friendships_collection,
            {
                'user_id': user_id,
                'friend_id': friend_id,
                'nickname': None,
                'tags': [],
            },
        )
    except DirectusError:
        # Якщо дві вкладки одночасно прийняли заявку, унікальний індекс міг
        # уже створити запис. У такому випадку просто повертаємо його.
        existing = await _friendship_between(user_id, friend_id)
        if existing:
            return existing
        raise


async def _owned_friendship(friendship_id: str, user_id: str) -> dict:
    row = await get_directus().get_item(settings.directus_friendships_collection, friendship_id)
    if not row or _relation_id(row.get('user_id')) != user_id:
        raise HTTPException(status_code=404, detail='Запис друга не знайдено.')
    return row


async def _users_by_ids(ids: list[str]) -> dict[str, dict]:
    unique_ids = list(dict.fromkeys(ids))
    if not unique_ids:
        return {}
    users = await get_directus().get_items(
        settings.directus_users_collection,
        fields=['id', 'display_name', 'username', 'avatar_url', 'birth_date'],
        filter_={'id': {'_in': unique_ids}},
    )
    return {str(user['id']): user for user in users}


async def _pending_request(recipient_id: str, requester_id: str) -> dict | None:
    rows = await get_directus().get_items(
        settings.directus_notifications_collection,
        filter_={
            '_and': [
                {NOTIF_USER_FIELD: {'_eq': recipient_id}},
                {NOTIF_TYPE_FIELD: {'_eq': 'friend_request'}},
                {NOTIF_RELATED_FIELD: {'_eq': requester_id}},
            ]
        },
        limit=1,
    )
    return rows[0] if rows else None


async def _owned_request(request_id: str, recipient_id: str) -> dict:
    row = await get_directus().get_item(settings.directus_notifications_collection, request_id)
    if (
        not row
        or _relation_id(row.get(NOTIF_USER_FIELD)) != recipient_id
        or str(row.get(NOTIF_TYPE_FIELD) or '') != 'friend_request'
    ):
        raise HTTPException(status_code=404, detail='Заявку в друзі не знайдено.')
    if not _relation_id(row.get(NOTIF_RELATED_FIELD)):
        raise HTTPException(status_code=409, detail='У заявки відсутній відправник.')
    return row


async def _delete_pending_requests(recipient_id: str, requester_id: str) -> None:
    rows = await get_directus().get_items(
        settings.directus_notifications_collection,
        fields=['id'],
        filter_={
            '_and': [
                {NOTIF_USER_FIELD: {'_eq': recipient_id}},
                {NOTIF_TYPE_FIELD: {'_eq': 'friend_request'}},
                {NOTIF_RELATED_FIELD: {'_eq': requester_id}},
            ]
        },
    )
    for row in rows:
        await get_directus().delete_item(settings.directus_notifications_collection, row['id'])


async def _owner_wishlists(owner_id: str) -> list[dict]:
    client = get_directus()
    filter_ = {settings.directus_wishes_owner_field: {'_eq': owner_id}}

    sort_candidates = [settings.directus_wishes_created_field, 'created_at', 'date_created']
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

    rows = await client.get_items(settings.directus_wishes_collection, filter_=filter_)
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
    return await client.get_items(settings.directus_wish_items_collection, filter_=filter_)


async def _accessible_lists_by_owner(owner_ids: list[str], users: dict[str, dict]) -> dict[str, list[dict]]:
    result: dict[str, list[dict]] = defaultdict(list)
    for owner_id in owner_ids:
        owner = users.get(owner_id)
        if not owner:
            continue
        lists = await _owner_wishlists(owner_id)
        result[owner_id] = [
            item for item in lists if _can_view_wishlist(item, owner, added=True)
        ]
    return result


async def _friendship_data(row: dict, friend: dict) -> FriendshipData:
    friend_id = _relation_id(row.get('friend_id'))
    if not friend_id:
        raise HTTPException(status_code=404, detail='Користувача не знайдено.')
    available = await _accessible_lists_by_owner([friend_id], {friend_id: friend})
    return FriendshipData(
        id=str(row['id']),
        friend_id=friend_id,
        nickname=row.get('nickname'),
        tags=_tags(row.get('tags')),
        created_at=row.get('created_at') or row.get('date_created'),
        accessible_lists_count=len(available.get(friend_id, [])),
        user=_visible_user(friend, added=True),
    )


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


@router.get('/requests', response_model=list[FriendRequestData])
async def list_friend_requests(user_id: str = Depends(current_user_id)) -> list[FriendRequestData]:
    try:
        rows = await get_directus().get_items(
            settings.directus_notifications_collection,
            filter_={
                '_and': [
                    {NOTIF_USER_FIELD: {'_eq': user_id}},
                    {NOTIF_TYPE_FIELD: {'_eq': 'friend_request'}},
                ]
            },
        )
        requester_ids = [
            value for row in rows if (value := _relation_id(row.get(NOTIF_RELATED_FIELD)))
        ]
        users = await _users_by_ids(requester_ids)
        rows.sort(key=lambda row: str(row.get('sent_at') or row.get('date_created') or ''), reverse=True)

        result: list[FriendRequestData] = []
        for row in rows:
            requester_id = _relation_id(row.get(NOTIF_RELATED_FIELD))
            requester = users.get(requester_id or '')
            if not requester or not requester_id:
                continue
            # Застаріла заявка не повинна висіти, якщо дружба вже створена.
            if await _friendship_between(user_id, requester_id):
                continue
            result.append(
                FriendRequestData(
                    id=str(row['id']),
                    requester_id=requester_id,
                    created_at=row.get('sent_at') or row.get('date_created'),
                    is_read=bool(row.get('delivered', False)),
                    user=_visible_user(requester, added=False),
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
            fields=['id', 'display_name', 'username', 'avatar_url', 'birth_date'],
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
        candidate_ids = [str(candidate['id']) for candidate in candidates]

        outgoing: dict[str, str] = {}
        incoming: dict[str, str] = {}
        if candidate_ids:
            outgoing_rows = await get_directus().get_items(
                settings.directus_notifications_collection,
                fields=['id', NOTIF_USER_FIELD, NOTIF_RELATED_FIELD],
                filter_={
                    '_and': [
                        {NOTIF_TYPE_FIELD: {'_eq': 'friend_request'}},
                        {NOTIF_RELATED_FIELD: {'_eq': user_id}},
                        {NOTIF_USER_FIELD: {'_in': candidate_ids}},
                    ]
                },
            )
            for row in outgoing_rows:
                recipient = _relation_id(row.get(NOTIF_USER_FIELD))
                if recipient:
                    outgoing[recipient] = str(row['id'])

            incoming_rows = await get_directus().get_items(
                settings.directus_notifications_collection,
                fields=['id', NOTIF_RELATED_FIELD],
                filter_={
                    '_and': [
                        {NOTIF_TYPE_FIELD: {'_eq': 'friend_request'}},
                        {NOTIF_USER_FIELD: {'_eq': user_id}},
                        {NOTIF_RELATED_FIELD: {'_in': candidate_ids}},
                    ]
                },
            )
            for row in incoming_rows:
                requester = _relation_id(row.get(NOTIF_RELATED_FIELD))
                if requester:
                    incoming[requester] = str(row['id'])

        result: list[SearchUserData] = []
        for candidate in candidates:
            candidate_id = str(candidate['id'])
            if candidate_id in existing_ids:
                request_status = 'friends'
                request_id = None
            elif candidate_id in incoming:
                request_status = 'incoming'
                request_id = incoming[candidate_id]
            elif candidate_id in outgoing:
                request_status = 'outgoing'
                request_id = outgoing[candidate_id]
            else:
                request_status = 'none'
                request_id = None

            visible = _visible_user(candidate, added=request_status == 'friends')
            result.append(
                SearchUserData(
                    **visible.model_dump(),
                    already_added=request_status == 'friends',
                    can_add=request_status == 'none',
                    request_status=request_status,
                    request_id=request_id,
                )
            )
            if len(result) >= limit:
                break
        return result
    except DirectusError as exc:
        raise _directus_error(exc) from exc


@router.post('', response_model=FriendRequestSentData, status_code=status.HTTP_201_CREATED)
async def add_friend(
    payload: FriendCreate,
    user_id: str = Depends(current_user_id),
) -> FriendRequestSentData:
    if payload.friend_id == user_id:
        raise HTTPException(status_code=400, detail='Не можна додати самого себе.')

    client = get_directus()
    try:
        target = await client.get_item(
            settings.directus_users_collection,
            payload.friend_id,
            fields=['id', 'display_name', 'username', 'avatar_url', 'birth_date'],
        )
        if not target:
            raise HTTPException(status_code=404, detail='Користувача не знайдено.')
        if await _friendship_between(user_id, payload.friend_id):
            raise HTTPException(status_code=409, detail='Цей користувач уже є у твоїх друзях.')

        outgoing = await _pending_request(payload.friend_id, user_id)
        if outgoing:
            raise HTTPException(status_code=409, detail='Заявку цьому користувачу вже надіслано.')

        incoming = await _pending_request(user_id, payload.friend_id)
        if incoming:
            raise HTTPException(
                status_code=409,
                detail='Цей користувач уже надіслав тобі заявку. Прийми її у блоці заявок.',
            )

        requester = await client.get_item(
            settings.directus_users_collection,
            user_id,
            fields=['id', 'display_name', 'username'],
        )
        requester_name = escape(_user_name(requester))
        created = await create_notification(
            recipient_id=payload.friend_id,
            notif_type='friend_request',
            related_id=user_id,
            telegram_text=(
                f'👋 <b>{requester_name}</b> хоче додати тебе в друзі у Wishlle.\n'
                'Відкрий застосунок, щоб прийняти або відхилити заявку.'
            ),
            required=True,
        )
        if not created:
            raise HTTPException(status_code=502, detail='Не вдалося створити заявку в друзі.')

        return FriendRequestSentData(
            id=str(created['id']),
            recipient_id=payload.friend_id,
            created_at=created.get('sent_at') or created.get('date_created'),
        )
    except DirectusError as exc:
        raise _directus_error(exc) from exc


@router.post('/requests/{request_id}/accept', response_model=FriendshipData)
async def accept_friend_request(
    request_id: str,
    user_id: str = Depends(current_user_id),
) -> FriendshipData:
    client = get_directus()
    try:
        request_row = await _owned_request(request_id, user_id)
        requester_id = _relation_id(request_row.get(NOTIF_RELATED_FIELD))
        if not requester_id or requester_id == user_id:
            raise HTTPException(status_code=409, detail='Некоректна заявка в друзі.')

        requester = await client.get_item(
            settings.directus_users_collection,
            requester_id,
            fields=['id', 'display_name', 'username', 'avatar_url', 'birth_date'],
        )
        accepter = await client.get_item(
            settings.directus_users_collection,
            user_id,
            fields=['id', 'display_name', 'username'],
        )
        if not requester:
            raise HTTPException(status_code=404, detail='Відправника заявки не знайдено.')

        # Без нового поля status: прийнята дружба — це два звичайні записи
        # у friendships, по одному для кожного користувача.
        own_row = await _ensure_friendship(user_id, requester_id)
        await _ensure_friendship(requester_id, user_id)

        # Сам запис notification і є pending-заявкою. Після відповіді видаляємо
        # його, щоб він більше не рахувався активним.
        await _delete_pending_requests(user_id, requester_id)
        # Якщо обидва користувачі встигли надіслати заявки один одному — чистимо
        # і дзеркальну заявку.
        await _delete_pending_requests(requester_id, user_id)

        accepter_name = escape(_user_name(accepter))
        await create_notification(
            recipient_id=requester_id,
            notif_type='friend_accepted',
            related_id=user_id,
            telegram_text=f'🤝 <b>{accepter_name}</b> прийняв(ла) твою заявку в друзі у Wishlle.',
        )

        return await _friendship_data(own_row, requester)
    except DirectusError as exc:
        raise _directus_error(exc) from exc


@router.delete('/requests/{request_id}', status_code=status.HTTP_204_NO_CONTENT)
async def reject_friend_request(
    request_id: str,
    user_id: str = Depends(current_user_id),
) -> Response:
    try:
        await _owned_request(request_id, user_id)
        await get_directus().delete_item(settings.directus_notifications_collection, request_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
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
            fields=['id', 'display_name', 'username', 'avatar_url', 'birth_date'],
        )
        if not target:
            raise HTTPException(status_code=404, detail='Користувача не знайдено.')
        return await _friendship_data(row, target)
    except DirectusError as exc:
        raise _directus_error(exc) from exc


@router.delete('/{friendship_id}', status_code=status.HTTP_204_NO_CONTENT)
async def delete_friend(
    friendship_id: str,
    user_id: str = Depends(current_user_id),
) -> Response:
    try:
        row = await _owned_friendship(friendship_id, user_id)
        friend_id = _relation_id(row.get('friend_id'))
        await get_directus().delete_item(settings.directus_friendships_collection, friendship_id)

        # Після підтвердження дружба симетрична, тому видалення прибирає її
        # у обох користувачів. Старі односторонні записи також не ламаються.
        if friend_id:
            reverse = await _friendship_between(friend_id, user_id)
            if reverse:
                await get_directus().delete_item(settings.directus_friendships_collection, reverse['id'])
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
            fields=['id', 'display_name', 'username', 'avatar_url', 'birth_date'],
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
                    date_created=(
                        wishlist.get(settings.directus_wishes_created_field)
                        or wishlist.get('created_at')
                        or wishlist.get('date_created')
                    ),
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
