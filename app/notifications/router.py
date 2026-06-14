"""Центр сповіщень на полях ER-схеми:
recipient_id, type, related_id, sent_at, delivered, error_message.

Для заявок у друзі related_id зберігає UUID користувача-відправника.
Сам запис friend_request одночасно є pending-заявкою; після прийняття або
відхилення він видаляється через API модуля friends.
"""
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response, status

from app.core.config import settings
from app.core.directus import DirectusError, get_directus
from app.notifications.schemas import NotificationData, UnreadCount
from app.profile.router import current_user_id

router = APIRouter()

USER_FIELD = settings.directus_notifications_user_field
TYPE_FIELD = settings.directus_notifications_days_field
RELATED_FIELD = settings.directus_notifications_event_field

UI_TYPES = {'friend_request', 'friend_accepted', 'event_invite', 'event_reminder', 'reservation'}


def _rel(value: Any) -> str | None:
    if isinstance(value, dict):
        value = value.get('id')
    return str(value) if value is not None else None


def _name(user: dict | None, language: str) -> str:
    if not user:
        return 'Someone' if language == 'en' else 'Користувач'
    return str(
        user.get('display_name')
        or (f"@{user.get('username')}" if user.get('username') else '')
        or ('Someone' if language == 'en' else 'Користувач')
    )


async def _language(user_id: str) -> str:
    user = await get_directus().get_item(
        settings.directus_users_collection,
        user_id,
        fields=['id', settings.directus_users_locale_field],
    )
    return 'en' if user and user.get(settings.directus_users_locale_field) == 'en' else 'uk'


async def _enrich(rows: list[dict], language: str) -> list[NotificationData]:
    client = get_directus()

    event_ids: set[str] = set()
    list_ids: set[str] = set()
    user_ids: set[str] = set()
    for row in rows:
        notif_type = str(row.get(TYPE_FIELD) or '')
        related_id = _rel(row.get(RELATED_FIELD))
        if not related_id:
            continue
        if notif_type in ('event_invite', 'event_reminder'):
            event_ids.add(related_id)
        elif notif_type == 'reservation':
            list_ids.add(related_id)
        elif notif_type in ('friend_request', 'friend_accepted'):
            user_ids.add(related_id)

    events: dict[str, str] = {}
    if event_ids:
        try:
            event_rows = await client.get_items(
                settings.directus_events_collection,
                fields=['id', settings.directus_events_title_field],
                filter_={'id': {'_in': list(event_ids)}},
            )
            events = {
                str(row['id']): row.get(settings.directus_events_title_field) or ''
                for row in event_rows
            }
        except DirectusError:
            pass

    wishlists: dict[str, str] = {}
    if list_ids:
        try:
            list_rows = await client.get_items(
                settings.directus_wishes_collection,
                fields=['id', settings.directus_wishes_title_field],
                filter_={'id': {'_in': list(list_ids)}},
            )
            wishlists = {
                str(row['id']): row.get(settings.directus_wishes_title_field) or ''
                for row in list_rows
            }
        except DirectusError:
            pass

    users: dict[str, dict] = {}
    if user_ids:
        try:
            user_rows = await client.get_items(
                settings.directus_users_collection,
                fields=['id', 'display_name', 'username'],
                filter_={'id': {'_in': list(user_ids)}},
            )
            users = {str(row['id']): row for row in user_rows}
        except DirectusError:
            pass

    result: list[NotificationData] = []
    for row in rows:
        notif_type = str(row.get(TYPE_FIELD) or 'info')
        related_id = _rel(row.get(RELATED_FIELD))

        if language == 'en':
            title, body = {
                'friend_request': ('New friend request 👋', 'Someone wants to add you as a friend.'),
                'friend_accepted': ('Friend request accepted 🤝', 'You are now friends.'),
                'event_invite': ('Event invitation 🎉', 'You were invited to an event.'),
                'event_reminder': ('Upcoming event 🗓️', 'A planned event is coming up.'),
                'reservation': ('Gift reserved 🎁', 'Someone reserved an item from your wishlist.'),
            }.get(notif_type, ('Notification', None))
        else:
            title, body = {
                'friend_request': ('Нова заявка в друзі 👋', 'Хтось хоче додати тебе в друзі.'),
                'friend_accepted': ('Заявку прийнято 🤝', 'Тепер ви друзі.'),
                'event_invite': ('Запрошення на подію 🎉', 'Вас запросили на подію.'),
                'event_reminder': ('Скоро подія 🗓️', 'Наближається запланована подія.'),
                'reservation': ('Подарунок зарезервовано 🎁', 'Хтось зарезервував товар із твого списку.'),
            }.get(notif_type, ('Сповіщення', None))

        if notif_type == 'friend_request' and related_id:
            actor = _name(users.get(related_id), language)
            body = (
                f'{actor} wants to add you as a friend. Open the Friends page to respond.'
                if language == 'en'
                else f'{actor} хоче додати тебе в друзі. Відкрий сторінку друзів, щоб відповісти.'
            )
        elif notif_type == 'friend_accepted' and related_id:
            actor = _name(users.get(related_id), language)
            body = (
                f'{actor} accepted your friend request.'
                if language == 'en'
                else f'{actor} прийняв(ла) твою заявку в друзі.'
            )
        elif notif_type in ('event_invite', 'event_reminder') and related_id and events.get(related_id):
            event_title = events[related_id]
            if language == 'en':
                body = (
                    f'You were invited to “{event_title}”.'
                    if notif_type == 'event_invite'
                    else f'The event “{event_title}” is coming up.'
                )
            else:
                body = (
                    f'Вас запросили на «{event_title}».'
                    if notif_type == 'event_invite'
                    else f'Наближається подія «{event_title}».'
                )
        elif notif_type == 'reservation' and related_id and wishlists.get(related_id):
            list_title = wishlists[related_id]
            body = (
                f'Someone reserved a gift from “{list_title}”.'
                if language == 'en'
                else f'Хтось зарезервував подарунок зі списку «{list_title}».'
            )

        result.append(
            NotificationData(
                id=str(row['id']),
                type=notif_type,
                title=title,
                body=body,
                related_id=related_id,
                is_read=bool(row.get('delivered', False)),
                created_at=row.get('sent_at') or row.get('date_created'),
                nav={
                    'friend_request': 'friends',
                    'friend_accepted': 'friends',
                    'event_invite': 'events',
                    'event_reminder': 'events',
                    'reservation': 'lists',
                }.get(notif_type),
            )
        )
    return result


@router.get('', response_model=list[NotificationData])
async def list_notifications(
    limit: int = 50,
    user_id: str = Depends(current_user_id),
) -> list[NotificationData]:
    client = get_directus()
    limit = max(1, min(limit, 100))
    try:
        rows = await client.get_items(
            settings.directus_notifications_collection,
            filter_={
                '_and': [
                    {USER_FIELD: {'_eq': user_id}},
                    {TYPE_FIELD: {'_in': list(UI_TYPES)}},
                ]
            },
            limit=limit,
        )
        rows.sort(key=lambda row: str(row.get('sent_at') or row.get('date_created') or ''), reverse=True)
        return await _enrich(rows, await _language(user_id))
    except DirectusError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get('/unread', response_model=UnreadCount)
async def unread_count(user_id: str = Depends(current_user_id)) -> UnreadCount:
    try:
        rows = await get_directus().get_items(
            settings.directus_notifications_collection,
            fields=['id'],
            filter_={
                '_and': [
                    {USER_FIELD: {'_eq': user_id}},
                    {TYPE_FIELD: {'_in': list(UI_TYPES)}},
                    {'delivered': {'_eq': False}},
                ]
            },
            limit=100,
        )
        return UnreadCount(count=len(rows))
    except DirectusError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


async def _owned(notification_id: str, user_id: str) -> dict:
    row = await get_directus().get_item(settings.directus_notifications_collection, notification_id)
    if not row or _rel(row.get(USER_FIELD)) != user_id:
        raise HTTPException(status_code=404, detail='Сповіщення не знайдено.')
    return row


@router.post('/{notification_id}/read', status_code=status.HTTP_204_NO_CONTENT)
async def mark_read(
    notification_id: str,
    user_id: str = Depends(current_user_id),
) -> Response:
    try:
        await _owned(notification_id, user_id)
        await get_directus().update_item(
            settings.directus_notifications_collection,
            notification_id,
            {'delivered': True},
        )
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except DirectusError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post('/read-all', response_model=UnreadCount)
async def mark_all_read(user_id: str = Depends(current_user_id)) -> UnreadCount:
    client = get_directus()
    try:
        rows = await client.get_items(
            settings.directus_notifications_collection,
            fields=['id'],
            filter_={
                '_and': [
                    {USER_FIELD: {'_eq': user_id}},
                    {TYPE_FIELD: {'_in': list(UI_TYPES)}},
                    {'delivered': {'_eq': False}},
                ]
            },
            limit=100,
        )
        for row in rows:
            await client.update_item(
                settings.directus_notifications_collection,
                row['id'],
                {'delivered': True},
            )
        return UnreadCount(count=0)
    except DirectusError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.delete('/{notification_id}', status_code=status.HTTP_204_NO_CONTENT)
async def delete_notification(
    notification_id: str,
    user_id: str = Depends(current_user_id),
) -> Response:
    try:
        await _owned(notification_id, user_id)
        await get_directus().delete_item(settings.directus_notifications_collection, notification_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except DirectusError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
