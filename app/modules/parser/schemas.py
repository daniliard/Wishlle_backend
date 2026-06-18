from pydantic import BaseModel, Field, HttpUrl


class ParseUrlRequest(BaseModel):
    url: HttpUrl


class ParsedMetadata(BaseModel):
    url: HttpUrl
    title: str | None = Field(default=None, max_length=512)
    description: str | None = Field(default=None, max_length=2048)
    image: HttpUrl | None = None
    site_name: str | None = None
    price: str | None = None
    currency: str | None = None
