"""Модуль подій згідно дипломної роботи.

Типи подій:
- private: honoree_id обов'язковий. Усі учасники бачать список іменинника.
- group:   honoree_id = NULL. Усі бачать списки всіх учасників.

is_auto=true — авто-створені події ДН друзів (не редагуються вручну).

event_participants: status (invited/accepted/declined), role (honoree/participant).
"""
from datetime import date, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response, status

from app.core.config import settings
from app.core.directus import DirectusError, get_directus
from app.notifications.service import create_notification
from app.events.schemas import (
    EventCreate,
    EventData,
    EventDetailData,
    EventUpdate,
    EventWishlistData,
    InviteRequest,
    ParticipantData,
    ParticipantUser,
    RespondRequest,
)
from app.profile.router import current_user_id

router = APIRouter()


def _rel(value: Any) -> str | None:
    if isinstance(value, dict):
        value = value.get('id')
    return str(value) if value is not None else None


def _date_str(value: Any) -> str | None:
    return str(value) if value is not None else None


async def _users_by_ids(ids: list[str]) -> dict[str, dict]:
    ids = [i for i in dict.fromkeys(ids) if i]
    if not ids:
        return {}
    users = await get_directus().get_items(
        settings.directus_users_collection,
        fields=['id', 'display_name', 'username', 'avatar_url', 'birth_date'],
        filter_={'id': {'_in': ids}},
    )
    return {str(u['id']): u for u in users}


async def _event_participants(event_id: str) -> list[dict]:
    return await get_directus().get_items(
        settings.directus_event_participants_collection,
        filter_={'event_id': {'_eq': event_id}},
    )


async def _my_participation(event_id: str, user_id: str) -> dict | None:
    rows = await get_directus().get_items(
        settings.directus_event_participants_collection,
        filter_={'_and': [
            {'event_id': {'_eq': event_id}},
            {'user_id': {'_eq': user_id}},
        ]},
        limit=1,
    )
    return rows[0] if rows else None


def _user_name(user: dict | None) -> str:
    if not user:
        return 'Користувач'
    return user.get('display_name') or user.get('username') or 'Користувач'


async def _owner_wishlists(owner_id: str, owner: dict) -> list[EventWishlistData]:
    client = get_directus()
    lists = await client.get_items(
        settings.directus_wishes_collection,
        filter_={settings.directus_wishes_owner_field: {'_eq': owner_id}},
    )
    result: list[EventWishlistData] = []
    for wl in lists:
        list_id = str(wl['id'])
        items = await client.get_items(
            settings.directus_wish_items_collection,
            filter_={'wishlist_id': {'_eq': list_id}},
        )
        result.append(EventWishlistData(
            id=list_id,
            title=wl.get('title') or 'Список побажань',
            emoji=wl.get('emoji') or '🎁',
            items_count=len(items),
            owner_id=owner_id,
            owner_name=_user_name(owner),
        ))
    return result


def _build_event_data(event: dict, user_id: str, participants: list[dict]) -> EventData:
    owner_id = _rel(event.get(settings.directus_events_owner_field)) or ''
    my = next((p for p in participants if _rel(p.get('user_id')) == user_id), None)
    return EventData(
        id=str(event['id']),
        owner_id=owner_id,
        title=event.get('title') or 'Подія',
        description=event.get('description'),
        event_date=_date_str(event.get(settings.directus_events_date_field)),
        location=event.get('location'),
        event_type=event.get('event_type') or 'group',
        honoree_id=_rel(event.get('honoree_id')),
        is_auto=bool(event.get('is_auto', False)),
        cover_image=event.get('cover_image'),
        is_owner=owner_id == user_id,
        my_status=(my or {}).get('status'),
        participants_count=len(participants),
    )


# ── Список моїх подій (створені мною + куди мене запросили) ─────────────────
@router.get('', response_model=list[EventData])
async def list_events(user_id: str = Depends(current_user_id)) -> list[EventData]:
    client = get_directus()
    try:
        owned = await client.get_items(
            settings.directus_events_collection,
            filter_={settings.directus_events_owner_field: {'_eq': user_id}},
        )
        my_parts = await client.get_items(
            settings.directus_event_participants_collection,
            filter_={'user_id': {'_eq': user_id}},
        )
        invited_event_ids = [
            eid for p in my_parts if (eid := _rel(p.get('event_id')))
        ]

        owned_ids = {str(e['id']) for e in owned}
        extra_ids = [eid for eid in invited_event_ids if eid not in owned_ids]
        invited_events: list[dict] = []
        if extra_ids:
            invited_events = await client.get_items(
                settings.directus_events_collection,
                filter_={'id': {'_in': extra_ids}},
            )

        all_events = owned + invited_events

        # Підвантажуємо учасників для кожної події
        result: list[EventData] = []
        for event in all_events:
            parts = await _event_participants(str(event['id']))
            result.append(_build_event_data(event, user_id, parts))

        result.sort(key=lambda e: e.event_date or '9999')
        return result
    except DirectusError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


# ── Деталі події з учасниками та списками ──────────────────────────────────
@router.get('/{event_id}', response_model=EventDetailData)
async def event_details(
    event_id: str,
    user_id: str = Depends(current_user_id),
) -> EventDetailData:
    client = get_directus()
    try:
        event = await client.get_item(settings.directus_events_collection, event_id)
        if not event:
            raise HTTPException(status_code=404, detail='Подію не знайдено.')

        owner_id = _rel(event.get(settings.directus_events_owner_field))
        parts = await _event_participants(event_id)

        # Доступ: власник або учасник
        is_owner = owner_id == user_id
        is_participant = any(_rel(p.get('user_id')) == user_id for p in parts)
        if not is_owner and not is_participant:
            raise HTTPException(status_code=403, detail='Немає доступу до цієї події.')

        base = _build_event_data(event, user_id, parts)

        user_ids = [pid for p in parts if (pid := _rel(p.get('user_id')))]
        if owner_id:
            user_ids.append(owner_id)
        users = await _users_by_ids(user_ids)

        participants: list[ParticipantData] = []
        for p in parts:
            pid = _rel(p.get('user_id'))
            if not pid:
                continue
            u = users.get(pid, {})
            participants.append(ParticipantData(
                id=str(p['id']),
                user_id=pid,
                status=p.get('status') or 'invited',
                role=p.get('role') or 'participant',
                user=ParticipantUser(
                    id=pid,
                    display_name=u.get('display_name'),
                    username=u.get('username'),
                    avatar_url=u.get('avatar_url'),
                ),
            ))

        # Які списки показувати:
        #  private -> списки іменинника (honoree)
        #  group   -> списки всіх учасників (крім самого глядача)
        wishlists: list[EventWishlistData] = []
        event_type = event.get('event_type') or 'group'
        if event_type == 'private':
            honoree_id = _rel(event.get('honoree_id'))
            if honoree_id:
                honoree = users.get(honoree_id) or await client.get_item(
                    settings.directus_users_collection, honoree_id,
                    fields=['id', 'display_name', 'username'],
                )
                wishlists = await _owner_wishlists(honoree_id, honoree or {})
        else:
            seen: set[str] = set()
            target_ids = [pid for p in parts if (pid := _rel(p.get('user_id'))) and pid != user_id]
            if owner_id and owner_id != user_id and owner_id not in target_ids:
                target_ids.append(owner_id)
            for pid in target_ids:
                if pid in seen:
                    continue
                seen.add(pid)
                owner_user = users.get(pid) or {}
                wishlists.extend(await _owner_wishlists(pid, owner_user))

        return EventDetailData(
            **base.model_dump(),
            participants=participants,
            wishlists=wishlists,
        )
    except DirectusError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


# ── Створення події ────────────────────────────────────────────────────────
@router.post('', response_model=EventData, status_code=status.HTTP_201_CREATED)
async def create_event(
    payload: EventCreate,
    user_id: str = Depends(current_user_id),
) -> EventData:
    client = get_directus()
    try:
        data: dict[str, Any] = {
            settings.directus_events_owner_field: user_id,
            'title': payload.title,
            'description': payload.description,
            settings.directus_events_date_field: payload.event_date.isoformat(),
            'location': payload.location,
            'event_type': payload.event_type,
            'honoree_id': payload.honoree_id,
            'is_auto': False,
            'cover_image': payload.cover_image,
        }
        created = await client.create_item(settings.directus_events_collection, data)
        event_id = str(created['id'])

        # Додаємо honoree як учасника з роллю honoree (для private)
        participant_rows: list[dict] = []
        if payload.event_type == 'private' and payload.honoree_id:
            participant_rows.append({
                'event_id': event_id,
                'user_id': payload.honoree_id,
                'status': 'accepted',
                'role': 'honoree',
            })

        # Решта запрошених
        for pid in payload.participant_ids:
            if pid == payload.honoree_id:
                continue
            participant_rows.append({
                'event_id': event_id,
                'user_id': pid,
                'status': 'invited',
                'role': 'participant',
            })

        for row in participant_rows:
            await client.create_item(settings.directus_event_participants_collection, row)

        # Сповіщення запрошеним (не honoree, бо він accepted автоматично)
        event_title = created.get('title') or 'подію'
        for row in participant_rows:
            if row.get('role') == 'participant':
                await create_notification(
                    recipient_id=row['user_id'],
                    notif_type='event_invite',
                    telegram_text=f'<b>Запрошення на подію 🎉</b>\nВас запросили на «{event_title}».',
                    related_id=event_id,
                )

        parts = await _event_participants(event_id)
        return _build_event_data(created, user_id, parts)
    except DirectusError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


# ── Оновлення (не для авто-подій) ──────────────────────────────────────────
@router.patch('/{event_id}', response_model=EventData)
async def update_event(
    event_id: str,
    payload: EventUpdate,
    user_id: str = Depends(current_user_id),
) -> EventData:
    client = get_directus()
    try:
        event = await client.get_item(settings.directus_events_collection, event_id)
        if not event:
            raise HTTPException(status_code=404, detail='Подію не знайдено.')
        if _rel(event.get(settings.directus_events_owner_field)) != user_id:
            raise HTTPException(status_code=403, detail='Тільки власник може редагувати подію.')
        if event.get('is_auto'):
            raise HTTPException(status_code=400, detail='Автоматичні події не можна редагувати.')

        update = payload.model_dump(exclude_unset=True, mode='json')
        if 'event_date' in update and update['event_date']:
            update[settings.directus_events_date_field] = update.pop('event_date')
        if update:
            event = await client.update_item(settings.directus_events_collection, event_id, update)

        parts = await _event_participants(event_id)
        return _build_event_data(event, user_id, parts)
    except DirectusError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


# ── Видалення ──────────────────────────────────────────────────────────────
@router.delete('/{event_id}', status_code=status.HTTP_204_NO_CONTENT)
async def delete_event(
    event_id: str,
    user_id: str = Depends(current_user_id),
) -> Response:
    client = get_directus()
    try:
        event = await client.get_item(settings.directus_events_collection, event_id)
        if not event:
            raise HTTPException(status_code=404, detail='Подію не знайдено.')
        if _rel(event.get(settings.directus_events_owner_field)) != user_id:
            raise HTTPException(status_code=403, detail='Тільки власник може видалити подію.')

        parts = await _event_participants(event_id)
        for p in parts:
            await client.delete_item(settings.directus_event_participants_collection, p['id'])
        await client.delete_item(settings.directus_events_collection, event_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except DirectusError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


# ── Запросити учасників ────────────────────────────────────────────────────
@router.post('/{event_id}/invite', response_model=EventDetailData)
async def invite_participants(
    event_id: str,
    payload: InviteRequest,
    user_id: str = Depends(current_user_id),
) -> EventDetailData:
    client = get_directus()
    try:
        event = await client.get_item(settings.directus_events_collection, event_id)
        if not event:
            raise HTTPException(status_code=404, detail='Подію не знайдено.')
        if _rel(event.get(settings.directus_events_owner_field)) != user_id:
            raise HTTPException(status_code=403, detail='Тільки власник може запрошувати.')

        existing = await _event_participants(event_id)
        existing_ids = {_rel(p.get('user_id')) for p in existing}
        honoree_id = _rel(event.get('honoree_id'))

        for pid in payload.user_ids:
            if pid in existing_ids or pid == honoree_id:
                continue
            await client.create_item(settings.directus_event_participants_collection, {
                'event_id': event_id,
                'user_id': pid,
                'status': 'invited',
                'role': 'participant',
            })
            await create_notification(
                recipient_id=pid,
                notif_type='event_invite',
                telegram_text=f'<b>Запрошення на подію 🎉</b>\nВас запросили на «{event.get("title") or "подію"}».',
                related_id=event_id,
            )

        return await event_details(event_id, user_id)
    except DirectusError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


# ── Відповісти на запрошення (accept/decline) ──────────────────────────────
@router.post('/{event_id}/respond', response_model=EventData)
async def respond_invite(
    event_id: str,
    payload: RespondRequest,
    user_id: str = Depends(current_user_id),
) -> EventData:
    client = get_directus()
    try:
        participation = await _my_participation(event_id, user_id)
        if not participation:
            raise HTTPException(status_code=404, detail='Вас не запрошено на цю подію.')
        if participation.get('role') == 'honoree':
            raise HTTPException(status_code=400, detail='Іменинник не відповідає на запрошення.')

        await client.update_item(
            settings.directus_event_participants_collection,
            participation['id'],
            {'status': payload.status},
        )
        event = await client.get_item(settings.directus_events_collection, event_id)
        parts = await _event_participants(event_id)
        return _build_event_data(event, user_id, parts)
    except DirectusError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


# ── Прибрати учасника (власником) або вийти самому ─────────────────────────
@router.delete('/{event_id}/participants/{participant_user_id}', status_code=status.HTTP_204_NO_CONTENT)
async def remove_participant(
    event_id: str,
    participant_user_id: str,
    user_id: str = Depends(current_user_id),
) -> Response:
    client = get_directus()
    try:
        event = await client.get_item(settings.directus_events_collection, event_id)
        if not event:
            raise HTTPException(status_code=404, detail='Подію не знайдено.')

        owner_id = _rel(event.get(settings.directus_events_owner_field))
        # Власник може прибрати будь-кого; користувач — лише себе
        if user_id != owner_id and user_id != participant_user_id:
            raise HTTPException(status_code=403, detail='Недостатньо прав.')
        if participant_user_id == _rel(event.get('honoree_id')):
            raise HTTPException(status_code=400, detail='Не можна прибрати іменинника.')

        participation = await _my_participation(event_id, participant_user_id)
        if participation:
            await client.delete_item(settings.directus_event_participants_collection, participation['id'])
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except DirectusError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


# ── Авто-створення подій ДН друзів ─────────────────────────────────────────
@router.post('/sync-birthdays', response_model=list[EventData])
async def sync_birthday_events(user_id: str = Depends(current_user_id)) -> list[EventData]:
    """Створює приватні авто-події для днів народження друзів,
    у яких заповнено birth_date. Ідемпотентно: не дублює існуючі."""
    client = get_directus()
    try:
        friendships = await client.get_items(
            settings.directus_friendships_collection,
            filter_={'user_id': {'_eq': user_id}},
        )
        friend_ids = [fid for f in friendships if (fid := _rel(f.get('friend_id')))]
        friends = await _users_by_ids(friend_ids)

        # Вже існуючі авто-події цього користувача
        existing = await client.get_items(
            settings.directus_events_collection,
            filter_={'_and': [
                {settings.directus_events_owner_field: {'_eq': user_id}},
                {'is_auto': {'_eq': True}},
            ]},
        )
        existing_honorees = {_rel(e.get('honoree_id')) for e in existing}

        created_events: list[dict] = []
        today = date.today()
        for fid, friend in friends.items():
            if fid in existing_honorees:
                continue
            bd_raw = friend.get('birth_date')
            if not bd_raw:
                continue
            try:
                bd = datetime.fromisoformat(str(bd_raw).replace('Z', '')).date()
            except ValueError:
                continue

            # Найближчий ДН
            next_bd = bd.replace(year=today.year)
            if next_bd < today:
                next_bd = bd.replace(year=today.year + 1)

            name = _user_name(friend)
            created = await client.create_item(settings.directus_events_collection, {
                settings.directus_events_owner_field: user_id,
                'title': f'День народження — {name}',
                'description': 'Автоматично створено на основі дати народження друга.',
                settings.directus_events_date_field: next_bd.isoformat(),
                'event_type': 'private',
                'honoree_id': fid,
                'is_auto': True,
            })
            event_id = str(created['id'])
            await client.create_item(settings.directus_event_participants_collection, {
                'event_id': event_id,
                'user_id': fid,
                'status': 'accepted',
                'role': 'honoree',
            })
            created_events.append(created)

        result: list[EventData] = []
        for event in created_events:
            parts = await _event_participants(str(event['id']))
            result.append(_build_event_data(event, user_id, parts))
        return result
    except DirectusError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
