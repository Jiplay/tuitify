"""`clean_tracks` is the gate between server payloads and the playback queue."""

from __future__ import annotations

import pytest

from src.navidrome.utils import clean_tracks, normalize_title, track_signature


def _track(**overrides):
    base = {"id": "1", "title": "Song", "url": "http://stream/1", "artist_name": "A"}
    return {**base, **overrides}


@pytest.mark.parametrize(
    "track",
    [
        pytest.param({"title": "No id", "url": "u"}, id="missing-id"),
        pytest.param({"id": "1", "url": "u"}, id="missing-title"),
        pytest.param({"id": "1", "title": "", "url": "u"}, id="empty-title"),
        pytest.param({"id": "1", "title": "No url"}, id="missing-url"),
        pytest.param({"id": "1", "title": "T", "url": ""}, id="empty-url"),
        pytest.param("not a dict", id="string"),
        pytest.param(None, id="none"),
        pytest.param(42, id="int"),
    ],
)
def test_unplayable_entries_are_dropped(track):
    assert clean_tracks([track]) == []


def test_playable_entries_survive():
    assert clean_tracks([_track()]) == [_track()]


def test_junk_alongside_good_tracks_does_not_lose_the_good_ones():
    tracks = [None, _track(), "junk", _track(id="2", title="Other")]
    assert [t["id"] for t in clean_tracks(tracks)] == ["1", "2"]


def test_duplicate_ids_are_collapsed():
    assert len(clean_tracks([_track(), _track()])) == 1


def test_duplicate_signatures_are_collapsed():
    """Same song, different ids (e.g. a single and an album cut)."""
    duplicates = [_track(), _track(id="2")]
    assert len(clean_tracks(duplicates)) == 1


def test_signature_ignores_bracketed_noise():
    a = {"title": "Song (Remastered 2011)", "artist_name": "Artist"}
    b = {"title": "Song [Live]", "artist_name": "Artist"}
    assert track_signature(a) == track_signature(b)


def test_signature_tolerates_missing_fields():
    assert track_signature({}) == ""


def test_normalize_title_strips_features():
    assert normalize_title("Song feat. Someone Else") == "song"
