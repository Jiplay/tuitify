from __future__ import annotations

import math
import random
from collections.abc import Callable

from rich.text import Text
from textual.color import Color, Gradient
from textual.widget import Widget

# Fallback tempo when a track has no BPM metadata. 120 BPM is the single most
# common tempo in popular music, so a "blind" pulse still lands musically.
DEFAULT_BPM = 120.0

# Partial vertical blocks let the top of each bar animate at sub-cell
# resolution, so motion looks smooth instead of stepping a whole row at a time.
_PARTIAL_BLOCKS = " ▁▂▃▄▅▆▇█"

PositionProvider = Callable[[], float]
BpmProvider = Callable[[], float | None]
PaletteProvider = Callable[[], list[str]]


class BeatVisualizer(Widget):
    """A colourful EQ-style bar animation phase-locked to the track's beat.

    Motion is driven by *playback position* (not wall-clock), so the animation
    freezes when paused and stays aligned across seeks. Each frame the current
    position is folded into the beat period to get a phase in [0, 1); the start
    of every beat injects an energy pulse that decays until the next one, and
    each bar mixes that shared pulse with its own idle sway for liveliness.
    """

    DEFAULT_CSS = """
    BeatVisualizer {
        width: 100%;
        height: 100%;
        content-align: center middle;
    }
    """

    def __init__(
        self,
        position_provider: PositionProvider,
        bpm_provider: BpmProvider,
        palette_provider: PaletteProvider,
        *,
        fps: int = 30,
        id: str | None = None,
    ) -> None:
        super().__init__(id=id)
        self._position = position_provider
        self._bpm = bpm_provider
        self._palette = palette_provider
        self._fps = fps
        self._timer = None
        # Per-bar personality: a fixed phase offset and idle sway frequency so
        # the bars don't move in lockstep. Regenerated lazily to match width.
        self._offsets: list[float] = []
        self._freqs: list[float] = []
        self._n_bars = 0

    def on_mount(self) -> None:
        self._timer = self.set_interval(1 / self._fps, self.refresh, pause=True)

    def set_active(self, active: bool) -> None:
        """Run the animation only while the visualizer is actually on screen."""
        if self._timer is None:
            return
        if active:
            self._timer.resume()
        else:
            self._timer.pause()

    # --- Beat maths ----------------------------------------------------

    def _beat_energy(self) -> float:
        """Shared pulse in [0, 1]: 1.0 at each beat's onset, decaying after."""
        bpm = self._bpm() or DEFAULT_BPM
        bpm = max(40.0, min(bpm, 220.0))
        beat_period = 60.0 / bpm
        pos_seconds = max(self._position(), 0.0) / 1000.0
        phase = (pos_seconds % beat_period) / beat_period
        # Sharp attack, exponential-ish decay across the beat.
        return (1.0 - phase) ** 2.2

    def _ensure_bars(self, n_bars: int) -> None:
        if n_bars == self._n_bars and self._offsets:
            return
        self._n_bars = n_bars
        rng = random.Random(1337)  # stable across frames -> no jitter/flicker
        self._offsets = [rng.uniform(0.0, math.tau) for _ in range(n_bars)]
        self._freqs = [rng.uniform(1.6, 3.4) for _ in range(n_bars)]

    # --- Rendering -----------------------------------------------------

    def render(self) -> Text:
        width = self.size.width
        height = self.size.height
        if width <= 0 or height <= 0:
            return Text()

        # One block char + one space per bar reads cleanest across widths.
        n_bars = max(1, (width + 1) // 2)
        self._ensure_bars(n_bars)

        gradient = self._gradient(height)
        energy = self._beat_energy()
        pos_seconds = max(self._position(), 0.0) / 1000.0

        # Precompute each bar's fill level in cells (float for partial tops).
        levels: list[float] = []
        for i in range(n_bars):
            sway = 0.5 + 0.5 * math.sin(pos_seconds * self._freqs[i] + self._offsets[i])
            # Idle floor keeps bars alive between beats; the beat pulse adds punch.
            level = 0.12 + 0.30 * sway + 0.68 * energy * (0.55 + 0.45 * sway)
            levels.append(min(level, 1.0) * height)

        blocks_top = len(_PARTIAL_BLOCKS) - 1
        lines: list[Text] = []
        for row in range(height):  # 0 = top row
            cell_from_bottom = height - 1 - row
            line = Text()
            for i, filled in enumerate(levels):
                full = int(filled)
                if cell_from_bottom < full:
                    char = "█"
                elif cell_from_bottom == full:
                    frac = filled - full
                    char = _PARTIAL_BLOCKS[max(1, min(round(frac * blocks_top), blocks_top))]
                    if frac <= 0.02:
                        char = " "
                else:
                    char = " "
                line.append(char, style=gradient[cell_from_bottom])
                if i < n_bars - 1:
                    line.append(" ")
            lines.append(line)

        out = Text()
        for idx, line in enumerate(lines):
            out.append_text(line)
            if idx < len(lines) - 1:
                out.append("\n")
        return out

    def _gradient(self, height: int) -> list[str]:
        """A hex colour per vertical cell (index 0 = bottom), from the theme."""
        colors = self._palette() or ["#98C379", "#56B6C6", "#E5C07B", "#E06C75"]
        stops = [Color.parse(c) for c in colors]
        if len(stops) == 1:
            stops = stops * 2
        gradient = Gradient.from_colors(*stops)
        if height <= 1:
            return [gradient.get_color(1.0).hex]
        return [gradient.get_color(cell / (height - 1)).hex for cell in range(height)]
