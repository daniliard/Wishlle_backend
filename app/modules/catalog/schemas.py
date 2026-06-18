from pydantic import BaseModel


class CatalogItemData(BaseModel):
    id: str
    title: str
    description: str | None = None
    price: float | None = None
    currency: str = 'UAH'
    image_url: str | None = None
    product_url: str | None = None
    category: str | None = None
    is_featured: bool = False
    sort_order: int = 0


class AddToListRequest(BaseModel):
    wishlist_id: str


class CatalogCategory(BaseModel):
    value: str
    count: int
