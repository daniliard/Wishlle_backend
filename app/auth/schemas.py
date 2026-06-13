from pydantic import BaseModel


class TelegramAuthRequest(BaseModel):
    init_data: str


class GoogleAuthRequest(BaseModel):
    id_token: str


class AuthResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: str


class TelegramUser(BaseModel):
    id: int
    first_name: str | None = None
    last_name: str | None = None
    username: str | None = None
    language_code: str | None = None


class GoogleUser(BaseModel):
    sub: str
    email: str | None = None
    name: str | None = None
    picture: str | None = None
