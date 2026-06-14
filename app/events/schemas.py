from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, model_validator


EventType = Literal['private', 'group']
ParticipantStatus = Literal['invited', 'accepted', 'declined']
ParticipantRole = Literal['honoree', 'participant']


class EventCreate(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    description: str | None = None
    event_date: date
    location: str | None = Field(default=None, max_length=255)
    event_type: EventType
    honoree_id: str | None = None
    # Існуюче поле cover_image використовуємо також як ярлик/emoji події.
    cover_image: str | None = Field(default=None, max_length=255)
    participant_ids: list[str] = Field(default_factory=list)

    @model_validator(mode='after')
    def _check_honoree(self):
        if self.event_type == 'private' and not self.honoree_id:
            raise ValueError('Для приватної події потрібно вказати іменинника (honoree_id).')
        if self.event_type == 'group' and self.honoree_id:
            raise ValueError('Групова подія не може мати honoree_id.')
        return self


class EventUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = None
    event_date: date | None = None
    location: str | None = Field(default=None, max_length=255)
    event_type: EventType | None = None
    honoree_id: str | None = None
    cover_image: str | None = Field(default=None, max_length=255)

    @model_validator(mode='after')
    def _check_partial_honoree(self):
        if self.event_type == 'private' and not self.honoree_id:
            raise ValueError('Для приватної події потрібно вказати іменинника (honoree_id).')
        if self.event_type == 'group' and self.honoree_id:
            raise ValueError('Групова подія не може мати honoree_id.')
        return self


class ParticipantUser(BaseModel):
    id: str
    display_name: str | None = None
    username: str | None = None
    avatar_url: str | None = None


class ParticipantData(BaseModel):
    id: str
    user_id: str
    status: ParticipantStatus
    role: ParticipantRole
    user: ParticipantUser


class EventWishlistData(BaseModel):
    id: str
    title: str
    emoji: str = '🎁'
    items_count: int = 0
    owner_id: str
    owner_name: str
    visibility: str = 'public'


class EventData(BaseModel):
    id: str
    owner_id: str
    title: str
    description: str | None = None
    event_date: str | None = None
    location: str | None = None
    event_type: EventType
    honoree_id: str | None = None
    is_auto: bool = False
    cover_image: str | None = None
    is_owner: bool = False
    my_status: ParticipantStatus | None = None
    participants_count: int = 0


class EventDetailData(EventData):
    participants: list[ParticipantData] = Field(default_factory=list)
    wishlists: list[EventWishlistData] = Field(default_factory=list)


class InviteRequest(BaseModel):
    user_ids: list[str] = Field(min_length=1)


class RespondRequest(BaseModel):
    status: Literal['accepted', 'declined']
