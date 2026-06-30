from __future__ import annotations

from typing import Any

from src.navidrome.client import NavidromeClient


class NavidromeSearcher:
    """Search-focused wrapper around the Navidrome client."""

    def __init__(self, client: NavidromeClient):
        self._client = client

    def search(self, query: str, num_results: int | None = None) -> list[dict[str, Any]]:
        return self._client.search_songs(query=query, num_results=num_results)

    def search_media_details(
        self,
        query: str,
        num_results: int | None = None,
        media_type: str = "music",
    ) -> list[dict[str, Any]]:
        return self._client.search_media_details(
            query=query,
            num_results=num_results,
            media_type=media_type,
        )
