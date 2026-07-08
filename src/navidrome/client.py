from __future__ import annotations

import hashlib
import secrets
from typing import Any
from urllib.parse import urlencode

import requests
from requests.adapters import HTTPAdapter, Retry

from .config import NavidromeConfig


API_VERSION = "1.16.1"
CLIENT_NAME = "tuitify"
DEFAULT_TIMEOUT = 20
# Transient blips (a restarting server, a dropped connection) are retried
# transparently; anything past this is a real outage worth surfacing.
DEFAULT_RETRIES = 2
# `fetch_all_songs_raw` trusts the server to eventually return a short page.
# A server that always returns a full page would otherwise spin forever.
MAX_LIBRARY_PAGES = 1000


class NavidromeError(Exception):
    """Any failure while talking to the Navidrome server.

    Network errors, HTTP errors, malformed JSON, and unexpected payload
    shapes are all funnelled into this one type, so callers degrade with a
    single `except` clause instead of guessing which library raised what.
    """


class SubsonicError(NavidromeError):
    """The server answered, but reported an application-level error."""


def _describe(error: Exception) -> str:
    """Turn a requests exception into something worth showing a user."""
    if isinstance(error, requests.Timeout):
        return "server timed out"
    if isinstance(error, requests.ConnectionError):
        return "cannot reach server"
    if isinstance(error, requests.exceptions.InvalidJSONError):
        return "server returned a malformed response"
    if isinstance(error, requests.HTTPError):
        status = getattr(error.response, "status_code", None)
        return f"server returned HTTP {status}" if status else "server returned an HTTP error"
    return str(error) or error.__class__.__name__


def _coerce_int(value: Any) -> int | None:
    """Best-effort int from whatever the server put in a numeric field."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _build_session(retries: int) -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=retries,
        backoff_factor=0.4,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
        # Let `raise_for_status` produce the error so every failure path
        # converges on the same HTTPError -> NavidromeError translation.
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


class NavidromeClient:
    """Subsonic API client for a Navidrome server.

    Handles search, streaming, recommendations, and cover art. Stream and
    cover-art URLs are returned fully authenticated so they can be handed
    straight to VLC or `requests`.

    Every method either returns usable data or raises `NavidromeError`;
    nothing else escapes. Recommendation lookups swallow failures entirely
    and return an empty list, because a missing suggestion is not an error
    worth interrupting playback for.
    """

    def __init__(
        self,
        config: NavidromeConfig,
        default_results: int = 30,
        timeout: int = DEFAULT_TIMEOUT,
        retries: int = DEFAULT_RETRIES,
    ):
        self.config = config
        self.default_results = default_results
        self.timeout = timeout
        self._session = _build_session(retries)
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
        self, endpoint: str, params: dict[str, Any] | None = None, timeout: int | None = None
    ) -> dict[str, Any]:
        url = self._build_url(endpoint, params or {})
        try:
            response = self._session.get(url, timeout=timeout or self.timeout)
            response.raise_for_status()
            body = response.json()
        except requests.RequestException as error:
            raise NavidromeError(_describe(error)) from error
        except ValueError as error:  # a body that isn't JSON at all
            raise NavidromeError("server returned a malformed response") from error

        payload = body.get("subsonic-response") if isinstance(body, dict) else None
        if not isinstance(payload, dict):
            raise NavidromeError("server returned an unexpected response")

        if payload.get("status") != "ok":
            error_body = payload.get("error")
            message = error_body.get("message") if isinstance(error_body, dict) else None
            raise SubsonicError(message or "Subsonic request failed")
        return payload

    @staticmethod
    def _songs(payload: dict[str, Any], container_key: str) -> list[dict[str, Any]]:
        """Pull the song list out of a Subsonic envelope, whatever its shape.

        The container or the `song` key may be absent (no results), and some
        Subsonic servers collapse a single-element list into a bare object.
        """
        container = payload.get(container_key)
        if not isinstance(container, dict):
            return []
        songs = container.get("song")
        if isinstance(songs, dict):
            songs = [songs]
        if not isinstance(songs, list):
            return []
        return [song for song in songs if isinstance(song, dict)]

    # --- Connection ----------------------------------------------------

    def ping(self) -> None:
        """Validate the connection and credentials; raises on failure."""
        self._request("ping", timeout=10)

    # --- URLs ----------------------------------------------------------

    def stream_url(self, song_id: str | None) -> str:
        if not song_id:
            return ""
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
        return [self._to_track(song) for song in self._songs(payload, "starred2")]

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
        return [self._to_track(song) for song in self._songs(payload, "searchResult3")]

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
        for _ in range(MAX_LIBRARY_PAGES):
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
            batch = self._songs(payload, "searchResult3")
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
        """Best-effort similar songs; an empty list means "nothing to suggest"."""
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
            except NavidromeError:
                continue
            songs = self._songs(payload, key)
            if songs:
                return [self._to_track(song) for song in songs]

        return []

    def get_random_songs(self, count: int = 50) -> list[dict[str, Any]]:
        """Best-effort random songs; an empty list means "nothing to suggest"."""
        try:
            payload = self._request("getRandomSongs", {"size": count})
        except NavidromeError:
            return []
        return [self._to_track(song) for song in self._songs(payload, "randomSongs")]

    # --- Mapping -------------------------------------------------------

    def _to_track(self, song: dict[str, Any]) -> dict[str, Any]:
        song_id = song.get("id")
        duration = _coerce_int(song.get("duration"))
        return {
            "id": song_id,
            "title": song.get("title") or "Unknown title",
            "thumbnail": self.cover_art_url(song.get("coverArt") or song_id),
            "url": self.stream_url(song_id),
            "total_play_time": self._format_duration(duration),
            "duration": duration,
            "bpm": _coerce_int(song.get("bpm")),
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
