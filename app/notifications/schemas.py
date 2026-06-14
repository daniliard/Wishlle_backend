from pydantic import BaseModel, Field


class NotificationData(BaseModel):
    id: str
    type: str
    title: str
    body: str | None = None
    related_id: str | None = None
    is_read: bool = False
    created_at: str | None = None
    # Додаткові дані для навігації на фронті (event_id, friendship_id тощо)
    data: dict = Field(default_factory=dict)


class UnreadCount(BaseModel):
    count: int
