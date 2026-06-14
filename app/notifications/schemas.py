from pydantic import BaseModel


class NotificationData(BaseModel):
    id: str
    type: str
    title: str
    body: str | None = None
    related_id: str | None = None
    is_read: bool = False
    created_at: str | None = None
    # Куди вести на фронті (friends/events/lists)
    nav: str | None = None


class UnreadCount(BaseModel):
    count: int
