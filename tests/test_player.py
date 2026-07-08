"""libVLC is a native library that can be absent, wedged, or plain lying.

Constructing the player may fail loudly (the app degrades to search-only), but
once it exists the read accessors must never raise: they run on the event loop
four times a second.
"""

from __future__ import annotations

import pytest

from src.navidrome import player as player_module
from src.navidrome.player import NavidromeStreamVLC, PlayerUnavailable, _vlc_call


class _FakeVLCPlayer:
    """A media player that misbehaves in the ways libVLC actually does."""

    def __init__(self, time=0, length=0, volume=80, playing=1, raises=False):
        self._time = time
        self._length = length
        self._volume = volume
        self._playing = playing
        self._raises = raises
        self.sought_to = None

    def _maybe_raise(self):
        if self._raises:
            raise OSError("libVLC exploded")

    def get_time(self):
        self._maybe_raise()
        return self._time

    def get_length(self):
        self._maybe_raise()
        return self._length

    def is_playing(self):
        self._maybe_raise()
        return self._playing

    def audio_get_volume(self):
        self._maybe_raise()
        return self._volume

    def audio_set_volume(self, volume):
        self._maybe_raise()
        self._volume = volume

    def set_time(self, position):
        self._maybe_raise()
        self.sought_to = position

    def stop(self):
        self._maybe_raise()

    def pause(self):
        self._maybe_raise()


def _player(**kwargs) -> NavidromeStreamVLC:
    """Build the wrapper without touching real libVLC."""
    instance = object.__new__(NavidromeStreamVLC)
    instance.player = _FakeVLCPlayer(**kwargs)
    instance._reset_position_tracking()
    return instance


# --- Construction -----------------------------------------------------------


def test_missing_libvlc_raises_player_unavailable(monkeypatch):
    monkeypatch.setattr(player_module, "vlc", None)
    with pytest.raises(PlayerUnavailable, match="not available"):
        NavidromeStreamVLC(client=None)


def test_libvlc_returning_no_instance_raises_player_unavailable(monkeypatch):
    class _NullVLC:
        @staticmethod
        def Instance(*args):
            return None  # what libVLC does when its plugin cache is broken

    monkeypatch.setattr(player_module, "vlc", _NullVLC)
    with pytest.raises(PlayerUnavailable, match="could not start"):
        NavidromeStreamVLC(client=None)


# --- _vlc_call --------------------------------------------------------------


def test_vlc_call_returns_the_value():
    assert _vlc_call(lambda: 42) == 42


def test_vlc_call_swallows_exceptions():
    def _boom():
        raise OSError("libVLC exploded")

    assert _vlc_call(_boom, default=-1) == -1


# --- Read accessors never raise ---------------------------------------------


def test_accessors_survive_a_crashed_media_player():
    player = _player(raises=True)
    assert player.current_time_ms() == 0
    assert player.total_length_ms() == 0
    assert player.get_volume() == 0


def test_mutators_survive_a_crashed_media_player():
    player = _player(raises=True)
    player.stop()
    player.toggle_pause()
    player.restart()
    player.seek_relative_ms(10_000)
    assert player.set_volume(50) == 50  # reports intent even if VLC ignores it


@pytest.mark.parametrize(
    "reported, expected",
    [(-1, 0), (0, 0), (80, 80), (100, 100), (200, 100), (None, 0)],
)
def test_volume_is_clamped_to_a_sane_range(reported, expected):
    assert _player(volume=reported).get_volume() == expected


@pytest.mark.parametrize("reported, expected", [(-1, 0), (None, 0), (5000, 5000)])
def test_length_is_never_negative(reported, expected):
    assert _player(length=reported).total_length_ms() == expected


def test_position_is_zero_before_playback_starts():
    """libVLC reports -1 until it has a media loaded."""
    assert _player(time=-1).current_time_ms() == 0


def test_position_is_clamped_to_the_track_length():
    player = _player(time=99_000, length=60_000, playing=0)
    assert player.current_time_ms() == 60_000


# --- Seeking ----------------------------------------------------------------


def test_seek_relative_clamps_to_the_start():
    player = _player(time=3_000, length=60_000, playing=0)
    player.seek_relative_ms(-10_000)
    assert player.player.sought_to == 0


def test_seek_relative_clamps_to_just_before_the_end():
    player = _player(time=59_000, length=60_000, playing=0)
    player.seek_relative_ms(10_000)
    assert player.player.sought_to == 59_750


def test_seek_relative_without_a_known_length_still_moves():
    player = _player(time=10_000, length=0, playing=0)
    player.seek_relative_ms(10_000)
    assert player.player.sought_to == 20_000


def test_restart_seeks_to_zero():
    player = _player(time=30_000, length=60_000)
    player.restart()
    assert player.player.sought_to == 0
