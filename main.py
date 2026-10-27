import asyncio
import os

import flet as ft

from poc_stream import (
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
    is_paused = False
    duration_seconds = 0.0

    heading = ft.Text("Your sound, in motion.", size=30, weight=ft.FontWeight.W_700, color="#F4F6FA")
    subtitle = ft.Text("Stream a link or play a file from your device.", size=14, color=MUTED)
    status = ft.Text("READY · NOTHING PLAYING", size=11, color=ACCENT, weight=ft.FontWeight.W_700)
    track_title = ft.Text("Nothing playing yet", size=21, weight=ft.FontWeight.W_600, color="#F4F6FA", max_lines=1, overflow=ft.TextOverflow.ELLIPSIS)
    artist = ft.Text("Choose a URL or local audio file to get started", size=13, color=MUTED, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS)
    cover = ft.Container(
        width=184, height=184, border_radius=18, bgcolor="#242B37",
        alignment=ft.Alignment.CENTER,
        content=ft.Column(
            [ft.Icon(ft.Icons.GRAPHIC_EQ, size=62, color=ACCENT), ft.Text("AUDIO FLUX", size=10, color=MUTED, weight=ft.FontWeight.W_700)],
            alignment=ft.MainAxisAlignment.CENTER, horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=12,
        ),
    )

    source_input = ft.TextField(
        label="Audio URL or local file path", hint_text="https://… or C:\\Music\\track.mp3",
        prefix_icon=ft.Icons.LINK, expand=True, border_radius=12, filled=True, bgcolor=PANEL_SOFT,
        border_color="#2A303C", focused_border_color=ACCENT, on_submit=lambda e: start_playback(),
    )
    download_folder = ft.TextField(
        label="Download folder (optional)", hint_text="Leave blank to save in this project folder",
        prefix_icon=ft.Icons.FOLDER_OUTLINED, expand=True, border_radius=12, filled=True, bgcolor=PANEL_SOFT,
        border_color="#2A303C", focused_border_color=ACCENT,
    )
    elapsed = ft.Text("0:00", size=11, color=MUTED)
    total = ft.Text("0:00", size=11, color=MUTED)
    seek_slider = ft.Slider(min=0, max=100, value=0, active_color=ACCENT, inactive_color="#343B47", on_change_end=lambda e: on_seek(e))
    volume_value = ft.Text("—", size=12, color=MUTED, width=36, text_align=ft.TextAlign.RIGHT)
    volume_slider = ft.Slider(min=0, max=100, value=50, active_color=ACCENT, inactive_color="#343B47", expand=True, on_change=lambda e: on_volume_change(e))
    speed_value = ft.Text("1.00×", size=13, color=ACCENT, weight=ft.FontWeight.W_700, width=48, text_align=ft.TextAlign.RIGHT)
    speed_slider = ft.Slider(min=0.5, max=2, divisions=30, value=1, active_color=ACCENT, inactive_color="#343B47", expand=True, on_change=lambda e: on_speed_change(e))
    play_button = ft.IconButton(icon=ft.Icons.PLAY_ARROW_ROUNDED, icon_size=34, icon_color=BG, bgcolor=ACCENT, tooltip="Play / pause", on_click=lambda e: toggle_pause(e))
    pause_label = ft.Text("PLAY", size=10, color=MUTED, weight=ft.FontWeight.W_700)

    def set_status(message, color=ACCENT):
        status.value = message.upper()
        status.color = color
        page.update()

    def update_track(track_info):
        nonlocal current_source, duration_seconds, is_paused
        if not track_info or not track_info.get("audio_source"):
            set_status("Could not load this audio", "#FF8A80")
            return
        current_source = track_info["audio_source"]
        duration_seconds = 0.0
        is_paused = False
        title = os.path.basename(source_input.value.strip()) if not source_input.value.strip().startswith("http") else "Online stream"
        track_title.value = os.path.splitext(title)[0] or "Audio stream"
        artist.value = f"By {track_info.get('artist') or 'Unknown artist'}"
        image_path = track_info.get("cover")
        if image_path:
            cover.content = ft.Image(src=image_path, width=184, height=184, fit=ft.BoxFit.COVER, border_radius=18)
        status.value = "LIVE WEB STREAM" if str(track_info.get("audio_source", "")).startswith("http") else "LOCAL AUDIO FILE"
        status.color = ACCENT
        play_button.icon = ft.Icons.PAUSE_ROUNDED
        pause_label.value = "PAUSE"
        page.update()
        play_audio(current_source)
        page.run_task(poll_playback)

    def playback_worker():
        source = source_input.value.strip()
        mode = "online" if source.lower().startswith(("http://", "https://")) else "offline"
        set_status("Loading audio…", ACCENT)
        try:
            update_track(audio_player(mode, source))
        except Exception as exc:
            set_status(f"Unable to play audio: {exc}", "#FF8A80")

    def start_playback(e=None):
        if not source_input.value or not source_input.value.strip():
            set_status("Enter a URL or local file path", "#FFCC80")
            return
        set_status("Connecting…", ACCENT)
        page.run_thread(playback_worker)

    def toggle_pause(e=None):
        nonlocal is_paused
        if not current_source:
            start_playback()
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

    def stop_playback(e=None):
        nonlocal current_source, is_paused
        close_audio()
        current_source = None
        is_paused = False
        play_button.icon = ft.Icons.PLAY_ARROW_ROUNDED
        pause_label.value = "PLAY"
        track_title.value = "Nothing playing yet"
        artist.value = "Choose a URL or local audio file to get started"
        seek_slider.value = 0
        elapsed.value = "0:00"
        total.value = "0:00"
        set_status("Stopped", MUTED)

    def on_seek(e):
        if duration_seconds > 0:
            position = float(e.control.value)
            adjust_current_time(position)

    def on_volume_change(e):
        value = int(e.control.value)
        try:
            from poc_stream import player
            player.volume = value
        except Exception:
            # Keep the controls useful even if no track is loaded yet.
            pass
        volume_value.value = f"{value}%"
        page.update()

    def on_speed_change(e):
        value = round(float(e.control.value), 2)
        adjust_track_speed(value)
        speed_value.value = f"{value:.2f}×"
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

    def start_download(e=None):
        source = (source_input.value or "").strip()
        if not source:
            set_status("Enter a URL to download", "#FFCC80")
            return
        set_status("Downloading audio…", ACCENT)
        page.run_thread(download_worker, source, (download_folder.value or "").strip())

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
                speed_slider.value = speed
                speed_value.value = f"{float(speed):.2f}×"
            page.update()
            await asyncio.sleep(0.7)

    # Define these referenced functions before the controls are used at runtime.
    source_input.on_submit = start_playback

    top_bar = ft.Row(
        [ft.Row([ft.Container(width=10, height=10, border_radius=50, bgcolor=ACCENT), ft.Text("AUDIO FLUX", size=12, weight=ft.FontWeight.W_700, color="#F4F6FA")], spacing=10),
         ft.Container(content=ft.Text("PLAYER  /  01", size=10, color=MUTED, weight=ft.FontWeight.W_700), padding=ft.Padding.symmetric(horizontal=12, vertical=8), bgcolor=PANEL, border_radius=30)],
        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
    )

    player_card = ft.Container(
        content=ft.Column([
            ft.Row([cover, ft.Column([status, track_title, artist], spacing=10, expand=True)], spacing=24, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            ft.Column([ft.Row([elapsed, total], alignment=ft.MainAxisAlignment.SPACE_BETWEEN), seek_slider], spacing=0),
            ft.Row([
                ft.Container(width=42),
                ft.IconButton(icon=ft.Icons.STOP_ROUNDED, icon_color=MUTED, icon_size=25, tooltip="Stop", on_click=stop_playback),
                play_button,
                ft.Column([pause_label], alignment=ft.MainAxisAlignment.CENTER),
                ft.Container(expand=True),
                ft.Row([ft.Icon(ft.Icons.VOLUME_DOWN_ROUNDED, color=MUTED, size=18), ft.IconButton(icon=ft.Icons.REMOVE, icon_size=16, icon_color=MUTED, tooltip="Volume down", on_click=lambda e: nudge_volume(-1)), volume_slider, ft.IconButton(icon=ft.Icons.ADD, icon_size=16, icon_color=MUTED, tooltip="Volume up", on_click=lambda e: nudge_volume(1)), volume_value], spacing=2, width=290),
            ], alignment=ft.MainAxisAlignment.CENTER, vertical_alignment=ft.CrossAxisAlignment.CENTER),
        ], spacing=24),
        padding=24, bgcolor=PANEL, border_radius=22, border=ft.Border.all(1, "#252B36"),
    )

    source_card = ft.Container(
        content=ft.Column([
            ft.Row([ft.Icon(ft.Icons.ADD_LINK, color=ACCENT, size=19), ft.Text("Add audio", size=16, weight=ft.FontWeight.W_600, color="#F4F6FA")], spacing=10),
            source_input,
            download_folder,
            ft.Row([
                ft.Button(content=ft.Row([ft.Icon(ft.Icons.PLAY_ARROW, size=18), ft.Text("Play audio")], spacing=8), on_click=start_playback, style=ft.ButtonStyle(bgcolor=ACCENT, color=BG, padding=ft.Padding.symmetric(horizontal=18, vertical=14), shape=ft.RoundedRectangleBorder(radius=10))),
                ft.OutlinedButton(content=ft.Row([ft.Icon(ft.Icons.DOWNLOAD_OUTLINED, size=18), ft.Text("Download")], spacing=8), on_click=start_download, style=ft.ButtonStyle(color="#E5E8EF", padding=ft.Padding.symmetric(horizontal=18, vertical=14), shape=ft.RoundedRectangleBorder(radius=10))),
            ], spacing=10),
        ], spacing=14),
        padding=20, bgcolor=PANEL, border_radius=18, border=ft.Border.all(1, "#252B36"),
    )

    settings_card = ft.Container(
        content=ft.Column([
            ft.Row([ft.Icon(ft.Icons.TUNE_ROUNDED, color=ACCENT, size=18), ft.Text("Fine-tune", size=15, weight=ft.FontWeight.W_600, color="#F4F6FA")], spacing=9),
            ft.Row([ft.Text("Playback speed", size=12, color=MUTED), speed_slider, speed_value], spacing=12),
            ft.Text("0.5× slower                                  2× faster", size=10, color="#737B89"),
        ], spacing=10),
        padding=18, bgcolor=PANEL, border_radius=18, border=ft.Border.all(1, "#252B36"),
    )

    page.add(ft.Column([
        top_bar,
        ft.Column([heading, subtitle], spacing=4),
        source_card,
        player_card,
        settings_card,
        ft.Row([ft.Icon(ft.Icons.INFO_OUTLINE, size=14, color="#737B89"), ft.Text("Local files stay on your device. Stream URLs are resolved by yt-dlp.", size=11, color="#737B89")], spacing=7),
    ], spacing=20, expand=True, scroll=ft.ScrollMode.AUTO, horizontal_alignment=ft.CrossAxisAlignment.STRETCH))

    initial_volume = get_current_volume()
    if initial_volume is not None:
        volume_slider.value = initial_volume
        volume_value.value = f"{int(initial_volume)}%"
    initial_speed = get_current_track_speed()
    if initial_speed is not None:
        speed_slider.value = initial_speed
        speed_value.value = f"{float(initial_speed):.2f}×"
    page.update()


if __name__ == "__main__":
    ft.run(main)
