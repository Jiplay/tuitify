from __future__ import annotations

import hashlib
import secrets
from typing import Any
from urllib.parse import urlencode

import requests

from .config import NavidromeConfig


API_VERSION = "1.16.1"
CLIENT_NAME = "tuitify"


class SubsonicError(Exception):
    """Raised when the Subsonic server returns an error response."""


class NavidromeClient:
    """Subsonic API client for a Navidrome server.

    Handles search, streaming, recommendations, and cover art. Stream and
    cover-art URLs are returned fully authenticated so they can be handed
    straight to VLC or `requests`.
    """

    def __init__(self, config: NavidromeConfig, default_results: int = 30):
        self.config = config
        self.default_results = default_results
        # Token auth: t = md5(password + salt). Valid for the whole session,
        # so prebuilt stream/cover URLs stay usable.
        self._salt = secrets.token_hex(8)
        self._token = hashlib.md5(
            (config.password + self._salt).encode("utf-8")
        ).hexdigest()

    @property
    def _auth_params(self) -> dict[str, str]:
        return {
            "u": self.config.username,
            "t": self._token,
            "s": self._salt,
            "v": API_VERSION,
            "c": CLIENT_NAME,
            "f": "json",
        }

    def _build_url(self, endpoint: str, params: dict[str, Any]) -> str:
        query = dict(self._auth_params)
        query.update({key: value for key, value in params.items() if value is not None})
        return f"{self.config.base_url}/rest/{endpoint}?{urlencode(query)}"

    def _request(
        self, endpoint: str, params: dict[str, Any] | None = None, timeout: int = 20
    ) -> dict[str, Any]:
        url = self._build_url(endpoint, params or {})
        response = requests.get(url, timeout=timeout)
        response.raise_for_status()
        payload = response.json().get("subsonic-response", {})
        if payload.get("status") != "ok":
            error = payload.get("error", {})
            raise SubsonicError(error.get("message") or "Subsonic request failed")
        return payload

    # --- Connection ----------------------------------------------------

    def ping(self) -> None:
        """Validate the connection and credentials; raises on failure."""
        self._request("ping")

    # --- URLs ----------------------------------------------------------

    def stream_url(self, song_id: str) -> str:
        return self._build_url("stream", {"id": song_id})

    def cover_art_url(self, cover_id: str | None, size: int = 600) -> str | None:
        if not cover_id:
            return None
        return self._build_url("getCoverArt", {"id": cover_id, "size": size})

    def get_stream_info(self, url: str) -> tuple[str, int | None]:
        """Stream URLs are already direct; returned unchanged for the player."""
        return url, None

    # --- Favorites -----------------------------------------------------

    def star(self, song_id: str) -> None:
        self._request("star", {"id": song_id})

    def unstar(self, song_id: str) -> None:
        self._request("unstar", {"id": song_id})

    def get_starred_songs(self) -> list[dict[str, Any]]:
        """Return every starred (liked) song in the library."""
        payload = self._request("getStarred2")
        songs = payload.get("starred2", {}).get("song", [])
        return [self._to_track(song) for song in songs]

    # --- Search --------------------------------------------------------

    def search_songs(
        self, query: str, num_results: int | None = None
    ) -> list[dict[str, Any]]:
        count = num_results if num_results is not None else self.default_results
        payload = self._request(
            "search3",
            {
                "query": query,
                "songCount": count,
                "albumCount": 0,
                "artistCount": 0,
            },
        )
        songs = payload.get("searchResult3", {}).get("song", [])
        return [self._to_track(song) for song in songs]

    def search_media_details(
        self,
        query: str,
        num_results: int | None = None,
        media_type: str = "music",
    ) -> list[dict[str, Any]]:
        return self.search_songs(query=query, num_results=num_results)

    def fetch_all_songs_raw(self, page_size: int = 500) -> list[dict[str, Any]]:
        """Return the raw Subsonic song dicts for the entire library.

        Navidrome's ``search3`` treats an empty query as match-all, so we
        walk it with ``songOffset`` until a short page signals the end. The
        raw dicts are returned untouched (no URL building) so callers can
        safely persist them across sessions; map them with ``to_track`` at
        use time to rebuild session-scoped stream/cover URLs.
        """
        songs: list[dict[str, Any]] = []
        offset = 0
        while True:
            payload = self._request(
                "search3",
                {
                    "query": "",
                    "songCount": page_size,
                    "songOffset": offset,
                    "albumCount": 0,
                    "artistCount": 0,
                },
            )
            batch = payload.get("searchResult3", {}).get("song", [])
            if not batch:
                break
            songs.extend(batch)
            if len(batch) < page_size:
                break
            offset += page_size
        return songs

    def to_track(self, song: dict[str, Any]) -> dict[str, Any]:
        """Map a raw Subsonic song dict to the app's track shape."""
        return self._to_track(song)

    # --- Recommendations ----------------------------------------------

    def get_similar_songs(
        self, seed: dict[str, Any], count: int = 50
    ) -> list[dict[str, Any]]:
        artist_id = seed.get("artist_id")
        song_id = seed.get("id")

        for endpoint, identifier, key in (
            ("getSimilarSongs2", artist_id, "similarSongs2"),
            ("getSimilarSongs", song_id, "similarSongs"),
        ):
            if not identifier:
                continue
            try:
                payload = self._request(endpoint, {"id": identifier, "count": count})
            except (requests.RequestException, SubsonicError):
                continue
            songs = payload.get(key, {}).get("song", [])
            if songs:
                return [self._to_track(song) for song in songs]

        return []

    def get_random_songs(self, count: int = 50) -> list[dict[str, Any]]:
        try:
            payload = self._request("getRandomSongs", {"size": count})
        except (requests.RequestException, SubsonicError):
            return []
        songs = payload.get("randomSongs", {}).get("song", [])
        return [self._to_track(song) for song in songs]

    # --- Mapping -------------------------------------------------------

    def _to_track(self, song: dict[str, Any]) -> dict[str, Any]:
        song_id = song.get("id")
        duration = song.get("duration")
        return {
            "id": song_id,
            "title": song.get("title", "Unknown title"),
            "thumbnail": self.cover_art_url(song.get("coverArt") or song_id),
            "url": self.stream_url(song_id),
            "total_play_time": self._format_duration(duration),
            "duration": duration,
            "bpm": song.get("bpm"),
            "artist_name": song.get("artist") or "Unknown artist",
            "album_name": song.get("album"),
            "artist_id": song.get("artistId"),
            "starred": bool(song.get("starred")),
            "source": "navidrome",
        }

    @staticmethod
    def _format_duration(total_seconds: int | None) -> str:
        if not total_seconds or total_seconds < 0:
            return "00:00"

        hours, rem = divmod(int(total_seconds), 3600)
        minutes, seconds = divmod(rem, 60)

        if hours:
            return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        return f"{minutes:02d}:{seconds:02d}"
