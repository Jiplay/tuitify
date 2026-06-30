<div align="center">
  <img src="images/logo.png" alt="Tuitify Logo" width="200"/>
</div>

<p align="center">
  <strong>Terminal-first Navidrome client with smart autoplay radio.</strong>
</p>

---

[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![uv](https://img.shields.io/badge/uv-0.10.22+-green.svg)](https://github.com/astral-sh/uv)
[![license](https://img.shields.io/badge/License-MIT-blue.svg)](https://opensource.org/licenses/MIT)

## Overview

**Tuitify** is a terminal-first music client for your [Navidrome](https://www.navidrome.org/) server. It gives you a fast, keyboard-driven way to browse and play your own music library — straight from the command line — with radio-style autoplay recommendations built in.

Connect Tuitify to your Navidrome server with just a **URL, username, and password**. Search your library, stream tracks over VLC-backed playback, and keep listening through an automatically generated queue of similar songs. Album artwork, progress tracking, next-up previews, playback controls, and keyboard shortcuts make it smooth and practical for daily use.

![UI](images/screenshot_2.png)

## Why Tuitify?

Tuitify talks to your self-hosted Navidrome server using the Subsonic API. Your music, your server, your terminal — no ads, no third-party accounts, just continuous listening from the command line.

## Features

- **Library Search**: Instantly search the songs in your Navidrome library.
- **Smart Radio Engine**: Seeds recommendations from Navidrome's similar songs (with a random-song fallback) for non-stop playback.
- **Direct Streaming**: Streams audio straight from Navidrome via `vlc` — no transcoding service required.
- **Simple Setup**: Just a server URL, username, and password.
- **Keyboard Centric**: Optimized for efficiency with customizable keybindings.
- **Album Art**: Real-time display of track artwork in your terminal.
- **Theme Support**: Cycle through various themes with persistent preferences.
- **Performance Cache**: Built-in cache layer to speed up repeated searches.

### Support Matrix

| Terminal            | TGP support | Sixel support | Works with textual-image |
|---------------------|:-----------:|:-------------:|:------------------------:|
| Black Box           |          ❌ |            ✅ |                       ✅ |
| foot                |          ❌ |            ✅ |                       ✅ |
| GNOME Terminal      |          ❌ |            ❌ |                          |
| iTerm2              |          ❌ |            ✅ |                       ✅ |
| kitty               |          ✅ |            ❌ |                       ✅ |
| konsole             |          ✅ |            ✅ |                       ✅ |
| tmux                |          ❌ |            ✅ |                       ✅ |
| Visual Studio Code  |          ❌ |            ✅ |                       ✅ |
| Warp                |          ❌ |            ❌ |                       ❌ |
| wezterm             |          ✅ |            ✅ |                       ✅ |
| Windows Console     |          ❌ |            ❌ |                          |
| Windows Terminal    |          ❌ |            ✅ |                       ✅ |
| xterm               |          ❌ |            ✅ |                       ✅ |

✅ = Supported; ❌ = Not Supported

## Getting Started

### Prerequisites
- **Python 3.12+**
- **VLC Media Player**: Ensure VLC is installed on your system as it's the core playback engine.
- **A Navidrome server**: You'll need its URL plus a username and password.


### Installation

1. Clone the repository:
   ```bash
   git clone https://github.com/Hemanth2332/tuitify.git
   cd tuitify
   ```

2. Install dependencies (using `uv` or `pip`):
   ```bash
   uv sync
   # or
   pip install .
   ```

### Running the App

Simply run the `main.py` file:
```bash
uv run ./main.py
```

### Configuration

On first launch, Tuitify shows a connection screen asking for three things:

- **Server URL** — e.g. `https://music.example.com`
- **Username**
- **Password**

Tuitify verifies the connection, then saves the settings to `~/.config/tuitify/config.json`
so you won't be asked again. Press **`Ctrl+S`** at any time to reopen the connection
screen and change servers.

You can also configure it without the UI by setting environment variables
(these override the saved config):

```bash
export NAVIDROME_URL="https://music.example.com"
export NAVIDROME_USERNAME="your-username"
export NAVIDROME_PASSWORD="your-password"
```

> Authentication uses the Subsonic token scheme (salted MD5), so your password is
> never sent in plain text over the wire.

## Controls

- `i`: Focus search input
- `Enter`: Search / Play selected track
- `Space`: Play / Pause
- `n`: Skip to next track
- `s`: Shuffle all — play random tracks from your whole library
- `p`: Toggle compact player-only view (hides search + chrome)
- `l`: Like / unlike the current track (stars it in Navidrome) ♥
- `r`: Loop — repeat the current track endlessly until you skip 🔁
- `tab`: switch between panels
- `←` / `→`: Seek backward/forward (10s)
- `+` / `-`: Volume up/down
- `Ctrl+S`: Open Navidrome connection settings
- `q`: Quit Tuitify
- `t`: cycle through themes

Feel Free to change the keybindings to your own preference in `src/tui/keybindings.py`.

# Single binary file Support:
Added support for binary file. You can download the binary from Releases

Use this command to create a single binary file from source.

For Windows:
```bash
uv run pyinstaller --onefile --add-data "src/tui/styles.tcss;src/tui" main.py
```

For Linux and Mac:
```bash
uv run pyinstaller --onefile --add-data=src/tui/styles.tcss:src/tui main.py
```

## Project Structure

- `src/tui/`: Main TUI application logic, layout, and the connection screen.
- `src/navidrome/`: Navidrome/Subsonic client, config, streaming player, and recommendation engine.
- `src/search/`: Search-specific wrapper around the Navidrome client.


## Contribution

Tuitify is built to grow, and contributions are always welcome.

If you'd like to contribute:

1. Fork the repository
2. Create a new branch (`feature/your-feature-name`)
3. Commit your changes
4. Open a Pull Request

Ideas, discussions, and improvements are always appreciated.

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
