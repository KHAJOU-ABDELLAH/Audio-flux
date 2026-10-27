import yt_dlp
import mpv
import os
from mutagen import File

player = mpv.MPV(ytdl=False)

def search_youtube(query, limit=12):
    """Return YouTube search matches with the metadata needed by the UI."""
    query = (query or "").strip()
    if not query:
        return []

    ydl_opts = {
        "quiet": True,
        "skip_download": True,
        "extract_flat": "in_playlist",
        "noplaylist": True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        search_info = ydl.extract_info(f"ytsearch{limit}:{query}", download=False)

    tracks = []
    for entry in (search_info or {}).get("entries", []):
        if not entry:
            continue
        video_id = entry.get("id")
        video_url = entry.get("webpage_url") or entry.get("original_url")
        if not video_url:
            candidate = entry.get("url")
            video_url = candidate if candidate and str(candidate).startswith("http") else None
        if not video_url and video_id:
            video_url = f"https://www.youtube.com/watch?v={video_id}"
        if not video_url:
            continue

        thumbnails = entry.get("thumbnails") or []
        best_thumbnail = max(
            thumbnails,
            key=lambda image: (image.get("width") or 0) * (image.get("height") or 0),
            default={},
        )
        thumbnail = best_thumbnail.get("url") or entry.get("thumbnail")
        tracks.append({
            "id": video_id or video_url,
            "title": entry.get("title") or "Untitled video",
            "artist": entry.get("artist") or entry.get("creator") or entry.get("uploader") or "Unknown artist",
            "thumbnail": thumbnail,
            "duration": entry.get("duration"),
            "url": video_url,
        })
    return tracks


def stream_audio_from_youtube(video_url):
    ydl_opts = {
        'format': 'bestaudio/best',
        'quiet': True,
        'noplaylist': True,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info_dict = ydl.extract_info(video_url, download=False)
        if not info_dict:
            return "Unknown Artist", None, "placeholder.png"

        direct_audio_url = info_dict.get('url')
        chosen_artist = info_dict.get('artist') or info_dict.get('uploader') or "Unknown Artist"

        image_url = info_dict.get('thumbnail') or "placeholder.png"
        thumbnail_list = info_dict.get('thumbnails')
        if thumbnail_list:
            best_thumbnail = thumbnail_list[-1]
            image_url = best_thumbnail.get('url') or image_url

        print(f"Direct Audio Link:\n{direct_audio_url}")

    return chosen_artist, direct_audio_url, image_url

def download_audio(video_url, path=None):
    if path and not os.path.exists(path):
        os.makedirs(path)

    output_template = os.path.join(path or '.', '%(title)s.%(ext)s')
    ydl_opts = {
        'quiet': True,
        'noplaylist': True,
        # Changed from 'bestaudio' to prefer m4a (supports thumbnails)
        'format': 'm4a/bestaudio/best',
        'outtmpl': output_template,
        'writethumbnail': True,
        'ffmpeg_location': r"C:\Users\Abdellah\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-9.0.2-full_build\bin",
        'postprocessors': [
            {'key': 'FFmpegMetadata'},
            {'key': 'EmbedThumbnail'}
        ]
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([video_url])

def audio_player(mode, source_path_or_url):
    chosen_artist = "Unknown Artist"
    image_target = "placeholder.png"

    if mode == "offline":
        try:
            audio = File(source_path_or_url)
            if audio is not None:
                for key in audio.keys():
                    if "artist" in key.lower() or "tpe1" in key.lower():
                        val = audio[key]
                        if hasattr(val, 'text'):
                            chosen_artist = str(val.text[0])
                        elif isinstance(val, list):
                            chosen_artist = str(val[0])
                        else:
                            chosen_artist = str(val)
                        break

                raw_image_bytes = None
                if 'covr' in audio:
                    raw_image_bytes = bytes(audio['covr'][0])
                else:
                    for key in audio.keys():
                        if key.startswith("APIC"):
                            raw_image_bytes = audio[key].data
                            break

                if raw_image_bytes:
                    folder = os.path.dirname(os.path.abspath(source_path_or_url))
                    image_target = os.path.join(folder, "temp_cover.jpg")
                    with open(image_target, "wb") as img_file:
                        img_file.write(raw_image_bytes)
        except Exception as e:
            print(f"Error reading offline file metadata: {e}")

        return {
            "artist": chosen_artist,
            "cover": image_target,
            "audio_source": source_path_or_url
        }

    elif mode == "online":
        try:
            artist_val, url_val, img_val = stream_audio_from_youtube(source_path_or_url)
            return {
                "artist": artist_val,
                "cover": img_val,
                "audio_source": url_val
            }
        except Exception as e:
            print(f"Error streaming audio: {e}")
            return None

def play_audio(audio_source):
    if not audio_source:
        print("Error: No audio source provided.")
        return
    try:
        player.play(audio_source)
    except Exception as e:
        print(f"Error playing audio: {e}")

def scan_download_folder(path):
    folder = path.strip() if path and path.strip() else os.getcwd()

    audio_files = []
    if os.path.exists(folder):
        for filename in os.listdir(folder):
            if filename.endswith((".mp3", ".m4a", ".mp4", ".webm", ".opus")):
                full_path = os.path.join(folder, filename)
                audio_files.append((filename, full_path))
    return audio_files
def pause_audio():
    try:
        player.pause = True
    except Exception as e:
        print(f"Error pausing audio: {e}")
def resume_audio():
    try:
        player.pause = False
    except Exception as e:
        print(f"Error resuming audio: {e}")
def close_audio():
    try:
        player.stop()
    except Exception as e:
        print(f"Error stopping audio: {e}")
def volume_up():
    try:
        if player.volume <= 95:
            player.volume += 5
    except Exception as e:
        print(f"Error increasing volume: {e}")
def volume_down():
    try:
        if player.volume >= 5:
            player.volume -= 5
    except Exception as e:
        print(f"Error decreasing volume: {e}")
def get_current_volume():
    try:
        return player.volume
    except Exception as e:
        print(f"Error getting current volume: {e}")
        return None
def current_time():
    try:
        return player.time_pos
    except Exception as e:
        print(f"Error getting current time: {e}")
        return None
def adjust_current_time(seconds):
    try:
        if player.time_pos is not None:
            player.time_pos = seconds
    except Exception as e:
        print(f"Error adjusting current time: {e}")
def get_audio_duration():
    try:
        return player.duration
    except Exception as e:
        print(f"Error getting audio duration: {e}")
        return None
def get_current_track_speed():
    try:
        return player.speed
    except Exception as e:
        print(f"Error getting current track speed: {e}")
        return None
def adjust_track_speed(speed):
    try:
        player.speed = speed
    except Exception as e:
        print(f"Error adjusting track speed: {e}")
