"""Каталог рекомендованих товарів (адмінський контент).

Товари наповнюються адміном через Directus. Користувачі переглядають
каталог і додають товари до власних списків побажань.
"""
from typing import Any

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
        return [_to_item(r) for r in rows]
    except DirectusError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get('/categories', response_model=list[CatalogCategory])
async def list_categories(user_id: str = Depends(current_user_id)) -> list[CatalogCategory]:
    """Повертає категорії з кількістю товарів — для динамічного сайдбару."""
    client = get_directus()
    try:
        rows = await client.get_items(
            settings.directus_catalog_collection,
            fields=['category'],
            limit=500,
        )
        counts: dict[str, int] = {}
        for r in rows:
            cat = r.get('category')
            if cat:
                counts[cat] = counts.get(cat, 0) + 1
        return [CatalogCategory(value=k, count=v) for k, v in sorted(counts.items())]
    except DirectusError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post('/{item_id}/add-to-list', status_code=status.HTTP_201_CREATED)
async def add_catalog_item_to_list(
    item_id: str,
    payload: AddToListRequest,
    user_id: str = Depends(current_user_id),
) -> dict:
    """Додає товар з каталогу до власного списку побажань користувача."""
    client = get_directus()
    try:
        # Каталожний товар
        catalog_item = await client.get_item(settings.directus_catalog_collection, item_id)
        if not catalog_item:
            raise HTTPException(status_code=404, detail='Товар не знайдено в каталозі.')

        # Перевіряємо, що список належить користувачу
        wishlist = await client.get_item(settings.directus_wishes_collection, payload.wishlist_id)
        if not wishlist:
            raise HTTPException(status_code=404, detail='Список не знайдено.')
        if _rel(wishlist.get(settings.directus_wishes_owner_field)) != user_id:
            raise HTTPException(status_code=403, detail='Це не ваш список.')

        created = await client.create_item(settings.directus_wish_items_collection, {
            'wishlist_id': payload.wishlist_id,
            'title': catalog_item.get('title') or 'Товар',
            'url': catalog_item.get('product_url'),
            'price': catalog_item.get('price'),
            'image_url': catalog_item.get('image_url'),
            'notes': catalog_item.get('description'),
            'status': 'available',
        })
        return {'id': str(created['id']), 'wishlist_id': payload.wishlist_id}
    except DirectusError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
