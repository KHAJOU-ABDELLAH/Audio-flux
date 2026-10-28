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


BG = "#0E100F"
PANEL = "#151816"
PANEL_SOFT = "#1B1E1C"
ACCENT = "#C7F36A"
MUTED = "#858B84"
TEXT = "#F0F2ED"
BORDER = "#252A26"


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
    page.padding = 0
    page.window.min_width = 1100
    page.window.min_height = 700
    page.theme = ft.Theme(color_scheme_seed=ACCENT)

    current_source = None
    selected_track = None
    is_paused = False
    player_expanded = False
    last_nonzero_volume = 50
    duration_seconds = 0.0
    current_view = "home"
    home_feed_mode = "for_you"
    library_section = "all"
    current_search_query = ""
    search_fetch_limit = 24
    search_generation = 0
    loaded_search_tracks = []
    quick_pick_tracks = []
    search_view_mode = "list"
    quick_view_mode = "grid"
    favorites_view_mode = "list"
    playback_queue = []
    downloaded_tracks = []
    repeat_enabled = False
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
    active_side_panel = "lyrics"
    playback_generation = 0
    track_ended = False
    opened_playlist_id = None

    greeting = "Good evening" if datetime.now().hour >= 18 else "Good afternoon" if datetime.now().hour >= 12 else "Good morning"
    heading = ft.Text(greeting, size=24, weight=ft.FontWeight.W_700, color=TEXT)
    subtitle = ft.Text("A familiar favorite. A new discovery. Your next listen.", size=11, color=MUTED)
    status = ft.Text("READY · NOTHING PLAYING", size=11, color=ACCENT, weight=ft.FontWeight.W_700)
    track_title = ft.Text("Nothing playing yet", size=21, weight=ft.FontWeight.W_600, color=TEXT, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS)
    artist = ft.Text("Search for a track to get started", size=13, color=MUTED, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS)
    lyrics_status = ft.Text("Choose a song to see its lyrics.", size=11, color=MUTED)
    lyrics_now_title = ft.Text("Nothing playing", size=16, weight=ft.FontWeight.W_700, color=TEXT, max_lines=2, overflow=ft.TextOverflow.ELLIPSIS)
    lyrics_now_artist = ft.Text("Search for a track to get started", size=11, color=MUTED, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS)
    lyrics_credit = ft.Text("Lyric vision provided by lrclib.net", size=9, color=MUTED)
    synced_badge = ft.Row([
        ft.Container(width=6, height=6, border_radius=3, bgcolor=ACCENT),
        ft.Text("Synced lyrics", size=10, color=ACCENT, weight=ft.FontWeight.W_600),
    ], spacing=6, visible=False)
    lyrics_list = ft.ListView(spacing=10, padding=ft.Padding.only(top=10, bottom=12), expand=True, scroll=ft.ScrollMode.AUTO)
    queue_list = ft.Column(spacing=8, scroll=ft.ScrollMode.AUTO, expand=True, visible=False)
    up_next_list = ft.Column(spacing=8)
    lyrics_tab_button = ft.TextButton("Lyrics", on_click=lambda e: show_side_panel("lyrics"))
    queue_tab_button = ft.TextButton("Queue · 0", on_click=lambda e: show_side_panel("queue"))
    lyrics_button = ft.IconButton(
        icon=ft.Icons.LYRICS_ROUNDED, icon_color=MUTED, tooltip="Show synced lyrics",
        on_click=lambda e: show_side_panel("lyrics"),
    )
    lyrics_body = ft.Column([
        lyrics_now_title,
        lyrics_now_artist,
        lyrics_credit,
        synced_badge,
        lyrics_status,
        lyrics_list,
    ], expand=True, spacing=6)
    up_next_section = ft.Column([
        ft.Text("UP NEXT", size=10, color=MUTED, weight=ft.FontWeight.W_700),
        up_next_list,
    ], spacing=8)
    lyrics_panel = ft.Container(
        visible=True,
        content=ft.Column([
            ft.Row([
                lyrics_tab_button,
                queue_tab_button,
                ft.Container(expand=True),
                ft.IconButton(icon=ft.Icons.REFRESH_ROUNDED, icon_color=MUTED, icon_size=16, tooltip="Retry lyrics lookup", on_click=lambda e: retry_lyrics()),
                ft.IconButton(icon=ft.Icons.CLOSE_ROUNDED, icon_color=MUTED, icon_size=16, tooltip="Hide panel", on_click=lambda e: hide_side_panel()),
            ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
            lyrics_body,
            queue_list,
            up_next_section,
        ], expand=True, spacing=8),
        padding=ft.Padding.only(left=16, right=16, top=14, bottom=12),
        bgcolor=PANEL,
        width=292,
        border=ft.Border.only(left=ft.BorderSide(1, BORDER)),
    )
    cover = ft.Container(
        width=220, height=220, border_radius=12, bgcolor="#242B37",
        alignment=ft.Alignment.CENTER,
        content=ft.Column(
            [ft.Icon(ft.Icons.GRAPHIC_EQ, size=62, color=ACCENT), ft.Text("AUDIO FLUX", size=10, color=MUTED, weight=ft.FontWeight.W_700)],
            alignment=ft.MainAxisAlignment.CENTER, horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=12,
        ),
    )

    search_input = ft.TextField(
        label=None, hint_text="Search songs, artists, or paste a YouTube link · Ctrl + K",
        prefix_icon=ft.Icons.SEARCH, expand=True, border_radius=6, filled=True, bgcolor=PANEL_SOFT,
        border_color=ft.Colors.TRANSPARENT, focused_border_color=ft.Colors.TRANSPARENT,
        height=34, text_size=11, hint_style=ft.TextStyle(size=11, color=MUTED),
        content_padding=ft.Padding.symmetric(vertical=0), on_submit=lambda e: start_search(),
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
        thumb_color=ACCENT, height=20, on_change_end=lambda e: on_seek(e),
    )
    compact_elapsed = ft.Text("0:00", size=9, color=MUTED, width=32)
    compact_total = ft.Text("0:00", size=9, color=MUTED, width=32, text_align=ft.TextAlign.RIGHT)
    compact_cover = ft.Container(width=44, height=44, bgcolor="#242B37", border_radius=8, alignment=ft.Alignment.CENTER, content=ft.Icon(ft.Icons.MUSIC_NOTE_ROUNDED, color=ACCENT, size=22))
    compact_title = ft.Text("Nothing playing", size=12, weight=ft.FontWeight.W_600, color="#F4F6FA", max_lines=1, overflow=ft.TextOverflow.ELLIPSIS)
    compact_artist = ft.Text("Choose a track to begin", size=10, color=MUTED, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS)
    compact_play_button = ft.IconButton(icon=ft.Icons.PLAY_ARROW_ROUNDED, icon_color=BG, bgcolor=ACCENT, tooltip="Play / pause", on_click=lambda e: toggle_pause(e))
    compact_favorite_button = ft.IconButton(icon=ft.Icons.FAVORITE_BORDER_ROUNDED, icon_color=MUTED, tooltip="Add to favorites", on_click=lambda e: toggle_favorite(selected_track) if selected_track else None)
    volume_mute_button = ft.IconButton(icon=ft.Icons.VOLUME_UP_ROUNDED, icon_color=MUTED, tooltip="Mute", on_click=lambda e: toggle_mute(e))
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
    compact_repeat_button = ft.IconButton(icon=ft.Icons.REPEAT_ROUNDED, icon_color=MUTED, tooltip="Repeat off", on_click=lambda e: toggle_repeat())
    compact_shuffle_button = ft.IconButton(icon=ft.Icons.SHUFFLE_ROUNDED, icon_color=MUTED, tooltip="Shuffle play", on_click=lambda e: shuffle_play())
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
        padding=ft.Padding.only(top=4, bottom=8),
    )

    def set_status(message, color=ACCENT):
        status.value = message.upper()
        status.color = color
        page.update()

    def show_side_panel(name):
        nonlocal active_side_panel, player_expanded
        if player_expanded:
            player_expanded = False
            player_card.visible = False
            compact_player.visible = True
            content_region.visible = True
        active_side_panel = name
        lyrics_panel.visible = True
        lyrics_body.visible = name == "lyrics"
        queue_list.visible = name == "queue"
        up_next_section.visible = name == "lyrics"
        lyrics_tab_button.style = ft.ButtonStyle(color=ACCENT if name == "lyrics" else MUTED)
        queue_tab_button.style = ft.ButtonStyle(color=ACCENT if name == "queue" else MUTED)
        lyrics_button.icon_color = ACCENT if name == "lyrics" else MUTED
        if name == "queue":
            refresh_queue()
        page.update()

    def hide_side_panel():
        lyrics_panel.visible = False
        lyrics_button.icon_color = MUTED
        page.update()

    def toggle_lyrics():
        if lyrics_panel.visible and active_side_panel == "lyrics":
            hide_side_panel()
        else:
            show_side_panel("lyrics")

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
                    size=20 if index == current_lyric_index else 14,
                    color=ACCENT if index == current_lyric_index else "#7E847C",
                    weight=ft.FontWeight.W_700 if index == current_lyric_index else ft.FontWeight.W_400,
                ),
                key=f"lyric-{index}",
                on_click=(lambda e, position=timestamp: adjust_current_time(position)) if timestamp is not None else None,
                padding=ft.Padding.symmetric(vertical=4),
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
            text_control.size = 20 if line_index == index else 14
            text_control.color = ACCENT if line_index == index else "#7E847C"
            text_control.weight = ft.FontWeight.W_700 if line_index == index else ft.FontWeight.W_400
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
                lyrics_status.value = "Tap a line to seek"
                synced_badge.visible = True
            elif record and record.get("plainLyrics"):
                lyrics_entries = [(None, line.strip()) for line in record["plainLyrics"].splitlines() if line.strip()]
                lyric_timestamps = []
                lyrics_status.value = "Lyrics found · timing unavailable"
                synced_badge.visible = False
            else:
                lyrics_entries = []
                lyric_timestamps = []
                lyrics_status.value = "Synced lyrics aren’t available for this track yet."
                synced_badge.visible = False
            current_lyric_index = -2
            render_lyrics()
        except Exception as exc:
            if request_id != lyrics_generation:
                return
            lyrics_entries = []
            lyric_timestamps = []
            synced_badge.visible = False
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
        lyrics_panel.visible = True
        lyrics_button.icon_color = ACCENT
        synced_badge.visible = False
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
        nonlocal current_source, duration_seconds, is_paused, selected_track, current_lyrics_artist, playback_generation, track_ended
        if not track_info or not track_info.get("audio_source"):
            set_status("Could not load this audio", "#FF8A80")
            return
        playback_generation += 1
        track_ended = False
        current_source = track_info["audio_source"]
        selected_track = track
        record_track_started(track)
        refresh_home_sections()
        duration_seconds = 0.0
        is_paused = False
        compact_seek_slider.max = 100
        compact_seek_slider.value = 0
        compact_elapsed.value = "0:00"
        compact_total.value = "0:00"
        player_card.visible = player_expanded
        compact_player.visible = not player_expanded
        track_title.value = track.get("title") or "Audio stream"
        compact_title.value = track.get("title") or "Audio stream"
        artist_name = track_info.get("artist") or track.get("artist") or "Unknown artist"
        current_lyrics_artist = artist_name
        artist.value = f"By {artist_name}"
        compact_artist.value = artist_name
        lyrics_now_title.value = track.get("title") or "Audio stream"
        lyrics_now_artist.value = artist_name
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
        refresh_queue()
        resume_position = float(track.get("_resume_position") or 0)
        if resume_position > 5:
            page.run_task(seek_after_start, resume_position)
        page.run_task(poll_playback, playback_generation)

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
        lyrics_panel.visible = not player_expanded
        player_card.expand = player_expanded
        page.update()

    def set_volume(value):
        nonlocal last_nonzero_volume
        value = max(0, min(100, int(value)))
        volume_slider.value = value
        volume_value.value = f"{value}%"
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

    def upcoming_track():
        if playback_queue:
            return playback_queue[0]
        candidates = candidate_tracks()
        if not candidates:
            return None
        current_key = track_key(selected_track or {})
        start_index = next((i for i, item in enumerate(candidates) if track_key(item) == current_key), -1)
        if not candidates:
            return None
        return candidates[(start_index + 1) % len(candidates)] if current_source else candidates[0]

    def up_next_row(track):
        thumb = track_artwork(track, 42, 8)
        duration = _format_time(track.get("duration")) if track.get("duration") else ""
        return ft.Container(
            content=ft.Row([
                thumb,
                ft.Column([
                    ft.Text(track.get("title") or "Untitled", size=12, color=TEXT, weight=ft.FontWeight.W_600, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                    ft.Text(track.get("artist") or "Unknown artist", size=10, color=MUTED, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                ], spacing=2, expand=True),
                ft.Text(duration, size=10, color=MUTED),
            ], spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            on_click=lambda e, item=track: play_track(item),
            padding=ft.Padding.symmetric(vertical=4),
        )

    def refresh_queue():
        queue_tab_button.text = f"Queue · {len(playback_queue)}"
        if not playback_queue:
            queue_list.controls = [ft.Text("Your queue is empty. Add tracks from a menu.", size=12, color=MUTED)]
        else:
            queue_list.controls = [
                ft.Container(
                    content=ft.Row([
                        track_artwork(item, 36, 7),
                        ft.Column([
                            ft.Text(item.get("title") or "Untitled", size=13, color=TEXT, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                            ft.Text(item.get("artist") or "Unknown artist", size=10, color=MUTED, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                        ], spacing=3, expand=True),
                        ft.IconButton(icon=ft.Icons.CLOSE_ROUNDED, icon_color=MUTED, tooltip="Remove from queue", on_click=lambda e, target=item: remove_from_queue(target)),
                    ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER), padding=8, bgcolor=PANEL_SOFT, border_radius=10,
                ) for item in playback_queue
            ]
        nxt = upcoming_track()
        if nxt and track_key(nxt) != track_key(selected_track or {}):
            up_next_list.controls = [up_next_row(nxt)]
        elif nxt:
            up_next_list.controls = [up_next_row(nxt)]
        else:
            up_next_list.controls = [ft.Text("Play a track and the next one will land here.", size=11, color=MUTED)]

    def remove_from_queue(track):
        identity = track_key(track)
        index = next((i for i, queued in enumerate(playback_queue) if track_key(queued) == identity), None)
        if index is not None:
            playback_queue.pop(index)
            refresh_queue()
            page.update()

    def open_queue(e=None):
        show_side_panel("queue")

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
        if current_source and selected_track and len(candidates) > 1:
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
        compact_repeat_button.icon_color = ACCENT if repeat_enabled else MUTED
        compact_repeat_button.tooltip = "Repeat on" if repeat_enabled else "Repeat off"
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
            refresh_sidebar_playlists()
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

    def track_artwork(track, size, radius=8):
        src = track.get("thumbnail") if track else None
        if src and src != "placeholder.png":
            return ft.Container(
                content=ft.Image(src=src, width=size, height=size, fit=ft.BoxFit.COVER, border_radius=radius),
                width=size, height=size, border_radius=radius, clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
            )
        return ft.Container(
            width=size, height=size, border_radius=radius, bgcolor="#242B37",
            alignment=ft.Alignment.CENTER,
            content=ft.Icon(ft.Icons.MUSIC_NOTE_ROUNDED, color=ACCENT, size=max(16, size // 3)),
        )

    def track_menu_items(track, delete_context=None):
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
        return menu_items

    def result_tile(track, grid=False, delete_context=None):
        duration = _format_time(track.get("duration")) if track.get("duration") else "—"
        display_title = track.get("_display_title") or track.get("title") or "Untitled"
        display_artist = track.get("_display_artist") or track.get("artist") or "Unknown artist"
        favorite_button = ft.IconButton(
            icon=ft.Icons.FAVORITE_ROUNDED if is_favorite(track) else ft.Icons.FAVORITE_BORDER_ROUNDED,
            icon_color="#F0788A" if is_favorite(track) else MUTED,
            tooltip="Remove favorite" if is_favorite(track) else "Add to favorites",
            icon_size=18,
            on_click=lambda e, item=track: toggle_favorite(item, e),
        )
        menu = ft.PopupMenuButton(icon=ft.Icons.MORE_VERT_ROUNDED, icon_color=MUTED, tooltip="More track actions", items=track_menu_items(track, delete_context))
        if grid:
            cover_size = 158
            play_badge = ft.Container(
                content=ft.Icon(ft.Icons.PLAY_ARROW_ROUNDED, color=BG, size=22),
                width=34, height=34, border_radius=17, bgcolor=ACCENT,
                bottom=8, right=8, on_click=lambda e, item=track: play_track(item),
            )
            duration_badge = ft.Container(
                content=ft.Text(duration, size=9, color=TEXT, weight=ft.FontWeight.W_600),
                bgcolor="#111814CC", padding=ft.Padding.symmetric(horizontal=6, vertical=3),
                border_radius=6, top=8, right=8,
            )
            cover = ft.Stack(
                controls=[
                    track_artwork(track, cover_size, 10),
                    duration_badge,
                    play_badge,
                ],
                width=cover_size,
                height=cover_size,
            )
            content = ft.Column([
                cover,
                ft.Text(display_title, size=12, color=TEXT, weight=ft.FontWeight.W_600, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                ft.Text(display_artist, size=10, color=MUTED, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                ft.Row([favorite_button, menu], spacing=0),
            ], spacing=6)
            tile = ft.Container(content=content, width=cover_size, padding=0, bgcolor=ft.Colors.TRANSPARENT, animate=180, tooltip=str(track.get("title") or ""))
        else:
            resume_seconds = float(track.get("_resume_position") or 0)
            secondary_text = f"{display_artist}  ·  Resume at {_format_time(resume_seconds)}" if resume_seconds > 3 else display_artist
            content = ft.Row([
                track_artwork(track, 48, 8),
                ft.Column([
                    ft.Text(display_title, size=13, weight=ft.FontWeight.W_600, color=TEXT, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                    ft.Text(secondary_text, size=11, color=MUTED, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                ], spacing=3, expand=True),
                ft.Text(duration, size=11, color=MUTED, width=42, text_align=ft.TextAlign.RIGHT),
                ft.IconButton(icon=ft.Icons.PLAY_ARROW_ROUNDED, icon_color=ACCENT, tooltip="Play", on_click=lambda e, item=track: play_track(item)),
                favorite_button,
                menu,
            ], spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER)
            tile = ft.Container(
                content=content, padding=8, bgcolor=PANEL_SOFT, border_radius=12,
                animate=180, tooltip=str(track.get("title") or ""),
            )

        def handle_hover(e):
            hovered = str(e.data).lower() == "true"
            if not grid:
                tile.bgcolor = "#222824" if hovered else PANEL_SOFT
            page.update()

        tile.on_hover = handle_hover
        if grid:
            tile.on_click = lambda e, item=track: play_track(item)
        return tile

    def continue_card(track):
        resume_seconds = float(track.get("_resume_position") or 0)
        duration_value = float(track.get("duration") or 0)
        progress = min(1.0, resume_seconds / duration_value) if duration_value else (0.35 if resume_seconds else 0)
        card = ft.Container(
            content=ft.Column([
                ft.Row([
                    track_artwork(track, 58, 10),
                    ft.Column([
                        ft.Text(track.get("title") or "Untitled", size=13, color=TEXT, weight=ft.FontWeight.W_600, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                        ft.Text(track.get("artist") or "Unknown artist", size=11, color=MUTED, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                    ], spacing=3, expand=True),
                    ft.IconButton(
                        icon=ft.Icons.PLAY_ARROW_ROUNDED, icon_color=BG, bgcolor=ACCENT,
                        icon_size=18, width=34, height=34, tooltip="Resume",
                        on_click=lambda e, item=track: play_track(item),
                    ),
                    ft.PopupMenuButton(icon=ft.Icons.MORE_VERT_ROUNDED, icon_color=MUTED, items=track_menu_items(track)),
                ], spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                ft.ProgressBar(value=progress, color=ACCENT, bgcolor="#2A312C", bar_height=3, border_radius=4),
            ], spacing=10),
            padding=12, bgcolor=PANEL_SOFT, border_radius=14, expand=True,
            on_click=lambda e, item=track: play_track(item),
        )
        return card

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
        nonlocal current_view, current_search_query, search_fetch_limit, search_generation, loaded_search_tracks, player_expanded
        query = (search_input.value or "").strip()
        if not query:
            set_status("Type a song, artist, or album to search", "#FFCC80")
            return
        landing_panel.visible = False
        quick_picks_panel.visible = False
        continue_panel.visible = False
        forgotten_panel.visible = False
        results_panel.visible = True
        player_expanded = False
        player_card.visible = False
        content_region.visible = True
        compact_player.visible = not player_expanded
        current_view = "search"
        main_content.content = home_view
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
        update_navigation()
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
        nonlocal player_expanded, current_view, current_search_query
        # Let the stop-button event finish before removing its parent control.
        await asyncio.sleep(0.08)
        if current_source is not None:
            return
        if current_view == "search":
            current_view = "home"
            current_search_query = ""
            search_input.value = ""
        play_button.icon = ft.Icons.PLAY_ARROW_ROUNDED
        pause_label.value = "PLAY"
        track_title.value = "Nothing playing yet"
        artist.value = "Search for a track to get started"
        compact_play_button.icon = ft.Icons.PLAY_ARROW_ROUNDED
        compact_title.value = "Nothing playing"
        compact_artist.value = "Choose a track to begin"
        lyrics_now_title.value = "Nothing playing"
        lyrics_now_artist.value = "Search for a track to get started"
        player_card.visible = False
        compact_player.visible = True
        player_expanded = False
        content_region.visible = True
        lyrics_panel.visible = True
        synced_badge.visible = False
        quick_picks_panel.visible = True
        landing_panel.visible = True
        results_panel.visible = False
        seek_slider.value = 0
        compact_seek_slider.value = 0
        compact_elapsed.value = "0:00"
        compact_total.value = "0:00"
        elapsed.value = "0:00"
        total.value = "0:00"
        status.value = "STOPPED · READY WHEN YOU ARE"
        status.color = MUTED
        refresh_home_sections()
        update_navigation()
        page.update()

    def stop_playback(e=None):
        nonlocal current_source, selected_track, is_paused, lyrics_generation, player_expanded, playback_generation
        playback_generation += 1
        lyrics_generation += 1
        lyrics_panel.visible = True
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

    async def poll_playback(generation):
        nonlocal duration_seconds, last_poll_position, last_history_save, track_ended
        while current_source is not None and generation == playback_generation:
            position = current_time()
            duration = get_audio_duration()
            if duration and duration > 0:
                duration_seconds = float(duration)
                seek_slider.max = duration_seconds
                compact_seek_slider.max = duration_seconds
                total.value = _format_time(duration_seconds)
                compact_total.value = _format_time(duration_seconds)
            if position is not None:
                position = max(0.0, float(position))
                seek_slider.value = min(position, duration_seconds) if duration_seconds else 0
                compact_seek_slider.value = min(position, duration_seconds) if duration_seconds else 0
                elapsed.value = _format_time(position)
                compact_elapsed.value = _format_time(position)
                update_lyrics(position)
                if current_history_key and not is_paused:
                    previous = last_poll_position
                    if previous is not None and position >= previous:
                        listened = min(position - previous, 2.0)
                        item = listening_history.get(current_history_key)
                        if item is not None:
                            item["played_seconds"] = float(item.get("played_seconds", 0)) + listened
                    history_item = listening_history.get(current_history_key)
                    if history_item is not None:
                        if duration_seconds and position >= duration_seconds - 2:
                            history_item["resume_position"] = 0
                        else:
                            history_item["resume_position"] = position
                    last_poll_position = position
                    now = asyncio.get_running_loop().time()
                    if now - last_history_save >= 5:
                        save_listening_history()
                        last_history_save = now
                        refresh_home_sections()
                        if current_view == "library" and library_section == "top50":
                            refresh_library()
                if (
                    duration_seconds
                    and not is_paused
                    and not repeat_enabled
                    and position >= max(0.0, duration_seconds - 0.8)
                    and not track_ended
                ):
                    track_ended = True
                    next_track()
                    return
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

    library_nav_count = ft.Text("0", size=10, color=MUTED, width=28, text_align=ft.TextAlign.RIGHT)
    favorites_nav_count = ft.Text("0", size=10, color=MUTED, width=28, text_align=ft.TextAlign.RIGHT)
    downloads_nav_count = ft.Text("0", size=10, color=MUTED, width=28, text_align=ft.TextAlign.RIGHT)
    nav_refs = {}

    def make_nav_item(key, icon, label, on_click, count_control=None):
        icon_ctrl = ft.Icon(icon, size=16, color=MUTED)
        label_ctrl = ft.Text(label, size=12, color=MUTED, expand=True, weight=ft.FontWeight.W_500)
        row = [icon_ctrl, label_ctrl]
        if count_control is not None:
            row.append(count_control)
        box = ft.Container(
            content=ft.Row(row, spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            padding=ft.Padding.symmetric(horizontal=12, vertical=10),
            border_radius=8,
            on_click=on_click,
            ink=True,
        )
        nav_refs[key] = (box, icon_ctrl, label_ctrl, count_control)
        return box

    def style_nav_item(key, active):
        box, icon_ctrl, label_ctrl, count_control = nav_refs[key]
        box.bgcolor = "#24301C" if active else ft.Colors.TRANSPARENT
        icon_ctrl.color = ACCENT if active else MUTED
        label_ctrl.color = TEXT if active else MUTED
        label_ctrl.weight = ft.FontWeight.W_600 if active else ft.FontWeight.W_500
        if count_control is not None:
            count_control.color = TEXT if active else MUTED

    home_nav = make_nav_item("home", ft.Icons.HOME_ROUNDED, "Home", lambda e: switch_view("home"))
    search_nav = make_nav_item("search", ft.Icons.SEARCH_ROUNDED, "Search", lambda e: (switch_view("search"), search_input.focus()))
    library_nav = make_nav_item("library", ft.Icons.LIBRARY_MUSIC_ROUNDED, "Library", lambda e: switch_view("library"), library_nav_count)
    favorites_nav = make_nav_item("favorites", ft.Icons.FAVORITE_BORDER_ROUNDED, "Favorites", lambda e: switch_view("library", "favorites"), favorites_nav_count)
    downloads_nav = make_nav_item("downloads", ft.Icons.DOWNLOAD_OUTLINED, "Downloads", lambda e: switch_view("library", "downloads"), downloads_nav_count)
    playlists_nav = make_nav_item("playlists", ft.Icons.QUEUE_MUSIC_ROUNDED, "Playlists", lambda e: switch_view("library", "playlists"))
    settings_nav = make_nav_item("settings", ft.Icons.SETTINGS_OUTLINED, "Settings", open_settings)
    sidebar_playlist_list = ft.Column(spacing=2)

    def playlist_sidebar_item(playlist):
        count = len(playlist.get("tracks") or [])
        return ft.Container(
            content=ft.Row([
                ft.Icon(ft.Icons.QUEUE_MUSIC_ROUNDED, size=15, color=MUTED),
                ft.Column([
                    ft.Text(playlist.get("name", "Playlist"), size=11, color=TEXT, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                    ft.Text(f"{count} tracks", size=9, color=MUTED),
                ], spacing=1, expand=True),
            ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            padding=ft.Padding.symmetric(horizontal=10, vertical=7),
            border_radius=8,
            on_click=lambda e, pid=playlist.get("id"): switch_view("library", "playlists", playlist_id=pid),
            ink=True,
            tooltip=playlist.get("name", "Playlist"),
        )

    def refresh_sidebar_playlists():
        items = [playlist_sidebar_item(playlist) for playlist in playlists[:4]]
        if not items:
            items = [ft.Text("Create your first playlist", size=10, color=MUTED)]
        sidebar_playlist_list.controls = items

    playlist_heading = ft.Row([
        ft.Text("YOUR PLAYLISTS", size=9, color=MUTED, weight=ft.FontWeight.W_700, expand=True),
        ft.IconButton(icon=ft.Icons.ADD_ROUNDED, icon_color=MUTED, icon_size=15, tooltip="Create playlist", on_click=show_create_playlist),
    ], vertical_alignment=ft.CrossAxisAlignment.CENTER)
    refresh_sidebar_playlists()
    sidebar = ft.Container(
        content=ft.Column([
            ft.Row([
                ft.Container(
                    content=ft.Icon(ft.Icons.GRAPHIC_EQ, size=16, color=ACCENT),
                    width=28, height=28, border_radius=8, bgcolor="#1C2418",
                    alignment=ft.Alignment.CENTER,
                ),
                ft.Text("Audio Flux", size=14, color=TEXT, weight=ft.FontWeight.W_700),
            ], spacing=9),
            ft.Container(height=10),
            home_nav,
            search_nav,
            library_nav,
            favorites_nav,
            downloads_nav,
            playlists_nav,
            ft.Container(height=8),
            playlist_heading,
            sidebar_playlist_list,
            ft.Container(expand=True),
            settings_nav,
            ft.Row([ft.Icon(ft.Icons.MUSIC_NOTE_ROUNDED, size=13, color=MUTED), ft.Text("Music from YouTube", size=9, color=MUTED)], spacing=7),
        ], spacing=2, expand=True),
        width=208, padding=ft.Padding.symmetric(horizontal=12, vertical=16),
        bgcolor=BG, border=ft.Border.only(right=ft.BorderSide(1, BORDER)),
    )
    search_action_button = ft.IconButton(
        icon=ft.Icons.ARROW_FORWARD_ROUNDED, icon_color=BG, bgcolor=ACCENT,
        icon_size=17, width=30, height=30, tooltip="Search YouTube", on_click=start_search,
    )
    search_bar = ft.Container(
        content=ft.Row([search_input, search_progress, search_action_button], spacing=6),
        width=430, padding=ft.Padding.only(left=8, right=4), bgcolor=PANEL_SOFT,
        border=ft.Border.all(1, BORDER), border_radius=8,
    )
    top_bar = ft.Container(
        content=ft.Row([
            ft.IconButton(icon=ft.Icons.ARROW_BACK_ROUNDED, icon_color=MUTED, icon_size=17,
                          tooltip="Back to Home", on_click=lambda e: switch_view("home")),
            search_bar,
            ft.Container(expand=True),
            ft.IconButton(icon=ft.Icons.NOTIFICATIONS_NONE_ROUNDED, icon_color=MUTED,
                          tooltip="Notifications", on_click=lambda e: set_status("You're all caught up")),
            ft.Container(content=ft.Text("A", size=11, weight=ft.FontWeight.W_700, color=TEXT),
                         width=28, height=28, border_radius=14, bgcolor="#343832",
                         alignment=ft.Alignment.CENTER),
        ], spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER),
        height=58, padding=ft.Padding.symmetric(horizontal=18),
        border=ft.Border.only(bottom=ft.BorderSide(1, BORDER)),
    )

    player_card = ft.Container(
        content=ft.Column([
            ft.Row([
                cover, ft.Column([status, track_title, artist], spacing=10, expand=True),
                lyrics_button,
                ft.IconButton(icon=ft.Icons.QUEUE_MUSIC_ROUNDED, icon_color=MUTED, tooltip="Open queue", on_click=open_queue),
                ft.IconButton(icon=ft.Icons.CLOSE_FULLSCREEN_ROUNDED, icon_color=MUTED, tooltip="Collapse player", on_click=toggle_expanded_player),
            ], spacing=14, vertical_alignment=ft.CrossAxisAlignment.CENTER),
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

    compact_play_button.icon_size = 26
    compact_play_button.width = 42
    compact_play_button.height = 42
    compact_player = ft.Container(
        visible=True,
        content=ft.Row([
            ft.Row([
                compact_cover,
                ft.Column([compact_title, compact_artist], spacing=2, expand=True),
                compact_favorite_button,
            ], spacing=10, width=250, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            ft.Column([
                ft.Row([
                    compact_shuffle_button,
                    ft.IconButton(icon=ft.Icons.SKIP_PREVIOUS_ROUNDED, icon_color=MUTED, tooltip="Previous track", on_click=previous_track),
                    compact_play_button,
                    ft.IconButton(icon=ft.Icons.SKIP_NEXT_ROUNDED, icon_color=MUTED, tooltip="Next track", on_click=next_track),
                    compact_repeat_button,
                ], alignment=ft.MainAxisAlignment.CENTER, spacing=2),
                ft.Row([
                    compact_elapsed,
                    ft.Container(content=compact_seek_slider, expand=True),
                    compact_total,
                ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            ], expand=True, spacing=0, horizontal_alignment=ft.CrossAxisAlignment.CENTER),
            ft.Row([
                lyrics_button,
                ft.IconButton(icon=ft.Icons.QUEUE_MUSIC_ROUNDED, icon_color=MUTED, tooltip="Open queue", on_click=open_queue),
                volume_mute_button,
                ft.Container(content=volume_slider, width=96),
                ft.IconButton(icon=ft.Icons.OPEN_IN_FULL_ROUNDED, icon_color=MUTED, tooltip="Expand player", on_click=toggle_expanded_player),
            ], spacing=0, vertical_alignment=ft.CrossAxisAlignment.CENTER),
        ], spacing=16, vertical_alignment=ft.CrossAxisAlignment.CENTER),
        padding=ft.Padding.symmetric(horizontal=16, vertical=8), bgcolor="#121513",
        border=ft.Border.only(top=ft.BorderSide(1, BORDER)),
        height=82,
    )

    def search_mood(query):
        search_input.value = query
        start_search()

    mood_options = [
        ("Slow mornings", "Soft picks to ease into the day", "acoustic chill songs", ft.Icons.WB_SUNNY_OUTLINED, "#26372E"),
        ("Soul after hours", "Warm voices, timeless songs", "soul classics", ft.Icons.NIGHTLIGHT_ROUNDED, "#292D3C"),
        ("In your feelings", "A little space for the lyrics", "emotional indie songs", ft.Icons.FAVORITE_ROUNDED, "#3A2930"),
    ]
    mood_tiles = []
    mood_artwork_controls = []
    for mood_title, mood_caption, mood_query, mood_icon, mood_color in mood_options:
        mood_art = ft.Container(content=ft.Icon(mood_icon, size=17, color=ACCENT), width=36, height=36,
                                border_radius=7, bgcolor=mood_color, alignment=ft.Alignment.CENTER)
        mood_artwork_controls.append(mood_art)
        mood_tiles.append(ft.Container(
            content=ft.Row([
                mood_art,
                ft.Column([
                    ft.Text(mood_title, size=10, color=TEXT, weight=ft.FontWeight.W_600, max_lines=1),
                    ft.Text(mood_caption, size=8, color=MUTED, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                ], spacing=2, expand=True),
            ], spacing=8),
            padding=8, bgcolor=PANEL_SOFT, border_radius=7, expand=True,
            on_click=lambda e, query=mood_query: search_mood(query),
            ink=True, tooltip=f"Search {mood_query}",
        ))
    landing_panel = ft.Container(
        content=ft.Column([
            ft.Row([
                ft.Text("Find your next mood", size=14, color=TEXT, weight=ft.FontWeight.W_600, expand=True),
                ft.TextButton("Explore more", style=ft.ButtonStyle(color=MUTED), on_click=lambda e: search_input.focus()),
            ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
            ft.Row(mood_tiles, spacing=8),
        ], spacing=8),
        padding=12, bgcolor=BG, border_radius=8, visible=True,
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
        selected=["grid"], allow_empty_selection=False, show_selected_icon=False,
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
                quick_pick_tracks = random.sample(candidates, min(4, len(candidates)))
                quick_pick_list.content = render_track_collection(quick_pick_tracks, quick_view_mode)
                for index, artwork in enumerate(mood_artwork_controls):
                    track = quick_pick_tracks[index] if index < len(quick_pick_tracks) else None
                    if track and track.get("thumbnail"):
                        artwork.content = ft.Image(src=track["thumbnail"], width=36, height=36, fit=ft.BoxFit.COVER, border_radius=7)
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
                    ft.Text("Quick picks", size=16, color=TEXT, weight=ft.FontWeight.W_600),
                    quick_pick_status,
                ], spacing=3, expand=True),
                ft.Row([
                    quick_pick_progress,
                    quick_view_toggle,
                    ft.TextButton("Refresh picks", icon=ft.Icons.REFRESH_ROUNDED, style=ft.ButtonStyle(color=MUTED), on_click=shuffle_quick_picks),
                ], spacing=8),
            ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
            quick_pick_list,
        ], spacing=12),
        padding=ft.Padding.only(top=4, bottom=8),
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

    def home_music_section(title, description, icon, content, action=None):
        header = ft.Row([
            ft.Text(title, size=16, color=TEXT, weight=ft.FontWeight.W_600, expand=True),
            action or ft.Container(),
        ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN)
        return ft.Container(
            content=ft.Column([
                header,
                ft.Text(description, size=11, color=MUTED),
                content,
            ], spacing=8),
            padding=ft.Padding.only(top=4, bottom=8),
        )

    continue_panel = home_music_section(
        "Continue listening",
        "Resume a recent track from where you left off.",
        ft.Icons.QUEUE_MUSIC_ROUNDED,
        continue_list,
        ft.TextButton("Recently played", style=ft.ButtonStyle(color=MUTED), on_click=lambda e: switch_view("library", "top50")),
    )
    forgotten_panel = home_music_section(
        "Forgotten favorites",
        "Your saved tracks that have been waiting for a replay.",
        ft.Icons.HISTORY_ROUNDED,
        forgotten_list,
    )

    def set_home_feed(mode):
        nonlocal home_feed_mode
        home_feed_mode = mode
        for_you_chip.bgcolor = "#F3F6F0" if mode == "for_you" else ft.Colors.TRANSPARENT
        for_you_label.color = BG if mode == "for_you" else MUTED
        new_finds_chip.bgcolor = "#F3F6F0" if mode == "new_finds" else ft.Colors.TRANSPARENT
        new_finds_label.color = BG if mode == "new_finds" else MUTED
        if mode == "new_finds":
            shuffle_quick_picks()
        refresh_home_sections()
        page.update()

    for_you_label = ft.Text("For you", size=11, weight=ft.FontWeight.W_600, color=BG)
    new_finds_label = ft.Text("New finds", size=11, weight=ft.FontWeight.W_600, color=MUTED)
    for_you_chip = ft.Container(
        content=for_you_label, padding=ft.Padding.symmetric(horizontal=12, vertical=6),
        bgcolor="#F3F6F0", border_radius=16, on_click=lambda e: set_home_feed("for_you"),
    )
    new_finds_chip = ft.Container(
        content=new_finds_label, padding=ft.Padding.symmetric(horizontal=12, vertical=6),
        bgcolor=ft.Colors.TRANSPARENT, border_radius=16, on_click=lambda e: set_home_feed("new_finds"),
    )
    home_feed_toggle = ft.Container(
        content=ft.Row([for_you_chip, new_finds_chip], spacing=4),
        padding=4, bgcolor=PANEL_SOFT, border_radius=20,
    )

    def refresh_nav_counts():
        library_nav_count.value = str(len(all_library_tracks()))
        favorites_nav_count.value = str(len(favorites))
        downloads_nav_count.value = str(len(downloaded_tracks) or len(scan_download_folder(download_directory or os.getcwd())))

    def refresh_home_sections():
        recent = continue_tracks()
        forgotten = forgotten_favorites()
        continue_list.content = ft.Row(
            [continue_card(track) for track in recent[:2]],
            spacing=10,
        ) if recent else ft.Text("Your recent listening will appear here.", size=12, color=MUTED)
        forgotten_list.content = render_track_collection(forgotten, "list") if forgotten else ft.Text("No forgotten favorites yet. Songs you haven't played in a while will show up here.", size=12, color=MUTED)
        on_home = current_view == "home"
        continue_panel.visible = on_home and home_feed_mode == "for_you" and bool(recent)
        forgotten_panel.visible = on_home and home_feed_mode == "for_you" and bool(forgotten)
        quick_picks_panel.visible = on_home
        landing_panel.visible = on_home
        refresh_nav_counts()
        refresh_queue()

    home_heading = ft.Row([
        ft.Column([heading, subtitle], spacing=4, expand=True),
        home_feed_toggle,
    ], vertical_alignment=ft.CrossAxisAlignment.START)
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
        sources = [
            downloaded_tracks,
            favorites,
            [track for track in listening_history.values() if track.get("url")],
            *[playlist.get("tracks", []) for playlist in playlists],
        ]
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
        visible = [item for item in playlists if item.get("id") == opened_playlist_id] if opened_playlist_id else playlists
        if opened_playlist_id and not visible:
            visible = playlists
        cards = []
        for playlist in visible:
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
        if opened_playlist_id:
            return ft.Column([
                ft.TextButton("All playlists", icon=ft.Icons.ARROW_BACK_ROUNDED, on_click=lambda e: switch_view("library", "playlists")),
                *cards,
            ], spacing=12)
        return ft.Column(cards, spacing=12)

    def refresh_library():
        refresh_downloads()
        refresh_favorites()
        refresh_sidebar_playlists()
        refresh_nav_counts()
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

    main_content = ft.Container(
        content=home_view, expand=True,
        padding=ft.Padding.only(left=24, right=18, top=16, bottom=8),
    )
    content_region = ft.Row(
        [main_content, lyrics_panel], expand=True, spacing=0,
        vertical_alignment=ft.CrossAxisAlignment.STRETCH,
    )

    def update_navigation():
        style_nav_item("home", current_view == "home")
        style_nav_item("search", current_view == "search")
        style_nav_item("library", current_view == "library" and library_section == "all")
        style_nav_item("favorites", current_view == "library" and library_section == "favorites")
        style_nav_item("downloads", current_view == "library" and library_section == "downloads")
        style_nav_item("playlists", current_view == "library" and library_section == "playlists")
        style_nav_item("settings", False)
        refresh_nav_counts()

    def switch_view(view_name, section=None, playlist_id=None):
        nonlocal current_view, library_section, current_search_query, search_generation, loaded_search_tracks, search_fetch_limit, player_expanded, opened_playlist_id
        player_expanded = False
        player_card.visible = False
        content_region.visible = True
        compact_player.visible = True
        if view_name == "library":
            current_view = "library"
            if section:
                library_section = section
            opened_playlist_id = playlist_id if section == "playlists" else None
            main_content.content = library_view
            refresh_library()
        elif view_name == "search":
            current_view = "search"
            main_content.content = home_view
            continue_panel.visible = False
            forgotten_panel.visible = False
            quick_picks_panel.visible = not bool(current_search_query)
            landing_panel.visible = not bool(current_search_query)
            if current_search_query and loaded_search_tracks:
                results_panel.visible = True
            else:
                results_panel.visible = True
                if not current_search_query:
                    results_summary.value = "Search songs, artists, or paste a YouTube link"
                    results_list.content = ft.Text("Type a query above, or pick a mood to start exploring.", size=12, color=MUTED)
        else:
            current_view = "home"
            results_panel.visible = False
            landing_panel.visible = True
            quick_picks_panel.visible = True
            refresh_home_sections()
            main_content.content = home_view
        update_navigation()
        page.update()

    update_navigation()
    workspace = ft.Column([top_bar, content_region, player_card], spacing=0, expand=True)
    app_layout = ft.Column([
        ft.Row([sidebar, workspace], spacing=0, expand=True, vertical_alignment=ft.CrossAxisAlignment.STRETCH),
        compact_player,
    ], spacing=0, expand=True)
    page.add(app_layout)

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
