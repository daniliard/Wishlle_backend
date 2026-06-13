import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any

from aiogram import Bot
from aiogram.enums import ParseMode

from app.core.config import settings
from app.core.directus import DirectusClient, DirectusError, get_directus


logger = logging.getLogger(__name__)


def _days_until(target: date, today: date) -> int:
    upcoming = target.replace(year=today.year)
    if upcoming < today:
        upcoming = upcoming.replace(year=today.year + 1)
    return (upcoming - today).days


def _format_event_message(title: str, days_left: int) -> str:
    when = "сьогодні" if days_left == 0 else (
        "завтра" if days_left == 1 else f"через {days_left} дн."
    )
    return (
        f"🎁 Нагадування: <b>{title}</b> — {when}.\n"
        f"Не забудь оновити свій вішлист на Wishlle!"
    )


def _format_wish_message(owner_name: str | None, wish_title: str) -> str:
    who = owner_name or "Хтось"
    return (
        f"✨ <b>{who}</b> додав нове бажання у свій список: "
        f"<i>{wish_title}</i>"
    )


def _parse_iso_date(raw: Any) -> date | None:
    if not raw:
        return None
    if isinstance(raw, date) and not isinstance(raw, datetime):
        return raw
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).date()
    except ValueError:
        return None


async def _fetch_telegram_users(
    client: DirectusClient,
) -> dict[Any, dict[str, Any]]:
    tg_field = settings.directus_users_telegram_field
    users = await client.get_items(
        settings.directus_users_collection,
        fields=["id", tg_field, "full_name", "username"],
        filter_={tg_field: {"_nnull": True}},
    )
    return {u["id"]: u for u in users if u.get(tg_field)}


async def _was_already_sent(
    client: DirectusClient,
    user_id: Any,
    event_id: Any,
    days_before: int,
) -> bool:
    items = await client.get_items(
        settings.directus_notifications_collection,
        fields=["id"],
        filter_={
            "_and": [
                {settings.directus_notifications_user_field: {"_eq": user_id}},
                {settings.directus_notifications_event_field: {"_eq": event_id}},
                {settings.directus_notifications_days_field: {"_eq": days_before}},
            ]
        },
        limit=1,
    )
    return bool(items)


async def _mark_as_sent(
    client: DirectusClient,
    user_id: Any,
    event_id: Any,
    days_before: int,
) -> None:
    await client.create_item(
        settings.directus_notifications_collection,
        {
            settings.directus_notifications_user_field: user_id,
            settings.directus_notifications_event_field: event_id,
            settings.directus_notifications_days_field: days_before,
        },
    )


async def collect_due_events(
    client: DirectusClient, today: date
) -> list[tuple[dict[str, Any], dict[str, Any], int]]:
    reminder_days = set(settings.notifier_reminder_days)
    title_field = settings.directus_events_title_field
    date_field = settings.directus_events_date_field
    owner_field = settings.directus_events_owner_field

    events = await client.get_items(
        settings.directus_events_collection,
        fields=["id", title_field, date_field, owner_field],
    )

    users_by_id = await _fetch_telegram_users(client)

    due: list[tuple[dict[str, Any], dict[str, Any], int]] = []
    for event in events:
        event_date = _parse_iso_date(event.get(date_field))
        if event_date is None:
            continue

        owner_ref = event.get(owner_field)
        owner_id = owner_ref.get("id") if isinstance(owner_ref, dict) else owner_ref
        user = users_by_id.get(owner_id)
        if user is None:
            continue

        days_left = _days_until(event_date, today)
        if days_left in reminder_days:
            due.append((user, event, days_left))
    return due


async def collect_recent_wishes(
    client: DirectusClient, now: datetime
) -> list[tuple[dict[str, Any], str]]:
    if not settings.notifier_wishes_enabled:
        return []

    title_field = settings.directus_wishes_title_field
    owner_field = settings.directus_wishes_owner_field
    created_field = settings.directus_wishes_created_field
    threshold = now - timedelta(hours=settings.notifier_wishes_lookback_hours)

    wishes = await client.get_items(
        settings.directus_wishes_collection,
        fields=["id", title_field, owner_field, created_field],
        filter_={created_field: {"_gte": threshold.isoformat()}},
        sort=[f"-{created_field}"],
    )

    users_by_id = await _fetch_telegram_users(client)

    result: list[tuple[dict[str, Any], str]] = []
    for wish in wishes:
        owner_ref = wish.get(owner_field)
        owner_id = owner_ref.get("id") if isinstance(owner_ref, dict) else owner_ref
        user = users_by_id.get(owner_id)
        if user is None:
            continue
        result.append((user, wish.get(title_field) or "Без назви"))
    return result


async def send_daily_reminders() -> dict[str, int]:
    client = get_directus()
    today = date.today()
    now = datetime.now(tz=timezone.utc)
    title_field = settings.directus_events_title_field
    bot = Bot(token=settings.telegram_bot_token)

    sent_events = 0
    sent_wishes = 0

    try:
        try:
            due_events = await collect_due_events(client, today)
        except DirectusError as exc:
            logger.exception("Failed to fetch events: %s", exc)
            due_events = []

        for user, event, days_left in due_events:
            chat_id = user[settings.directus_users_telegram_field]
            user_id = user["id"]
            event_id = event["id"]

            try:
                if await _was_already_sent(client, user_id, event_id, days_left):
                    continue
            except DirectusError as exc:
                logger.warning("Notifications check failed: %s", exc)
                continue

            title = event.get(title_field) or "Без назви"
            try:
                await bot.send_message(
                    chat_id=chat_id,
                    text=_format_event_message(title, days_left),
                    parse_mode=ParseMode.HTML,
                )
            except Exception as exc:
                logger.warning("Failed to send event to %s: %s", chat_id, exc)
                continue

            try:
                await _mark_as_sent(client, user_id, event_id, days_left)
                sent_events += 1
            except DirectusError as exc:
                logger.warning("Failed to mark notification: %s", exc)

        try:
            wishes = await collect_recent_wishes(client, now)
        except DirectusError as exc:
            logger.exception("Failed to fetch wishes: %s", exc)
            wishes = []

        for user, wish_title in wishes:
            chat_id = user[settings.directus_users_telegram_field]
            try:
                await bot.send_message(
                    chat_id=chat_id,
                    text=_format_wish_message(user.get("full_name"), wish_title),
                    parse_mode=ParseMode.HTML,
                )
                sent_wishes += 1
            except Exception as exc:
                logger.warning("Failed to send wish to %s: %s", chat_id, exc)
    finally:
        await bot.session.close()

    return {"events": sent_events, "wishes": sent_wishes}
