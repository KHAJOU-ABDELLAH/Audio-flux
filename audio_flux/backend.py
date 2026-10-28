import json
import os
import re
import shutil
import time
from difflib import SequenceMatcher
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode, quote
from urllib.request import Request, urlopen

import mpv
import yt_dlp
from mutagen import File

try:
    player = mpv.MPV(ytdl=False)
    player.volume = 50
except Exception as exc:
    raise RuntimeError(
        "Audio Flux needs libmpv. Install mpv and make sure it is available on PATH."
    ) from exc


def resolve_ffmpeg_location():
    """Return an ffmpeg directory yt-dlp can use, or None to search PATH."""
    explicit = os.environ.get("AUDIO_FLUX_FFMPEG")
    if explicit:
        path = Path(explicit)
        if path.is_file():
            return str(path.parent)
        if path.is_dir():
            return str(path)
    found = shutil.which("ffmpeg")
    return str(Path(found).parent) if found else None


def looks_like_youtube_url(query):
    lowered = (query or "").strip().lower()
    return any(token in lowered for token in ("youtube.com/", "youtu.be/", "music.youtube.com/"))


def _entry_to_track(entry):
    if not entry:
        return None
    video_id = entry.get("id")
    video_url = entry.get("webpage_url") or entry.get("original_url")
    if not video_url:
        candidate = entry.get("url")
        video_url = candidate if candidate and str(candidate).startswith("http") else None
    if not video_url and video_id and entry.get("ie_key") in (None, "Youtube"):
        video_url = f"https://www.youtube.com/watch?v={video_id}"
    if not video_url:
        return None

    thumbnails = entry.get("thumbnails") or []
    best_thumbnail = max(
        thumbnails,
        key=lambda image: (image.get("width") or 0) * (image.get("height") or 0),
        default={},
    )
    return {
        "id": video_id or video_url,
        "title": entry.get("title") or "Untitled video",
        "artist": entry.get("artist") or entry.get("creator") or entry.get("uploader") or "Unknown artist",
        "thumbnail": best_thumbnail.get("url") or entry.get("thumbnail"),
        "duration": entry.get("duration"),
        "url": video_url,
    }


def _tracks_from_info(info, limit=None):
    if not info:
        return []
    entries = info.get("entries")
    if entries is None:
        track = _entry_to_track(info)
        return [track] if track else []
    tracks = []
    for entry in entries:
        track = _entry_to_track(entry)
        if track:
            tracks.append(track)
        if limit and len(tracks) >= limit:
            break
    return tracks


def search_youtube(query, limit=24):
    """Return YouTube matches, including a pasted watch or playlist URL."""
    query = (query or "").strip()
    if not query:
        return []

    ydl_opts = {
        "quiet": True,
        "skip_download": True,
        "extract_flat": "in_playlist",
        "noplaylist": not (looks_like_youtube_url(query) and "/playlist" in query.lower()),
    }
    target = query if looks_like_youtube_url(query) else f"ytsearch{limit}:{query}"
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        search_info = ydl.extract_info(target, download=False)
    return _tracks_from_info(search_info, limit=limit)


def _youtube_music_lyrics(video_id, track_name, artist_name):
    """Return YouTube Music lyrics, preserving timestamps where available."""
    try:
        from ytmusicapi import YTMusic
    except ImportError:
        return None

    try:
        client = YTMusic()
    except Exception:
        return None
    candidates = []
    seen = set()
    if video_id:
        candidates.append(video_id)
        seen.add(video_id)

    queries = list(dict.fromkeys(
        query for query in (
            " ".join(part for part in (artist_name, track_name) if part).strip(),
            track_name,
        ) if query
    ))
    wanted_title = re.sub(r"[^a-z0-9]+", " ", track_name.lower()).strip()
    for query in queries:
        try:
            results = client.search(query, filter="songs", limit=5)
        except Exception:
            continue
        if not isinstance(results, list):
            continue
        for result in results:
            try:
                candidate_id = result.get("videoId")
                if not candidate_id or candidate_id in seen:
                    continue
                result_title = re.sub(r"[^a-z0-9]+", " ", (result.get("title") or "").lower()).strip()
                title_match = SequenceMatcher(None, wanted_title, result_title).ratio()
                wanted_words = set(wanted_title.split())
                overlap = len(wanted_words.intersection(result_title.split())) / max(1, len(wanted_words))
                if wanted_title and title_match < 0.48 and overlap < 0.6:
                    continue
                candidates.append(candidate_id)
                seen.add(candidate_id)
            except (AttributeError, TypeError):
                continue

    plain_fallback = None
    for candidate_id in candidates:
        try:
            watch = client.get_watch_playlist(videoId=candidate_id, limit=1)
            browse_id = watch.get("lyrics") if isinstance(watch, dict) else None
            if not browse_id:
                continue
            lyrics = client.get_lyrics(browse_id, timestamps=True)
            if not lyrics:
                continue
            if lyrics.get("hasTimestamps"):
                lines = lyrics.get("lyrics") or []
                synced = []
                for line in lines:
                    start_ms = line.get("start_time") if isinstance(line, dict) else getattr(line, "start_time", None)
                    line_text = line.get("text") if isinstance(line, dict) else getattr(line, "text", None)
                    if start_ms is None or not line_text:
                        continue
                    centiseconds = max(0, int(start_ms)) // 10
                    minutes, remainder = divmod(centiseconds, 6000)
                    seconds, fraction = divmod(remainder, 100)
                    synced.append(f"[{minutes:02d}:{seconds:02d}.{fraction:02d}]{line_text.strip()}")
                if synced:
                    return {
                        "syncedLyrics": "\n".join(synced),
                        "plainLyrics": None,
                        "source": lyrics.get("source") or "YouTube Music",
                    }
            else:
                text = lyrics.get("lyrics")
                if isinstance(text, str) and text.strip() and plain_fallback is None:
                    plain_fallback = {
                        "plainLyrics": text.strip(),
                        "syncedLyrics": None,
                        "source": lyrics.get("source") or "YouTube Music",
                    }
        except Exception:
            continue
    return plain_fallback


def get_track_lyrics(track_name, artist_name=None, duration=None, video_id=None):
    """Try timed sources first, then broaden coverage with plain-text lyrics."""
    track_name = (track_name or "").strip()
    artist_name = (artist_name or "").strip()
    if not track_name:
        return None

    youtube_record = _youtube_music_lyrics(video_id, track_name, artist_name)
    if youtube_record and youtube_record.get("syncedLyrics"):
        return youtube_record

    headers = {"User-Agent": "AudioFlux/1.0 (https://github.com/KHAJOU-ABDELLAH/Audio-flux)"}

    class ServiceBusy(RuntimeError):
        def __init__(self, status, retry_after):
            super().__init__(f"Lyrics service returned HTTP {status}")
            self.retry_after = retry_after

    def wait_before_retry(value):
        if not value:
            time.sleep(1)
            return
        try:
            delay = max(0.0, float(value))
        except ValueError:
            try:
                retry_at = parsedate_to_datetime(value)
                if retry_at.tzinfo is None:
                    retry_at = retry_at.replace(tzinfo=timezone.utc)
                delay = max(0.0, (retry_at - datetime.now(timezone.utc)).total_seconds())
            except (TypeError, ValueError, OverflowError):
                delay = 1.0
        time.sleep(delay)

    def fetch_json(url, timeout=10):
        request = Request(url, headers=headers)
        try:
            with urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            if exc.code == 404:
                return None
            if exc.code == 429:
                retry_after = exc.headers.get("Retry-After", "a little while")
                raise RuntimeError(f"Lyrics service rate limit; retry after {retry_after} seconds") from exc
            if 500 <= exc.code < 600:
                raise ServiceBusy(exc.code, exc.headers.get("Retry-After")) from exc
            raise RuntimeError(f"Lyrics service returned HTTP {exc.code}") from exc

    def fetch_with_retry(url):
        for attempt in range(2):
            try:
                return fetch_json(url)
            except ServiceBusy as exc:
                # Honor the server's Retry-After before retrying or falling back.
                wait_before_retry(exc.retry_after)
                if attempt == 1:
                    raise

    record = None
    lrclib_unavailable = False
    if artist_name:
        exact_params = {"track_name": track_name, "artist_name": artist_name}
        if duration and 1 <= float(duration) <= 3600:
            exact_params["duration"] = round(float(duration))
        exact_url = f"https://lrclib.net/api/get?{urlencode(exact_params)}"
        try:
            record = fetch_with_retry(exact_url)
        except ServiceBusy:
            lrclib_unavailable = True
            record = None
        except Exception:
            if youtube_record:
                return youtube_record
            record = None
    if record and record.get("syncedLyrics"):
        record["source"] = "LRCLIB"
        return record

    search_params = {"track_name": track_name}
    if artist_name:
        search_params["artist_name"] = artist_name
    if artist_name:
        time.sleep(0.25)
    try:
        matches = fetch_with_retry(f"https://lrclib.net/api/search?{urlencode(search_params)}") or []
    except Exception as exc:
        lrclib_unavailable = True
        matches = []
    if not isinstance(matches, list):
        return None
    for match in matches:
        if match.get("syncedLyrics"):
            match["source"] = "LRCLIB"
            return match
    if record and record.get("plainLyrics"):
        record["source"] = "LRCLIB"
        return record
    if isinstance(matches, list):
        for match in matches:
            if match.get("plainLyrics"):
                match["source"] = "LRCLIB"
                return match

    # LRCLIB specializes in timestamped community lyrics; Lyrics.ovh can
    # provide a plain-text fallback for tracks absent from its catalog.
    def fetch_lyrics_ovh(artist, title):
        if not artist or not title:
            return None
        lyrics_url = (
            f"https://api.lyrics.ovh/v1/{quote(artist, safe='')}/"
            f"{quote(title, safe='')}"
        )
        try:
            lyrics_record = fetch_json(lyrics_url, timeout=5)
        except Exception:
            return None
        if isinstance(lyrics_record, dict) and lyrics_record.get("lyrics", "").strip():
            return {
                "syncedLyrics": None,
                "plainLyrics": lyrics_record["lyrics"].strip(),
                "source": "Lyrics.ovh",
            }
        return None

    if artist_name and artist_name.casefold() not in {"unknown artist", "downloaded audio"}:
        lyrics_ovh_record = fetch_lyrics_ovh(artist_name, track_name)
        if lyrics_ovh_record:
            return lyrics_ovh_record

    # Resolve uploader-style YouTube artist names through the service's Deezer
    # suggestions, then retry its lyrics endpoint with canonical metadata.
    try:
        search_query = " ".join(part for part in (artist_name, track_name) if part)
        suggestions = fetch_json(
            f"https://api.lyrics.ovh/suggest/{quote(search_query, safe='')}", timeout=5
        )
    except Exception:
        suggestions = None
    candidates = suggestions.get("data", []) if isinstance(suggestions, dict) else []
    seen_lyrics_queries = set()
    for candidate in candidates[:3] if isinstance(candidates, list) else []:
        if not isinstance(candidate, dict):
            continue
        suggested_title = (candidate.get("title") or "").strip()
        artist_data = candidate.get("artist")
        suggested_artist = (
            str(artist_data.get("name") or "").strip() if isinstance(artist_data, dict) else ""
        )
        query_key = (suggested_artist.casefold(), suggested_title.casefold())
        if not suggested_title or not suggested_artist or query_key in seen_lyrics_queries:
            continue
        seen_lyrics_queries.add(query_key)
        lyrics_ovh_record = fetch_lyrics_ovh(suggested_artist, suggested_title)
        if lyrics_ovh_record:
            return lyrics_ovh_record

    if youtube_record:
        return youtube_record
    if lrclib_unavailable:
        raise RuntimeError("Lyrics sources are temporarily unavailable. Try again shortly.")
    return None


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

    return chosen_artist, direct_audio_url, image_url

def download_audio(video_url, path=None):
    if path and not os.path.exists(path):
        os.makedirs(path)

    output_template = os.path.join(path or '.', '%(title)s.%(ext)s')
    ydl_opts = {
        'quiet': True,
        'noplaylist': True,
        'format': 'm4a/bestaudio/best',
        'outtmpl': output_template,
    }
    ffmpeg_dir = resolve_ffmpeg_location()
    if ffmpeg_dir:
        ydl_opts['ffmpeg_location'] = ffmpeg_dir
        ydl_opts['writethumbnail'] = True
        ydl_opts['postprocessors'] = [
            {'key': 'FFmpegMetadata'},
            {'key': 'EmbedThumbnail'},
        ]
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
            if filename.lower().endswith((".mp3", ".m4a", ".mp4", ".webm", ".opus", ".wav", ".flac", ".aac", ".ogg")):
                full_path = os.path.join(folder, filename)
                audio_files.append((filename, full_path))
    return sorted(audio_files, key=lambda item: item[0].lower())
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

def set_repeat_mode(enabled):
    try:
        player.loop_file = "inf" if enabled else "no"
    except Exception as e:
        print(f"Error changing repeat mode: {e}")
