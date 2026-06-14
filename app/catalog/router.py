"""Каталог рекомендованих товарів (адмінський контент).

Товари наповнюються адміном через Directus. Користувачі переглядають
каталог і додають товари до власних списків побажань.
"""
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from fastapi import APIRouter, Depends, HTTPException, status

from app.catalog.schemas import AddToListRequest, CatalogCategory, CatalogItemData
from app.core.config import settings
from app.core.directus import DirectusError, get_directus
from app.profile.router import current_user_id

router = APIRouter()


def _rel(value: Any) -> str | None:
    if isinstance(value, dict):
        value = value.get('id')
    return str(value) if value is not None else None


def _to_item(row: dict) -> CatalogItemData:
    return CatalogItemData(
        id=str(row['id']),
        title=row.get('title') or 'Товар',
        description=row.get('description'),
        price=row.get('price'),
        currency=row.get('currency') or 'UAH',
        image_url=row.get('image_url'),
        product_url=row.get('product_url'),
        category=row.get('category'),
        is_featured=bool(row.get('is_featured', False)),
        sort_order=int(row.get('sort_order') or 0),
    )


def _normalized_url(value: Any) -> str:
    text = str(value or '').strip()
    if not text:
        return ''
    try:
        parsed = urlsplit(text)
        # UTM та fragment не повинні дозволяти додати той самий товар вдруге.
        query = '&'.join(
            part for part in parsed.query.split('&')
            if part and not part.lower().startswith(('utm_', 'gclid=', 'fbclid='))
        )
        return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path.rstrip('/'), query, ''))
    except ValueError:
        return text.rstrip('/').lower()


def _schema_mismatch(exc: DirectusError) -> bool:
    text = str(exc).lower()
    return any(token in text for token in ('field', 'column', 'does not exist', 'unknown'))


async def _existing_catalog_item(wishlist_id: str, item_id: str, product_url: str | None) -> dict | None:
    client = get_directus()
    try:
        rows = await client.get_items(
            settings.directus_wish_items_collection,
            fields=['id', 'catalog_item_id', 'url'],
            filter_={'_and': [
                {'wishlist_id': {'_eq': wishlist_id}},
                {'catalog_item_id': {'_eq': item_id}},
            ]},
            limit=1,
        )
        if rows:
            return rows[0]
    except DirectusError as exc:
        if not _schema_mismatch(exc):
            raise

    # Fallback для старої інсталяції без catalog_item_id.
    target_url = _normalized_url(product_url)
    rows = await client.get_items(
        settings.directus_wish_items_collection,
        fields=['id', 'url'],
        filter_={'wishlist_id': {'_eq': wishlist_id}},
        limit=500,
    )
    if target_url:
        return next((row for row in rows if _normalized_url(row.get('url')) == target_url), None)
    return None


@router.get('', response_model=list[CatalogItemData])
async def list_catalog(
    category: str | None = None,
    search: str | None = None,
    featured: bool = False,
    limit: int = 100,
    user_id: str = Depends(current_user_id),
) -> list[CatalogItemData]:
    client = get_directus()
    limit = max(1, min(limit, 200))

    conditions: list[dict] = []
    if category and category != 'all':
        conditions.append({'category': {'_eq': category}})
    if featured:
        conditions.append({'is_featured': {'_eq': True}})
    if search and search.strip():
        conditions.append({
            '_or': [
                {'title': {'_icontains': search.strip()}},
                {'description': {'_icontains': search.strip()}},
            ]
        })
    filter_ = {'_and': conditions} if conditions else None

    try:
        rows = await client.get_items(
            settings.directus_catalog_collection,
            filter_=filter_,
            sort=['sort_order'],
            limit=limit,
        )
        return [_to_item(row) for row in rows]
    except DirectusError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get('/categories', response_model=list[CatalogCategory])
async def list_categories(user_id: str = Depends(current_user_id)) -> list[CatalogCategory]:
    client = get_directus()
    try:
        rows = await client.get_items(
            settings.directus_catalog_collection,
            fields=['category'],
            limit=500,
        )
        counts: dict[str, int] = {}
        for row in rows:
            category = row.get('category')
            if category:
                counts[category] = counts.get(category, 0) + 1
        return [CatalogCategory(value=key, count=value) for key, value in sorted(counts.items())]
    except DirectusError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post('/{item_id}/add-to-list', status_code=status.HTTP_201_CREATED)
async def add_catalog_item_to_list(
    item_id: str,
    payload: AddToListRequest,
    user_id: str = Depends(current_user_id),
) -> dict:
    """Копіює каталожний товар у власний список, але лише один раз."""
    client = get_directus()
    try:
        catalog_item = await client.get_item(settings.directus_catalog_collection, item_id)
        if not catalog_item:
            raise HTTPException(status_code=404, detail='Товар не знайдено в каталозі.')

        wishlist = await client.get_item(settings.directus_wishes_collection, payload.wishlist_id)
        if not wishlist:
            raise HTTPException(status_code=404, detail='Список не знайдено.')
        if _rel(wishlist.get(settings.directus_wishes_owner_field)) != user_id:
            raise HTTPException(status_code=403, detail='Це не ваш список.')

        if await _existing_catalog_item(
            payload.wishlist_id,
            item_id,
            catalog_item.get('product_url'),
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail='Цей товар уже є в обраному списку.',
            )

        base_data = {
            'wishlist_id': payload.wishlist_id,
            'title': catalog_item.get('title') or 'Товар',
            'url': catalog_item.get('product_url'),
            'price': catalog_item.get('price'),
            'image_url': catalog_item.get('image_url'),
            'notes': catalog_item.get('description'),
            'status': 'available',
        }
        try:
            created = await client.create_item(
                settings.directus_wish_items_collection,
                {
                    **base_data,
                    'source': 'catalog',
                    'catalog_item_id': item_id,
                },
            )
        except DirectusError as exc:
            if not _schema_mismatch(exc):
                raise
            created = await client.create_item(
                settings.directus_wish_items_collection,
                base_data,
            )
        return {'id': str(created['id']), 'wishlist_id': payload.wishlist_id}
    except DirectusError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
