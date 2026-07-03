from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from rapidfuzz import fuzz, process, utils

from src.navidrome.client import NavidromeClient


CACHE_VERSION = 1
# Below this score a match is more noise than signal (empirically tuned on a
# real ~4.5k library with typo'd queries).
DEFAULT_SCORE_CUTOFF = 60


class LocalLibrary:
    """In-memory, typo-tolerant index of the whole track library.

    Every song's metadata is fetched once (paginated) and kept in RAM so
    searches are instant and fuzzy. The *raw* Subsonic dicts are also
    persisted to disk so later launches skip the network fetch until an
    explicit resync; stream/cover URLs are rebuilt on every load because
    they embed a per-session auth token.
    """

    def __init__(self, client: NavidromeClient, cache_path: Path | None = None):
        self._client = client
        self._cache_path = cache_path or self._default_cache_path()
        self._tracks: list[dict[str, Any]] = []
        # Matched-against strings, index-aligned with ``self._tracks``.
        self._choices: list[str] = []
        self.loaded = False

    @staticmethod
    def _default_cache_path() -> Path:
        base = Path(
            os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache")
        ) / "tuitify"
        return base / "library.json"

    # --- Loading -------------------------------------------------------

    def load(self, force: bool = False) -> int:
        """Populate the index and return the track count.

        Uses the on-disk cache when present unless ``force`` is set, in
        which case the library is re-fetched from the server. Safe to call
        from a worker thread — it never touches the UI.
        """
        raw: list[dict[str, Any]] | None = None
        if not force:
            raw = self._read_disk_cache()

        if raw is None:
            raw = self._client.fetch_all_songs_raw()
            self._write_disk_cache(raw)

        self._index(raw)
        return len(self._tracks)

    def _index(self, raw_songs: list[dict[str, Any]]) -> None:
        # Rebuild fresh (session-scoped) URLs by re-mapping every raw dict.
        self._tracks = [self._client.to_track(song) for song in raw_songs]
        self._choices = [
            f"{track.get('title', '')} {track.get('artist_name', '')}"
            for track in self._tracks
        ]
        self.loaded = True

    # --- Searching -----------------------------------------------------

    def search(
        self,
        query: str,
        limit: int = 30,
        score_cutoff: int = DEFAULT_SCORE_CUTOFF,
    ) -> list[dict[str, Any]]:
        """Return the best fuzzy matches for ``query``, most relevant first."""
        if not self._tracks or not query.strip():
            return []
        matches = process.extract(
            query,
            self._choices,
            scorer=fuzz.token_set_ratio,
            processor=utils.default_process,
            limit=limit,
            score_cutoff=score_cutoff,
        )
        return [self._tracks[index] for _, _, index in matches]

    # --- Disk cache ----------------------------------------------------

    def _read_disk_cache(self) -> list[dict[str, Any]] | None:
        try:
            data = json.loads(self._cache_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict) or data.get("version") != CACHE_VERSION:
            return None
        tracks = data.get("songs")
        if isinstance(tracks, list) and tracks:
            return tracks
        return None

    def _write_disk_cache(self, raw_songs: list[dict[str, Any]]) -> None:
        payload = {
            "version": CACHE_VERSION,
            "fetched_at": time.time(),
            "songs": raw_songs,
        }
        try:
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            self._cache_path.write_text(json.dumps(payload), encoding="utf-8")
        except OSError:
            # A missing cache only costs a re-fetch next time; never fatal.
            pass
