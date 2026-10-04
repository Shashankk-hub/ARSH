# ARSH

ARSH is a floating desktop music player built with Python and PyQt6. It lets you search for YouTube tracks, play a pasted YouTube link, control playback, and use an automatically generated radio queue.

## Requirements

- Python 3.10 or newer
- Deno (recommended) or another JavaScript runtime supported by `yt-dlp`, available on `PATH`
- Internet access for search and audio playback
- A desktop session with audio output

## Run

Clone the repository and launch it with the script for your operating system. The launcher creates a local `.venv`, installs Python dependencies, and starts the app.

Install [Deno](https://deno.com/) first so `yt-dlp` can fully extract YouTube audio. `yt-dlp` also supports Node.js, Bun, and QuickJS as JavaScript runtimes.

**Windows:** double-click `run.bat`, or run it in Command Prompt.

**macOS:** open Terminal in the project folder and run:

```bash
chmod +x run_player.sh
./run_player.sh
```

**Linux:** install Python 3, `python3-venv`, and the system libraries required by Qt6 for your distribution, then run:

```bash
chmod +x run_player.sh
./run_player.sh
```

On Debian/Ubuntu, the venv package can be installed with `sudo apt install python3-venv`. Some Linux desktop environments require a tray extension/service for the optional system tray menu.

To start manually after installing dependencies, run `python main.py` on Windows or `python3 main.py` on macOS/Linux.

## Features

- Floating, always-on-top player with compact and expanded views
- YouTube search and direct YouTube video links
- Playback controls, seeking, volume, shuffle, and repeat
- Radio recommendations and a system tray menu
- Audio downloads are kept in a temporary directory and removed when the app exits

## Development

Install dependencies with `python -m pip install -r requirements.txt` (use `python3` where appropriate). GitHub Actions installs the dependencies and checks Python syntax on Windows, macOS, and Ubuntu whenever changes are pushed or a pull request is opened.
