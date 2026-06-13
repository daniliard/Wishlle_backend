from typing import Any

from app.auth.schemas import GoogleUser, TelegramUser
from app.core.config import settings
from app.core.directus import DirectusClient


async def find_user_by_telegram_id(
    client: DirectusClient, telegram_id: int
) -> dict[str, Any] | None:
    items = await client.get_items(
        settings.directus_users_collection,
        filter_={"telegram_id": {"_eq": telegram_id}},
        limit=1,
    )
    return items[0] if items else None


async def find_user_by_google_sub(
    client: DirectusClient, google_sub: str
) -> dict[str, Any] | None:
    items = await client.get_items(
        settings.directus_users_collection,
        filter_={"google_sub": {"_eq": google_sub}},
        limit=1,
    )
    return items[0] if items else None


async def create_user_from_telegram(
    client: DirectusClient, tg_user: TelegramUser
) -> dict[str, Any]:
    display_name = " ".join(filter(None, [tg_user.first_name, tg_user.last_name])) or None
    payload = {
        "telegram_id": tg_user.id,
        "username": tg_user.username,
        "display_name": display_name,
        "language": tg_user.language_code or "uk",
        "auth_provider": "telegram",
    }
    return await client.create_item(settings.directus_users_collection, payload)


async def create_user_from_google(
    client: DirectusClient, google_user: GoogleUser
) -> dict[str, Any]:
    payload = {
        "google_sub": google_user.sub,
        "display_name": google_user.name,
        "avatar_url": google_user.picture,
        "auth_provider": "google",
    }
    return await client.create_item(settings.directus_users_collection, payload)
