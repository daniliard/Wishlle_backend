from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class FriendCreate(BaseModel):
    friend_id: str = Field(min_length=1)


class FriendUpdate(BaseModel):
    nickname: str | None = Field(default=None, max_length=80)
    tags: list[str] | None = Field(default=None, max_length=8)

    @field_validator('nickname', mode='before')
    @classmethod
    def clean_nickname(cls, value: Any) -> str | None:
        if value is None:
            return None
        value = str(value).strip()
        return value or None

    @field_validator('tags')
    @classmethod
    def clean_tags(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        result: list[str] = []
        seen: set[str] = set()
        for raw in value:
            tag = str(raw).strip()
            if not tag:
                continue
            if len(tag) > 30:
                raise ValueError('Тег може містити максимум 30 символів.')
            key = tag.casefold()
            if key in seen:
                continue
            seen.add(key)
            result.append(tag)
        return result[:8]


class FriendUserData(BaseModel):
    id: str
    display_name: str | None = None
    username: str | None = None
    avatar_url: str | None = None
    birth_date: date | None = None


class SearchUserData(FriendUserData):
    already_added: bool = False
    can_add: bool = True
    request_status: Literal['none', 'outgoing', 'incoming', 'friends'] = 'none'
    request_id: str | None = None


class FriendshipData(BaseModel):
    id: str
    friend_id: str
    nickname: str | None = None
    tags: list[str] = Field(default_factory=list)
    created_at: str | None = None
    accessible_lists_count: int = 0
    user: FriendUserData


class FriendRequestData(BaseModel):
    id: str
    requester_id: str
    created_at: str | None = None
    is_read: bool = False
    user: FriendUserData


class FriendRequestSentData(BaseModel):
    id: str
    recipient_id: str
    status: Literal['pending'] = 'pending'
    created_at: str | None = None


class FriendWishlistData(BaseModel):
    id: str
    title: str
    emoji: str = '🎁'
    visibility: str = 'public'
    date_created: str | None = None
    items_count: int = 0
    preview_items: list[dict] = Field(default_factory=list)


class FriendDetailsData(BaseModel):
    friendship_id: str
    nickname: str | None = None
    tags: list[str] = Field(default_factory=list)
    user: FriendUserData
    wishlists: list[FriendWishlistData] = Field(default_factory=list)
