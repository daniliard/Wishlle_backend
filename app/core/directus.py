from typing import Any

import httpx

from app.core.config import settings


class DirectusError(Exception):
    pass


class DirectusClient:
    def __init__(self, base_url: str, token: str, timeout: float) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=timeout,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
            },
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> Any:
        try:
            response = await self._client.request(
                method, path, params=params, json=json
            )
        except httpx.HTTPError as exc:
            raise DirectusError(f"Directus request failed: {exc}") from exc

        if response.status_code == 204:
            return None

        if response.status_code >= 400:
            raise DirectusError(
                f"Directus {method} {path} → {response.status_code}: {response.text}"
            )

        payload = response.json()
        return payload.get("data", payload)

    async def get_items(
        self,
        collection: str,
        *,
        fields: list[str] | None = None,
        filter_: dict[str, Any] | None = None,
        limit: int = -1,
        sort: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"limit": limit}
        if fields:
            params["fields"] = ",".join(fields)
        if filter_:
            import json as _json
            params["filter"] = _json.dumps(filter_)
        if sort:
            params["sort"] = ",".join(sort)
        data = await self._request("GET", f"/items/{collection}", params=params)
        return data or []

    async def get_item(
        self,
        collection: str,
        item_id: str | int,
        *,
        fields: list[str] | None = None,
    ) -> dict[str, Any] | None:
        params: dict[str, Any] = {}
        if fields:
            params["fields"] = ",".join(fields)
        try:
            return await self._request(
                "GET", f"/items/{collection}/{item_id}", params=params
            )
        except DirectusError:
            return None

    async def create_item(
        self, collection: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        return await self._request("POST", f"/items/{collection}", json=payload)

    async def update_item(
        self, collection: str, item_id: str | int, payload: dict[str, Any]
    ) -> dict[str, Any]:
        return await self._request(
            "PATCH", f"/items/{collection}/{item_id}", json=payload
        )

    async def upload_file(
        self,
        *,
        filename: str,
        content: bytes,
        content_type: str,
        title: str | None = None,
    ) -> dict[str, Any]:
        files = {"file": (filename, content, content_type)}
        data = {"title": title} if title else None
        try:
            response = await self._client.post("/files", files=files, data=data)
        except httpx.HTTPError as exc:
            raise DirectusError(f"Directus file upload failed: {exc}") from exc
        if response.status_code >= 400:
            raise DirectusError(
                f"Directus POST /files → {response.status_code}: {response.text}"
            )
        payload = response.json()
        return payload.get("data", payload)

    async def get_file(self, file_id: str) -> tuple[bytes, str | None]:
        try:
            response = await self._client.get(f"/assets/{file_id}")
        except httpx.HTTPError as exc:
            raise DirectusError(f"Directus asset request failed: {exc}") from exc
        if response.status_code >= 400:
            raise DirectusError(
                f"Directus GET /assets/{file_id} → {response.status_code}: {response.text}"
            )
        return response.content, response.headers.get("content-type")


_client: DirectusClient | None = None


def get_directus() -> DirectusClient:
    global _client
    if _client is None:
        _client = DirectusClient(
            base_url=settings.directus_url,
            token=settings.directus_token,
            timeout=settings.directus_timeout,
        )
    return _client


async def close_directus() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
