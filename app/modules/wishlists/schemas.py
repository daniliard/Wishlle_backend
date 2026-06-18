from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


WishlistVisibility = Literal['public', 'friends', 'private']


class WishlistCreate(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    emoji: str = Field(default='🎁', max_length=255)
    visibility: WishlistVisibility = 'public'
    is_public: bool | None = Field(default=None, exclude=True)

    @model_validator(mode='before')
    @classmethod
    def map_legacy_visibility(cls, values):
        if isinstance(values, dict) and 'visibility' not in values and 'is_public' in values:
            values = {**values, 'visibility': 'public' if values.get('is_public') else 'private'}
        return values

    @field_validator('title')
    @classmethod
    def clean_title(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError('Вкажи назву списку.')
        return value

    @field_validator('emoji')
    @classmethod
    def clean_emoji(cls, value: str) -> str:
        return value.strip() or '🎁'


class WishlistUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=120)
    emoji: str | None = Field(default=None, max_length=255)
    visibility: WishlistVisibility | None = None
    is_public: bool | None = Field(default=None, exclude=True)

    @model_validator(mode='before')
    @classmethod
    def map_legacy_visibility(cls, values):
        if isinstance(values, dict) and 'visibility' not in values and 'is_public' in values:
            values = {**values, 'visibility': 'public' if values.get('is_public') else 'private'}
        return values

    @field_validator('title')
    @classmethod
    def clean_title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError('Вкажи назву списку.')
        return value

    @field_validator('emoji')
    @classmethod
    def clean_emoji(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or '🎁'


class WishlistData(BaseModel):
    id: str
    title: str
    emoji: str = '🎁'
    visibility: WishlistVisibility = 'public'
    # Старе поле лишаємо у відповіді для сумісності з уже відкритими клієнтами.
    is_public: bool = True
    date_created: str | None = None
    items_count: int = 0
    available_count: int = 0
    reserved_count: int = 0
    preview_items: list[dict] = Field(default_factory=list)


class WishItemCreate(BaseModel):
    title: str = Field(min_length=1, max_length=180)
    url: str | None = Field(default=None, max_length=2000)
    price: Decimal | None = Field(default=None, ge=0)
    image_url: str | None = Field(default=None, max_length=2000)
    notes: str | None = Field(default=None, max_length=1000)

    @field_validator('title')
    @classmethod
    def clean_title(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError('Вкажи назву бажання.')
        return value

    @field_validator('url', 'image_url', 'notes')
    @classmethod
    def clean_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None


class WishItemUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=180)
    url: str | None = Field(default=None, max_length=2000)
    price: Decimal | None = Field(default=None, ge=0)
    image_url: str | None = Field(default=None, max_length=2000)
    notes: str | None = Field(default=None, max_length=1000)
    status: Literal['available', 'reserved', 'purchased'] | None = None

    @field_validator('title')
    @classmethod
    def clean_title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError('Вкажи назву бажання.')
        return value

    @field_validator('url', 'image_url', 'notes')
    @classmethod
    def clean_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None


class WishItemData(BaseModel):
    id: str
    wishlist_id: str
    title: str
    url: str | None = None
    price: Decimal | None = None
    image_url: str | None = None
    notes: str | None = None
    status: str = 'available'
    date_created: str | None = None
