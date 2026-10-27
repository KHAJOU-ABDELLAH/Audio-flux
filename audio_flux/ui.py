import asyncio
import json
import os
import random
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
    pause_audio,
    play_audio,
    resume_audio,
    search_youtube,
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


def main(page: ft.Page):
    page.title = "Audio Flux"
    page.theme_mode = ft.ThemeMode.DARK
    page.bgcolor = BG
    page.padding = 28
    page.window_min_width = 760
    page.window_min_height = 720
    page.theme = ft.Theme(color_scheme_seed=ACCENT)

    current_source = None
    selected_track = None
    is_paused = False
    duration_seconds = 0.0
    settings_file = Path(os.environ.get("APPDATA") or Path.home()) / "AudioFlux" / "settings.json"
    try:
        saved_settings = json.loads(settings_file.read_text(encoding="utf-8"))
        download_directory = str(saved_settings.get("download_directory", "")).strip()
    except (OSError, json.JSONDecodeError, AttributeError):
        download_directory = ""

    heading = ft.Text("Find your next favorite.", size=30, weight=ft.FontWeight.W_700, color="#F4F6FA")
    subtitle = ft.Text("Search YouTube and play it straight from your library.", size=14, color=MUTED)
    status = ft.Text("READY · NOTHING PLAYING", size=11, color=ACCENT, weight=ft.FontWeight.W_700)
    track_title = ft.Text("Nothing playing yet", size=21, weight=ft.FontWeight.W_600, color="#F4F6FA", max_lines=1, overflow=ft.TextOverflow.ELLIPSIS)
    artist = ft.Text("Search for a track to get started", size=13, color=MUTED, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS)
    cover = ft.Container(
        width=184, height=184, border_radius=18, bgcolor="#242B37",
        alignment=ft.Alignment.CENTER,
        content=ft.Column(
            [ft.Icon(ft.Icons.GRAPHIC_EQ, size=62, color=ACCENT), ft.Text("AUDIO FLUX", size=10, color=MUTED, weight=ft.FontWeight.W_700)],
            alignment=ft.MainAxisAlignment.CENTER, horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=12,
        ),
    )

    search_input = ft.TextField(
        label="Search songs, artists, or albums", hint_text="Try a song title or artist name",
        prefix_icon=ft.Icons.SEARCH, expand=True, border_radius=12, filled=True, bgcolor=PANEL_SOFT,
        border_color="#2A303C", focused_border_color=ACCENT, on_submit=lambda e: start_search(),
    )
    download_folder = ft.TextField(
        label="Download folder", hint_text="Leave blank to use the app's current folder",
        prefix_icon=ft.Icons.FOLDER_OUTLINED, expand=True, border_radius=12, filled=True, bgcolor=PANEL_SOFT,
        border_color="#2A303C", focused_border_color=ACCENT,
    )
    elapsed = ft.Text("0:00", size=11, color=MUTED)
    total = ft.Text("0:00", size=11, color=MUTED)
    seek_slider = ft.Slider(min=0, max=100, value=0, active_color=ACCENT, inactive_color="#343B47", on_change_end=lambda e: on_seek(e))
    volume_value = ft.Text("—", size=12, color=MUTED, width=36, text_align=ft.TextAlign.RIGHT)
    volume_slider = ft.Slider(min=0, max=100, value=50, active_color=ACCENT, inactive_color="#343B47", expand=True, on_change=lambda e: on_volume_change(e))
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
    play_button = ft.IconButton(icon=ft.Icons.PLAY_ARROW_ROUNDED, icon_size=34, icon_color=BG, bgcolor=ACCENT, tooltip="Play / pause", on_click=lambda e: toggle_pause(e))
    pause_label = ft.Text("PLAY", size=10, color=MUTED, weight=ft.FontWeight.W_700)
    search_progress = ft.ProgressRing(width=18, height=18, stroke_width=2, color=ACCENT, visible=False)
    results_summary = ft.Text("Search to see matching tracks", size=12, color=MUTED)
    results_list = ft.Column(spacing=8)
    results_panel = ft.Container(
        visible=False,
        content=ft.Column([
            ft.Row([ft.Text("Results", size=16, weight=ft.FontWeight.W_600, color="#F4F6FA"), results_summary], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
            results_list,
        ], spacing=12),
        padding=18, bgcolor=PANEL, border_radius=18, border=ft.Border.all(1, "#252B36"),
    )

    def set_status(message, color=ACCENT):
        status.value = message.upper()
        status.color = color
        page.update()

    def update_track(track_info, track):
        nonlocal current_source, duration_seconds, is_paused, selected_track
        if not track_info or not track_info.get("audio_source"):
            set_status("Could not load this audio", "#FF8A80")
            return
        current_source = track_info["audio_source"]
        selected_track = track
        duration_seconds = 0.0
        is_paused = False
        landing_panel.visible = False
        player_card.visible = True
        track_title.value = track.get("title") or "Audio stream"
        artist.value = f"By {track_info.get('artist') or track.get('artist') or 'Unknown artist'}"
        image_path = track_info.get("cover") or track.get("thumbnail")
        if image_path:
            cover.content = ft.Image(src=image_path, width=184, height=184, fit=ft.BoxFit.COVER, border_radius=18)
        status.value = "NOW PLAYING · YOUTUBE"
        status.color = ACCENT
        play_button.icon = ft.Icons.PAUSE_ROUNDED
        pause_label.value = "PAUSE"
        page.update()
        play_audio(current_source)
        page.run_task(poll_playback)

    def playback_worker(track):
        set_status(f"Loading {track.get('title') or 'track'}…", ACCENT)
        try:
            track_info = audio_player("online", track["url"])
            update_track(track_info, track)
        except Exception as exc:
            set_status(f"Unable to play audio: {exc}", "#FF8A80")

    def play_track(track, e=None):
        if not track or not track.get("url"):
            set_status("Choose a track from the search results", "#FFCC80")
            return
        set_status("Connecting…", ACCENT)
        page.run_thread(playback_worker, track)

    def result_tile(track):
        artwork = ft.Image(
            src=track.get("thumbnail") or "",
            width=54,
            height=54,
            fit=ft.BoxFit.COVER,
            border_radius=8,
        ) if track.get("thumbnail") else ft.Container(
            width=54, height=54, border_radius=8, bgcolor="#242B37",
            alignment=ft.Alignment.CENTER,
            content=ft.Icon(ft.Icons.MUSIC_NOTE_ROUNDED, color=ACCENT, size=24),
        )
        duration = _format_time(track.get("duration")) if track.get("duration") else "—"
        return ft.Container(
            content=ft.Row([
                artwork,
                ft.Column([
                    ft.Text(track.get("title") or "Untitled", size=13, weight=ft.FontWeight.W_600, color="#F4F6FA", max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                    ft.Text(track.get("artist") or "Unknown artist", size=11, color=MUTED, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                ], spacing=4, expand=True),
                ft.Text(duration, size=11, color=MUTED, width=42, text_align=ft.TextAlign.RIGHT),
                ft.IconButton(icon=ft.Icons.PLAY_CIRCLE_OUTLINE_ROUNDED, icon_color=ACCENT, tooltip="Play", on_click=lambda e, item=track: play_track(item)),
                ft.IconButton(icon=ft.Icons.DOWNLOAD_OUTLINED, icon_color=MUTED, tooltip="Download", on_click=lambda e, item=track: start_download(item)),
            ], spacing=12, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            padding=8, bgcolor=PANEL_SOFT, border_radius=12,
        )

    def search_worker(query):
        try:
            tracks = search_youtube(query)
            if tracks:
                results_summary.value = f"{len(tracks)} tracks for “{query}”"
                results_list.controls = [result_tile(track) for track in tracks]
            else:
                results_summary.value = f"No tracks found for “{query}”"
                results_list.controls = [ft.Text("Try a different song or artist.", size=12, color=MUTED)]
            results_panel.visible = True
        except Exception as exc:
            results_summary.value = "Search failed"
            results_list.controls = [ft.Text(str(exc), size=12, color="#FF8A80")]
            results_panel.visible = True
        finally:
            search_progress.visible = False
            page.update()

    def start_search(e=None):
        query = (search_input.value or "").strip()
        if not query:
            set_status("Type a song, artist, or album to search", "#FFCC80")
            return
        landing_panel.visible = False
        quick_picks_panel.visible = False
        future_panel.visible = False
        results_panel.visible = True
        results_summary.value = f"Searching YouTube for “{query}”…"
        results_list.controls = [ft.Row([ft.ProgressRing(width=18, height=18, color=ACCENT), ft.Text("Finding tracks…", size=12, color=MUTED)], spacing=10)]
        search_progress.visible = True
        page.update()
        page.run_thread(search_worker, query)

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
            pause_label.value = "PAUSE"
        else:
            pause_audio()
            is_paused = True
            set_status("Paused", MUTED)
            play_button.icon = ft.Icons.PLAY_ARROW_ROUNDED
            pause_label.value = "PLAY"
        page.update()

    async def return_to_discovery():
        # Let the stop-button event finish before removing its parent control.
        await asyncio.sleep(0.08)
        if current_source is not None:
            return
        play_button.icon = ft.Icons.PLAY_ARROW_ROUNDED
        pause_label.value = "PLAY"
        track_title.value = "Nothing playing yet"
        artist.value = "Search for a track to get started"
        player_card.visible = False
        landing_panel.visible = True
        quick_picks_panel.visible = True
        future_panel.visible = True
        results_panel.visible = False
        seek_slider.value = 0
        elapsed.value = "0:00"
        total.value = "0:00"
        status.value = "STOPPED · READY WHEN YOU ARE"
        status.color = MUTED
        page.update()

    def stop_playback(e=None):
        nonlocal current_source, selected_track, is_paused
        close_audio()
        current_source = None
        selected_track = None
        is_paused = False
        page.run_task(return_to_discovery)

    def on_seek(e):
        if duration_seconds > 0:
            position = float(e.control.value)
            adjust_current_time(position)

    def on_volume_change(e):
        value = int(e.control.value)
        try:
            from .backend import player
            player.volume = value
        except Exception:
            # Keep the controls useful even if no track is loaded yet.
            pass
        volume_value.value = f"{value}%"
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

    def open_settings(e=None):
        download_folder.value = download_directory
        page.show_dialog(settings_dialog)

    async def poll_playback():
        nonlocal duration_seconds
        while current_source is not None:
            position = current_time()
            duration = get_audio_duration()
            if duration and duration > 0:
                duration_seconds = float(duration)
                seek_slider.max = duration_seconds
                total.value = _format_time(duration_seconds)
            if position is not None:
                position = max(0.0, float(position))
                seek_slider.value = min(position, duration_seconds) if duration_seconds else 0
                elapsed.value = _format_time(position)
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

    top_bar = ft.Row(
        [ft.Row([ft.Container(width=10, height=10, border_radius=50, bgcolor=ACCENT), ft.Text("AUDIO FLUX", size=12, weight=ft.FontWeight.W_700, color="#F4F6FA")], spacing=10),
         ft.Row([
             ft.Container(content=ft.Text("PLAYER  /  01", size=10, color=MUTED, weight=ft.FontWeight.W_700), padding=ft.Padding.symmetric(horizontal=12, vertical=8), bgcolor=PANEL, border_radius=30),
             ft.IconButton(icon=ft.Icons.SETTINGS_OUTLINED, icon_color=MUTED, tooltip="Settings", on_click=open_settings),
         ], spacing=8)],
        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
    )

    player_card = ft.Container(
        content=ft.Column([
            ft.Row([cover, ft.Column([status, track_title, artist], spacing=10, expand=True)], spacing=24, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            ft.Column([ft.Row([elapsed, total], alignment=ft.MainAxisAlignment.SPACE_BETWEEN), seek_slider], spacing=0),
            ft.Row([
                ft.IconButton(icon=ft.Icons.STOP_ROUNDED, icon_color=MUTED, icon_size=25, tooltip="Stop", on_click=stop_playback),
                play_button,
                ft.Column([pause_label], alignment=ft.MainAxisAlignment.CENTER),
            ], alignment=ft.MainAxisAlignment.CENTER, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            ft.Row([
                ft.Row([
                    ft.Text("SPEED", size=10, color=MUTED, weight=ft.FontWeight.W_700),
                    speed_options,
                ], spacing=10),
                ft.Row([
                    ft.Icon(ft.Icons.VOLUME_DOWN_ROUNDED, color=MUTED, size=18),
                    ft.IconButton(icon=ft.Icons.REMOVE, icon_size=16, icon_color=MUTED, tooltip="Volume down", on_click=lambda e: nudge_volume(-1)),
                    volume_slider,
                    ft.IconButton(icon=ft.Icons.ADD, icon_size=16, icon_color=MUTED, tooltip="Volume up", on_click=lambda e: nudge_volume(1)),
                    volume_value,
                ], spacing=2, width=290),
            ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN, vertical_alignment=ft.CrossAxisAlignment.CENTER),
        ], spacing=24),
        visible=False, padding=24, bgcolor=PANEL, border_radius=22, border=ft.Border.all(1, "#252B36"),
    )

    waveform_heights = [48, 84, 62, 112, 72, 132, 88, 54, 104, 66, 42]
    waveform = ft.Row([
        ft.Container(
            width=9, height=height, border_radius=8,
            bgcolor=ACCENT if index in (3, 5, 8) else "#526B50",
        )
        for index, height in enumerate(waveform_heights)
    ], spacing=7, alignment=ft.MainAxisAlignment.CENTER, vertical_alignment=ft.CrossAxisAlignment.CENTER)
    landing_panel = ft.Container(
        content=ft.Row([
            ft.Column([
                ft.Text("A LITTLE LESS ALGORITHM. A LOT MORE YOU.", size=10, color=ACCENT, weight=ft.FontWeight.W_700),
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
                        width=116, height=116, border_radius=58, bgcolor="#29372D",
                        alignment=ft.Alignment.CENTER,
                        border=ft.Border.all(1, "#526B50"),
                    ),
                    waveform,
                    ft.Text("TUNE IN TO YOUR TASTE", size=9, color="#AAB2B9", weight=ft.FontWeight.W_700),
                ], horizontal_alignment=ft.CrossAxisAlignment.CENTER, alignment=ft.MainAxisAlignment.CENTER, spacing=14),
                width=230, height=220, bgcolor="#121A18", border_radius=18,
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
    quick_pick_list = ft.Column(spacing=8)
    quick_pick_status = ft.Text("Finding a few tracks for you…", size=11, color=MUTED)
    quick_pick_progress = ft.ProgressRing(width=16, height=16, stroke_width=2, color=ACCENT, visible=True)

    def quick_pick_worker(query):
        try:
            candidates = search_youtube(query, limit=30)
            candidates = [
                track for track in candidates
                if track.get("url") and track.get("thumbnail")
                and (track.get("duration") is None or 60 <= track["duration"] <= 900)
            ]
            if candidates:
                tracks = random.sample(candidates, min(6, len(candidates)))
                quick_pick_list.controls = [result_tile(track) for track in tracks]
                quick_pick_status.value = f"Fresh tracks from YouTube · {query}"
            else:
                quick_pick_list.controls = [ft.Text("No fresh tracks found. Shuffle to try again.", size=12, color=MUTED)]
                quick_pick_status.value = "No tracks found"
        except Exception as exc:
            quick_pick_list.controls = [ft.Text(f"Quick picks couldn't load: {exc}", size=12, color="#FF8A80")]
            quick_pick_status.value = "Search unavailable"
        finally:
            quick_pick_progress.visible = False
            page.update()

    def shuffle_quick_picks(e=None):
        quick_pick_status.value = "Finding fresh tracks…"
        quick_pick_progress.visible = True
        quick_pick_list.controls = [ft.Row([
            ft.ProgressRing(width=18, height=18, color=ACCENT),
            ft.Text("Loading real tracks and cover art…", size=12, color=MUTED),
        ], spacing=10)]
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
                    ft.TextButton("Shuffle", icon=ft.Icons.SHUFFLE_ROUNDED, on_click=shuffle_quick_picks),
                ], spacing=8),
            ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
            quick_pick_list,
        ], spacing=14),
        padding=18, bgcolor=PANEL, border_radius=18, border=ft.Border.all(1, "#252B36"),
    )

    def future_shelf(title, description, icon):
        return ft.Container(
            content=ft.Column([
                ft.Row([
                    ft.Icon(icon, size=18, color=ACCENT),
                    ft.Text(title, size=14, color="#F4F6FA", weight=ft.FontWeight.W_600, expand=True),
                    ft.Container(
                        content=ft.Text("COMING SOON", size=8, color="#AAB2B9", weight=ft.FontWeight.W_700),
                        padding=ft.Padding.symmetric(horizontal=8, vertical=5),
                        bgcolor="#242B32", border_radius=20,
                    ),
                ], spacing=8),
                ft.Text(description, size=11, color=MUTED),
                ft.Container(height=4),
                ft.Row([
                    ft.Container(width=34, height=34, border_radius=7, bgcolor="#202630"),
                    ft.Container(width=34, height=34, border_radius=7, bgcolor="#202630"),
                    ft.Container(width=34, height=34, border_radius=7, bgcolor="#202630"),
                    ft.Text("Your tracks will live here", size=10, color="#737B89"),
                ], spacing=8),
            ], spacing=11),
            expand=True, padding=17, bgcolor=PANEL, border_radius=17,
            border=ft.Border.all(1, "#252B36"),
        )

    future_panel = ft.Row([
        future_shelf("Forgotten Favorites", "Rediscover songs you haven't heard in a while.", ft.Icons.HISTORY_ROUNDED),
        future_shelf("Keep Listening", "Pick up where your last listening session left off.", ft.Icons.QUEUE_MUSIC_ROUNDED),
    ], spacing=12, vertical_alignment=ft.CrossAxisAlignment.STRETCH)

    search_card = ft.Container(
        content=ft.Column([
            ft.Row([ft.Icon(ft.Icons.SEARCH_ROUNDED, color=ACCENT, size=19), ft.Text("Discover", size=16, weight=ft.FontWeight.W_600, color="#F4F6FA")], spacing=10),
            ft.Row([
                search_input,
                ft.Button(
                    content=ft.Row([search_progress, ft.Icon(ft.Icons.SEARCH_ROUNDED, size=18), ft.Text("Search")], spacing=8),
                    on_click=start_search,
                    style=ft.ButtonStyle(bgcolor=ACCENT, color=BG, padding=ft.Padding.symmetric(horizontal=18, vertical=14), shape=ft.RoundedRectangleBorder(radius=10)),
                ),
            ], spacing=12),
        ], spacing=14),
        padding=20, bgcolor=PANEL, border_radius=18, border=ft.Border.all(1, "#252B36"),
    )

    page.add(ft.Column([
        top_bar,
        ft.Column([heading, subtitle], spacing=4),
        search_card,
        landing_panel,
        quick_picks_panel,
        future_panel,
        results_panel,
        player_card,
        ft.Row([ft.Icon(ft.Icons.INFO_OUTLINE, size=14, color="#737B89"), ft.Text("Tracks are searched and streamed from YouTube using yt-dlp.", size=11, color="#737B89")], spacing=7),
    ], spacing=20, expand=True, scroll=ft.ScrollMode.AUTO, horizontal_alignment=ft.CrossAxisAlignment.STRETCH))

    initial_volume = get_current_volume()
    if initial_volume is not None:
        volume_slider.value = initial_volume
        volume_value.value = f"{int(initial_volume)}%"
    initial_speed = get_current_track_speed()
    if initial_speed is not None:
        choices = (1.0, 1.5, 2.0)
        nearest = min(choices, key=lambda choice: abs(choice - float(initial_speed)))
        speed_options.selected = [str(nearest).removesuffix(".0")]
    page.update()
    shuffle_quick_picks()
