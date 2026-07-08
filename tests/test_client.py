"""The client's contract: return usable data, or raise `NavidromeError`.

Nothing from `requests`, `json`, or a creative Subsonic implementation should
escape past this layer.
"""

from __future__ import annotations

import pytest
import requests

from src.navidrome.client import (
    MAX_LIBRARY_PAGES,
    NavidromeClient,
    NavidromeError,
    SubsonicError,
)
from src.navidrome.config import NavidromeConfig


def _client(**session_behaviour) -> NavidromeClient:
    client = NavidromeClient(
        NavidromeConfig(url="http://music.test", username="u", password="p")
    )
    client._session = _FakeSession(**session_behaviour)
    return client


class _FakeSession:
    """Stands in for `requests.Session`, replaying a scripted response."""

    def __init__(self, body=None, raises=None, http_status=None, bodies=None):
        self.body = body
        self.raises = raises
        self.http_status = http_status
        self.bodies = list(bodies) if bodies is not None else None
        self.calls = 0

    def get(self, url, timeout=None):
        self.calls += 1
        if self.raises is not None:
            raise self.raises
        body = self.bodies.pop(0) if self.bodies else self.body
        return _FakeResponse(body, self.http_status)


class _FakeResponse:
    def __init__(self, body, http_status):
        self._body = body
        self._http_status = http_status

    def raise_for_status(self):
        if self._http_status is not None:
            raise requests.HTTPError(
                f"{self._http_status}", response=_Status(self._http_status)
            )

    def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


class _Status:
    def __init__(self, status_code):
        self.status_code = status_code


def _ok(payload: dict) -> dict:
    return {"subsonic-response": {"status": "ok", **payload}}


# --- Error translation ------------------------------------------------------


@pytest.mark.parametrize(
    "raises, expected",
    [
        (requests.ConnectionError("no route"), "cannot reach server"),
        (requests.Timeout("slow"), "server timed out"),
        (requests.exceptions.InvalidJSONError("nope"), "malformed"),
    ],
)
def test_network_failures_become_navidrome_errors(raises, expected):
    client = _client(raises=raises)
    with pytest.raises(NavidromeError, match=expected):
        client.ping()


def test_http_error_reports_the_status_code():
    client = _client(body={}, http_status=503)
    with pytest.raises(NavidromeError, match="HTTP 503"):
        client.ping()


def test_non_json_body_becomes_a_navidrome_error():
    client = _client(body=ValueError("not json"))
    with pytest.raises(NavidromeError, match="malformed"):
        client.ping()


@pytest.mark.parametrize(
    "body",
    [
        pytest.param([], id="list-at-root"),
        pytest.param({"unexpected": 1}, id="missing-envelope"),
        pytest.param({"subsonic-response": "nope"}, id="envelope-not-an-object"),
        pytest.param("plain string", id="string-at-root"),
    ],
)
def test_unexpected_payload_shapes_become_navidrome_errors(body):
    client = _client(body=body)
    with pytest.raises(NavidromeError, match="unexpected response"):
        client.ping()


def test_application_error_becomes_a_subsonic_error():
    client = _client(
        body={
            "subsonic-response": {
                "status": "failed",
                "error": {"code": 40, "message": "Wrong username or password"},
            }
        }
    )
    with pytest.raises(SubsonicError, match="Wrong username or password"):
        client.ping()
    # A SubsonicError is still a NavidromeError, so one `except` covers both.
    assert issubclass(SubsonicError, NavidromeError)


def test_application_error_without_a_message_still_raises():
    client = _client(body={"subsonic-response": {"status": "failed", "error": "?"}})
    with pytest.raises(SubsonicError, match="Subsonic request failed"):
        client.ping()


# --- Payload extraction -----------------------------------------------------


def test_search_tolerates_a_missing_song_list():
    client = _client(body=_ok({"searchResult3": {}}))
    assert client.search_songs("anything") == []


def test_search_tolerates_a_missing_container():
    client = _client(body=_ok({}))
    assert client.search_songs("anything") == []


def test_search_accepts_a_single_song_returned_as_an_object():
    """Some Subsonic servers collapse a one-element list into a bare object."""
    client = _client(
        body=_ok({"searchResult3": {"song": {"id": "1", "title": "Solo"}}})
    )
    results = client.search_songs("solo")
    assert [track["title"] for track in results] == ["Solo"]


def test_search_skips_non_object_entries():
    client = _client(
        body=_ok({"searchResult3": {"song": [{"id": "1", "title": "Real"}, "junk", None]}})
    )
    assert len(client.search_songs("x")) == 1


# --- Recommendations degrade quietly ----------------------------------------


def test_random_songs_returns_empty_when_the_server_is_down():
    client = _client(raises=requests.ConnectionError("down"))
    assert client.get_random_songs() == []


def test_similar_songs_returns_empty_when_the_server_is_down():
    client = _client(raises=requests.ConnectionError("down"))
    assert client.get_similar_songs({"id": "1", "artist_id": "a"}) == []


def test_similar_songs_falls_back_to_the_second_endpoint():
    client = _client(
        bodies=[
            _ok({"similarSongs2": {}}),  # artist lookup finds nothing
            _ok({"similarSongs": {"song": [{"id": "9", "title": "Next"}]}}),
        ]
    )
    results = client.get_similar_songs({"id": "1", "artist_id": "a"})
    assert [track["id"] for track in results] == ["9"]


# --- Track mapping ----------------------------------------------------------


def test_to_track_survives_a_junk_payload():
    client = _client()
    track = client.to_track({"id": "1", "duration": "not-a-number", "bpm": None})
    assert track["duration"] is None
    assert track["bpm"] is None
    assert track["total_play_time"] == "00:00"
    assert track["title"] == "Unknown title"
    assert track["artist_name"] == "Unknown artist"


def test_to_track_coerces_string_numbers():
    client = _client()
    track = client.to_track({"id": "1", "duration": "185", "bpm": "128"})
    assert track["duration"] == 185
    assert track["bpm"] == 128
    assert track["total_play_time"] == "03:05"


def test_to_track_without_an_id_has_no_stream_url():
    """`clean_tracks` drops these; building a URL for them must not raise."""
    client = _client()
    assert client.to_track({"title": "Orphan"})["url"] == ""


# --- Pagination -------------------------------------------------------------


def test_fetch_all_songs_stops_on_a_short_page():
    page = [{"id": str(i)} for i in range(3)]
    client = _client(bodies=[_ok({"searchResult3": {"song": page}})])
    assert len(client.fetch_all_songs_raw(page_size=500)) == 3


def test_fetch_all_songs_cannot_loop_forever():
    """A server that always returns a full page must not hang the sync."""
    full_page = _ok({"searchResult3": {"song": [{"id": "x"}] * 2}})
    session = _FakeSession(body=full_page)
    client = _client()
    client._session = session

    songs = client.fetch_all_songs_raw(page_size=2)

    assert session.calls == MAX_LIBRARY_PAGES
    assert len(songs) == MAX_LIBRARY_PAGES * 2
