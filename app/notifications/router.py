"""Центр сповіщень — читання, позначення прочитаними, видалення."""
import json
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

# Типи, які показуємо в центрі сповіщень (системні логи notifier'а пропускаємо,
# бо там type — число днів, а не рядок-тип)
UI_TYPES = {'friend_request', 'friend_accepted', 'event_invite', 'event_reminder', 'reservation'}


def _to_data(row: dict) -> NotificationData:
    raw_data = row.get('data')
    parsed: dict[str, Any] = {}
    if isinstance(raw_data, dict):
        parsed = raw_data
    elif isinstance(raw_data, str) and raw_data.strip():
        try:
            parsed = json.loads(raw_data)
        except (TypeError, ValueError):
            parsed = {}

    return NotificationData(
        id=str(row['id']),
        type=str(row.get(TYPE_FIELD) or 'info'),
        title=row.get('title') or '',
        body=row.get('body'),
        related_id=str(row[RELATED_FIELD]) if row.get(RELATED_FIELD) else None,
        is_read=bool(row.get('is_read', False)),
        created_at=row.get('sent_at') or row.get('date_created'),
        data=parsed,
    )


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
        return [_to_data(r) for r in rows]
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
                {'is_read': {'_eq': False}},
            ]},
            limit=100,
        )
        return UnreadCount(count=len(rows))
    except DirectusError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


async def _owned(notification_id: str, user_id: str) -> dict:
    row = await get_directus().get_item(settings.directus_notifications_collection, notification_id)
    if not row or str(row.get(USER_FIELD)) != user_id:
        raise HTTPException(status_code=404, detail='Сповіщення не знайдено.')
    return row


@router.post('/{notification_id}/read', response_model=NotificationData)
async def mark_read(
    notification_id: str,
    user_id: str = Depends(current_user_id),
) -> NotificationData:
    client = get_directus()
    try:
        await _owned(notification_id, user_id)
        updated = await client.update_item(
            settings.directus_notifications_collection,
            notification_id,
            {'is_read': True},
        )
        return _to_data(updated)
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
                {'is_read': {'_eq': False}},
            ]},
            limit=100,
        )
        for r in rows:
            await client.update_item(
                settings.directus_notifications_collection, r['id'], {'is_read': True}
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
