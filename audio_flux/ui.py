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
    current_view = "home"
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

    heading = ft.Text("Find your next favorite.", size=30, weight=ft.FontWeight.W_700, color="#F4F6FA")
    subtitle = ft.Text("Search YouTube and play it straight from your library.", size=14, color=MUTED)
    status = ft.Text("READY · NOTHING PLAYING", size=11, color=ACCENT, weight=ft.FontWeight.W_700)
    track_title = ft.Text("Nothing playing yet", size=21, weight=ft.FontWeight.W_600, color="#F4F6FA", max_lines=1, overflow=ft.TextOverflow.ELLIPSIS)
    artist = ft.Text("Search for a track to get started", size=13, color=MUTED, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS)
    cover = ft.Container(
        width=220, height=220, border_radius=18, bgcolor="#242B37",
        alignment=ft.Alignment.CENTER,
        content=ft.Column(
            [ft.Icon(ft.Icons.GRAPHIC_EQ, size=62, color=ACCENT), ft.Text("AUDIO FLUX", size=10, color=MUTED, weight=ft.FontWeight.W_700)],
            alignment=ft.MainAxisAlignment.CENTER, horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=12,
        ),
    )

    search_input = ft.TextField(
        label="Search songs, artists, or albums", hint_text="Try a song title or artist name  ·  Ctrl + K",
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
        if image_path and image_path != "placeholder.png":
            cover.content = ft.Image(src=image_path, width=220, height=220, fit=ft.BoxFit.COVER, border_radius=18)
            player_card.image = ft.DecorationImage(src=image_path, fit=ft.BoxFit.COVER, opacity=0.13)
            palette = ("#344D38", "#39405A", "#573B47", "#34505B", "#514831", "#4D3B59")
            glow = palette[sum(ord(char) for char in str(track.get("id") or track.get("title"))) % len(palette)]
            player_card.shadow = ft.BoxShadow(blur_radius=30, spread_radius=2, color=glow)
        else:
            cover.content = ft.Icon(ft.Icons.GRAPHIC_EQ, size=62, color=ACCENT)
            player_card.image = None
        status.value = "NOW PLAYING · DOWNLOADS" if track.get("mode") == "offline" else "NOW PLAYING · YOUTUBE"
        status.color = ACCENT
        play_button.icon = ft.Icons.PAUSE_ROUNDED
        pause_label.value = "PAUSE"
        page.update()
        play_audio(current_source)
        page.run_task(poll_playback)

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

    def shuffle_play():
        if playback_queue:
            candidates = playback_queue
        elif current_view == "downloads":
            candidates = downloaded_tracks
        elif current_view == "favorites":
            candidates = favorites
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
        try:
            favorites_file.parent.mkdir(parents=True, exist_ok=True)
            favorites_file.write_text(json.dumps(favorites, indent=2), encoding="utf-8")
        except OSError as exc:
            set_status(f"Couldn't save favorite: {exc}", "#FF8A80")
            return
        if favorites_list is not None:
            refresh_favorites()
        if results_panel.visible:
            results_list.content = render_track_collection(loaded_search_tracks, search_view_mode)
        if quick_pick_tracks:
            quick_pick_list.content = render_track_collection(quick_pick_tracks, quick_view_mode)
        page.update()

    def add_to_queue(track):
        playback_queue.append(track)
        set_status(f"Added to queue · {track.get('title') or 'Track'}", ACCENT)

    def result_tile(track, grid=False):
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
        queue_action = ft.IconButton(
            icon=ft.Icons.QUEUE_MUSIC_ROUNDED, icon_color=MUTED, tooltip="Add to queue",
            on_click=lambda e, item=track: add_to_queue(item),
        )
        download_action = ft.IconButton(
            icon=ft.Icons.DOWNLOAD_OUTLINED, icon_color=MUTED, tooltip="Download",
            on_click=lambda e, item=track: start_download(item),
        )
        actions = ft.Row([favorite_button, queue_action, download_action], spacing=0, visible=grid)
        if track.get("mode") == "offline":
            actions.controls = [favorite_button, queue_action]
        if grid:
            content = ft.Column([
                artwork,
                ft.Text(track.get("title") or "Untitled", size=13, color="#F4F6FA", weight=ft.FontWeight.W_600, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                ft.Text(track.get("artist") or "Unknown artist", size=11, color=MUTED, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                ft.Row([ft.Text(duration, size=10, color=MUTED, expand=True), play_action], spacing=2),
                actions,
            ], spacing=7)
        else:
            content = ft.Row([
                artwork,
                ft.Column([
                    ft.Text(track.get("title") or "Untitled", size=13, weight=ft.FontWeight.W_600, color="#F4F6FA", max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                    ft.Text(track.get("artist") or "Unknown artist", size=11, color=MUTED, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
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
        )

        def handle_hover(e):
            hovered = str(e.data).lower() == "true"
            actions.visible = grid or hovered
            tile.bgcolor = "#202832" if hovered else PANEL_SOFT
            tile.border = ft.Border.all(1, "#3D5441" if hovered else "#252B36")
            page.update()

        tile.on_hover = handle_hover
        return tile

    def render_track_collection(tracks, mode="list"):
        tiles = [result_tile(track, grid=(mode == "grid")) for track in tracks]
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
        future_panel.visible = False
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
        landing_panel.visible = not hero_dismissed
        quick_picks_panel.visible = True
        future_panel.visible = True
        results_panel.visible = False
        seek_slider.value = 0
        elapsed.value = "0:00"
        total.value = "0:00"
        status.value = "STOPPED · READY WHEN YOU ARE"
        status.color = MUTED
        page.update()
        if landing_panel.visible:
            page.run_task(animate_waveform)

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

    def on_seek_hover(e):
        height = 30 if str(e.data).lower() == "true" else 24
        seek_slider.height = height
        seek_slider_container.height = height
        page.update()

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
            if current_view == "downloads":
                refresh_downloads()
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
        if current_view == "downloads":
            refresh_downloads()

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

    home_nav = ft.TextButton(
        "Home", icon=ft.Icons.HOME_ROUNDED, on_click=lambda e: switch_view("home"),
    )
    downloads_nav = ft.TextButton(
        "Downloads", icon=ft.Icons.DOWNLOAD_OUTLINED, on_click=lambda e: switch_view("downloads"),
    )
    favorites_nav = ft.TextButton(
        "Favorites", icon=ft.Icons.FAVORITE_BORDER_ROUNDED, on_click=lambda e: switch_view("favorites"),
    )
    settings_nav = ft.TextButton(
        "Settings", icon=ft.Icons.SETTINGS_OUTLINED, on_click=open_settings,
    )
    top_bar = ft.Row(
        [
            ft.Row([
                ft.Container(width=10, height=10, border_radius=50, bgcolor=ACCENT),
                ft.Text("AUDIO FLUX", size=12, weight=ft.FontWeight.W_700, color="#F4F6FA"),
            ], spacing=10),
            ft.Row([
                home_nav,
                downloads_nav,
                favorites_nav,
                settings_nav,
            ], spacing=4),
        ],
        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
    )

    player_card = ft.Container(
        content=ft.Column([
            ft.Row([cover, ft.Column([status, track_title, artist], spacing=10, expand=True)], spacing=24, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            ft.Column([ft.Row([elapsed, total], alignment=ft.MainAxisAlignment.SPACE_BETWEEN), seek_slider_container], spacing=0),
            ft.Row([
                shuffle_button,
                ft.IconButton(icon=ft.Icons.STOP_ROUNDED, icon_color=MUTED, icon_size=25, tooltip="Stop", on_click=stop_playback),
                play_button,
                repeat_button,
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

    home_view = ft.Column([
        ft.Column([heading, subtitle], spacing=4),
        search_card,
        landing_panel,
        quick_picks_panel,
        future_panel,
        results_panel,
        ft.Row([ft.Icon(ft.Icons.INFO_OUTLINE, size=14, color="#737B89"), ft.Text("Tracks are searched and streamed from YouTube using yt-dlp.", size=11, color="#737B89")], spacing=7),
    ], spacing=20, expand=True, scroll=ft.ScrollMode.AUTO, horizontal_alignment=ft.CrossAxisAlignment.STRETCH)

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

    downloads_view = ft.Column([
        ft.Row([
            ft.IconButton(icon=ft.Icons.ARROW_BACK_ROUNDED, icon_color=MUTED, tooltip="Back to Home", on_click=lambda e: switch_view("home")),
            ft.Column([
                ft.Text("Your Downloads", size=28, color="#F4F6FA", weight=ft.FontWeight.W_700),
                ft.Text("Play tracks saved on this device.", size=13, color=MUTED),
            ], spacing=4, expand=True),
            ft.OutlinedButton("Refresh", icon=ft.Icons.REFRESH_ROUNDED, on_click=refresh_downloads),
        ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
        ft.Container(
            content=ft.Row([ft.Icon(ft.Icons.FOLDER_OUTLINED, size=16, color=ACCENT), downloads_folder_label], spacing=9),
            padding=ft.Padding.symmetric(horizontal=13, vertical=10), bgcolor=PANEL, border_radius=11,
        ),
        downloads_summary,
        downloads_list,
    ], spacing=16, expand=True, scroll=ft.ScrollMode.AUTO, horizontal_alignment=ft.CrossAxisAlignment.STRETCH)

    favorites_summary = ft.Text("", size=12, color=MUTED)
    favorites_list = ft.Container()
    favorites_view_toggle = ft.SegmentedButton(
        segments=[ft.Segment(value="list", icon=ft.Icons.VIEW_LIST_ROUNDED), ft.Segment(value="grid", icon=ft.Icons.GRID_VIEW_ROUNDED)],
        selected=[favorites_view_mode], allow_empty_selection=False, show_selected_icon=False,
        on_change=lambda e: set_favorites_view(e.control.selected[0]),
        tooltip="Change Favorites layout",
    )

    def set_favorites_view(mode):
        nonlocal favorites_view_mode
        favorites_view_mode = mode
        refresh_favorites()
        page.update()

    def refresh_favorites():
        favorites_summary.value = f"{len(favorites)} saved tracks" if favorites else "Your personal collection"
        if favorites:
            favorites_list.content = render_track_collection(favorites, favorites_view_mode)
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

    favorites_view = ft.Column([
        ft.Row([
            ft.IconButton(icon=ft.Icons.ARROW_BACK_ROUNDED, icon_color=MUTED, tooltip="Back to Home", on_click=lambda e: switch_view("home")),
            ft.Column([
                ft.Text("Favorites", size=28, color="#F4F6FA", weight=ft.FontWeight.W_700),
                ft.Text("The tracks you want to come back to.", size=13, color=MUTED),
            ], spacing=4, expand=True),
            favorites_view_toggle,
        ], alignment=ft.MainAxisAlignment.START),
        favorites_summary,
        favorites_list,
    ], spacing=16, expand=True, scroll=ft.ScrollMode.AUTO, horizontal_alignment=ft.CrossAxisAlignment.STRETCH)

    main_content = ft.Container(content=home_view, expand=True)

    def switch_view(view_name):
        nonlocal current_view
        current_view = view_name
        if view_name == "downloads":
            main_content.content = downloads_view
            downloads_nav.style = ft.ButtonStyle(color=ACCENT)
            home_nav.style = ft.ButtonStyle(color=MUTED)
            favorites_nav.style = ft.ButtonStyle(color=MUTED)
            refresh_downloads()
        elif view_name == "favorites":
            main_content.content = favorites_view
            favorites_nav.style = ft.ButtonStyle(color=ACCENT)
            home_nav.style = ft.ButtonStyle(color=MUTED)
            downloads_nav.style = ft.ButtonStyle(color=MUTED)
            refresh_favorites()
        else:
            main_content.content = home_view
            home_nav.style = ft.ButtonStyle(color=ACCENT)
            downloads_nav.style = ft.ButtonStyle(color=MUTED)
            favorites_nav.style = ft.ButtonStyle(color=MUTED)
        page.update()

    home_nav.style = ft.ButtonStyle(color=ACCENT)
    downloads_nav.style = ft.ButtonStyle(color=MUTED)
    favorites_nav.style = ft.ButtonStyle(color=MUTED)
    page.add(ft.Column([top_bar, player_card, main_content], spacing=16, expand=True))

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
    initial_speed = get_current_track_speed()
    if initial_speed is not None:
        choices = (1.0, 1.5, 2.0)
        nearest = min(choices, key=lambda choice: abs(choice - float(initial_speed)))
        speed_options.selected = [str(nearest).removesuffix(".0")]
    page.update()
    shuffle_quick_picks()
    page.run_task(animate_waveform)
