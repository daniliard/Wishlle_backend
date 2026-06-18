"""Спільний сервіс сповіщень.

Працює СУВОРО з полями колекції notifications за ER-схемою:
recipient_id, type, related_id, sent_at, delivered, error_message.

Жодних додаткових полів (title/body/is_read) не використовуємо —
текст сповіщення генерується на льоту з type + related_id.

Типи (поле type — string):
- friend_request   — нова заявка в друзі
- friend_accepted  — заявку прийнято
- event_invite     — запрошення на подію
- event_reminder   — нагадування про подію
- reservation      — твій товар зарезервували
"""
import logging
from datetime import datetime, timezone

from aiogram import Bot
from aiogram.enums import ParseMode

from app.core.config import settings
from app.core.directus import DirectusClient, DirectusError, get_directus

logger = logging.getLogger("wishlle.notifications")

USER_FIELD = settings.directus_notifications_user_field    # recipient_id
TYPE_FIELD = settings.directus_notifications_days_field     # type
RELATED_FIELD = settings.directus_notifications_event_field  # related_id


async def _telegram_id(client: DirectusClient, user_id: str) -> str | None:
    user = await client.get_item(
        settings.directus_users_collection, user_id,
        fields=['id', settings.directus_users_telegram_field],
    )
    if not user:
        return None
    return user.get(settings.directus_users_telegram_field)


async def _send_telegram(chat_id: str, text: str) -> tuple[bool, str | None]:
    bot = Bot(token=settings.telegram_bot_token)
    try:
        await bot.send_message(chat_id=chat_id, text=text, parse_mode=ParseMode.HTML)
        return True, None
    except Exception as exc:  # noqa: BLE001
        logger.warning("Telegram send failed to %s: %s", chat_id, exc)
        return False, str(exc)
    finally:
        await bot.session.close()


async def create_notification(
    recipient_id: str,
    notif_type: str,
    *,
    telegram_text: str | None = None,
    related_id: str | None = None,
    send_telegram: bool = True,
    required: bool = False,
) -> dict | None:
    """Створює запис сповіщення (тільки поля зі схеми) і шле в Telegram.

    telegram_text — готовий текст саме для Telegram-повідомлення.
    У БД зберігаються лише type + related_id; текст для UI будується
    окремо при читанні (див. router._build_text).
    Помилки не пробрасуються, щоб не ламати основну дію.
    """
    client = get_directus()
    now = datetime.now(tz=timezone.utc)

    payload = {
        USER_FIELD: recipient_id,
        TYPE_FIELD: notif_type,
        'sent_at': now.isoformat(),
        'delivered': False,
    }
    if related_id is not None:
        payload[RELATED_FIELD] = related_id

    created = None
    try:
        created = await client.create_item(settings.directus_notifications_collection, payload)
    except DirectusError as exc:
        logger.warning("Could not store notification: %s", exc)
        if required:
            raise

    if send_telegram and telegram_text:
        try:
            chat_id = await _telegram_id(client, recipient_id)
            if chat_id:
                ok, err = await _send_telegram(str(chat_id), telegram_text)
                if created and err:
                    try:
                        await client.update_item(
                            settings.directus_notifications_collection,
                            created['id'],
                            {'error_message': err[:500]},
                        )
                    except DirectusError:
                        pass
        except Exception as exc:  # noqa: BLE001
            logger.warning("Telegram notification failed: %s", exc)

    return created
