"""Центр сповіщень. Працює тільки з полями ER-схеми:
recipient_id, type, related_id, sent_at, delivered, error_message.

Текст сповіщення будується на льоту з type + related_id.
"delivered" використовується як ознака "прочитано" в UI-центрі.
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


# Шаблони тексту для UI (uk). Назви сутностей підставляються з related-даних.
TEXT_TEMPLATES = {
    'friend_request':  ('Нова заявка в друзі 👋', 'Хтось хоче додати тебе в друзі.'),
    'friend_accepted': ('Заявку прийнято 🤝', 'Тепер ви друзі.'),
    'event_invite':    ('Запрошення на подію 🎉', 'Вас запросили на подію.'),
    'event_reminder':  ('Скоро подія 🗓️', 'Наближається запланована подія.'),
    'reservation':     ('Подарунок зарезервовано 🎁', 'Хтось зарезервував товар із твого списку.'),
}

# Куди веде сповіщення на фронті
NAV_TARGET = {
    'friend_request': 'friends',
    'friend_accepted': 'friends',
    'event_invite': 'events',
    'event_reminder': 'events',
    'reservation': 'lists',
}


async def _enrich(rows: list[dict]) -> list[NotificationData]:
    """Підтягує назви подій/списків для red_id, щоб текст був конкретним."""
    client = get_directus()

    event_ids: set[str] = set()
    list_ids: set[str] = set()
    for r in rows:
        t = str(r.get(TYPE_FIELD) or '')
        rid = _rel(r.get(RELATED_FIELD))
        if not rid:
            continue
        if t in ('event_invite', 'event_reminder'):
            event_ids.add(rid)
        elif t == 'reservation':
            list_ids.add(rid)

    events: dict[str, str] = {}
    if event_ids:
        try:
            ev_rows = await client.get_items(
                settings.directus_events_collection,
                fields=['id', settings.directus_events_title_field],
                filter_={'id': {'_in': list(event_ids)}},
            )
            events = {str(e['id']): e.get(settings.directus_events_title_field) or '' for e in ev_rows}
        except DirectusError:
            pass

    lists: dict[str, str] = {}
    if list_ids:
        try:
            wl_rows = await client.get_items(
                settings.directus_wishes_collection,
                fields=['id', 'title'],
                filter_={'id': {'_in': list(list_ids)}},
            )
            lists = {str(w['id']): w.get('title') or '' for w in wl_rows}
        except DirectusError:
            pass

    result: list[NotificationData] = []
    for r in rows:
        t = str(r.get(TYPE_FIELD) or 'info')
        rid = _rel(r.get(RELATED_FIELD))
        title, body = TEXT_TEMPLATES.get(t, ('Сповіщення', None))

        # Конкретизуємо текст назвою
        if t in ('event_invite', 'event_reminder') and rid and events.get(rid):
            body = f'«{events[rid]}»'
            if t == 'event_invite':
                body = f'Вас запросили на «{events[rid]}».'
            else:
                body = f'Наближається подія «{events[rid]}».'
        elif t == 'reservation' and rid and lists.get(rid):
            body = f'Хтось зарезервував подарунок зі списку «{lists[rid]}».'

        result.append(NotificationData(
            id=str(r['id']),
            type=t,
            title=title,
            body=body,
            related_id=rid,
            is_read=bool(r.get('delivered', False)),
            created_at=r.get('sent_at') or r.get('date_created'),
            nav=NAV_TARGET.get(t),
        ))
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
            filter_={'_and': [
                {USER_FIELD: {'_eq': user_id}},
                {TYPE_FIELD: {'_in': list(UI_TYPES)}},
            ]},
            limit=limit,
        )
        rows.sort(key=lambda r: str(r.get('sent_at') or r.get('date_created') or ''), reverse=True)
        return await _enrich(rows)
    except DirectusError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get('/unread', response_model=UnreadCount)
async def unread_count(user_id: str = Depends(current_user_id)) -> UnreadCount:
    client = get_directus()
    try:
        rows = await client.get_items(
            settings.directus_notifications_collection,
            fields=['id'],
            filter_={'_and': [
                {USER_FIELD: {'_eq': user_id}},
                {TYPE_FIELD: {'_in': list(UI_TYPES)}},
                {'delivered': {'_eq': False}},
            ]},
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
    client = get_directus()
    try:
        await _owned(notification_id, user_id)
        await client.update_item(
            settings.directus_notifications_collection,
            notification_id,
            {'delivered': True},   # delivered = "прочитано" в UI
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
            filter_={'_and': [
                {USER_FIELD: {'_eq': user_id}},
                {TYPE_FIELD: {'_in': list(UI_TYPES)}},
                {'delivered': {'_eq': False}},
            ]},
            limit=100,
        )
        for r in rows:
            await client.update_item(
                settings.directus_notifications_collection, r['id'], {'delivered': True}
            )
        return UnreadCount(count=0)
    except DirectusError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.delete('/{notification_id}', status_code=status.HTTP_204_NO_CONTENT)
async def delete_notification(
    notification_id: str,
    user_id: str = Depends(current_user_id),
) -> Response:
    client = get_directus()
    try:
        await _owned(notification_id, user_id)
        await client.delete_item(settings.directus_notifications_collection, notification_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except DirectusError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
