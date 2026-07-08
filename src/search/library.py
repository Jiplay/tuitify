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
# Queries shorter than this skip the substring booster: a 2-3 char fragment
# is a substring of half the library and would drown real matches in noise.
_PARTIAL_MIN_LEN = 4


def _match_score(query: str, choice: str, **_: Any) -> float:
    """Fuzzy score blending whole-string and substring matching.

    ``token_set_ratio`` is strong for multi-word and typo'd queries but
    punishes a short fragment buried in a longer string (e.g. "shatta"
    against "shattaland xavier picardo" scores ~39). ``partial_ratio``
    covers exactly that prefix/substring case, so we take the better of the
    two — gated by length so tiny fragments don't match everything.

    ``process.extract`` passes strings already lowercased/stripped by its
    ``processor``, so casing is handled upstream; this only affects ranking.
    """
    score = fuzz.token_set_ratio(query, choice)
    if len(query) >= _PARTIAL_MIN_LEN:
        score = max(score, fuzz.partial_ratio(query, choice))
    return score


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
        # True when the most recent ``load`` was served from the on-disk
        # cache (i.e. it may be stale and worth revalidating from the server).
        self.served_from_cache = False

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
            self.served_from_cache = False
        else:
            self.served_from_cache = True

        self._index(raw)
        return len(self._tracks)

    def _index(self, raw_songs: list[dict[str, Any]]) -> None:
        # Rebuild fresh (session-scoped) URLs by re-mapping every raw dict.
        # A stale cache file can hold anything, so skip entries that aren't
        # song objects instead of failing the whole index.
        self._tracks = [
            self._client.to_track(song)
            for song in raw_songs
            if isinstance(song, dict)
        ]
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
        """Return the best fuzzy matches for ``query``, most relevant first.

        Never raises: a search that blows up should degrade to "no results",
        not take down the app mid-keystroke.
        """
        if not self._tracks or not query.strip():
            return []
        try:
            matches = process.extract(
                query,
                self._choices,
                scorer=_match_score,
                processor=utils.default_process,
                limit=limit,
                score_cutoff=score_cutoff,
            )
        except Exception:
            return []
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
        # Write-then-rename: a crash (or a full disk) mid-write leaves the
        # previous good cache in place rather than a truncated JSON file.
        temp_path = self._cache_path.with_suffix(".json.tmp")
        try:
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            temp_path.write_text(json.dumps(payload), encoding="utf-8")
            temp_path.replace(self._cache_path)
        except (OSError, TypeError, ValueError):
            # A missing cache only costs a re-fetch next time; never fatal.
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass
