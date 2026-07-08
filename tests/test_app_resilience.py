"""End-to-end: the app survives what its dependencies throw at it.

Textual exits the app on any unhandled exception — in an action, a worker, a
timer, or a widget's render. These drive the real `Tuitify` through a headless
pilot and assert it is still alive afterwards.
"""

from __future__ import annotations

import ast
import io
import pathlib

import pytest
from PIL import Image as PILImage
from textual.geometry import Region
from textual.strip import Strip
from textual.widget import Widget

from src.tui.artwork import SafeImage, _shield
from src.tui.tui import Tuitify


SRC = pathlib.Path(__file__).resolve().parent.parent / "src"
_REAL_RENDERABLE = SafeImage._Renderable


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    """Keep the app off the user's real config, cache, and server."""
    monkeypatch.setenv("TUITIFY_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    for var in ("NAVIDROME_URL", "NAVIDROME_USERNAME", "NAVIDROME_PASSWORD"):
        monkeypatch.delenv(var, raising=False)


def _jpeg(size=(600, 600)) -> bytes:
    buffer = io.BytesIO()
    PILImage.new("RGB", size, (10, 120, 200)).save(buffer, format="JPEG")
    return buffer.getvalue()


def _truncated_jpeg() -> bytes:
    data = _jpeg()
    return data[: len(data) // 2]


# --- The reported bug -------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        # Opens fine, explodes on decode inside render(). The original crash.
        pytest.param(_truncated_jpeg(), id="truncated-image"),
        # Raises inside the `image` setter instead.
        pytest.param(b"<html>502 Bad Gateway</html>", id="error-page"),
        pytest.param(b"", id="empty"),
        pytest.param(b"\x00\x01\x02\x03" * 64, id="garbage"),
    ],
)
async def test_undecodable_artwork_stream_does_not_kill_the_app(payload):
    """Hand the widget an undecoded stream, exactly as the old code did."""
    app = Tuitify()
    async with app.run_test() as pilot:
        art = app.query_one("#album-art", SafeImage)

        art.image = io.BytesIO(payload)
        await pilot.pause()

        # Rendering is where a truncated image used to take the app down.
        assert art.render() == ""
        assert art.image is None
        assert app.is_running


async def test_nonsense_artwork_values_do_not_kill_the_app():
    app = Tuitify()
    async with app.run_test() as pilot:
        art = app.query_one("#album-art", SafeImage)

        art.image = object()
        await pilot.pause()

        assert art.render() == ""
        assert app.is_running


async def test_good_artwork_still_displays():
    app = Tuitify()
    async with app.run_test() as pilot:
        art = app.query_one("#album-art", SafeImage)

        art.image = PILImage.open(io.BytesIO(_jpeg((64, 64))))
        await pilot.pause()

        assert art.image is not None
        assert art.render() != ""
        assert app.is_running


async def test_a_renderable_that_explodes_during_rich_consumption_is_blanked():
    """`render()` only builds the renderable; Rich consumes it afterwards.

    A terminal-protocol failure surfaces there, past every earlier guard.
    """

    class _Exploding:
        def __init__(self, *args, **kwargs):
            pass

        def cleanup(self):
            pass

        def __rich_console__(self, console, options):
            raise RuntimeError("terminal protocol failure")

        def __rich_measure__(self, console, options):
            raise RuntimeError("terminal protocol failure")

    app = Tuitify()
    async with app.run_test() as pilot:
        art = app.query_one("#album-art", SafeImage)

        # Render once for real, so the next frame isn't served from the cache.
        art.image = PILImage.open(io.BytesIO(_jpeg((32, 32))))
        await pilot.pause()
        assert art.image is not None

        type(art)._Renderable = _Exploding
        try:
            art.image = PILImage.open(io.BytesIO(_jpeg((32, 32))))
            await pilot.pause()  # a real compositor pass over the broken renderable

            assert art.image is None  # the guard fired and blanked the widget
            assert art.render_lines(Region(0, 0, 20, 5)) == [Strip.blank(20)] * 5
            assert app.is_running
        finally:
            type(art)._Renderable = _REAL_RENDERABLE


def test_shield_turns_a_failing_render_into_a_blank_frame():
    """The Sixel backend decodes in a child widget we can only patch in place."""
    widget = Widget()

    def _boom(crop):
        raise OSError("sixel encoding failed")

    widget.render_lines = _boom
    _shield(widget)

    assert widget.render_lines(Region(0, 0, 8, 3)) == [Strip.blank(8)] * 3


class _FakeResponse:
    def __init__(self, body: bytes):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def raise_for_status(self):
        pass

    def iter_content(self, chunk_size):
        yield self._body


async def test_the_reported_bug_end_to_end(monkeypatch):
    """A truncated cover download: warn, blank the art, keep playing."""
    monkeypatch.setattr(
        "src.tui.artwork.requests.get",
        lambda *a, **k: _FakeResponse(_truncated_jpeg()),
    )

    app = Tuitify()
    async with app.run_test() as pilot:
        warnings = []
        app.notify = lambda message, **kw: warnings.append(message)

        app._load_artwork("http://music.test/cover")
        await app.workers.wait_for_complete()
        await pilot.pause()

        art = app.query_one("#album-art", SafeImage)
        assert art.image is None
        assert art.render() == ""
        assert warnings == ["Cover art could not be loaded."]
        assert app.is_running


async def test_repeated_artwork_failures_warn_only_once(monkeypatch):
    monkeypatch.setattr(
        "src.tui.artwork.requests.get", lambda *a, **k: _FakeResponse(b"nope")
    )

    app = Tuitify()
    async with app.run_test() as pilot:
        warnings = []
        app.notify = lambda message, **kw: warnings.append(message)

        for _ in range(3):
            app._apply_artwork(None)
        await pilot.pause()

        assert warnings == ["Cover art could not be loaded."]

        # A success resets the streak, so the next failure warns again.
        app._apply_artwork(PILImage.open(io.BytesIO(_jpeg((32, 32)))))
        app._apply_artwork(None)
        assert len(warnings) == 2


# --- Actions -----------------------------------------------------------------


async def test_a_raising_action_is_reported_not_fatal():
    app = Tuitify()
    async with app.run_test() as pilot:
        notifications = []
        app.notify = lambda message, **kw: notifications.append((message, kw))

        def _boom() -> None:
            raise RuntimeError("kaboom")

        app.action_toggle_loop = _boom
        await app.run_action("toggle_loop")
        await pilot.pause()

        assert app.is_running
        assert any("kaboom" in message for message, _ in notifications)


async def test_playback_keys_are_inert_without_a_player():
    """No server configured => no player. Every transport key must no-op."""
    app = Tuitify()
    async with app.run_test() as pilot:
        assert app.player is None

        for action in (
            "toggle_pause",
            "next_track",
            "previous_track",
            "seek_forward",
            "seek_backward",
            "volume_up",
            "volume_down",
        ):
            await app.run_action(action)

        await pilot.pause()
        assert app.is_running


async def test_progress_timer_survives_a_broken_player():
    class _BrokenPlayer:
        def __getattr__(self, name):
            def _boom(*args, **kwargs):
                raise OSError("libVLC died")

            return _boom

    app = Tuitify()
    async with app.run_test() as pilot:
        app.player = _BrokenPlayer()
        app.current_track = {"id": "1", "title": "T", "duration": 100}

        app._tick()  # what set_interval calls, 4x a second
        await pilot.pause()

        assert app.is_running


async def test_worker_failures_are_reported_not_fatal():
    app = Tuitify()
    async with app.run_test() as pilot:
        reported = []
        app._report = lambda context, error: reported.append((context, error))

        app._load_artwork("http://127.0.0.1:1/definitely-not-listening")
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()

        # A dead art endpoint is handled inside the worker, not a crash.
        assert reported == []
        assert app.is_running


# --- Toasts ------------------------------------------------------------------
#
# Only the newest toast should ever be on screen. This leans on Textual
# dispatching `_on_notify` for every class in the MRO — ours clears, then
# `App`'s adds. If an upgrade changes that, these fail rather than silently
# swallowing every toast (or stacking them again).


async def test_a_burst_of_toasts_leaves_only_the_newest():
    """A held volume key posts several toasts before any of them is added."""
    app = Tuitify()
    async with app.run_test() as pilot:
        for level in range(5):
            app.notify(f"Volume: {level}%")
        await pilot.pause()

        assert [n.message for n in app._notifications] == ["Volume: 4%"]


async def test_toasts_posted_across_separate_turns_still_replace():
    app = Tuitify()
    async with app.run_test() as pilot:
        app.notify("first")
        await pilot.pause()
        app.notify("second")
        await pilot.pause()

        assert [n.message for n in app._notifications] == ["second"]


async def test_a_single_toast_is_still_shown_exactly_once():
    """Guards both regressions: clearing too much, and adding twice."""
    app = Tuitify()
    async with app.run_test() as pilot:
        app.notify("hello")
        await pilot.pause()

        assert [n.message for n in app._notifications] == ["hello"]


async def test_toast_severity_and_title_survive_the_override():
    app = Tuitify()
    async with app.run_test() as pilot:
        app.notify("boom", title="Playback", severity="error", timeout=10)
        await pilot.pause()

        (toast,) = list(app._notifications)
        assert (toast.message, toast.title) == ("boom", "Playback")
        assert (toast.severity, toast.timeout) == ("error", 10)


# --- The invariant that makes the above hold --------------------------------


def _work_decorators():
    for path in SRC.rglob("*.py"):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            for decorator in node.decorator_list:
                if not isinstance(decorator, ast.Call):
                    continue
                name = decorator.func
                if isinstance(name, ast.Name) and name.id == "work":
                    yield path, node.name, decorator


def test_every_worker_opts_out_of_exit_on_error():
    """`exit_on_error=True` (the default) exits the app when a worker raises.

    Every `@work` must opt out so `on_worker_state_changed` can report instead.
    """
    offenders = []
    found = 0
    for path, function, decorator in _work_decorators():
        found += 1
        opted_out = any(
            keyword.arg == "exit_on_error" and keyword.value.value is False
            for keyword in decorator.keywords
        )
        if not opted_out:
            offenders.append(f"{path.name}::{function}")

    assert found > 0, "no @work decorators found — did the scan break?"
    assert offenders == [], f"workers that would exit the app on error: {offenders}"
