"""Cron-нагадування про події Wishlle без зміни структури БД.

Використовуються тільки наявні поля:
- events: id, owner_id, title, event_date, location;
- event_participants: event_id, user_id, status;
- users: id, telegram_id, language;
- notifications: recipient_id, type, related_id, sent_at, delivered.

Окреме поле ``days_before`` не потрібне. Від повторного надсилання в той
самий день захищаємося за ``recipient_id + event_id + type + sent_at``.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, datetime, time, timezone
from typing import Any
from zoneinfo import ZoneInfo

from app.core.config import settings
from app.core.directus import DirectusClient, DirectusError, get_directus
from app.modules.notifications.service import create_notification

logger = logging.getLogger(__name__)

REMINDER_TYPE = "event_reminder"


def _rel(value: Any) -> str | None:
    if isinstance(value, dict):
        value = value.get("id")
    return str(value) if value is not None else None


def _parse_datetime(raw: Any, tz: ZoneInfo) -> tuple[datetime | None, bool]:
    """Повертає локальний datetime та ознаку, чи був у значенні час."""
    if not raw:
        return None, False

    if isinstance(raw, datetime):
        value = raw
        had_time = True
    elif isinstance(raw, date):
        value = datetime.combine(raw, time.min)
        had_time = False
    else:
        text = str(raw).strip()
        had_time = "T" in text or " " in text
        try:
            value = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            try:
                value = datetime.combine(date.fromisoformat(text[:10]), time.min)
                had_time = False
            except ValueError:
                return None, False

    if value.tzinfo is None:
        value = value.replace(tzinfo=tz)
    else:
        value = value.astimezone(tz)
    return value, had_time


def _parse_notification_date(raw: Any, tz: ZoneInfo) -> date | None:
    if not raw:
        return None
    try:
        value = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(tz).date()


def _when_text(days_left: int, language: str) -> str:
    if language == "en":
        if days_left == 0:
            return "today"
        if days_left == 1:
            return "tomorrow"
        return f"in {days_left} days"

    if days_left == 0:
        return "сьогодні"
    if days_left == 1:
        return "завтра"
    return f"через {days_left} дн."


def _format_event_datetime(value: datetime, had_time: bool, language: str) -> str:
    if language == "en":
        base = value.strftime("%d.%m.%Y")
        return f"{base} at {value.strftime('%H:%M')}" if had_time else base
    base = value.strftime("%d.%m.%Y")
    return f"{base} о {value.strftime('%H:%M')}" if had_time else base


def _telegram_text(
    *,
    title: str,
    event_dt: datetime,
    had_time: bool,
    days_left: int,
    location: str | None,
    language: str,
) -> str:
    when = _when_text(days_left, language)
    date_text = _format_event_datetime(event_dt, had_time, language)

    if language == "en":
        lines = [
            "<b>Event reminder 🗓️</b>",
            f"<b>{title}</b> — {when}.",
            f"📅 {date_text}",
        ]
        if location:
            lines.append(f"📍 {location}")
        lines.append(f'<a href="{settings.app_public_url}">Open Wishlle</a>')
        return "\n".join(lines)

    lines = [
        "<b>Нагадування про подію 🗓️</b>",
        f"<b>{title}</b> — {when}.",
        f"📅 {date_text}",
    ]
    if location:
        lines.append(f"📍 {location}")
    lines.append(f'<a href="{settings.app_public_url}">Відкрити Wishlle</a>')
    return "\n".join(lines)


async def _already_sent_today(
    client: DirectusClient,
    *,
    recipient_id: str,
    event_id: str,
    local_today: date,
    tz: ZoneInfo,
) -> bool:
    rows = await client.get_items(
        settings.directus_notifications_collection,
        fields=["id", "sent_at", "date_created"],
        filter_={
            "_and": [
                {settings.directus_notifications_user_field: {"_eq": recipient_id}},
                {settings.directus_notifications_days_field: {"_eq": REMINDER_TYPE}},
                {settings.directus_notifications_event_field: {"_eq": event_id}},
            ]
        },
        limit=100,
    )
    return any(
        _parse_notification_date(row.get("sent_at") or row.get("date_created"), tz)
        == local_today
        for row in rows
    )


async def _load_due_events(
    client: DirectusClient,
    *,
    now: datetime,
) -> list[tuple[dict[str, Any], datetime, bool, int]]:
    reminder_days = {int(value) for value in settings.notifier_reminder_days}
    if not reminder_days:
        return []

    events = await client.get_items(
        settings.directus_events_collection,
        fields=[
            "id",
            settings.directus_events_owner_field,
            settings.directus_events_title_field,
            settings.directus_events_date_field,
            "location",
        ],
    )

    due: list[tuple[dict[str, Any], datetime, bool, int]] = []
    for event in events:
        event_dt, had_time = _parse_datetime(
            event.get(settings.directus_events_date_field), now.tzinfo  # type: ignore[arg-type]
        )
        if event_dt is None:
            continue

        days_left = (event_dt.date() - now.date()).days
        if days_left not in reminder_days or days_left < 0:
            continue
        if days_left == 0 and had_time and event_dt < now:
            continue
        due.append((event, event_dt, had_time, days_left))
    return due


async def _accepted_participants_by_event(
    client: DirectusClient,
    event_ids: list[str],
) -> dict[str, set[str]]:
    if not event_ids:
        return {}

    rows = await client.get_items(
        settings.directus_event_participants_collection,
        fields=["event_id", "user_id", "status"],
        filter_={
            "_and": [
                {"event_id": {"_in": event_ids}},
                {"status": {"_eq": "accepted"}},
            ]
        },
    )

    result: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        event_id = _rel(row.get("event_id"))
        user_id = _rel(row.get("user_id"))
        if event_id and user_id:
            result[event_id].add(user_id)
    return result


async def _users_by_ids(
    client: DirectusClient,
    user_ids: set[str],
) -> dict[str, dict[str, Any]]:
    if not user_ids:
        return {}
    rows = await client.get_items(
        settings.directus_users_collection,
        fields=[
            "id",
            settings.directus_users_telegram_field,
            settings.directus_users_locale_field,
            "display_name",
            "username",
        ],
        filter_={"id": {"_in": list(user_ids)}},
    )
    return {str(row["id"]): row for row in rows}


async def send_due_event_reminders(
    *,
    only_user_id: str | None = None,
) -> dict[str, int]:
    """Створює in-app і Telegram-нагадування для власників та учасників.

    Pending/declined учасники нагадування не отримують. Для ручного тесту
    ``only_user_id`` обмежує відправку поточним користувачем.
    """
    client = get_directus()
    tz = ZoneInfo(settings.notifier_timezone)
    now = datetime.now(tz)

    due_events = await _load_due_events(client, now=now)
    event_ids = [str(event["id"]) for event, *_ in due_events]
    accepted = await _accepted_participants_by_event(client, event_ids)

    recipients_by_event: dict[str, set[str]] = {}
    all_recipient_ids: set[str] = set()
    for event, *_ in due_events:
        event_id = str(event["id"])
        recipients = set(accepted.get(event_id, set()))
        owner_id = _rel(event.get(settings.directus_events_owner_field))
        if owner_id:
            recipients.add(owner_id)
        if only_user_id is not None:
            recipients = {only_user_id} if only_user_id in recipients else set()
        recipients_by_event[event_id] = recipients
        all_recipient_ids.update(recipients)

    users = await _users_by_ids(client, all_recipient_ids)

    created_count = 0
    telegram_candidates = 0
    skipped_count = 0
    failed_count = 0

    for event, event_dt, had_time, days_left in due_events:
        event_id = str(event["id"])
        title = str(event.get(settings.directus_events_title_field) or "Подія")
        location = event.get("location")

        for recipient_id in recipients_by_event.get(event_id, set()):
            try:
                if await _already_sent_today(
                    client,
                    recipient_id=recipient_id,
                    event_id=event_id,
                    local_today=now.date(),
                    tz=tz,
                ):
                    skipped_count += 1
                    continue

                user = users.get(recipient_id, {})
                language = (
                    "en"
                    if user.get(settings.directus_users_locale_field) == "en"
                    else "uk"
                )
                telegram_text = _telegram_text(
                    title=title,
                    event_dt=event_dt,
                    had_time=had_time,
                    days_left=days_left,
                    location=str(location) if location else None,
                    language=language,
                )
                if user.get(settings.directus_users_telegram_field):
                    telegram_candidates += 1

                await create_notification(
                    recipient_id=recipient_id,
                    notif_type=REMINDER_TYPE,
                    related_id=event_id,
                    telegram_text=telegram_text,
                    send_telegram=True,
                    required=True,
                )
                created_count += 1
            except DirectusError as exc:
                failed_count += 1
                logger.warning(
                    "Could not create reminder for user=%s event=%s: %s",
                    recipient_id,
                    event_id,
                    exc,
                )
            except Exception as exc:  # noqa: BLE001
                failed_count += 1
                logger.exception(
                    "Reminder failed for user=%s event=%s: %s",
                    recipient_id,
                    event_id,
                    exc,
                )

    return {
        "events_due": len(due_events),
        "notifications_created": created_count,
        "telegram_candidates": telegram_candidates,
        "skipped_existing": skipped_count,
        "failed": failed_count,
    }


# Старе ім'я залишено, щоб не ламати імпорти scheduler.
async def send_daily_reminders() -> dict[str, int]:
    return await send_due_event_reminders()
