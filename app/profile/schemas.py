from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class ProfileData(BaseModel):
    id: str
    display_name: str | None = None
    username: str | None = None
    birth_date: date | None = None
    avatar_url: str | None = None
    auth_provider: str | None = None
    language: str | None = None


class ProfileUpdate(BaseModel):
    display_name: str | None = Field(default=None, max_length=100)
    username: str | None = Field(default=None, min_length=3, max_length=30)
    birth_date: date | None = None
    language: Literal['uk', 'en'] = 'uk'

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
