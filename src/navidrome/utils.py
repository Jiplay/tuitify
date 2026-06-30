from __future__ import annotations

import re
from typing import Any


def normalize_title(title: str) -> str:
    normalized = title.lower()
    normalized = re.sub(r"\(.*?\)", "", normalized)
    normalized = re.sub(r"\[.*?\]", "", normalized)
    normalized = re.sub(r"\b(feat|ft)\.?\s+[a-z0-9 ,&]+\b", "", normalized)
    normalized = re.sub(r"[^a-z0-9 ]", "", normalized)
    return " ".join(normalized.split())


def normalize_artist_name(name: str | None) -> str:
    normalized = str(name or "").lower()
    normalized = re.sub(r"[^a-z0-9 ]", "", normalized)
    return " ".join(normalized.split())


def track_signature(track: dict[str, Any]) -> str:
    title = normalize_title(str(track.get("title") or ""))
    artist = normalize_artist_name(track.get("artist_name"))
    if artist:
        return f"{artist}::{title}"
    return title


def clean_tracks(tracks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop tracks without an id/title and deduplicate by id and signature."""
    seen_ids: set[str] = set()
    seen_signatures: set[str] = set()
    cleaned: list[dict[str, Any]] = []

    for track in tracks:
        track_id = track.get("id")
        title = str(track.get("title", ""))
        if not track_id or not title:
            continue

        signature = track_signature(track)
        if track_id in seen_ids or signature in seen_signatures:
            continue

        seen_ids.add(track_id)
        seen_signatures.add(signature)
        cleaned.append(track)

    return cleaned
