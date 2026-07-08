"""Fetching and safely decoding album cover art.

Cover art is the app's most hostile input: the bytes come from a server we
don't control, over a connection that can drop mid-image. PIL decodes lazily,
so a *truncated* image opens without complaint and only explodes when someone
touches its pixels — which, left alone, happens inside `Image.render()` on
Textual's event loop, taking the whole app down with it.

So the rule here is: an image is only handed to the UI once it has been fully
decoded into memory, on a worker thread, where a failure is just a `None`.
"""

from __future__ import annotations

import io

import requests
from PIL import Image as PILImage
from textual.geometry import Region
from textual.strip import Strip
from textual.widget import Widget
from textual_image.widget import Image

# Cover art is a few hundred KB. Anything wildly past that is a misconfigured
# server or a redirect to something that isn't an image; don't buffer it.
MAX_ARTWORK_BYTES = 8 * 1024 * 1024
_CHUNK_BYTES = 64 * 1024


def fetch_artwork(url: str, timeout: float = 10.0) -> PILImage.Image | None:
    """Download and fully decode cover art. Returns None on any failure."""
    try:
        with requests.get(url, timeout=timeout, stream=True) as response:
            response.raise_for_status()
            data = _read_capped(response, MAX_ARTWORK_BYTES)
    except Exception:
        return None
    return decode_artwork(data)


def decode_artwork(data: bytes) -> PILImage.Image | None:
    """Decode image bytes into a detached, in-memory image, or None.

    `load()` forces the decode here rather than at render time, and `copy()`
    detaches the result from the byte stream so nothing lazy survives.
    """
    if not data:
        return None
    try:
        with PILImage.open(io.BytesIO(data)) as image:
            image.load()
            return image.copy()
    except Exception:
        return None


def _read_capped(response: requests.Response, limit: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    for chunk in response.iter_content(_CHUNK_BYTES):
        total += len(chunk)
        if total > limit:
            raise ValueError(f"artwork exceeds {limit} bytes")
        chunks.append(chunk)
    return b"".join(chunks)


def _shield(widget: Widget) -> Widget:
    """Make one widget's `render_lines` non-fatal, in place.

    Used for the Sixel backend, which does its decoding in a private child
    widget we can't subclass from here.
    """
    original = widget.render_lines

    def safe_render_lines(crop: Region) -> list[Strip]:
        try:
            return original(crop)
        except Exception:
            return [Strip.blank(crop.width) for _ in range(crop.height)]

    widget.render_lines = safe_render_lines  # type: ignore[method-assign]
    return widget


class SafeImage(Image, Renderable=Image._Renderable):  # type: ignore[misc]
    """An `Image` that blanks itself instead of taking the app down.

    `fetch_artwork` already rejects undecodable images, so this is the second
    line of defence: it covers terminal-protocol failures and anything else
    the rendering path throws once the image is on screen.

    `textual_image.widget.Image` is chosen at import time from the terminal's
    capabilities, so the guards are spread across every hook the backends use:
    the Unicode and TGP backends decode inside `render()`, while Sixel defers
    to a child widget's `render_lines()`.
    """

    def _forget_image(self) -> None:
        """Drop the current image without going through our own setter."""
        self._renderable = None
        self._image = None
        self._image_width = 0
        self._image_height = 0

    @Image.image.setter  # type: ignore[no-redef]
    def image(self, value: object) -> None:
        try:
            Image.image.fset(self, value)
        except Exception:
            # A half-applied setter leaves stale dimensions behind, so clear
            # it out rather than render against a broken image. Going through
            # the real setter keeps the redraw/recompose bookkeeping correct.
            try:
                Image.image.fset(self, None)
            except Exception:
                self._forget_image()
                self.refresh(layout=True)

    def compose(self):
        # No-op unless the Sixel backend is active, which composes a child that
        # decodes the image in its own `render_lines`.
        for child in super().compose():
            yield _shield(child)

    def render(self):
        try:
            return super().render()
        except Exception:
            # Refreshing from inside render would re-enter; just drop the
            # image so this and every later frame comes out blank.
            self._forget_image()
            return ""

    def render_lines(self, crop: Region) -> list[Strip]:
        # `render()` only builds the renderable — Rich consumes it afterwards,
        # and that consumption can raise too. This is the outermost hook where
        # a failed frame can still be swapped for a blank one.
        try:
            return super().render_lines(crop)
        except Exception:
            self._forget_image()
            return [Strip.blank(crop.width) for _ in range(crop.height)]
