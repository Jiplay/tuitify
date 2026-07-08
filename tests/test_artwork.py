"""Cover art is the app's most hostile input; these pin down the failure modes.

The bug that motivated this suite: a truncated download opens fine in PIL and
only raises when its pixels are touched, which happened during `render()` on
Textual's event loop and took the whole app down.
"""

from __future__ import annotations

import io

import pytest
import requests
from PIL import Image as PILImage

from src.tui.artwork import MAX_ARTWORK_BYTES, decode_artwork, fetch_artwork


def _jpeg_bytes(size: tuple[int, int] = (64, 64)) -> bytes:
    buffer = io.BytesIO()
    PILImage.new("RGB", size, (200, 30, 30)).save(buffer, format="JPEG")
    return buffer.getvalue()


def _png_bytes() -> bytes:
    buffer = io.BytesIO()
    PILImage.new("RGBA", (32, 32), (0, 0, 0, 0)).save(buffer, format="PNG")
    return buffer.getvalue()


# --- decode_artwork ---------------------------------------------------------


def test_decode_returns_a_detached_image():
    buffer = io.BytesIO()
    PILImage.new("RGB", (64, 64), (200, 30, 30)).save(buffer, format="PNG")

    image = decode_artwork(buffer.getvalue())
    assert image is not None
    assert image.size == (64, 64)
    # Fully decoded and detached from the byte stream: touching pixels must not
    # reach back into a closed file.
    assert image.getpixel((0, 0)) == (200, 30, 30)


def test_decode_preserves_transparency():
    image = decode_artwork(_png_bytes())
    assert image is not None
    assert image.mode == "RGBA"


def test_decode_rejects_truncated_image():
    """The reported crash: PIL opens this happily, then explodes on decode."""
    data = _jpeg_bytes((600, 600))
    truncated = data[: len(data) // 2]

    # Prove the naive path really does defer the failure to pixel access...
    lazy = PILImage.open(io.BytesIO(truncated))
    assert lazy.size == (600, 600)
    with pytest.raises(OSError):
        lazy.load()

    # ...and that we catch it up front instead.
    assert decode_artwork(truncated) is None


@pytest.mark.parametrize(
    "data",
    [
        pytest.param(b"", id="empty"),
        pytest.param(b"<html>502 Bad Gateway</html>", id="error-page"),
        pytest.param(b"\x00\x01\x02\x03" * 64, id="garbage"),
        pytest.param(_jpeg_bytes()[:2], id="header-only"),
    ],
)
def test_decode_rejects_non_images(data):
    assert decode_artwork(data) is None


# --- fetch_artwork ----------------------------------------------------------


class _FakeResponse:
    def __init__(self, chunks: list[bytes], error: Exception | None = None):
        self._chunks = chunks
        self._error = error

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def raise_for_status(self):
        if self._error is not None:
            raise self._error

    def iter_content(self, chunk_size):
        yield from self._chunks


def test_fetch_decodes_a_good_response(monkeypatch):
    monkeypatch.setattr(
        requests, "get", lambda *a, **k: _FakeResponse([_jpeg_bytes()])
    )
    assert fetch_artwork("http://example/cover") is not None


def test_fetch_returns_none_on_http_error(monkeypatch):
    monkeypatch.setattr(
        requests,
        "get",
        lambda *a, **k: _FakeResponse([], error=requests.HTTPError("404")),
    )
    assert fetch_artwork("http://example/cover") is None


def test_fetch_returns_none_when_connection_drops(monkeypatch):
    def _boom(*args, **kwargs):
        raise requests.ConnectionError("connection reset")

    monkeypatch.setattr(requests, "get", _boom)
    assert fetch_artwork("http://example/cover") is None


def test_fetch_returns_none_when_stream_dies_midway(monkeypatch):
    """A drop mid-download surfaces from `iter_content`, not `raise_for_status`."""

    class _DyingResponse(_FakeResponse):
        def iter_content(self, chunk_size):
            yield _jpeg_bytes()[:100]
            raise requests.ChunkedEncodingError("connection broken")

    monkeypatch.setattr(requests, "get", lambda *a, **k: _DyingResponse([]))
    assert fetch_artwork("http://example/cover") is None


def test_fetch_refuses_oversized_payloads(monkeypatch):
    """A redirect to something huge must not be buffered into memory."""
    chunk = b"\x00" * (1024 * 1024)
    oversized = [chunk] * (MAX_ARTWORK_BYTES // len(chunk) + 2)
    monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeResponse(oversized))
    assert fetch_artwork("http://example/cover") is None
