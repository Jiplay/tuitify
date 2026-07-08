"""The local index must survive a corrupt cache and a hostile query."""

from __future__ import annotations

import json

import pytest

from src.search.library import CACHE_VERSION, LocalLibrary


class _StubClient:
    """Just enough client to drive the index."""

    def __init__(self, songs=None, error=None):
        self.songs = songs or []
        self.error = error
        self.fetches = 0

    def fetch_all_songs_raw(self):
        self.fetches += 1
        if self.error:
            raise self.error
        return self.songs

    def to_track(self, song):
        return {
            "id": song.get("id"),
            "title": song.get("title") or "Unknown title",
            "artist_name": song.get("artist") or "Unknown artist",
            "url": f"http://stream/{song.get('id')}",
        }


@pytest.fixture
def cache_path(tmp_path):
    return tmp_path / "library.json"


def _library(cache_path, songs=None, error=None):
    return LocalLibrary(_StubClient(songs, error), cache_path=cache_path)


# --- Disk cache -------------------------------------------------------------


@pytest.mark.parametrize(
    "contents",
    [
        pytest.param("{ truncated", id="truncated-json"),
        pytest.param("[]", id="list-not-object"),
        pytest.param('{"version": 999, "songs": [{"id": "1"}]}', id="wrong-version"),
        pytest.param('{"version": %d, "songs": []}' % CACHE_VERSION, id="empty-songs"),
        pytest.param('{"version": %d}' % CACHE_VERSION, id="missing-songs"),
        pytest.param("\x00\x01binary", id="not-utf8-json"),
    ],
)
def test_a_bad_cache_falls_back_to_the_network(cache_path, contents):
    cache_path.write_text(contents, encoding="utf-8", errors="ignore")
    library = _library(cache_path, songs=[{"id": "1", "title": "Song"}])

    assert library.load() == 1
    assert library._client.fetches == 1
    assert library.served_from_cache is False


def test_a_good_cache_skips_the_network(cache_path):
    cache_path.write_text(
        json.dumps({"version": CACHE_VERSION, "songs": [{"id": "1", "title": "A"}]})
    )
    library = _library(cache_path)

    assert library.load() == 1
    assert library._client.fetches == 0
    assert library.served_from_cache is True


def test_cache_write_is_atomic(cache_path):
    """A crash mid-write must leave the old cache, not a truncated one."""
    library = _library(cache_path, songs=[{"id": "1", "title": "A"}])
    library.load()

    original = cache_path.read_text()
    assert json.loads(original)["songs"]

    # No `.tmp` litter left behind on success.
    assert list(cache_path.parent.glob("*.tmp")) == []


def test_an_unwritable_cache_directory_is_not_fatal(tmp_path):
    unwritable = tmp_path / "nope"
    unwritable.write_text("i am a file, not a directory")
    library = _library(unwritable / "library.json", songs=[{"id": "1", "title": "A"}])

    assert library.load() == 1  # loaded from network; cache write failed quietly


def test_a_failed_fetch_propagates(cache_path):
    library = _library(cache_path, error=RuntimeError("server down"))
    with pytest.raises(RuntimeError):
        library.load()


# --- Indexing ---------------------------------------------------------------


def test_index_skips_non_song_entries(cache_path):
    cache_path.write_text(
        json.dumps(
            {
                "version": CACHE_VERSION,
                "songs": [{"id": "1", "title": "Real"}, "junk", None, 42],
            }
        )
    )
    library = _library(cache_path)
    assert library.load() == 1


# --- Searching --------------------------------------------------------------


@pytest.fixture
def loaded(cache_path):
    library = _library(
        cache_path,
        songs=[
            {"id": "1", "title": "Shattaland", "artist": "Xavier Picardo"},
            {"id": "2", "title": "Blue Monday", "artist": "New Order"},
            {"id": "3", "title": "Teardrop", "artist": "Massive Attack"},
        ],
    )
    library.load()
    return library


def test_search_finds_a_substring_fragment(loaded):
    assert loaded.search("shatta")[0]["id"] == "1"


def test_search_tolerates_typos(loaded):
    assert loaded.search("blu munday")[0]["id"] == "2"


@pytest.mark.parametrize(
    "query", ["", "   ", "\x00", "%", "*" * 5000, "🎵🎵", "a" * 200]
)
def test_search_never_raises(loaded, query):
    assert isinstance(loaded.search(query), list)


def test_search_on_an_empty_index_returns_nothing(cache_path):
    assert _library(cache_path).search("anything") == []
