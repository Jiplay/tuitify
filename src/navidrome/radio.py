from __future__ import annotations

import random
from typing import Any

from .client import NavidromeClient, NavidromeError
from .utils import clean_tracks, normalize_title, track_signature


class RadioEngine:
    """Manages the playback queue and similar-song based continuation.

    Every method returns an empty result rather than raising when the server
    can't be reached, so a network blip stops the radio instead of the app.
    The one exception is `load_liked_pool`, which the UI calls directly and
    reports on.
    """

    def __init__(
        self,
        client: NavidromeClient,
        history_limit: int = 80,
        recommendation_limit: int = 60,
        exploration_rate: float = 0.15,
        artist_cooldown: int = 3,
        title_cooldown: int = 25,
        album_cooldown: int = 2,
    ):
        self.client = client
        self.history_limit = history_limit
        self.recommendation_limit = recommendation_limit
        self.exploration_rate = exploration_rate
        self.artist_cooldown = artist_cooldown
        self.title_cooldown = title_cooldown
        self.album_cooldown = album_cooldown
        self.history: list[dict[str, Any]] = []
        self.queue: list[dict[str, Any]] = []
        self.liked_pool: list[dict[str, Any]] = []

    def mark_played(self, track: dict[str, Any]) -> None:
        if self.history and track_signature(self.history[-1]) == track_signature(track):
            return
        self.history.append(track)
        if len(self.history) > self.history_limit:
            self.history.pop(0)
        self.queue = [
            queued_track
            for queued_track in self.queue
            if track_signature(queued_track) != track_signature(track)
        ]

    def fetch_next_from_seed(self, seed: dict[str, Any]) -> list[dict[str, Any]]:
        recommendations = clean_tracks(
            self.client.get_similar_songs(seed, count=self.recommendation_limit)
        )
        if not recommendations:
            recommendations = clean_tracks(
                self.client.get_random_songs(count=self.recommendation_limit)
            )

        seed_id = seed.get("id")
        seen_ids = {track.get("id") for track in self.history}
        queued_ids = {track.get("id") for track in self.queue if track.get("id")}
        recent_signatures = {track_signature(track) for track in self.history}
        queued_signatures = {track_signature(track) for track in self.queue}
        seen_ids.add(seed_id)
        recent_signatures.add(track_signature(seed))

        filtered = [
            track
            for track in recommendations
            if track.get("id")
            and track["id"] not in seen_ids
            and track["id"] not in queued_ids
            and track_signature(track) not in recent_signatures
            and track_signature(track) not in queued_signatures
        ]

        seed_title = normalize_title(str(seed.get("title") or ""))
        return [
            track
            for track in filtered
            if normalize_title(str(track.get("title") or "")) != seed_title
        ]

    def build_queue(
        self, seed: dict[str, Any], limit: int = 10
    ) -> list[dict[str, Any]]:
        candidates = self.fetch_next_from_seed(seed)
        if not candidates:
            return []

        planned: list[dict[str, Any]] = []
        available = list(candidates)
        while available and len(planned) < limit:
            ranked = sorted(
                available,
                key=lambda track: self._score_track(track, seed=seed, upcoming=planned),
                reverse=True,
            )
            selection_window = ranked[: min(5, len(ranked))]
            if random.random() < self.exploration_rate:
                next_track = random.choice(ranked[: min(12, len(ranked))])
            else:
                next_track = random.choice(selection_window)
            planned.append(next_track)
            selected_signature = track_signature(next_track)
            available = [
                track
                for track in available
                if track_signature(track) != selected_signature
            ]

        self.queue = list(planned)
        return planned

    def _fetch_random(self, count: int) -> list[dict[str, Any]]:
        candidates = clean_tracks(self.client.get_random_songs(count=count))
        seen_ids = {track.get("id") for track in self.history}
        seen_ids.update(track.get("id") for track in self.queue if track.get("id"))
        seen_signatures = {track_signature(track) for track in self.history}
        seen_signatures.update(track_signature(track) for track in self.queue)
        return [
            track
            for track in candidates
            if track.get("id")
            and track["id"] not in seen_ids
            and track_signature(track) not in seen_signatures
        ]

    def build_random_queue(self, limit: int = 10) -> list[dict[str, Any]]:
        candidates = self._fetch_random(count=max(limit * 3, 30))
        random.shuffle(candidates)
        planned = candidates[:limit]
        self.queue = list(planned)
        return planned

    def random_next(self) -> dict[str, Any] | None:
        candidates = self._fetch_random(count=30)
        if not candidates:
            return None
        next_song = random.choice(candidates)
        self.queue.append(next_song)
        return next_song

    def load_liked_pool(self) -> list[dict[str, Any]]:
        """Fetch and cache every liked (starred) song for shuffle playback."""
        self.liked_pool = clean_tracks(self.client.get_starred_songs())
        return self.liked_pool

    def _fetch_liked(self, count: int) -> list[dict[str, Any]]:
        if not self.liked_pool:
            # Autoplay path: a server hiccup here should stop the queue, not
            # the app. `load_liked_pool` still raises for explicit user actions.
            try:
                self.load_liked_pool()
            except NavidromeError:
                return []

        candidates = list(self.liked_pool)
        random.shuffle(candidates)

        seen_ids = {track.get("id") for track in self.history}
        seen_ids.update(track.get("id") for track in self.queue if track.get("id"))
        seen_signatures = {track_signature(track) for track in self.history}
        seen_signatures.update(track_signature(track) for track in self.queue)

        filtered = [
            track
            for track in candidates
            if track.get("id")
            and track["id"] not in seen_ids
            and track_signature(track) not in seen_signatures
        ]
        # Once every liked song has been played recently the filter empties out;
        # fall back to the full pool so shuffle keeps looping instead of stopping.
        pool = filtered or candidates
        return pool[:count]

    def build_liked_queue(self, limit: int = 10) -> list[dict[str, Any]]:
        planned = self._fetch_liked(count=limit)
        self.queue = list(planned)
        return planned

    def liked_next(self) -> dict[str, Any] | None:
        candidates = self._fetch_liked(count=30)
        if not candidates:
            return None
        next_song = random.choice(candidates)
        self.queue.append(next_song)
        return next_song

    def choose_next(
        self, candidates: list[dict[str, Any]], seed: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        if not candidates:
            return None
        ranked = sorted(
            candidates,
            key=lambda track: self._score_track(track, seed=seed),
            reverse=True,
        )
        top_window = ranked[: min(5, len(ranked))]
        if random.random() < self.exploration_rate:
            return random.choice(ranked[: min(12, len(ranked))])
        return random.choice(top_window)

    def next_track(self, seed: dict[str, Any] | None = None) -> dict[str, Any] | None:
        if seed is None:
            if not self.history:
                return None
            seed = self.history[-1]

        candidates = self.fetch_next_from_seed(seed)
        next_song = self.choose_next(candidates, seed=seed)
        if next_song:
            self.queue.append(next_song)
        return next_song

    @staticmethod
    def _artist_key(track: dict[str, Any]) -> str:
        return str(track.get("artist_name") or "").strip().lower()

    @staticmethod
    def _album_key(track: dict[str, Any]) -> str:
        return " ".join(str(track.get("album_name") or "").split()).strip().lower()

    def _score_track(
        self,
        track: dict[str, Any],
        seed: dict[str, Any] | None = None,
        upcoming: list[dict[str, Any]] | None = None,
    ) -> float:
        score = random.random() * 3.0
        duration = track.get("duration")
        if duration and 120 <= duration <= 320:
            score += 1.0

        if seed:
            seed_artist = self._artist_key(seed)
            track_artist = self._artist_key(track)
            if seed_artist and track_artist == seed_artist:
                score += 0.75

            seed_album = self._album_key(seed)
            track_album = self._album_key(track)
            if seed_album and track_album and seed_album == track_album:
                score += 0.5

        recent_history = self.history[-self.title_cooldown :]
        recent_artists = {
            self._artist_key(item)
            for item in recent_history[-self.artist_cooldown :]
            if self._artist_key(item)
        }
        recent_titles = {
            normalize_title(str(item.get("title") or ""))
            for item in recent_history
        }
        recent_albums = {
            self._album_key(item)
            for item in recent_history[-self.album_cooldown :]
            if self._album_key(item)
        }

        track_artist = self._artist_key(track)
        track_title = normalize_title(str(track.get("title") or ""))
        track_album = self._album_key(track)

        if track_artist and track_artist in recent_artists:
            score -= 2.75
        if track_title and track_title in recent_titles:
            score -= 4.0
        if track_album and track_album in recent_albums:
            score -= 1.25

        if upcoming:
            queued_artists = {
                self._artist_key(item)
                for item in upcoming[-self.artist_cooldown :]
                if self._artist_key(item)
            }
            queued_titles = {
                normalize_title(str(item.get("title") or ""))
                for item in upcoming
            }
            if track_artist and track_artist in queued_artists:
                score -= 1.75
            if track_title and track_title in queued_titles:
                score -= 5.0

            if upcoming and track_artist:
                last_planned_artist = self._artist_key(upcoming[-1])
                if last_planned_artist == track_artist:
                    score -= 1.5

        return score
