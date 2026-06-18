import re
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from app.core.config import settings
from app.modules.parser.schemas import ParsedMetadata


PRICE_PATTERN = re.compile(r"(\d+[\s.,]?\d*)\s*(грн|UAH|USD|EUR|\$|€)", re.IGNORECASE)


class ParserError(Exception):
    pass


async def fetch_html(url: str) -> str:
    headers = {
        "User-Agent": settings.parser_user_agent,
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "uk-UA,uk;q=0.9,en;q=0.8",
    }
    async with httpx.AsyncClient(
        timeout=settings.parser_timeout_seconds,
        follow_redirects=True,
        headers=headers,
    ) as client:
        try:
            response = await client.get(url)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ParserError(f"Failed to fetch URL: {exc}") from exc

        content_type = response.headers.get("content-type", "")
        if "html" not in content_type.lower():
            raise ParserError(f"Unsupported content type: {content_type}")

        return response.text


def _get_meta(soup: BeautifulSoup, *candidates: tuple[str, str]) -> str | None:
    for attr, value in candidates:
        tag = soup.find("meta", attrs={attr: value})
        if tag and tag.get("content"):
            return tag["content"].strip()
    return None


def _absolutize(base: str, link: str | None) -> str | None:
    if not link:
        return None
    parsed = urlparse(link)
    if parsed.scheme in ("http", "https"):
        return link
    return urljoin(base, link)


def _extract_price(text: str | None) -> tuple[str | None, str | None]:
    if not text:
        return None, None
    match = PRICE_PATTERN.search(text)
    if not match:
        return None, None
    amount = match.group(1).replace(" ", "").replace(",", ".")
    currency = match.group(2).upper().replace("$", "USD").replace("€", "EUR")
    if currency == "ГРН":
        currency = "UAH"
    return amount, currency


def parse_metadata(html: str, source_url: str) -> ParsedMetadata:
    soup = BeautifulSoup(html, "html.parser")

    title = (
        _get_meta(soup, ("property", "og:title"), ("name", "twitter:title"))
        or (soup.title.string.strip() if soup.title and soup.title.string else None)
    )

    description = _get_meta(
        soup,
        ("property", "og:description"),
        ("name", "twitter:description"),
        ("name", "description"),
    )

    image_raw = _get_meta(
        soup,
        ("property", "og:image"),
        ("name", "twitter:image"),
        ("name", "twitter:image:src"),
    )
    image = _absolutize(source_url, image_raw)

    site_name = _get_meta(soup, ("property", "og:site_name"))

    price = _get_meta(
        soup,
        ("property", "product:price:amount"),
        ("property", "og:price:amount"),
        ("itemprop", "price"),
    )
    currency = _get_meta(
        soup,
        ("property", "product:price:currency"),
        ("property", "og:price:currency"),
        ("itemprop", "priceCurrency"),
    )

    if not price:
        price, fallback_currency = _extract_price(description or title)
        currency = currency or fallback_currency

    return ParsedMetadata(
        url=source_url,
        title=title,
        description=description,
        image=image,
        site_name=site_name,
        price=price,
        currency=currency,
    )


async def parse_url(url: str) -> ParsedMetadata:
    html = await fetch_html(url)
    return parse_metadata(html, url)
