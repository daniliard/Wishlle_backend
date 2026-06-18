from pydantic import BaseModel, Field


class PublicItemData(BaseModel):
    id: str
    title: str
    url: str | None = None
    price: float | None = None
    image_url: str | None = None
    notes: str | None = None
    is_reserved: bool = False


class PublicListData(BaseModel):
    id: str
    title: str
    emoji: str = '🎁'
    owner_name: str
    owner_avatar: str | None = None
    items_count: int = 0
    items: list[PublicItemData] = Field(default_factory=list)
