# Audio Flux

A dark desktop music player for searching YouTube, streaming or downloading audio, and keeping a local library with playlists, favorites, playback history, and synced lyrics.

## Features

- Search YouTube by song, artist, mood, or a pasted watch/playlist link
- Stream audio with play, pause, seek, volume, speed, shuffle, repeat, and queue
- Auto-advance to the next track when a song ends
- Synced lyrics from [lrclib.net](https://lrclib.net), with a tap-to-seek lyric panel
- Downloads, favorites, playlists, continue listening, and a Top 50 of most-played tracks
- Keyboard shortcut: `Ctrl+K` (or `Cmd+K`) focuses search

## Requirements

- Python 3.10 or newer
- [mpv](https://mpv.io/) / libmpv (used for playback)
- [FFmpeg](https://ffmpeg.org/) on your PATH for downloads with metadata and cover art

On Windows you can install the extras with:

```powershell
winget install Gyan.FFmpeg
winget install mpv
```

If FFmpeg is not on PATH, set `AUDIO_FLUX_FFMPEG` to the `ffmpeg.exe` file or its folder.

## Setup

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## Run

```powershell
python main.py
```

You can also start it with `python main-app.py` or `python -m audio_flux`.

Settings, favorites, playlists, and listening history are stored in `%APPDATA%\AudioFlux` on Windows, or `~/AudioFlux` elsewhere.

## License

MIT. See [LICENSE](LICENSE).
