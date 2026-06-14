from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


Visibility = Literal['public', 'friends', 'private']


class PrivacySettings(BaseModel):
    profile_visibility: Visibility = 'friends'
    wishlists_visibility: Visibility = 'friends'
    show_birth_date: bool = True
    show_username: bool = True
    searchable_by_username: bool = True
    allow_friend_requests: bool = True


class NotificationSettings(BaseModel):
    in_app: bool = True
    telegram: bool = True
    event_reminders: bool = True
    birthday_reminders: bool = True
    friend_requests: bool = True
    reservations: bool = True
    wishlist_updates: bool = False


class ProfilePreferences(BaseModel):
    privacy: PrivacySettings = Field(default_factory=PrivacySettings)
    notifications: NotificationSettings = Field(default_factory=NotificationSettings)


def parse_preferences(value: Any) -> ProfilePreferences:
    if isinstance(value, str):
        import json
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            value = {}
    if not isinstance(value, dict):
        value = {}
    try:
        return ProfilePreferences.model_validate(value)
    except ValueError:
        return ProfilePreferences()


class ProfileData(BaseModel):
    id: str
    display_name: str | None = None
    username: str | None = None
    birth_date: date | None = None
    avatar_url: str | None = None
    auth_provider: str | None = None
    language: str | None = None
    has_telegram: bool = False
    preferences: ProfilePreferences = Field(default_factory=ProfilePreferences)


class ProfileUpdate(BaseModel):
    display_name: str | None = Field(default=None, max_length=100)
    username: str | None = Field(default=None, min_length=3, max_length=30)
    birth_date: date | None = None
    language: Literal['uk', 'en'] | None = None
    preferences: ProfilePreferences | None = None

    @field_validator('display_name', 'username', mode='before')
    @classmethod
    def trim_strings(cls, value):
        if value is None:
            return None
        value = str(value).strip()
        return value or None

    @field_validator('username')
    @classmethod
    def validate_username(cls, value: str | None):
        if value is None:
            return None
        if not value.replace('_', '').isalnum() or not value.isascii():
            raise ValueError('Нікнейм може містити лише латинські літери, цифри та символ _.')
        return value
