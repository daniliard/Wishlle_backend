"""Події Wishlle без змін структури БД.

Використовуються наявні поля ER-схеми:
- events: event_type, honoree_id, cover_image, ...
- event_participants: status (invited/accepted/declined), role
- notifications: type + related_id

Запрошена подія не входить до основного списку користувача, доки він її
не прийме. Pending-запрошення повертаються окремим endpoint /invitations.
"""
from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response, status

from app.core.config import settings
from app.core.directus import DirectusError, get_directus
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
from app.notifications.service import create_notification
from app.profile.router import current_user_id

router = APIRouter()


def _rel(value: Any) -> str | None:
    if isinstance(value, dict):
        value = value.get('id')
    return str(value) if value is not None else None


def _date_str(value: Any) -> str | None:
    return str(value) if value is not None else None


def _event_type(value: Any) -> str:
    normalized = str(value or '').strip().lower()
    if normalized in {'private', 'personal'}:
        return 'private'
    # Підтримка старого значення public як групової події.
    return 'group'


def _list_visibility(item: dict) -> str:
    value = str(item.get('visibility') or '').strip().lower()
    if value in {'public', 'friends', 'private'}:
        return value
    legacy = item.get('is_public')
    if isinstance(legacy, bool):
        return 'public' if legacy else 'private'
    if isinstance(legacy, (int, float)):
        return 'public' if int(legacy) else 'private'
    if isinstance(legacy, str):
        return 'public' if legacy.strip().lower() in {'1', 'true', 'yes', 'public'} else 'private'
    return 'public'


async def _users_by_ids(ids: list[str]) -> dict[str, dict]:
    ids = [item for item in dict.fromkeys(ids) if item]
    if not ids:
        return {}
    users = await get_directus().get_items(
        settings.directus_users_collection,
        fields=['id', 'display_name', 'username', 'avatar_url', 'birth_date'],
        filter_={'id': {'_in': ids}},
    )
    return {str(user['id']): user for user in users}


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
    """Повертає доступні в події списки власника.

    Приватні списки не показуємо. Public/friends відкриваються через вже
    існуючий reservation endpoint, бо учасники події обираються зі списку друзів.
    """
    client = get_directus()
    lists = await client.get_items(
        settings.directus_wishes_collection,
        filter_={settings.directus_wishes_owner_field: {'_eq': owner_id}},
    )
    result: list[EventWishlistData] = []
    for wishlist in lists:
        visibility = _list_visibility(wishlist)
        if visibility == 'private':
            continue
        list_id = str(wishlist['id'])
        items = await client.get_items(
            settings.directus_wish_items_collection,
            fields=['id'],
            filter_={'wishlist_id': {'_eq': list_id}},
        )
        result.append(EventWishlistData(
            id=list_id,
            title=wishlist.get('title') or 'Список побажань',
            emoji=wishlist.get('emoji') or '🎁',
            items_count=len(items),
            owner_id=owner_id,
            owner_name=_user_name(owner),
            visibility=visibility,
        ))
    return result


def _build_event_data(event: dict, user_id: str, participants: list[dict]) -> EventData:
    owner_id = _rel(event.get(settings.directus_events_owner_field)) or ''
    my_participation = next(
        (participant for participant in participants if _rel(participant.get('user_id')) == user_id),
        None,
    )
    accepted_count = sum(
        1 for participant in participants if str(participant.get('status') or '') == 'accepted'
    )
    return EventData(
        id=str(event['id']),
        owner_id=owner_id,
        title=event.get('title') or 'Подія',
        description=event.get('description'),
        event_date=_date_str(event.get(settings.directus_events_date_field)),
        location=event.get('location'),
        event_type=_event_type(event.get('event_type')),
        honoree_id=_rel(event.get('honoree_id')),
        is_auto=bool(event.get('is_auto', False)),
        cover_image=event.get('cover_image'),
        is_owner=owner_id == user_id,
        my_status=(my_participation or {}).get('status'),
        participants_count=accepted_count,
    )


async def _delete_invite_notifications(user_id: str, event_id: str) -> None:
    client = get_directus()
    rows = await client.get_items(
        settings.directus_notifications_collection,
        fields=['id'],
        filter_={'_and': [
            {settings.directus_notifications_user_field: {'_eq': user_id}},
            {settings.directus_notifications_days_field: {'_eq': 'event_invite'}},
            {settings.directus_notifications_event_field: {'_eq': event_id}},
        ]},
    )
    for row in rows:
        await client.delete_item(settings.directus_notifications_collection, row['id'])


async def _send_event_invite(recipient_id: str, event_id: str, title: str) -> None:
    await create_notification(
        recipient_id=recipient_id,
        notif_type='event_invite',
        telegram_text=f'<b>Запрошення на подію 🎉</b>\nВас запросили на «{title}».',
        related_id=event_id,
    )


async def _create_invited_participant(
    event_id: str,
    participant_id: str,
    role: str,
    event_title: str,
) -> dict:
    created = await get_directus().create_item(
        settings.directus_event_participants_collection,
        {
            'event_id': event_id,
            'user_id': participant_id,
            'status': 'invited',
            'role': role,
        },
    )
    await _send_event_invite(participant_id, event_id, event_title)
    return created


# ── Основний список: створені мною + лише ПРИЙНЯТІ запрошення ───────────────
@router.get('', response_model=list[EventData])
async def list_events(user_id: str = Depends(current_user_id)) -> list[EventData]:
    client = get_directus()
    try:
        owned = await client.get_items(
            settings.directus_events_collection,
            filter_={settings.directus_events_owner_field: {'_eq': user_id}},
        )
        accepted_parts = await client.get_items(
            settings.directus_event_participants_collection,
            filter_={'_and': [
                {'user_id': {'_eq': user_id}},
                {'status': {'_eq': 'accepted'}},
            ]},
        )
        accepted_event_ids = [
            event_id for row in accepted_parts if (event_id := _rel(row.get('event_id')))
        ]

        owned_ids = {str(event['id']) for event in owned}
        extra_ids = [event_id for event_id in accepted_event_ids if event_id not in owned_ids]
        accepted_events: list[dict] = []
        if extra_ids:
            accepted_events = await client.get_items(
                settings.directus_events_collection,
                filter_={'id': {'_in': extra_ids}},
            )

        result: list[EventData] = []
        for event in owned + accepted_events:
            participants = await _event_participants(str(event['id']))
            result.append(_build_event_data(event, user_id, participants))
        result.sort(key=lambda event: event.event_date or '9999')
        return result
    except DirectusError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


# ── Pending-запрошення: окремо, доки користувач не відповів ─────────────────
@router.get('/invitations', response_model=list[EventData])
async def list_invitations(user_id: str = Depends(current_user_id)) -> list[EventData]:
    client = get_directus()
    try:
        invited_rows = await client.get_items(
            settings.directus_event_participants_collection,
            filter_={'_and': [
                {'user_id': {'_eq': user_id}},
                {'status': {'_eq': 'invited'}},
            ]},
        )
        event_ids = [event_id for row in invited_rows if (event_id := _rel(row.get('event_id')))]
        if not event_ids:
            return []
        events = await client.get_items(
            settings.directus_events_collection,
            filter_={'id': {'_in': list(dict.fromkeys(event_ids))}},
        )
        result: list[EventData] = []
        for event in events:
            participants = await _event_participants(str(event['id']))
            result.append(_build_event_data(event, user_id, participants))
        result.sort(key=lambda event: event.event_date or '9999')
        return result
    except DirectusError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


# ── Авто-створення ДН друзів як особистих нагадувань власника ───────────────
@router.post('/sync-birthdays', response_model=list[EventData])
async def sync_birthday_events(user_id: str = Depends(current_user_id)) -> list[EventData]:
    client = get_directus()
    try:
        friendships = await client.get_items(
            settings.directus_friendships_collection,
            filter_={'user_id': {'_eq': user_id}},
        )
        friend_ids = [friend_id for row in friendships if (friend_id := _rel(row.get('friend_id')))]
        friends = await _users_by_ids(friend_ids)

        existing = await client.get_items(
            settings.directus_events_collection,
            filter_={'_and': [
                {settings.directus_events_owner_field: {'_eq': user_id}},
                {'is_auto': {'_eq': True}},
            ]},
        )
        existing_honorees = {_rel(event.get('honoree_id')) for event in existing}

        created_events: list[dict] = []
        today = date.today()
        for friend_id, friend in friends.items():
            if friend_id in existing_honorees:
                continue
            raw_birth_date = friend.get('birth_date')
            if not raw_birth_date:
                continue
            try:
                birth_date = datetime.fromisoformat(str(raw_birth_date).replace('Z', '')).date()
            except ValueError:
                continue

            next_birthday = birth_date.replace(year=today.year)
            if next_birthday < today:
                next_birthday = birth_date.replace(year=today.year + 1)

            created_events.append(await client.create_item(
                settings.directus_events_collection,
                {
                    settings.directus_events_owner_field: user_id,
                    'title': f'День народження — {_user_name(friend)}',
                    'description': 'Автоматично створено на основі дати народження друга.',
                    settings.directus_events_date_field: next_birthday.isoformat(),
                    'event_type': 'private',
                    'honoree_id': friend_id,
                    'is_auto': True,
                    'cover_image': '🎂',
                },
            ))

        result: list[EventData] = []
        for event in created_events:
            result.append(_build_event_data(event, user_id, []))
        return result
    except DirectusError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


# ── Створення ────────────────────────────────────────────────────────────────
@router.post('', response_model=EventData, status_code=status.HTTP_201_CREATED)
async def create_event(
    payload: EventCreate,
    user_id: str = Depends(current_user_id),
) -> EventData:
    client = get_directus()
    try:
        created = await client.create_item(
            settings.directus_events_collection,
            {
                settings.directus_events_owner_field: user_id,
                'title': payload.title,
                'description': payload.description,
                settings.directus_events_date_field: payload.event_date.isoformat(),
                'location': payload.location,
                'event_type': payload.event_type,
                'honoree_id': payload.honoree_id,
                'is_auto': False,
                'cover_image': payload.cover_image,
            },
        )
        event_id = str(created['id'])
        title = created.get('title') or 'подію'

        invited: dict[str, str] = {}
        if payload.event_type == 'private' and payload.honoree_id and payload.honoree_id != user_id:
            invited[payload.honoree_id] = 'honoree'
        for participant_id in payload.participant_ids:
            if participant_id and participant_id != user_id:
                invited.setdefault(participant_id, 'participant')

        for participant_id, role in invited.items():
            await _create_invited_participant(event_id, participant_id, role, title)

        participants = await _event_participants(event_id)
        return _build_event_data(created, user_id, participants)
    except DirectusError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


# ── Деталі події ─────────────────────────────────────────────────────────────
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
        is_owner = owner_id == user_id
        is_participant = any(_rel(part.get('user_id')) == user_id for part in parts)
        if not is_owner and not is_participant:
            raise HTTPException(status_code=403, detail='Немає доступу до цієї події.')

        base = _build_event_data(event, user_id, parts)
        user_ids = [participant_id for part in parts if (participant_id := _rel(part.get('user_id')))]
        if owner_id:
            user_ids.append(owner_id)
        honoree_id = _rel(event.get('honoree_id'))
        if honoree_id:
            user_ids.append(honoree_id)
        users = await _users_by_ids(user_ids)

        participants: list[ParticipantData] = []
        for part in parts:
            participant_id = _rel(part.get('user_id'))
            if not participant_id:
                continue
            user = users.get(participant_id, {})
            participants.append(ParticipantData(
                id=str(part['id']),
                user_id=participant_id,
                status=part.get('status') or 'invited',
                role=part.get('role') or 'participant',
                user=ParticipantUser(
                    id=participant_id,
                    display_name=user.get('display_name'),
                    username=user.get('username'),
                    avatar_url=user.get('avatar_url'),
                ),
            ))

        wishlists: list[EventWishlistData] = []
        event_type = _event_type(event.get('event_type'))
        if event_type == 'private':
            if honoree_id:
                honoree = users.get(honoree_id) or await client.get_item(
                    settings.directus_users_collection,
                    honoree_id,
                    fields=['id', 'display_name', 'username'],
                )
                wishlists = await _owner_wishlists(honoree_id, honoree or {})
        else:
            # Списки показуємо лише прийнятих учасників. Pending-запрошення
            # не відкриває чужі дані до підтвердження участі.
            target_ids = [
                participant_id
                for part in parts
                if str(part.get('status') or '') == 'accepted'
                and (participant_id := _rel(part.get('user_id')))
                and participant_id != user_id
            ]
            if owner_id and owner_id != user_id and owner_id not in target_ids:
                target_ids.append(owner_id)
            seen: set[str] = set()
            for participant_id in target_ids:
                if participant_id in seen:
                    continue
                seen.add(participant_id)
                wishlists.extend(await _owner_wishlists(participant_id, users.get(participant_id, {})))

        return EventDetailData(
            **base.model_dump(),
            participants=participants,
            wishlists=wishlists,
        )
    except DirectusError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


# ── Редагування, включно з типом і ярликом ──────────────────────────────────
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

        supplied = payload.model_fields_set
        current_type = _event_type(event.get('event_type'))
        next_type = payload.event_type if 'event_type' in supplied else current_type
        current_honoree = _rel(event.get('honoree_id'))
        next_honoree = payload.honoree_id if 'honoree_id' in supplied else current_honoree
        if next_type == 'group':
            next_honoree = None
        if next_type == 'private' and not next_honoree:
            raise HTTPException(status_code=422, detail='Для приватної події оберіть іменинника.')

        update = payload.model_dump(exclude_unset=True, mode='json')
        update['event_type'] = next_type
        update['honoree_id'] = next_honoree
        if 'event_date' in update and update['event_date']:
            update[settings.directus_events_date_field] = update.pop('event_date')
        event = await client.update_item(settings.directus_events_collection, event_id, update)

        parts = await _event_participants(event_id)
        title = event.get('title') or 'подію'
        if next_type == 'group':
            for part in parts:
                if part.get('role') == 'honoree':
                    await client.update_item(
                        settings.directus_event_participants_collection,
                        part['id'],
                        {'role': 'participant'},
                    )
        else:
            honoree_part = next(
                (part for part in parts if _rel(part.get('user_id')) == next_honoree),
                None,
            )
            for part in parts:
                if part.get('role') == 'honoree' and _rel(part.get('user_id')) != next_honoree:
                    await client.update_item(
                        settings.directus_event_participants_collection,
                        part['id'],
                        {'role': 'participant'},
                    )
            if next_honoree != user_id:
                if honoree_part:
                    update_part = {'role': 'honoree'}
                    # Новий іменинник повинен підтвердити участь.
                    if next_honoree != current_honoree:
                        update_part['status'] = 'invited'
                    await client.update_item(
                        settings.directus_event_participants_collection,
                        honoree_part['id'],
                        update_part,
                    )
                    if next_honoree != current_honoree:
                        await _send_event_invite(next_honoree, event_id, title)
                else:
                    await _create_invited_participant(event_id, next_honoree, 'honoree', title)

        parts = await _event_participants(event_id)
        return _build_event_data(event, user_id, parts)
    except DirectusError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


# ── Видалення ────────────────────────────────────────────────────────────────
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
        for part in parts:
            await client.delete_item(settings.directus_event_participants_collection, part['id'])
        notifications = await client.get_items(
            settings.directus_notifications_collection,
            fields=['id'],
            filter_={settings.directus_notifications_event_field: {'_eq': event_id}},
        )
        for notification in notifications:
            await client.delete_item(settings.directus_notifications_collection, notification['id'])
        await client.delete_item(settings.directus_events_collection, event_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except DirectusError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


# ── Запросити учасників ─────────────────────────────────────────────────────
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
        existing_ids = {_rel(part.get('user_id')) for part in existing}
        title = event.get('title') or 'подію'
        for participant_id in dict.fromkeys(payload.user_ids):
            if not participant_id or participant_id == user_id or participant_id in existing_ids:
                continue
            await _create_invited_participant(event_id, participant_id, 'participant', title)
            existing_ids.add(participant_id)

        return await event_details(event_id, user_id)
    except DirectusError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


# ── Прийняти/відхилити ──────────────────────────────────────────────────────
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

        await client.update_item(
            settings.directus_event_participants_collection,
            participation['id'],
            {'status': payload.status},
        )
        await _delete_invite_notifications(user_id, event_id)

        event = await client.get_item(settings.directus_events_collection, event_id)
        if not event:
            raise HTTPException(status_code=404, detail='Подію не знайдено.')
        parts = await _event_participants(event_id)
        return _build_event_data(event, user_id, parts)
    except DirectusError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


# ── Прибрати учасника або вийти ─────────────────────────────────────────────
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
        if user_id != owner_id and user_id != participant_user_id:
            raise HTTPException(status_code=403, detail='Недостатньо прав.')

        participation = await _my_participation(event_id, participant_user_id)
        if participation:
            await client.delete_item(settings.directus_event_participants_collection, participation['id'])
        await _delete_invite_notifications(participant_user_id, event_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except DirectusError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
