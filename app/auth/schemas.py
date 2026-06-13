from pydantic import BaseModel


class TelegramAuthRequest(BaseModel):
    init_data: str


class GoogleAuthRequest(BaseModel):
    id_token: str


class UserData(BaseModel):
    id: str
    display_name: str | None = None
    username: str | None     = None
    avatar_url: str | None   = None
    auth_provider: str | None = None


class AuthResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: str
    user: UserData


class TelegramUser(BaseModel):
    id: int
    first_name: str | None    = None
    last_name: str | None     = None
    username: str | None      = None
    photo_url: str | None     = None   # є в Login Widget
    language_code: str | None = None


class GoogleUser(BaseModel):
    sub: str
    email: str | None   = None
    name: str | None    = None
    picture: str | None = None
