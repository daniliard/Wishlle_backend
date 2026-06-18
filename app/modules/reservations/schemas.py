from pydantic import BaseModel, Field


class ReservationCreate(BaseModel):
    item_id: str


class ReservedItemData(BaseModel):
    id: str
    title: str
    url: str | None = None
    price: float | None = None
    image_url: str | None = None
    notes: str | None = None
    status: str = 'available'
    # Чи зарезервований саме поточним користувачем (щоб показати кнопку «Скасувати»)
    reserved_by_me: bool = False
    # Чи є взагалі активне резервування (для чужих — без імені)
    is_reserved: bool = False


class FriendListView(BaseModel):
    id: str
    title: str
    emoji: str = '🎁'
    visibility: str = 'public'
    owner_id: str
    owner_name: str
    items_count: int = 0
    items: list[ReservedItemData] = Field(default_factory=list)


class ReservationData(BaseModel):
    id: str
    item_id: str
    reserved_by: str
    created_at: str | None = None
