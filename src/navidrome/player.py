from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from .client import NavidromeClient

try:  # libVLC is a native library; it may be absent or unloadable.
    import vlc
except Exception as error:  # pragma: no cover - depends on the host machine
    vlc = None
    _VLC_IMPORT_ERROR: Exception | None = error
else:
    _VLC_IMPORT_ERROR = None


class PlayerUnavailable(RuntimeError):
    """libVLC is missing, or refused to start."""


def _vlc_call(method: Callable[..., Any], *args: Any, default: Any = None) -> Any:
    """Call into libVLC, treating any failure as "no answer".

    The bindings return sentinel values (-1, None) for most error states, but
    a torn-down or crashed media player can also raise. Neither should ever
    reach the UI thread: a missing playback position just means we draw the
    last known one.
    """
    try:
        return method(*args)
    except Exception:
        return default


class NavidromeStreamVLC:
    """Small VLC wrapper that plays one Navidrome track to completion.

    Constructing this raises `PlayerUnavailable` when libVLC is missing, so
    the app can start (and still search) without audio. Once constructed,
    the read accessors never raise — they fall back to a safe default.
    """

    def __init__(
        self,
        client: NavidromeClient,
        poll_interval: float = 1.0,
    ):
        self.service = client
        self.poll_interval = poll_interval

        if vlc is None:
            raise PlayerUnavailable(
                f"libVLC is not available ({_VLC_IMPORT_ERROR}). Install VLC to enable playback."
            )

        try:
            self.instance = vlc.Instance(
                "--no-video",
                "--intf=dummy",
                "--quiet",
                "--verbose=-1",
                "--no-media-library",
            )
        except Exception as error:
            raise PlayerUnavailable(f"could not start libVLC: {error}") from error

        # `vlc.Instance` returns None (rather than raising) when libVLC fails
        # to initialise, e.g. a broken plugin cache.
        if self.instance is None:
            raise PlayerUnavailable("could not start libVLC")

        self.player = self.instance.media_player_new()
        if self.player is None:
            raise PlayerUnavailable("could not create a libVLC media player")
        _vlc_call(self.player.audio_set_volume, 80)

        # Smooth-position tracking: VLC's get_time() is authoritative but
        # coarse/jumpy, so we interpolate with a wall clock between its updates.
        self._anchor_pos_ms = 0.0
        self._anchor_mono: float | None = None
        self._resync_threshold_ms = 1200

    def play_track(
        self,
        track: dict[str, Any],
        retry_on_error: bool = True,
        max_retries: int = 3,
        prefetched_stream_url: str | None = None,
        on_near_end: Callable[[], None] | None = None,
        near_end_seconds: int = 12,
    ) -> str:
        video_url = track.get("url")
        if not video_url:
            raise ValueError("Track must include a 'url'.")

        attempts = 0
        resume_position_ms = 0
        known_duration = track.get("duration")
        use_prefetched_stream = bool(prefetched_stream_url)

        while True:
            try:
                if use_prefetched_stream and prefetched_stream_url:
                    stream_url = prefetched_stream_url
                    use_prefetched_stream = False
                else:
                    stream_url, _duration = self.service.get_stream_info(video_url)
                self._start_stream(stream_url, resume_position_ms)
                return self._monitor_until_finished(
                    known_duration_seconds=known_duration,
                    on_near_end=on_near_end,
                    near_end_seconds=near_end_seconds,
                )
            except Exception:
                attempts += 1
                if not retry_on_error or attempts > max_retries:
                    raise
                resume_position_ms = max(_vlc_call(self.player.get_time, default=0) or 0, 0)
                time.sleep(1)

    def _start_stream(self, stream_url: str, resume_position_ms: int) -> None:
        media = self.instance.media_new(stream_url)
        if media is None:
            raise RuntimeError("VLC could not open the stream URL")
        self.player.set_media(media)
        self._reset_position_tracking()
        self.player.play()
        time.sleep(1)
        if resume_position_ms > 0:
            self.seek_ms(resume_position_ms)

    def _monitor_until_finished(
        self,
        known_duration_seconds: int | None = None,
        on_near_end: Callable[[], None] | None = None,
        near_end_seconds: int = 12,
    ) -> str:
        near_end_triggered = False
        while True:
            state = _vlc_call(self.player.get_state)
            if state == vlc.State.Ended:
                return "ended"
            if state == vlc.State.Stopped:
                return "stopped"
            if state == vlc.State.Error or state is None:
                raise RuntimeError("Playback error")

            if on_near_end and not near_end_triggered:
                current_time = _vlc_call(self.player.get_time, default=-1)
                duration_ms = self._resolve_duration_ms(known_duration_seconds)
                if duration_ms and current_time is not None and current_time >= 0:
                    remaining_ms = duration_ms - current_time
                    if remaining_ms <= near_end_seconds * 1000:
                        near_end_triggered = True
                        # A failing prefetch must not abort the track we're
                        # already playing successfully.
                        try:
                            on_near_end()
                        except Exception:
                            pass

            time.sleep(self.poll_interval)

    def _resolve_duration_ms(self, known_duration_seconds: int | None) -> int | None:
        if known_duration_seconds and known_duration_seconds > 0:
            return known_duration_seconds * 1000

        live_length_ms = self.total_length_ms()
        if live_length_ms > 0:
            return live_length_ms
        return None

    def stop(self) -> None:
        _vlc_call(self.player.stop)
        self._reset_position_tracking()

    def toggle_pause(self) -> None:
        _vlc_call(self.player.pause)

    def seek_ms(self, position_ms: int) -> None:
        """Jump to an absolute position. Silently ignored if VLC refuses."""
        _vlc_call(self.player.set_time, max(int(position_ms), 0))

    def seek_relative_ms(self, delta_ms: int) -> None:
        """Seek forwards/backwards, clamped inside the track."""
        target_ms = max(self.current_time_ms() + int(delta_ms), 0)
        length_ms = self.total_length_ms()
        if length_ms > 0:
            target_ms = min(target_ms, length_ms - 250)
        self.seek_ms(target_ms)

    def restart(self) -> None:
        self.seek_ms(0)

    def _reset_position_tracking(self) -> None:
        self._anchor_pos_ms = 0.0
        self._anchor_mono = None

    def current_time_ms(self) -> int:
        """Smoothly interpolated playback position in milliseconds.

        VLC reports position in coarse, uneven steps. We advance a wall-clock
        estimate from the last known anchor and only re-anchor to VLC when it
        diverges enough to be a real event (seek, pause, buffering), keeping the
        countdown gliding between VLC's sparse updates.
        """
        now = time.monotonic()
        raw = _vlc_call(self.player.get_time, default=-1)
        if raw is None or raw < 0:
            self._reset_position_tracking()
            return 0

        playing = bool(_vlc_call(self.player.is_playing, default=0))

        if self._anchor_mono is None:
            self._anchor_pos_ms = float(raw)
            self._anchor_mono = now

        elapsed_ms = (now - self._anchor_mono) * 1000.0 if playing else 0.0
        estimate = self._anchor_pos_ms + elapsed_ms

        if not playing or abs(raw - estimate) > self._resync_threshold_ms:
            self._anchor_pos_ms = float(raw)
            self._anchor_mono = now
            estimate = float(raw)

        length = self.total_length_ms()
        if length > 0:
            estimate = min(estimate, float(length))
        return int(max(estimate, 0.0))

    def total_length_ms(self) -> int:
        length = _vlc_call(self.player.get_length, default=0)
        return max(int(length or 0), 0)

    def get_volume(self) -> int:
        # libVLC reports -1 when it has no audio output yet.
        volume = _vlc_call(self.player.audio_get_volume, default=0)
        return max(0, min(int(volume or 0), 100))

    def set_volume(self, volume: int) -> int:
        volume = max(0, min(volume, 100))
        _vlc_call(self.player.audio_set_volume, volume)
        return volume
