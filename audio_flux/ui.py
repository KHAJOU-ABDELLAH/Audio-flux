import asyncio
import json
import os
import random
import re
from bisect import bisect_right
from datetime import datetime, timedelta, timezone
from pathlib import Path

import flet as ft

from .backend import (
    adjust_current_time,
    adjust_track_speed,
    audio_player,
    close_audio,
    current_time,
    download_audio,
    get_audio_duration,
    get_current_volume,
    get_current_track_speed,
    get_track_lyrics,
    pause_audio,
    play_audio,
    resume_audio,
    scan_download_folder,
    search_youtube,
    set_repeat_mode,
    volume_down,
    volume_up,
)


BG = "#0B0D12"
PANEL = "#141821"
PANEL_SOFT = "#1B202B"
ACCENT = "#B7F36B"
MUTED = "#9299A8"


def _format_time(seconds):
    if seconds is None or seconds < 0:
        return "0:00"
    seconds = int(seconds)
    return f"{seconds // 60}:{seconds % 60:02d}"


def _parse_lrc(lyrics):
    timestamp_pattern = re.compile(r"\[(\d{1,3}):(\d{2})(?:\.(\d{1,3}))?\]")
    parsed = []
    for raw_line in (lyrics or "").splitlines():
        timestamps = timestamp_pattern.findall(raw_line)
        if not timestamps:
            continue
        text = timestamp_pattern.sub("", raw_line).strip()
        if not text:
            continue
        for minutes, seconds, fraction in timestamps:
            fractional_seconds = float(f"0.{fraction}") if fraction else 0.0
            parsed.append((int(minutes) * 60 + int(seconds) + fractional_seconds, text))
    return sorted(parsed, key=lambda item: item[0])


def main(page: ft.Page):
    page.title = "Audio Flux"
    page.theme_mode = ft.ThemeMode.DARK
    page.bgcolor = BG
    page.padding = 20
    page.window_min_width = 760
    page.window_min_height = 720
    page.theme = ft.Theme(color_scheme_seed=ACCENT)

    current_source = None
    selected_track = None
    is_paused = False
    player_expanded = False
    last_nonzero_volume = 50
    duration_seconds = 0.0
    current_view = "home"
    library_section = "all"
    current_search_query = ""
    search_fetch_limit = 24
    search_generation = 0
    loaded_search_tracks = []
    quick_pick_tracks = []
    search_view_mode = "list"
    quick_view_mode = "list"
    favorites_view_mode = "list"
    playback_queue = []
    downloaded_tracks = []
    repeat_enabled = False
    hero_dismissed = False
    settings_file = Path(os.environ.get("APPDATA") or Path.home()) / "AudioFlux" / "settings.json"
    favorites_file = settings_file.parent / "favorites.json"
    history_file = settings_file.parent / "listening_history.json"
    playlists_file = settings_file.parent / "playlists.json"
    try:
        saved_settings = json.loads(settings_file.read_text(encoding="utf-8"))
        download_directory = str(saved_settings.get("download_directory", "")).strip()
    except (OSError, json.JSONDecodeError, AttributeError):
        download_directory = ""
    try:
        favorites = json.loads(favorites_file.read_text(encoding="utf-8"))
        if not isinstance(favorites, list):
            favorites = []
    except (OSError, json.JSONDecodeError):
        favorites = []
    try:
        listening_history = json.loads(history_file.read_text(encoding="utf-8"))
        if not isinstance(listening_history, dict):
            listening_history = {}
    except (OSError, json.JSONDecodeError):
        listening_history = {}
    hero_dismissed = bool(listening_history or favorites)
    try:
        playlists = json.loads(playlists_file.read_text(encoding="utf-8"))
        if not isinstance(playlists, list):
            playlists = []
    except (OSError, json.JSONDecodeError):
        playlists = []
    current_history_key = None
    last_poll_position = None
    last_history_save = 0.0
    current_lyrics_artist = ""
    pending_playlist_track = None
    pending_delete_track = None
    pending_delete_context = None
    lyrics_generation = 0
    lyrics_entries = []
    lyric_timestamps = []
    current_lyric_index = -2

    heading = ft.Text("Find your next favorite.", size=30, weight=ft.FontWeight.W_700, color="#F4F6FA")
    subtitle = ft.Text("Search YouTube and play it straight from your library.", size=14, color=MUTED)
    status = ft.Text("READY · NOTHING PLAYING", size=11, color=ACCENT, weight=ft.FontWeight.W_700)
    track_title = ft.Text("Nothing playing yet", size=21, weight=ft.FontWeight.W_600, color="#F4F6FA", max_lines=1, overflow=ft.TextOverflow.ELLIPSIS)
    artist = ft.Text("Search for a track to get started", size=13, color=MUTED, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS)
    lyrics_status = ft.Text("Lyrics appear here when available.", size=12, color=MUTED)
    lyrics_list = ft.ListView(spacing=12, padding=ft.Padding.symmetric(horizontal=18, vertical=10), height=330, scroll=ft.ScrollMode.AUTO)
    lyrics_button = ft.IconButton(
        icon=ft.Icons.LYRICS_ROUNDED, icon_color=MUTED, tooltip="Show synced lyrics",
        visible=False, on_click=lambda e: toggle_lyrics(),
    )
    lyrics_panel = ft.Container(
        visible=False,
        content=ft.Column([
            ft.Row([
                ft.Column([
                    ft.Text("LYRICS", size=10, color=ACCENT, weight=ft.FontWeight.W_700),
                    lyrics_status,
                ], spacing=3, expand=True),
                ft.IconButton(icon=ft.Icons.REFRESH_ROUNDED, icon_color=MUTED, tooltip="Retry lyrics lookup", on_click=lambda e: retry_lyrics()),
                ft.IconButton(icon=ft.Icons.CLOSE_ROUNDED, icon_color=MUTED, tooltip="Hide lyrics", on_click=lambda e: toggle_lyrics()),
            ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
            lyrics_list,
        ], spacing=8),
        padding=16, bgcolor=PANEL, border_radius=18, border=ft.Border.all(1, "#252B36"),
        width=340,
    )
    cover = ft.Container(
        width=220, height=220, border_radius=18, bgcolor="#242B37",
        alignment=ft.Alignment.CENTER,
        content=ft.Column(
            [ft.Icon(ft.Icons.GRAPHIC_EQ, size=62, color=ACCENT), ft.Text("AUDIO FLUX", size=10, color=MUTED, weight=ft.FontWeight.W_700)],
            alignment=ft.MainAxisAlignment.CENTER, horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=12,
        ),
    )

    search_input = ft.TextField(
        label=None, hint_text="Search songs, artists, albums  ·  Ctrl + K",
        prefix_icon=ft.Icons.SEARCH, expand=True, border_radius=12, filled=True, bgcolor=PANEL_SOFT,
        border_color="#2A303C", focused_border_color=ACCENT, on_submit=lambda e: start_search(),
    )
    download_folder = ft.TextField(
        label="Download folder", hint_text="Leave blank to use the app's current folder",
        prefix_icon=ft.Icons.FOLDER_OUTLINED, expand=True, border_radius=12, filled=True, bgcolor=PANEL_SOFT,
        border_color="#2A303C", focused_border_color=ACCENT,
    )
    playlist_name_input = ft.TextField(
        label="Playlist name", hint_text="Give your playlist a name", width=360,
        filled=True, bgcolor=PANEL_SOFT, border_color="#2A303C", focused_border_color=ACCENT,
    )
    playlist_selection = ft.Dropdown(
        label="Choose a playlist", options=[], width=360, filled=True,
        fill_color=PANEL_SOFT, border_color="#2A303C", focused_border_color=ACCENT,
    )
    delete_confirmation_text = ft.Text("", size=13, color=MUTED)
    elapsed = ft.Text("0:00", size=11, color=MUTED)
    total = ft.Text("0:00", size=11, color=MUTED)
    seek_slider = ft.Slider(
        min=0, max=100, value=0, active_color=ACCENT, inactive_color="#343B47",
        thumb_color=ACCENT, overlay_color="#34513B", height=24,
        on_change_end=lambda e: on_seek(e),
    )
    seek_slider_container = ft.Container(
        content=seek_slider, height=24, animate=120,
        on_hover=lambda e: on_seek_hover(e),
    )
    volume_value = ft.Text("—", size=12, color=MUTED, width=36, text_align=ft.TextAlign.RIGHT)
    volume_slider = ft.Slider(min=0, max=100, value=50, active_color=ACCENT, inactive_color="#343B47", width=126, on_change=lambda e: on_volume_change(e))
    compact_seek_slider = ft.Slider(
        min=0, max=100, value=0, active_color=ACCENT, inactive_color="#343B47",
        thumb_color=ACCENT, height=20, on_change_end=lambda e: adjust_current_time(float(e.control.value)),
    )
    compact_cover = ft.Container(width=44, height=44, bgcolor="#242B37", border_radius=8, alignment=ft.Alignment.CENTER, content=ft.Icon(ft.Icons.MUSIC_NOTE_ROUNDED, color=ACCENT, size=22))
    compact_title = ft.Text("Nothing playing", size=12, weight=ft.FontWeight.W_600, color="#F4F6FA", max_lines=1, overflow=ft.TextOverflow.ELLIPSIS)
    compact_artist = ft.Text("Choose a track to begin", size=10, color=MUTED, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS)
    compact_play_button = ft.IconButton(icon=ft.Icons.PLAY_ARROW_ROUNDED, icon_color=BG, bgcolor=ACCENT, tooltip="Play / pause", on_click=lambda e: toggle_pause(e))
    compact_favorite_button = ft.IconButton(icon=ft.Icons.FAVORITE_BORDER_ROUNDED, icon_color=MUTED, tooltip="Add to favorites", on_click=lambda e: toggle_favorite(selected_track) if selected_track else None)
    volume_mute_button = ft.IconButton(icon=ft.Icons.VOLUME_UP_ROUNDED, icon_color=MUTED, tooltip="Mute", on_click=lambda e: toggle_mute(e))
    queue_list = ft.Column(spacing=8, scroll=ft.ScrollMode.AUTO, height=380)
    speed_options = ft.SegmentedButton(
        segments=[
            ft.Segment(value="1", label="1×"),
            ft.Segment(value="1.5", label="1.5×"),
            ft.Segment(value="2", label="2×"),
        ],
        selected=["1"],
        allow_empty_selection=False,
        show_selected_icon=False,
        on_change=lambda e: on_speed_change(e),
    )
    shuffle_button = ft.IconButton(
        icon=ft.Icons.SHUFFLE_ROUNDED, icon_color=MUTED, tooltip="Play a random track",
        on_click=lambda e: shuffle_play(),
    )
    repeat_button = ft.IconButton(
        icon=ft.Icons.REPEAT_ROUNDED, icon_color=MUTED, tooltip="Repeat off",
        on_click=lambda e: toggle_repeat(),
    )
    play_button = ft.IconButton(icon=ft.Icons.PLAY_ARROW_ROUNDED, icon_size=34, icon_color=BG, bgcolor=ACCENT, tooltip="Play / pause", on_click=lambda e: toggle_pause(e))
    pause_label = ft.Text("PLAY", size=10, color=MUTED, weight=ft.FontWeight.W_700)
    search_progress = ft.ProgressRing(width=18, height=18, stroke_width=2, color=ACCENT, visible=False)
    results_summary = ft.Text("Search to see matching tracks", size=12, color=MUTED)
    results_list = ft.Container()
    search_view_toggle = ft.SegmentedButton(
        segments=[ft.Segment(value="list", icon=ft.Icons.VIEW_LIST_ROUNDED), ft.Segment(value="grid", icon=ft.Icons.GRID_VIEW_ROUNDED)],
        selected=["list"], allow_empty_selection=False, show_selected_icon=False,
        on_change=lambda e: set_search_view(e.control.selected[0]),
        tooltip="Change result layout",
    )
    load_more_button = ft.OutlinedButton(
        "Load more tracks", icon=ft.Icons.EXPAND_MORE_ROUNDED, visible=False,
        on_click=lambda e: load_more_results(),
    )
    results_panel = ft.Container(
        visible=False,
        content=ft.Column([
            ft.Row([
                ft.Column([ft.Text("Results", size=16, weight=ft.FontWeight.W_600, color="#F4F6FA"), results_summary], spacing=3),
                search_view_toggle,
            ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
            results_list,
            ft.Row([load_more_button], alignment=ft.MainAxisAlignment.CENTER),
        ], spacing=12),
        padding=18, bgcolor=PANEL, border_radius=18, border=ft.Border.all(1, "#252B36"),
    )

    def set_status(message, color=ACCENT):
        status.value = message.upper()
        status.color = color
        page.update()

    def toggle_lyrics():
        if not lyrics_button.visible:
            return
        lyrics_panel.visible = not lyrics_panel.visible
        lyrics_button.icon_color = ACCENT if lyrics_panel.visible else MUTED
        page.update()

    def scroll_to_lyric(index):
        async def scroll():
            await lyrics_list.scroll_to(scroll_key=f"lyric-{index}", duration=400)
        page.run_task(scroll)

    def render_lyrics():
        if not lyrics_entries:
            lyrics_list.controls = [ft.Text(lyrics_status.value, size=13, color=MUTED, text_align=ft.TextAlign.CENTER)]
            return
        controls = []
        for index, (timestamp, text) in enumerate(lyrics_entries):
            controls.append(ft.Container(
                content=ft.Text(
                    text,
                    size=18 if index == current_lyric_index else 15,
                    color=ACCENT if index == current_lyric_index else "#89919F",
                    weight=ft.FontWeight.W_600 if index == current_lyric_index else ft.FontWeight.W_400,
                    text_align=ft.TextAlign.CENTER,
                ),
                key=f"lyric-{index}",
                on_click=(lambda e, position=timestamp: adjust_current_time(position)) if timestamp is not None else None,
                alignment=ft.Alignment.CENTER,
                padding=6,
            ))
        lyrics_list.controls = controls

    def update_lyrics(position):
        nonlocal current_lyric_index
        if not lyric_timestamps:
            return
        index = bisect_right(lyric_timestamps, position) - 1
        if index == current_lyric_index:
            return
        current_lyric_index = index
        for line_index, control in enumerate(lyrics_list.controls):
            text_control = control.content
            text_control.size = 18 if line_index == index else 15
            text_control.color = ACCENT if line_index == index else "#89919F"
            text_control.weight = ft.FontWeight.W_600 if line_index == index else ft.FontWeight.W_400
        if index >= 0:
            scroll_to_lyric(index)

    def lyrics_worker(title, artist_name, duration, request_id):
        nonlocal lyrics_entries, lyric_timestamps, current_lyric_index
        try:
            record = get_track_lyrics(title, artist_name, duration)
            if request_id != lyrics_generation:
                return
            if record and record.get("syncedLyrics"):
                lyrics_entries = _parse_lrc(record["syncedLyrics"])
                lyric_timestamps = [entry[0] for entry in lyrics_entries]
                lyrics_status.value = "Synced lyrics · tap a line to seek"
            elif record and record.get("plainLyrics"):
                lyrics_entries = [(None, line.strip()) for line in record["plainLyrics"].splitlines() if line.strip()]
                lyric_timestamps = []
                lyrics_status.value = "Lyrics found · timing unavailable"
            else:
                lyrics_entries = []
                lyric_timestamps = []
                lyrics_status.value = "Synced lyrics aren’t available for this track yet."
            current_lyric_index = -2
            render_lyrics()
        except Exception as exc:
            if request_id != lyrics_generation:
                return
            lyrics_entries = []
            lyric_timestamps = []
            error = str(exc).lower()
            if 'temporarily unavailable' in error or 'http 5' in error:
                lyrics_status.value = 'Lyrics service is temporarily unavailable. Tap refresh to try again.'
            elif 'rate limit' in error:
                lyrics_status.value = 'Lyrics lookup is rate limited. Wait a moment, then tap refresh.'
            elif 'urlopen' in error or 'timed out' in error or 'connection' in error:
                lyrics_status.value = "Can't reach the lyrics service. Check your connection and retry."
            else:
                lyrics_status.value = "Lyrics couldn't load. Tap refresh to try again."
            render_lyrics()
        finally:
            if request_id == lyrics_generation:
                page.update()

    def load_lyrics(track, artist_name):
        nonlocal lyrics_generation, lyrics_entries, lyric_timestamps, current_lyric_index
        lyrics_generation += 1
        request_id = lyrics_generation
        lyrics_entries = []
        lyric_timestamps = []
        current_lyric_index = -2
        lyrics_panel.visible = False
        lyrics_button.visible = True
        lyrics_button.icon_color = MUTED
        lyrics_status.value = "Looking for synced lyrics…"
        lyrics_list.controls = [ft.Text(lyrics_status.value, size=13, color=MUTED, text_align=ft.TextAlign.CENTER)]
        page.run_thread(
            lyrics_worker,
            track.get("title") or "",
            artist_name or track.get("artist") or "",
            track.get("duration"),
            request_id,
        )

    def retry_lyrics():
        if selected_track is not None:
            load_lyrics(selected_track, current_lyrics_artist)

    def save_listening_history():
        try:
            history_file.parent.mkdir(parents=True, exist_ok=True)
            history_file.write_text(json.dumps(listening_history, indent=2), encoding="utf-8")
        except OSError as exc:
            print(f"Couldn't save listening history: {exc}")

    def record_track_started(track):
        nonlocal current_history_key, last_poll_position
        key = track.get("id") or track.get("url")
        if not key:
            return
        current_history_key = str(key)
        item = listening_history.setdefault(current_history_key, {
            name: track.get(name)
            for name in ("id", "title", "artist", "thumbnail", "duration", "url", "mode")
            if track.get(name) is not None
        })
        for name in ("id", "title", "artist", "thumbnail", "duration", "url", "mode"):
            if track.get(name) is not None:
                item[name] = track[name]
        item["last_played"] = datetime.now(timezone.utc).isoformat()
        last_poll_position = None
        save_listening_history()

    def update_track(track_info, track):
        nonlocal current_source, duration_seconds, is_paused, selected_track, current_lyrics_artist
        if not track_info or not track_info.get("audio_source"):
            set_status("Could not load this audio", "#FF8A80")
            return
        current_source = track_info["audio_source"]
        selected_track = track
        record_track_started(track)
        refresh_home_sections()
        duration_seconds = 0.0
        is_paused = False
        landing_panel.visible = False
        player_card.visible = player_expanded
        compact_player.visible = True
        track_title.value = track.get("title") or "Audio stream"
        compact_title.value = track.get("title") or "Audio stream"
        artist_name = track_info.get("artist") or track.get("artist") or "Unknown artist"
        current_lyrics_artist = artist_name
        artist.value = f"By {artist_name}"
        compact_artist.value = artist_name
        compact_favorite_button.icon = ft.Icons.FAVORITE_ROUNDED if is_favorite(track) else ft.Icons.FAVORITE_BORDER_ROUNDED
        compact_favorite_button.icon_color = "#F0788A" if is_favorite(track) else MUTED
        load_lyrics(track, artist_name)
        image_path = track_info.get("cover") or track.get("thumbnail")
        if image_path and image_path != "placeholder.png":
            cover.content = ft.Image(src=image_path, width=220, height=220, fit=ft.BoxFit.COVER, border_radius=18)
            compact_cover.content = ft.Image(src=image_path, width=44, height=44, fit=ft.BoxFit.COVER, border_radius=8)
            player_card.image = ft.DecorationImage(src=image_path, fit=ft.BoxFit.COVER, opacity=0.13)
            palette = ("#344D38", "#39405A", "#573B47", "#34505B", "#514831", "#4D3B59")
            glow = palette[sum(ord(char) for char in str(track.get("id") or track.get("title"))) % len(palette)]
            player_card.shadow = ft.BoxShadow(blur_radius=30, spread_radius=2, color=glow)
        else:
            cover.content = ft.Icon(ft.Icons.GRAPHIC_EQ, size=62, color=ACCENT)
            compact_cover.content = ft.Icon(ft.Icons.MUSIC_NOTE_ROUNDED, color=ACCENT, size=22)
            player_card.image = None
        status.value = "NOW PLAYING · DOWNLOADS" if track.get("mode") == "offline" else "NOW PLAYING · YOUTUBE"
        status.color = ACCENT
        play_button.icon = ft.Icons.PAUSE_ROUNDED
        compact_play_button.icon = ft.Icons.PAUSE_ROUNDED
        pause_label.value = "PAUSE"
        page.update()
        play_audio(current_source)
        resume_position = float(track.get("_resume_position") or 0)
        if resume_position > 5:
            page.run_task(seek_after_start, resume_position)
        page.run_task(poll_playback)

    async def seek_after_start(position):
        await asyncio.sleep(1)
        if current_source is not None:
            adjust_current_time(position)

    def playback_worker(track):
        set_status(f"Loading {track.get('title') or 'track'}…", ACCENT)
        try:
            track_info = audio_player(track.get("mode", "online"), track["url"])
            update_track(track_info, track)
        except Exception as exc:
            set_status(f"Unable to play audio: {exc}", "#FF8A80")

    def play_track(track, e=None):
        if not track or not track.get("url"):
            set_status("Choose a track from the search results", "#FFCC80")
            return
        set_status("Connecting…", ACCENT)
        page.run_thread(playback_worker, track)

    def play_local_file(path):
        track = {
            "title": Path(path).stem,
            "artist": "Downloaded audio",
            "url": path,
            "mode": "offline",
        }
        set_status(f"Loading {track['title']}…", ACCENT)
        page.run_thread(playback_worker, track)

    def candidate_tracks():
        if current_view == "library" and library_section == "downloads":
            return downloaded_tracks
        if current_view == "library" and library_section == "favorites":
            return favorites
        if current_view == "library" and library_section == "playlists":
            return all_library_tracks()
        return loaded_search_tracks or quick_pick_tracks or all_library_tracks()

    def next_track(e=None):
        if playback_queue:
            track = playback_queue.pop(0)
            refresh_queue()
            play_track(track)
            return
        candidates = candidate_tracks()
        if not candidates:
            set_status("No next track is available", "#FFCC80")
            return
        current_key = track_key(selected_track or {})
        start_index = next((i for i, item in enumerate(candidates) if track_key(item) == current_key), -1)
        play_track(candidates[(start_index + 1) % len(candidates)])

    def previous_track(e=None):
        position = current_time()
        if position is not None and float(position) > 3:
            adjust_current_time(0)
            return
        recent = sorted(listening_history.values(), key=lambda item: item.get("last_played", ""), reverse=True)
        current_key = track_key(selected_track or {})
        previous = next((item for item in recent if item.get("url") and track_key(item) != current_key), None)
        if previous:
            play_track(previous)
        else:
            set_status("No previous track is available", "#FFCC80")

    def toggle_expanded_player(e=None):
        nonlocal player_expanded
        if current_source is None:
            return
        player_expanded = not player_expanded
        player_card.visible = player_expanded
        compact_player.visible = not player_expanded
        content_region.visible = not player_expanded
        player_card.expand = player_expanded
        page.update()

    def set_volume(value):
        nonlocal last_nonzero_volume
        value = max(0, min(100, int(value)))
        volume_slider.value = value
        try:
            from .backend import player
            player.volume = value
        except Exception:
            pass
        if value > 0:
            last_nonzero_volume = value
        volume_mute_button.icon = ft.Icons.VOLUME_OFF_ROUNDED if value == 0 else ft.Icons.VOLUME_UP_ROUNDED
        page.update()

    def toggle_mute(e=None):
        nonlocal last_nonzero_volume
        if int(volume_slider.value or 0) > 0:
            last_nonzero_volume = int(volume_slider.value)
            set_volume(0)
        else:
            set_volume(last_nonzero_volume or 50)

    def refresh_queue():
        if not playback_queue:
            queue_list.controls = [ft.Text("Your queue is empty. Add tracks from a menu.", size=12, color=MUTED)]
        else:
            queue_list.controls = [
                ft.Container(
                    content=ft.Row([
                        ft.Column([
                            ft.Text(item.get("title") or "Untitled", size=13, color="#F4F6FA", max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                            ft.Text(item.get("artist") or "Unknown artist", size=10, color=MUTED, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                        ], spacing=3, expand=True),
                        ft.IconButton(icon=ft.Icons.CLOSE_ROUNDED, icon_color=MUTED, tooltip="Remove from queue", on_click=lambda e, target=item: remove_from_queue(target)),
                    ]), padding=8, bgcolor=PANEL_SOFT, border_radius=10,
                ) for item in playback_queue
            ]

    def remove_from_queue(track):
        identity = track_key(track)
        index = next((i for i, queued in enumerate(playback_queue) if track_key(queued) == identity), None)
        if index is not None:
            playback_queue.pop(index)
            refresh_queue()
            page.update()

    def open_queue(e=None):
        refresh_queue()
        page.show_dialog(queue_dialog)

    def shuffle_play():
        if playback_queue:
            candidates = playback_queue
        elif current_view == "library" and library_section == "downloads":
            candidates = downloaded_tracks
        elif current_view == "library" and library_section == "favorites":
            candidates = favorites
        elif current_view == "library" and library_section == "playlists":
            candidates = all_library_tracks()
        else:
            candidates = loaded_search_tracks or quick_pick_tracks
        if current_source and len(candidates) > 1:
            candidates = [track for track in candidates if track.get("url") != selected_track.get("url")]
        if not candidates:
            set_status("Load some tracks first, then shuffle play", "#FFCC80")
            return
        play_track(random.choice(candidates))

    def toggle_repeat():
        nonlocal repeat_enabled
        repeat_enabled = not repeat_enabled
        set_repeat_mode(repeat_enabled)
        repeat_button.icon_color = ACCENT if repeat_enabled else MUTED
        repeat_button.tooltip = "Repeat on" if repeat_enabled else "Repeat off"
        page.update()

    def track_key(track):
        return track.get("id") or track.get("url")

    def is_favorite(track):
        key = track_key(track)
        return any(track_key(saved) == key for saved in favorites)

    def save_favorites():
        try:
            favorites_file.parent.mkdir(parents=True, exist_ok=True)
            favorites_file.write_text(json.dumps(favorites, indent=2), encoding="utf-8")
        except OSError as exc:
            set_status(f"Couldn't save favorites: {exc}", "#FF8A80")
            return False
        return True

    def track_identities(track):
        return {str(track[name]) for name in ("id", "url") if track.get(name)}

    def toggle_favorite(track, e=None):
        nonlocal favorites
        key = track_key(track)
        if is_favorite(track):
            favorites = [saved for saved in favorites if track_key(saved) != key]
        else:
            favorites.append({
                name: track.get(name)
                for name in ("id", "title", "artist", "thumbnail", "duration", "url", "mode")
                if track.get(name) is not None
            })
            favorites[-1]["added_at"] = datetime.now(timezone.utc).isoformat()
        if not save_favorites():
            return
        if selected_track and track_key(selected_track) == key:
            compact_favorite_button.icon = ft.Icons.FAVORITE_ROUNDED if is_favorite(selected_track) else ft.Icons.FAVORITE_BORDER_ROUNDED
            compact_favorite_button.icon_color = "#F0788A" if is_favorite(selected_track) else MUTED
            compact_favorite_button.tooltip = "Remove favorite" if is_favorite(selected_track) else "Add to favorites"
        if favorites_list is not None:
            refresh_favorites()
        if results_panel.visible:
            results_list.content = render_track_collection(loaded_search_tracks, search_view_mode)
        if quick_pick_tracks:
            quick_pick_list.content = render_track_collection(quick_pick_tracks, quick_view_mode)
        if current_view == "library":
            refresh_library()
        refresh_home_sections()
        page.update()

    def add_to_queue(track):
        playback_queue.append(track)
        refresh_queue()
        set_status(f"Added to queue · {track.get('title') or 'Track'}", ACCENT)

    def save_playlists():
        try:
            playlists_file.parent.mkdir(parents=True, exist_ok=True)
            playlists_file.write_text(json.dumps(playlists, indent=2), encoding="utf-8")
        except OSError as exc:
            set_status(f"Couldn't save playlists: {exc}", "#FF8A80")
            return False
        return True

    def request_delete_track(track, context):
        nonlocal pending_delete_track, pending_delete_context
        pending_delete_track = dict(track)
        pending_delete_context = context
        title = track.get("title") or Path(track.get("url", "Track")).stem or "this track"
        section_names = {
            "all": "All Music and its saved sources",
            "downloads": "Downloads on this device",
            "favorites": "Favorites",
            "top50": "listening history and Top 50",
        }
        if context.startswith("playlist:"):
            playlist = next((item for item in playlists if item.get("id") == context.split(":", 1)[1]), None)
            destination = f"the playlist ‘{playlist.get('name', 'Playlist')}’" if playlist else "this playlist"
        else:
            destination = section_names.get(context, "this library section")
        delete_confirmation_text.value = f"Remove ‘{title}’ from {destination}?"
        if context in ("downloads", "all") and track.get("mode") == "offline":
            delete_confirmation_text.value += " Its downloaded file will also be deleted."
        page.show_dialog(delete_track_dialog)

    def confirm_delete_track(e=None):
        nonlocal favorites, current_history_key, last_poll_position, current_source, selected_track, is_paused, pending_delete_track, pending_delete_context
        track = pending_delete_track
        context = pending_delete_context
        if not track or not context:
            return
        identities = track_identities(track)
        try:
            if context in ("downloads", "all") and track.get("mode") == "offline":
                target_path = Path(track.get("url", ""))
                if target_path.is_file():
                    if selected_track and str(selected_track.get("url")) == str(target_path) and current_source is not None:
                        close_audio()
                        current_source = None
                        selected_track = None
                        is_paused = False
                        page.run_task(return_to_discovery)
                    target_path.unlink()

            if context in ("favorites", "all"):
                favorites = [item for item in favorites if not track_identities(item).intersection(identities)]
                if not save_favorites():
                    return

            if context in ("top50", "all"):
                for key, item in list(listening_history.items()):
                    if key in identities or track_identities(item).intersection(identities):
                        listening_history.pop(key, None)
                if current_history_key in identities:
                    current_history_key = None
                    last_poll_position = None
                save_listening_history()

            if context.startswith("playlist:"):
                playlist_id = context.split(":", 1)[1]
                playlist = next((item for item in playlists if item.get("id") == playlist_id), None)
                if playlist:
                    playlist["tracks"] = [item for item in playlist.get("tracks", []) if not track_identities(item).intersection(identities)]
                    if not save_playlists():
                        return
        except OSError as exc:
            set_status(f"Couldn't delete track: {exc}", "#FF8A80")
            return

        page.pop_dialog()
        pending_delete_track = None
        pending_delete_context = None
        if current_view == "library":
            refresh_library()
        refresh_home_sections()
        set_status("Track removed", ACCENT)

    def clear_listening_history(e=None):
        nonlocal current_history_key, last_poll_position
        listening_history.clear()
        current_history_key = None
        last_poll_position = None
        if current_source is not None and selected_track is not None:
            record_track_started(selected_track)
        else:
            save_listening_history()
        page.pop_dialog()
        refresh_home_sections()
        if current_view == "library":
            refresh_library()
        set_status("Listening history cleared", ACCENT)

    def show_create_playlist(e=None):
        playlist_name_input.value = ""
        page.show_dialog(create_playlist_dialog)

    def create_playlist(e=None):
        name = (playlist_name_input.value or "").strip()
        if not name:
            playlist_name_input.error_text = "Enter a playlist name"
            page.update()
            return
        if any(item.get("name", "").casefold() == name.casefold() for item in playlists):
            playlist_name_input.error_text = "A playlist with that name already exists"
            page.update()
            return
        playlist_name_input.error_text = None
        playlists.append({"id": f"playlist-{random.getrandbits(64):016x}", "name": name, "tracks": []})
        if save_playlists():
            page.pop_dialog()
            set_status(f"Created playlist · {name}", ACCENT)
            if current_view == "library":
                set_library_section("playlists")

    def show_add_to_playlist(track):
        nonlocal pending_playlist_track
        if not playlists:
            set_status("Create a playlist in Library to save tracks there", "#FFCC80")
            switch_view("library")
            set_library_section("playlists")
            return
        pending_playlist_track = track
        playlist_selection.options = [ft.dropdown.Option(key=item["id"], text=item["name"]) for item in playlists]
        playlist_selection.value = playlists[0]["id"]
        page.show_dialog(add_to_playlist_dialog)

    def confirm_add_to_playlist(e=None):
        if not pending_playlist_track:
            return
        target = next((item for item in playlists if item.get("id") == playlist_selection.value), None)
        if target is None:
            return
        track = {
            key: value for key, value in pending_playlist_track.items()
            if key in ("id", "title", "artist", "thumbnail", "duration", "url", "mode")
        }
        identity = track.get("id") or track.get("url")
        if not any((item.get("id") or item.get("url")) == identity for item in target["tracks"]):
            target["tracks"].append(track)
        if save_playlists():
            page.pop_dialog()
            set_status(f"Added to playlist · {target['name']}", ACCENT)
            if current_view == "library":
                refresh_library()

    def result_tile(track, grid=False, delete_context=None):
        size = 158 if grid else 54
        artwork = ft.Image(
            src=track.get("thumbnail") or "",
            width=size,
            height=size,
            fit=ft.BoxFit.COVER,
            border_radius=11 if grid else 8,
        ) if track.get("thumbnail") else ft.Container(
            width=size, height=size, border_radius=11 if grid else 8, bgcolor="#242B37",
            alignment=ft.Alignment.CENTER,
            content=ft.Icon(ft.Icons.MUSIC_NOTE_ROUNDED, color=ACCENT, size=30 if grid else 24),
        )
        duration = _format_time(track.get("duration")) if track.get("duration") else "—"
        display_title = track.get("_display_title") or track.get("title") or "Untitled"
        display_artist = track.get("_display_artist") or track.get("artist") or "Unknown artist"
        favorite_button = ft.IconButton(
            icon=ft.Icons.FAVORITE_ROUNDED if is_favorite(track) else ft.Icons.FAVORITE_BORDER_ROUNDED,
            icon_color="#F0788A" if is_favorite(track) else MUTED,
            tooltip="Remove favorite" if is_favorite(track) else "Add to favorites",
            on_click=lambda e, item=track: toggle_favorite(item, e),
        )
        play_action = ft.IconButton(
            icon=ft.Icons.PLAY_CIRCLE_OUTLINE_ROUNDED,
            icon_color=ACCENT, tooltip="Play", on_click=lambda e, item=track: play_track(item),
        )
        menu_items = [
            ft.PopupMenuItem(content="Add to queue", icon=ft.Icons.QUEUE_MUSIC_ROUNDED, on_click=lambda e, item=track: add_to_queue(item)),
            ft.PopupMenuItem(content="Add to playlist", icon=ft.Icons.PLAYLIST_ADD_ROUNDED, on_click=lambda e, item=track: show_add_to_playlist(item)),
        ]
        if track.get("mode") != "offline":
            menu_items.append(ft.PopupMenuItem(content="Download", icon=ft.Icons.DOWNLOAD_OUTLINED, on_click=lambda e, item=track: start_download(item)))
        if delete_context:
            menu_items.append(ft.PopupMenuItem(
                content="Delete from this section", icon=ft.Icons.DELETE_OUTLINE,
                on_click=lambda e, item=track, section=delete_context: request_delete_track(item, section),
            ))
        actions = ft.Row([
            favorite_button,
            ft.PopupMenuButton(icon=ft.Icons.MORE_VERT_ROUNDED, icon_color=MUTED, tooltip="More track actions", items=menu_items),
        ], spacing=0)
        if grid:
            resume_seconds = float(track.get("_resume_position") or 0)
            secondary_text = f"Resume at {_format_time(resume_seconds)}" if resume_seconds > 3 else display_artist
            content = ft.Column([
                artwork,
                ft.Text(display_title, size=13, color="#F4F6FA", weight=ft.FontWeight.W_600, max_lines=2, overflow=ft.TextOverflow.ELLIPSIS),
                ft.Text(secondary_text, size=11, color=MUTED, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                ft.Row([ft.Text(duration, size=10, color=MUTED, expand=True), play_action], spacing=2),
                actions,
            ], spacing=7)
        else:
            resume_seconds = float(track.get("_resume_position") or 0)
            secondary_text = f"{display_artist}  ·  Resume at {_format_time(resume_seconds)}" if resume_seconds > 3 else display_artist
            content = ft.Row([
                artwork,
                ft.Column([
                    ft.Text(display_title, size=13, weight=ft.FontWeight.W_600, color="#F4F6FA", max_lines=2, overflow=ft.TextOverflow.ELLIPSIS),
                    ft.Text(secondary_text, size=11, color=MUTED, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                ], spacing=4, expand=True),
                ft.Text(duration, size=11, color=MUTED, width=42, text_align=ft.TextAlign.RIGHT),
                play_action,
                actions,
            ], spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER)

        tile = ft.Container(
            content=content,
        width=190 if grid else None,
            padding=12 if grid else 8,
            bgcolor=PANEL_SOFT,
            border_radius=14 if grid else 12,
            border=ft.Border.all(1, "#252B36"),
            animate=180,
            tooltip=str(track.get("title") or ""),
        )

        def handle_hover(e):
            hovered = str(e.data).lower() == "true"
            tile.bgcolor = "#202832" if hovered else PANEL_SOFT
            tile.border = ft.Border.all(1, "#3D5441" if hovered else "#252B36")
            page.update()

        tile.on_hover = handle_hover
        return tile

    def render_track_collection(tracks, mode="list", delete_context=None):
        tiles = [result_tile(track, grid=(mode == "grid"), delete_context=delete_context) for track in tracks]
        if mode == "grid":
            return ft.Row(tiles, spacing=12, run_spacing=12, wrap=True)
        return ft.Column(tiles, spacing=8)

    def set_search_view(mode):
        nonlocal search_view_mode
        search_view_mode = mode
        results_list.content = render_track_collection(loaded_search_tracks, mode)
        page.update()

    def set_quick_view(mode):
        nonlocal quick_view_mode
        quick_view_mode = mode
        quick_pick_list.content = render_track_collection(quick_pick_tracks, mode)
        page.update()

    def search_worker(query, limit, append, request_id):
        nonlocal loaded_search_tracks, search_fetch_limit
        try:
            tracks = search_youtube(query, limit=limit)
            if request_id != search_generation:
                return
            if append:
                known_ids = {track.get("id") or track.get("url") for track in loaded_search_tracks}
                for track in tracks:
                    track_id = track.get("id") or track.get("url")
                    if track_id not in known_ids:
                        loaded_search_tracks.append(track)
                        known_ids.add(track_id)
            else:
                loaded_search_tracks = tracks

            search_fetch_limit = limit
            if loaded_search_tracks:
                results_summary.value = f"{len(loaded_search_tracks)} tracks for “{query}”"
                results_list.content = render_track_collection(loaded_search_tracks, search_view_mode)
                load_more_button.visible = limit < 120 and len(tracks) >= limit
                load_more_button.disabled = False
                load_more_button.content = "Load more tracks" if load_more_button.visible else "You've reached the end"
            else:
                results_summary.value = f"No tracks found for “{query}”"
                results_list.content = ft.Text("Try a different song or artist.", size=12, color=MUTED)
                load_more_button.visible = False
        except Exception as exc:
            if request_id != search_generation:
                return
            results_summary.value = "Search failed"
            results_list.content = ft.Text(str(exc), size=12, color="#FF8A80")
        finally:
            if request_id == search_generation:
                search_progress.visible = False
                load_more_button.disabled = False
                page.update()

    def load_more_results():
        nonlocal search_fetch_limit, search_generation
        if not current_search_query or search_fetch_limit >= 120:
            return
        next_limit = min(search_fetch_limit + 24, 120)
        search_generation += 1
        load_more_button.disabled = True
        search_progress.visible = True
        page.update()
        page.run_thread(search_worker, current_search_query, next_limit, True, search_generation)

    def start_search(e=None):
        nonlocal current_search_query, search_fetch_limit, search_generation, loaded_search_tracks
        query = (search_input.value or "").strip()
        if not query:
            set_status("Type a song, artist, or album to search", "#FFCC80")
            return
        landing_panel.visible = False
        quick_picks_panel.visible = False
        continue_panel.visible = False
        forgotten_panel.visible = False
        results_panel.visible = True
        current_search_query = query
        search_fetch_limit = 24
        loaded_search_tracks = []
        search_generation += 1
        request_id = search_generation
        load_more_button.visible = False
        load_more_button.disabled = True
        results_summary.value = f"Searching YouTube for “{query}”…"
        results_list.content = ft.Row([ft.ProgressRing(width=18, height=18, color=ACCENT), ft.Text("Finding tracks…", size=12, color=MUTED)], spacing=10)
        search_progress.visible = True
        page.update()
        page.run_thread(search_worker, query, search_fetch_limit, False, request_id)

    def toggle_pause(e=None):
        nonlocal is_paused
        if not current_source:
            if selected_track:
                play_track(selected_track)
            else:
                set_status("Search for and choose a track first", "#FFCC80")
            return
        if is_paused:
            resume_audio()
            is_paused = False
            set_status("Playing", ACCENT)
            play_button.icon = ft.Icons.PAUSE_ROUNDED
            compact_play_button.icon = ft.Icons.PAUSE_ROUNDED
            pause_label.value = "PAUSE"
        else:
            pause_audio()
            is_paused = True
            set_status("Paused", MUTED)
            play_button.icon = ft.Icons.PLAY_ARROW_ROUNDED
            compact_play_button.icon = ft.Icons.PLAY_ARROW_ROUNDED
            pause_label.value = "PLAY"
        page.update()

    async def return_to_discovery():
        nonlocal player_expanded
        # Let the stop-button event finish before removing its parent control.
        await asyncio.sleep(0.08)
        if current_source is not None:
            return
        play_button.icon = ft.Icons.PLAY_ARROW_ROUNDED
        pause_label.value = "PLAY"
        track_title.value = "Nothing playing yet"
        artist.value = "Search for a track to get started"
        compact_play_button.icon = ft.Icons.PLAY_ARROW_ROUNDED
        compact_title.value = "Nothing playing"
        compact_artist.value = "Choose a track to begin"
        player_card.visible = False
        compact_player.visible = False
        player_expanded = False
        content_region.visible = True
        lyrics_panel.visible = False
        lyrics_button.visible = False
        quick_picks_panel.visible = True
        landing_panel.visible = not hero_dismissed
        results_panel.visible = False
        seek_slider.value = 0
        compact_seek_slider.value = 0
        elapsed.value = "0:00"
        total.value = "0:00"
        status.value = "STOPPED · READY WHEN YOU ARE"
        status.color = MUTED
        refresh_home_sections()
        page.update()
        if landing_panel.visible:
            page.run_task(animate_waveform)

    def stop_playback(e=None):
        nonlocal current_source, selected_track, is_paused, lyrics_generation, player_expanded
        lyrics_generation += 1
        lyrics_panel.visible = False
        lyrics_button.visible = False
        if current_history_key:
            position = current_time()
            if position is not None and current_history_key in listening_history:
                listening_history[current_history_key]["resume_position"] = max(0.0, float(position))
            save_listening_history()
        close_audio()
        current_source = None
        selected_track = None
        is_paused = False
        player_expanded = False
        page.run_task(return_to_discovery)

    def on_seek(e):
        if duration_seconds > 0:
            position = float(e.control.value)
            adjust_current_time(position)

    def on_seek_hover(e):
        height = 30 if str(e.data).lower() == "true" else 24
        seek_slider.height = height
        seek_slider_container.height = height
        page.update()

    def on_volume_change(e):
        value = int(e.control.value)
        volume_value.value = f"{value}%"
        set_volume(value)
        page.update()

    def on_speed_change(e):
        selected = e.control.selected or ["1"]
        value = float(selected[0])
        adjust_track_speed(value)
        page.update()

    def nudge_volume(direction):
        volume_up() if direction > 0 else volume_down()
        value = get_current_volume()
        if value is not None:
            volume_slider.value = value
            volume_value.value = f"{int(value)}%"
            page.update()

    def download_worker(source, folder):
        try:
            download_audio(source, folder or None)
            set_status("Download complete", ACCENT)
            if current_view == "library":
                refresh_library()
        except Exception as exc:
            set_status(f"Download failed: {exc}", "#FF8A80")

    def start_download(track, e=None):
        if not track or not track.get("url"):
            set_status("Choose a track to download", "#FFCC80")
            return
        set_status(f"Downloading {track.get('title') or 'track'}…", ACCENT)
        page.run_thread(download_worker, track["url"], download_directory)

    def save_settings(e=None):
        nonlocal download_directory
        new_directory = (download_folder.value or "").strip()
        try:
            settings_file.parent.mkdir(parents=True, exist_ok=True)
            settings_file.write_text(
                json.dumps({"download_directory": new_directory}, indent=2),
                encoding="utf-8",
            )
        except OSError as exc:
            set_status(f"Could not save settings: {exc}", "#FF8A80")
            return
        download_directory = new_directory
        page.pop_dialog()
        set_status("Settings saved", ACCENT)
        if current_view == "library":
            refresh_library()

    def open_settings(e=None):
        download_folder.value = download_directory
        page.show_dialog(settings_dialog)

    async def poll_playback():
        nonlocal duration_seconds, last_poll_position, last_history_save
        while current_source is not None:
            position = current_time()
            duration = get_audio_duration()
            if duration and duration > 0:
                duration_seconds = float(duration)
                seek_slider.max = duration_seconds
                compact_seek_slider.max = duration_seconds
                total.value = _format_time(duration_seconds)
            if position is not None:
                position = max(0.0, float(position))
                seek_slider.value = min(position, duration_seconds) if duration_seconds else 0
                compact_seek_slider.value = min(position, duration_seconds) if duration_seconds else 0
                elapsed.value = _format_time(position)
                update_lyrics(position)
                if current_history_key and not is_paused:
                    previous = last_poll_position
                    if previous is not None and position >= previous:
                        listened = min(position - previous, 2.0)
                        item = listening_history.get(current_history_key)
                        if item is not None:
                            item["played_seconds"] = float(item.get("played_seconds", 0)) + listened
                    if duration_seconds and position >= duration_seconds - 2:
                        listening_history[current_history_key]["resume_position"] = 0
                    else:
                        listening_history[current_history_key]["resume_position"] = position
                    last_poll_position = position
                    now = asyncio.get_running_loop().time()
                    if now - last_history_save >= 5:
                        save_listening_history()
                        last_history_save = now
                        refresh_home_sections()
                        if current_view == "library" and library_section == "top50":
                            refresh_library()
            volume = get_current_volume()
            if volume is not None:
                volume_slider.value = volume
                volume_value.value = f"{int(volume)}%"
            speed = get_current_track_speed()
            if speed is not None:
                choices = (1.0, 1.5, 2.0)
                nearest = min(choices, key=lambda choice: abs(choice - float(speed)))
                speed_options.selected = [str(nearest).removesuffix(".0")]
            page.update()
            await asyncio.sleep(0.7)

    # Define these referenced functions before the controls are used at runtime.
    search_input.on_submit = start_search

    settings_dialog = ft.AlertDialog(
        modal=True,
        bgcolor=PANEL,
        shape=ft.RoundedRectangleBorder(radius=18),
        title=ft.Row([
            ft.Icon(ft.Icons.SETTINGS_OUTLINED, color=ACCENT, size=20),
            ft.Text("Settings", size=20, weight=ft.FontWeight.W_600, color="#F4F6FA"),
        ], spacing=10),
        content=ft.Column([
            ft.Text("Choose where your downloaded tracks should be saved.", size=13, color=MUTED),
            download_folder,
            ft.Text("Leave this blank to save downloads in the app's current folder.", size=11, color="#737B89"),
        ], tight=True, spacing=14, width=420),
        actions=[
            ft.TextButton("Cancel", on_click=lambda e: page.pop_dialog()),
            ft.Button(
                "Save settings",
                on_click=save_settings,
                style=ft.ButtonStyle(bgcolor=ACCENT, color=BG, shape=ft.RoundedRectangleBorder(radius=9)),
            ),
        ],
        actions_alignment=ft.MainAxisAlignment.END,
    )
    create_playlist_dialog = ft.AlertDialog(
        modal=True, bgcolor=PANEL, shape=ft.RoundedRectangleBorder(radius=18),
        title=ft.Text("Create a playlist", size=20, weight=ft.FontWeight.W_600, color="#F4F6FA"),
        content=ft.Column([
            ft.Text("Start a collection for a mood, moment, or favorite artist.", size=13, color=MUTED),
            playlist_name_input,
        ], tight=True, spacing=14, width=400),
        actions=[
            ft.TextButton("Cancel", on_click=lambda e: page.pop_dialog()),
            ft.Button("Create playlist", icon=ft.Icons.ADD_ROUNDED, on_click=create_playlist, style=ft.ButtonStyle(bgcolor=ACCENT, color=BG, shape=ft.RoundedRectangleBorder(radius=9))),
        ], actions_alignment=ft.MainAxisAlignment.END,
    )
    queue_dialog = ft.AlertDialog(
        modal=True, bgcolor=PANEL, shape=ft.RoundedRectangleBorder(radius=18),
        title=ft.Row([
            ft.Icon(ft.Icons.QUEUE_MUSIC_ROUNDED, color=ACCENT),
            ft.Text("Up next", size=20, color="#F4F6FA", weight=ft.FontWeight.W_600),
        ], spacing=10),
        content=queue_list,
        actions=[ft.TextButton("Done", on_click=lambda e: page.pop_dialog())],
        actions_alignment=ft.MainAxisAlignment.END,
    )
    add_to_playlist_dialog = ft.AlertDialog(
        modal=True, bgcolor=PANEL, shape=ft.RoundedRectangleBorder(radius=18),
        title=ft.Text("Add to playlist", size=20, weight=ft.FontWeight.W_600, color="#F4F6FA"),
        content=ft.Column([
            ft.Text("Choose where this track belongs.", size=13, color=MUTED),
            playlist_selection,
        ], tight=True, spacing=14, width=400),
        actions=[
            ft.TextButton("Cancel", on_click=lambda e: page.pop_dialog()),
            ft.Button("Add track", icon=ft.Icons.PLAYLIST_ADD_ROUNDED, on_click=confirm_add_to_playlist, style=ft.ButtonStyle(bgcolor=ACCENT, color=BG, shape=ft.RoundedRectangleBorder(radius=9))),
        ], actions_alignment=ft.MainAxisAlignment.END,
    )
    clear_history_dialog = ft.AlertDialog(
        modal=True, bgcolor=PANEL, shape=ft.RoundedRectangleBorder(radius=18),
        title=ft.Text("Clear listening history?", size=20, weight=ft.FontWeight.W_600, color="#F4F6FA"),
        content=ft.Text("This clears Continue Listening and your Top 50 playtime data. Favorites, downloads, and playlists will stay.", size=13, color=MUTED),
        actions=[
            ft.TextButton("Cancel", on_click=lambda e: page.pop_dialog()),
            ft.Button("Clear history", icon=ft.Icons.DELETE_OUTLINE, on_click=clear_listening_history, style=ft.ButtonStyle(bgcolor="#8E4552", color="#FFFFFF", shape=ft.RoundedRectangleBorder(radius=9))),
        ], actions_alignment=ft.MainAxisAlignment.END,
    )
    delete_track_dialog = ft.AlertDialog(
        modal=True, bgcolor=PANEL, shape=ft.RoundedRectangleBorder(radius=18),
        title=ft.Text("Delete this track?", size=20, weight=ft.FontWeight.W_600, color="#F4F6FA"),
        content=delete_confirmation_text,
        actions=[
            ft.TextButton("Cancel", on_click=lambda e: page.pop_dialog()),
            ft.Button("Delete", icon=ft.Icons.DELETE_OUTLINE, on_click=confirm_delete_track, style=ft.ButtonStyle(bgcolor="#8E4552", color="#FFFFFF", shape=ft.RoundedRectangleBorder(radius=9))),
        ], actions_alignment=ft.MainAxisAlignment.END,
    )

    home_nav = ft.TextButton(
        "Home", icon=ft.Icons.HOME_ROUNDED, on_click=lambda e: switch_view("home"),
    )
    library_nav = ft.TextButton(
        "Library", icon=ft.Icons.LIBRARY_MUSIC_ROUNDED, on_click=lambda e: switch_view("library"),
    )
    settings_nav = ft.TextButton(
        "Settings", icon=ft.Icons.SETTINGS_OUTLINED, on_click=open_settings,
    )
    search_action_button = ft.Button(
        content=ft.Row([search_progress, ft.Icon(ft.Icons.SEARCH_ROUNDED, size=17), ft.Text("Search")], spacing=7),
        on_click=start_search,
        style=ft.ButtonStyle(bgcolor=ACCENT, color=BG, padding=ft.Padding.symmetric(horizontal=14, vertical=11), shape=ft.RoundedRectangleBorder(radius=10)),
    )
    search_bar = ft.Container(
        content=ft.Row([search_input, search_action_button], spacing=8),
        expand=True,
    )
    top_bar = ft.Row(
        [
            ft.Row([
                ft.Container(width=10, height=10, border_radius=50, bgcolor=ACCENT),
                ft.Text("AUDIO FLUX", size=12, weight=ft.FontWeight.W_700, color="#F4F6FA"),
            ], spacing=10),
            search_bar,
            ft.Row([
                home_nav,
                library_nav,
                settings_nav,
            ], spacing=4),
        ],
        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
        spacing=18,
    )

    player_card = ft.Container(
        content=ft.Column([
            ft.Row([cover, ft.Column([status, track_title, artist], spacing=10, expand=True), ft.IconButton(icon=ft.Icons.CLOSE_FULLSCREEN_ROUNDED, icon_color=MUTED, tooltip="Collapse player", on_click=toggle_expanded_player)], spacing=24, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            ft.Column([ft.Row([elapsed, total], alignment=ft.MainAxisAlignment.SPACE_BETWEEN), seek_slider_container], spacing=0),
            ft.Row([
                shuffle_button,
                ft.IconButton(icon=ft.Icons.SKIP_PREVIOUS_ROUNDED, icon_color=MUTED, icon_size=25, tooltip="Previous track", on_click=previous_track),
                play_button,
                ft.IconButton(icon=ft.Icons.SKIP_NEXT_ROUNDED, icon_color=MUTED, icon_size=25, tooltip="Next track", on_click=next_track),
                repeat_button,
            ], alignment=ft.MainAxisAlignment.CENTER, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            ft.Row([
                ft.Row([
                    ft.Text("SPEED", size=10, color=MUTED, weight=ft.FontWeight.W_700),
                    speed_options,
                ], spacing=10),
                ft.IconButton(icon=ft.Icons.STOP_ROUNDED, icon_color=MUTED, tooltip="Stop playback", on_click=stop_playback),
            ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN, vertical_alignment=ft.CrossAxisAlignment.CENTER),
        ], spacing=24),
        visible=False, expand=True, padding=24, bgcolor=PANEL, border_radius=22, border=ft.Border.all(1, "#252B36"),
    )

    compact_player = ft.Container(
        visible=False,
        content=ft.Row([
            ft.Row([
                compact_cover,
                ft.Column([compact_title, compact_artist], spacing=3, width=125),
                compact_favorite_button,
        ], spacing=8, width=230),
            ft.Row([
                ft.IconButton(icon=ft.Icons.SKIP_PREVIOUS_ROUNDED, icon_color=MUTED, tooltip="Previous track", on_click=previous_track),
                compact_play_button,
                ft.IconButton(icon=ft.Icons.SKIP_NEXT_ROUNDED, icon_color=MUTED, tooltip="Next track", on_click=next_track),
            ], spacing=2),
            ft.Container(content=compact_seek_slider, expand=True, padding=ft.Padding.symmetric(horizontal=8)),
            ft.Row([
                volume_mute_button,
                ft.Container(content=volume_slider, width=90),
                ft.IconButton(icon=ft.Icons.QUEUE_MUSIC_ROUNDED, icon_color=MUTED, tooltip="Open queue", on_click=open_queue),
                lyrics_button,
                ft.IconButton(icon=ft.Icons.OPEN_IN_FULL_ROUNDED, icon_color=MUTED, tooltip="Expand player", on_click=toggle_expanded_player),
        ], spacing=1),
        ], spacing=12, vertical_alignment=ft.CrossAxisAlignment.CENTER),
        padding=ft.Padding.symmetric(horizontal=14, vertical=9), bgcolor=PANEL,
        border=ft.Border.only(top=ft.BorderSide(1, "#303744")),
    )

    waveform_bars = []
    for index, height in enumerate([28, 46, 36, 58, 42, 64, 48, 32, 54, 38, 24]):
        waveform_bars.append(ft.Container(
            width=9, height=height, border_radius=8,
            bgcolor=ACCENT if index in (3, 5, 8) else "#526B50",
            animate=ft.Animation(420, "easeInOut"),
        ))
    waveform = ft.Row(
        waveform_bars,
        spacing=7, alignment=ft.MainAxisAlignment.CENTER,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
    )

    def dismiss_hero(e=None):
        nonlocal hero_dismissed
        hero_dismissed = True
        landing_panel.visible = False
        page.update()

    async def animate_waveform():
        while landing_panel.visible:
            for bar in waveform_bars:
                bar.height = random.randint(14, 66)
            page.update()
            await asyncio.sleep(0.52)

    landing_panel = ft.Container(
        content=ft.Row([
            ft.Column([
                ft.Row([
                    ft.Text("A LITTLE LESS ALGORITHM. A LOT MORE YOU.", size=10, color=ACCENT, weight=ft.FontWeight.W_700, expand=True),
                    ft.IconButton(icon=ft.Icons.CLOSE_ROUNDED, icon_color="#AAB2B9", icon_size=17, tooltip="Dismiss banner", on_click=dismiss_hero),
                ], vertical_alignment=ft.CrossAxisAlignment.CENTER),
                ft.Text("Your next favorite\nis one search away.", size=31, height=82, color="#F4F6FA", weight=ft.FontWeight.W_700),
                ft.Text("Find the songs you love, then stream or save\nthem straight from your library.", size=13, color="#AAB2B9", height=42),
                ft.Row([
                    ft.Icon(ft.Icons.SEARCH_ROUNDED, size=15, color=ACCENT),
                    ft.Text("SEARCH", size=9, color="#E5EADF", weight=ft.FontWeight.W_700),
                    ft.Icon(ft.Icons.PLAY_ARROW_ROUNDED, size=15, color=ACCENT),
                    ft.Text("STREAM", size=9, color="#E5EADF", weight=ft.FontWeight.W_700),
                    ft.Icon(ft.Icons.DOWNLOAD_OUTLINED, size=14, color=ACCENT),
                    ft.Text("SAVE", size=9, color="#E5EADF", weight=ft.FontWeight.W_700),
                ], spacing=7),
            ], spacing=14, expand=True),
            ft.Container(
                content=ft.Column([
                    ft.Container(
                        content=ft.Icon(ft.Icons.HEADPHONES_ROUNDED, size=48, color="#E7F1D8"),
                        width=90, height=90, border_radius=45, bgcolor="#29372D",
                        alignment=ft.Alignment.CENTER,
                        border=ft.Border.all(1, "#526B50"),
                    ),
                    waveform,
                    ft.Text("TUNE IN TO YOUR TASTE", size=9, color="#AAB2B9", weight=ft.FontWeight.W_700),
                ], horizontal_alignment=ft.CrossAxisAlignment.CENTER, alignment=ft.MainAxisAlignment.CENTER, spacing=9),
                width=210, height=190, bgcolor="#121A18", border_radius=18,
                alignment=ft.Alignment.CENTER,
            ),
        ], spacing=28, vertical_alignment=ft.CrossAxisAlignment.CENTER),
        padding=28, border_radius=22,
        gradient=ft.LinearGradient(
            begin=ft.Alignment.TOP_LEFT,
            end=ft.Alignment.BOTTOM_RIGHT,
            colors=["#26352D", "#19221F", "#151A20"],
        ),
        border=ft.Border.all(1, "#35443A"),
        visible=not hero_dismissed,
    )
    quick_pick_searches = [
        "official music videos",
        "indie music official video",
        "classic soul official audio",
        "afrobeats official music video",
        "rock songs official audio",
        "pop music video official",
        "jazz performance official",
        "alternative music official audio",
    ]
    quick_pick_list = ft.Container()
    quick_view_toggle = ft.SegmentedButton(
        segments=[ft.Segment(value="list", icon=ft.Icons.VIEW_LIST_ROUNDED), ft.Segment(value="grid", icon=ft.Icons.GRID_VIEW_ROUNDED)],
        selected=["list"], allow_empty_selection=False, show_selected_icon=False,
        on_change=lambda e: set_quick_view(e.control.selected[0]),
        tooltip="Change Quick Picks layout",
    )
    quick_pick_status = ft.Text("Finding a few tracks for you…", size=11, color=MUTED)
    quick_pick_progress = ft.ProgressRing(width=16, height=16, stroke_width=2, color=ACCENT, visible=True)

    def quick_pick_worker(query):
        nonlocal quick_pick_tracks
        try:
            candidates = search_youtube(query, limit=30)
            candidates = [
                track for track in candidates
                if track.get("url") and track.get("thumbnail")
                and (track.get("duration") is None or 60 <= track["duration"] <= 900)
            ]
            if candidates:
                quick_pick_tracks = random.sample(candidates, min(6, len(candidates)))
                quick_pick_list.content = render_track_collection(quick_pick_tracks, quick_view_mode)
                quick_pick_status.value = f"Fresh tracks from YouTube · {query}"
            else:
                quick_pick_tracks = []
                quick_pick_list.content = ft.Text("No fresh tracks found. Shuffle to try again.", size=12, color=MUTED)
                quick_pick_status.value = "No tracks found"
        except Exception as exc:
            quick_pick_list.content = ft.Text(f"Quick picks couldn't load: {exc}", size=12, color="#FF8A80")
            quick_pick_status.value = "Search unavailable"
        finally:
            quick_pick_progress.visible = False
            page.update()

    def shuffle_quick_picks(e=None):
        quick_pick_status.value = "Finding fresh tracks…"
        quick_pick_progress.visible = True
        quick_pick_list.content = ft.Row([
            ft.ProgressRing(width=18, height=18, color=ACCENT),
            ft.Text("Loading real tracks and cover art…", size=12, color=MUTED),
        ], spacing=10)
        page.update()
        page.run_thread(quick_pick_worker, random.choice(quick_pick_searches))

    quick_picks_panel = ft.Container(
        content=ft.Column([
            ft.Row([
                ft.Column([
                    ft.Text("Quick Picks", size=17, color="#F4F6FA", weight=ft.FontWeight.W_600),
                    quick_pick_status,
                ], spacing=3),
                ft.Row([
                    quick_pick_progress,
                    quick_view_toggle,
                    ft.TextButton("Shuffle", icon=ft.Icons.SHUFFLE_ROUNDED, on_click=shuffle_quick_picks),
                ], spacing=8),
            ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
            quick_pick_list,
        ], spacing=14),
        padding=18, bgcolor=PANEL, border_radius=18, border=ft.Border.all(1, "#252B36"),
    )

    continue_list = ft.Container()
    forgotten_list = ft.Container()

    def continue_tracks():
        entries = sorted(
            listening_history.values(),
            key=lambda item: item.get("last_played", ""), reverse=True,
        )
        tracks = []
        for entry in entries:
            if entry.get("url"):
                track = dict(entry)
                track["_resume_position"] = float(entry.get("resume_position") or 0)
                tracks.append(track)
        return tracks[:5]

    def forgotten_favorites():
        now = datetime.now(timezone.utc)
        forgotten = []
        for favorite in favorites:
            key = str(favorite.get("id") or favorite.get("url") or "")
            history = listening_history.get(key, {})
            last_played = history.get("last_played")
            added_at = favorite.get("added_at")
            try:
                stamp = datetime.fromisoformat(last_played) if last_played else None
                old_enough = stamp is not None and now - stamp >= timedelta(days=30)
            except (TypeError, ValueError):
                old_enough = False
            if stamp is None and added_at:
                try:
                    old_enough = now - datetime.fromisoformat(added_at) >= timedelta(days=14)
                except (TypeError, ValueError):
                    old_enough = False
            if old_enough:
                forgotten.append(favorite)
        forgotten.sort(key=lambda item: (
            listening_history.get(str(item.get("id") or item.get("url") or ""), {}).get("last_played")
            or item.get("added_at", "")
        ))
        return forgotten[:5]

    def home_music_section(title, description, icon, content):
        return ft.Container(
            content=ft.Column([
                ft.Row([ft.Icon(icon, size=18, color=ACCENT), ft.Text(title, size=16, color="#F4F6FA", weight=ft.FontWeight.W_600, expand=True)], spacing=9),
                ft.Text(description, size=11, color=MUTED),
                content,
            ], spacing=10),
            padding=18, bgcolor=PANEL, border_radius=17,
            border=ft.Border.all(1, "#252B36"),
        )

    continue_panel = home_music_section("Continue Listening", "Resume a recent track from where you left off.", ft.Icons.QUEUE_MUSIC_ROUNDED, continue_list)
    forgotten_panel = home_music_section("Forgotten Favorites", "Your saved tracks that have been waiting for a replay.", ft.Icons.HISTORY_ROUNDED, forgotten_list)
    def refresh_home_sections():
        recent = continue_tracks()
        forgotten = forgotten_favorites()
        continue_list.content = render_track_collection(recent, "list") if recent else ft.Text("Your recent listening will appear here.", size=12, color=MUTED)
        forgotten_list.content = render_track_collection(forgotten, "list") if forgotten else ft.Text("No forgotten favorites yet. Songs you haven't played in a while will show up here.", size=12, color=MUTED)
        continue_panel.visible = bool(recent)
        forgotten_panel.visible = bool(forgotten)

    home_heading = ft.Column([heading, subtitle], spacing=4)
    home_info = ft.Row([ft.Icon(ft.Icons.INFO_OUTLINE, size=14, color="#737B89"), ft.Text("Tracks are searched and streamed from YouTube using yt-dlp.", size=11, color="#737B89")], spacing=7)
    home_view = ft.Column([
        home_heading,
        continue_panel,
        quick_picks_panel,
        forgotten_panel,
        landing_panel,
        results_panel,
        home_info,
    ], spacing=14, expand=True, scroll=ft.ScrollMode.AUTO, horizontal_alignment=ft.CrossAxisAlignment.STRETCH)

    downloads_summary = ft.Text("", size=12, color=MUTED)
    downloads_folder_label = ft.Text("", size=11, color=MUTED, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS)
    downloads_list = ft.Column(spacing=8)

    def download_tile(filename, path):
        try:
            size = os.path.getsize(path)
            size_label = f"{size / (1024 * 1024):.1f} MB" if size >= 1024 * 1024 else f"{max(1, size // 1024)} KB"
        except OSError:
            size_label = "Audio file"
        extension = Path(filename).suffix.removeprefix(".").upper() or "AUDIO"
        track = {"title": Path(filename).stem, "artist": "Downloaded audio", "url": path, "mode": "offline"}
        return ft.Container(
            content=ft.Row([
                ft.Container(
                    content=ft.Icon(ft.Icons.MUSIC_NOTE_ROUNDED, size=22, color=ACCENT),
                    width=52, height=52, border_radius=10, bgcolor="#26352D",
                    alignment=ft.Alignment.CENTER,
                ),
                ft.Column([
                    ft.Text(Path(filename).stem, size=13, color="#F4F6FA", weight=ft.FontWeight.W_600, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                    ft.Text(f"{extension} · {size_label}", size=11, color=MUTED),
                ], spacing=4, expand=True),
                ft.IconButton(icon=ft.Icons.PLAY_CIRCLE_OUTLINE_ROUNDED, icon_color=ACCENT, tooltip="Play downloaded track", on_click=lambda e, target=path: play_local_file(target)),
                ft.IconButton(icon=ft.Icons.DELETE_OUTLINE, icon_color="#D98791", tooltip="Delete downloaded track", on_click=lambda e, item=track: request_delete_track(item, "downloads")),
            ], spacing=12),
            padding=9, bgcolor=PANEL_SOFT, border_radius=12,
        )

    def refresh_downloads(e=None):
        nonlocal downloaded_tracks
        folder = download_directory or os.getcwd()
        downloads_folder_label.value = f"Folder · {folder}"
        tracks = scan_download_folder(folder)
        downloaded_tracks = [
            {"title": Path(filename).stem, "artist": "Downloaded audio", "url": path, "mode": "offline"}
            for filename, path in tracks
        ]
        downloads_summary.value = f"{len(tracks)} tracks" if tracks else "No downloaded tracks yet"
        if tracks:
            downloads_list.controls = [download_tile(filename, path) for filename, path in tracks]
        else:
            downloads_list.controls = [ft.Container(
                content=ft.Column([
                    ft.Icon(ft.Icons.FOLDER_OPEN_OUTLINED, size=42, color="#617064"),
                    ft.Text("Your downloads will show up here", size=15, color="#E4E8E3", weight=ft.FontWeight.W_600),
                    ft.Text("Download a track from Discover, then come back to play it anytime.", size=12, color=MUTED, text_align=ft.TextAlign.CENTER),
                    ft.Button(
                        "Go to Discover", icon=ft.Icons.SEARCH_ROUNDED,
                        on_click=lambda e: switch_view("home"),
                        style=ft.ButtonStyle(bgcolor=ACCENT, color=BG, shape=ft.RoundedRectangleBorder(radius=9)),
                    ),
                ], spacing=10, horizontal_alignment=ft.CrossAxisAlignment.CENTER),
                padding=38, bgcolor=PANEL, border_radius=18, alignment=ft.Alignment.CENTER,
            )]
        page.update()

    downloads_library_section = ft.Column([
        ft.Container(
            content=ft.Row([
                ft.Icon(ft.Icons.FOLDER_OUTLINED, size=16, color=ACCENT),
                downloads_folder_label,
                ft.Container(expand=True),
                ft.IconButton(icon=ft.Icons.REFRESH_ROUNDED, icon_color=MUTED, tooltip="Refresh downloads", on_click=refresh_downloads),
            ], spacing=9),
            padding=ft.Padding.symmetric(horizontal=13, vertical=6), bgcolor=PANEL, border_radius=11,
        ),
        downloads_summary,
        downloads_list,
    ], spacing=12)

    favorites_summary = ft.Text("", size=12, color=MUTED)
    favorites_list = ft.Container()

    def refresh_favorites():
        favorites_summary.value = f"{len(favorites)} saved tracks" if favorites else "Your personal collection"
        if favorites:
            favorites_list.content = render_track_collection(favorites, favorites_view_mode, delete_context="favorites")
        else:
            favorites_list.content = ft.Container(
                content=ft.Column([
                    ft.Container(
                        content=ft.Icon(ft.Icons.FAVORITE_ROUNDED, size=34, color="#F0788A"),
                        width=76, height=76, border_radius=38, bgcolor="#31232A",
                        alignment=ft.Alignment.CENTER,
                    ),
                    ft.Text("Save the songs you love", size=17, color="#F4F6FA", weight=ft.FontWeight.W_600),
                    ft.Text("Tap the heart on any track to keep it close.", size=12, color=MUTED),
                    ft.Button("Explore music", icon=ft.Icons.SEARCH_ROUNDED, on_click=lambda e: switch_view("home"), style=ft.ButtonStyle(bgcolor=ACCENT, color=BG, shape=ft.RoundedRectangleBorder(radius=9))),
                ], spacing=12, horizontal_alignment=ft.CrossAxisAlignment.CENTER),
                padding=40, bgcolor=PANEL, border_radius=18, alignment=ft.Alignment.CENTER,
            )

    library_summary = ft.Text("Your music, all together.", size=12, color=MUTED)
    library_content = ft.Container()

    def library_section_button(key, label, icon):
        return ft.TextButton(
            label, icon=icon,
            style=ft.ButtonStyle(color=ACCENT if library_section == key else MUTED),
            on_click=lambda e, section=key: set_library_section(section),
        )

    library_tabs = ft.Row([
        library_section_button("all", "All music", ft.Icons.MUSIC_NOTE_ROUNDED),
        library_section_button("downloads", "Downloads", ft.Icons.DOWNLOAD_OUTLINED),
        library_section_button("favorites", "Favorites", ft.Icons.FAVORITE_BORDER_ROUNDED),
        library_section_button("top50", "Top 50", ft.Icons.LEADERBOARD_ROUNDED),
        library_section_button("playlists", "Playlists", ft.Icons.QUEUE_MUSIC_ROUNDED),
    ], spacing=2, scroll=ft.ScrollMode.AUTO)

    def all_library_tracks():
        merged = []
        seen = set()
        sources = [downloaded_tracks, favorites, *[
            [track for track in listening_history.values() if track.get("url")]
        ]]
        for collection in sources:
            for track in collection:
                identity = str(track.get("url") or track.get("id") or "")
                if identity and identity not in seen:
                    seen.add(identity)
                    merged.append(track)
        return merged

    def top_played_tracks():
        tracks = [dict(track) for track in listening_history.values() if track.get("url") and float(track.get("played_seconds", 0)) > 0]
        tracks.sort(key=lambda track: float(track.get("played_seconds", 0)), reverse=True)
        tracks = tracks[:50]
        for rank, track in enumerate(tracks, start=1):
            seconds = int(track.get("played_seconds", 0))
            listened = f"{seconds // 3600}h {(seconds % 3600) // 60}m" if seconds >= 3600 else f"{seconds // 60}m"
            track["_display_title"] = f"{rank:02d}  ·  {track.get('title') or 'Untitled'}"
            track["_display_artist"] = f"{track.get('artist') or 'Unknown artist'}  ·  {listened} listened"
        return tracks

    def playlist_cards():
        if not playlists:
            return ft.Container(
                content=ft.Column([
                    ft.Icon(ft.Icons.QUEUE_MUSIC_ROUNDED, size=42, color=ACCENT),
                    ft.Text("Make it yours", size=17, color="#F4F6FA", weight=ft.FontWeight.W_600),
                    ft.Text("Create a playlist, then add tracks from search, favorites, or your downloads.", size=12, color=MUTED, text_align=ft.TextAlign.CENTER),
                    ft.Button("Create your first playlist", icon=ft.Icons.ADD_ROUNDED, on_click=show_create_playlist, style=ft.ButtonStyle(bgcolor=ACCENT, color=BG, shape=ft.RoundedRectangleBorder(radius=9))),
                ], spacing=12, horizontal_alignment=ft.CrossAxisAlignment.CENTER),
                padding=38, bgcolor=PANEL, border_radius=18, alignment=ft.Alignment.CENTER,
            )
        cards = []
        for playlist in playlists:
            tracks = playlist.get("tracks", [])
            cards.append(ft.Container(
                content=ft.Column([
                    ft.Row([
                        ft.Container(content=ft.Icon(ft.Icons.QUEUE_MUSIC_ROUNDED, color=ACCENT, size=21), width=48, height=48, border_radius=10, bgcolor="#26352D", alignment=ft.Alignment.CENTER),
                        ft.Column([
                            ft.Text(playlist.get("name", "Playlist"), size=15, color="#F4F6FA", weight=ft.FontWeight.W_600),
                            ft.Text(f"{len(tracks)} tracks", size=11, color=MUTED),
                        ], spacing=3, expand=True),
                        ft.TextButton("Shuffle", icon=ft.Icons.SHUFFLE_ROUNDED, on_click=lambda e, items=tracks: play_track(random.choice(items)) if items else set_status("Add tracks to this playlist first", "#FFCC80")),
                    ], spacing=12),
                    render_track_collection(tracks, "list", delete_context=f"playlist:{playlist.get('id')}") if tracks else ft.Text("Add songs from Discover using the playlist button on a track.", size=12, color=MUTED),
                ], spacing=10),
                padding=15, bgcolor=PANEL, border_radius=16, border=ft.Border.all(1, "#252B36"),
            ))
        return ft.Column(cards, spacing=12)

    def refresh_library():
        refresh_downloads()
        refresh_favorites()
        clear_history_button.visible = library_section == "all"
        for button, key in zip(library_tabs.controls, ("all", "downloads", "favorites", "top50", "playlists")):
            button.style = ft.ButtonStyle(color=ACCENT if library_section == key else MUTED)
        if library_section == "downloads":
            library_summary.value = f"{len(downloaded_tracks)} tracks saved on this device"
            library_content.content = downloads_library_section
        elif library_section == "favorites":
            library_summary.value = favorites_summary.value
            library_content.content = favorites_list
        elif library_section == "top50":
            top_tracks = top_played_tracks()
            total_seconds = sum(float(item.get("played_seconds", 0)) for item in top_tracks)
            library_summary.value = f"Top {len(top_tracks)} · { _format_time(total_seconds) } listened"
            library_content.content = render_track_collection(top_tracks, "list", delete_context="top50") if top_tracks else ft.Text("Start listening and your most-played tracks will build up here.", size=12, color=MUTED)
        elif library_section == "playlists":
            library_summary.value = f"{len(playlists)} playlists · your own collections"
            library_content.content = playlist_cards()
        else:
            tracks = all_library_tracks()
            library_summary.value = f"{len(tracks)} tracks across your downloads, favorites, and listening history"
            library_content.content = render_track_collection(tracks, "list", delete_context="all") if tracks else ft.Container(
                content=ft.Column([
                    ft.Icon(ft.Icons.LIBRARY_MUSIC_ROUNDED, size=42, color=ACCENT),
                    ft.Text("Your library starts here", size=17, color="#F4F6FA", weight=ft.FontWeight.W_600),
                    ft.Text("Download tracks, save favorites, or start listening to build your collection.", size=12, color=MUTED, text_align=ft.TextAlign.CENTER),
                    ft.Button("Discover music", icon=ft.Icons.SEARCH_ROUNDED, on_click=lambda e: switch_view("home"), style=ft.ButtonStyle(bgcolor=ACCENT, color=BG, shape=ft.RoundedRectangleBorder(radius=9))),
                ], spacing=12, horizontal_alignment=ft.CrossAxisAlignment.CENTER),
                padding=40, bgcolor=PANEL, border_radius=18, alignment=ft.Alignment.CENTER,
            )

    def set_library_section(section):
        nonlocal library_section
        library_section = section
        refresh_library()
        page.update()

    clear_history_button = ft.OutlinedButton(
        "Clear history", icon=ft.Icons.DELETE_OUTLINE,
        on_click=lambda e: page.show_dialog(clear_history_dialog),
        visible=library_section == "all",
    )

    library_view = ft.Column([
        ft.Row([
            ft.IconButton(icon=ft.Icons.ARROW_BACK_ROUNDED, icon_color=MUTED, tooltip="Back to Home", on_click=lambda e: switch_view("home")),
            ft.Column([
                ft.Text("Your Library", size=28, color="#F4F6FA", weight=ft.FontWeight.W_700),
                ft.Text("Downloads, favorites, listening history, and playlists.", size=13, color=MUTED),
            ], spacing=4, expand=True),
            clear_history_button,
            ft.OutlinedButton("New playlist", icon=ft.Icons.ADD_ROUNDED, on_click=show_create_playlist),
        ], alignment=ft.MainAxisAlignment.START),
        ft.Container(content=library_tabs, padding=ft.Padding.symmetric(horizontal=8, vertical=4), bgcolor=PANEL, border_radius=13),
        library_summary,
        library_content,
    ], spacing=16, expand=True, scroll=ft.ScrollMode.AUTO, horizontal_alignment=ft.CrossAxisAlignment.STRETCH)

    main_content = ft.Container(content=home_view, expand=True)
    content_region = ft.Row(
        [main_content, lyrics_panel], expand=True, spacing=16,
        vertical_alignment=ft.CrossAxisAlignment.START,
    )

    def switch_view(view_name, section=None):
        nonlocal current_view, library_section, current_search_query, search_generation, loaded_search_tracks, search_fetch_limit, player_expanded
        current_view = view_name
        player_expanded = False
        player_card.visible = False
        content_region.visible = True
        compact_player.visible = current_source is not None
        if view_name == "library":
            if section:
                library_section = section
            main_content.content = library_view
            library_nav.style = ft.ButtonStyle(color=ACCENT)
            home_nav.style = ft.ButtonStyle(color=MUTED)
            refresh_library()
        else:
            search_generation += 1
            current_search_query = ""
            loaded_search_tracks = []
            search_fetch_limit = 24
            search_input.value = ""
            search_progress.visible = False
            results_summary.value = "Search to see matching tracks"
            results_list.content = None
            load_more_button.visible = False
            load_more_button.disabled = False
            results_panel.visible = False
            landing_panel.visible = not hero_dismissed
            quick_picks_panel.visible = True
            refresh_home_sections()
            main_content.content = home_view
            home_nav.style = ft.ButtonStyle(color=ACCENT)
            library_nav.style = ft.ButtonStyle(color=MUTED)
        page.update()

    home_nav.style = ft.ButtonStyle(color=ACCENT)
    library_nav.style = ft.ButtonStyle(color=MUTED)
    page.add(ft.Column([top_bar, content_region, player_card, compact_player], spacing=12, expand=True))

    def handle_keyboard_shortcut(event):
        if (event.ctrl or event.meta) and (event.key or "").lower() == "k":
            if current_view != "home":
                switch_view("home")
            search_input.focus()
            page.update()

    page.on_keyboard_event = handle_keyboard_shortcut

    initial_volume = get_current_volume()
    if initial_volume is not None:
        volume_slider.value = initial_volume
        volume_value.value = f"{int(initial_volume)}%"
        if initial_volume > 0:
            last_nonzero_volume = int(initial_volume)
        volume_mute_button.icon = ft.Icons.VOLUME_OFF_ROUNDED if int(initial_volume) == 0 else ft.Icons.VOLUME_UP_ROUNDED
    initial_speed = get_current_track_speed()
    if initial_speed is not None:
        choices = (1.0, 1.5, 2.0)
        nearest = min(choices, key=lambda choice: abs(choice - float(initial_speed)))
        speed_options.selected = [str(nearest).removesuffix(".0")]
    refresh_home_sections()
    page.update()
    shuffle_quick_picks()
    page.run_task(animate_waveform)
