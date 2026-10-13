import yt_dlp
import mpv
import os
from mutagen import File

player = mpv.MPV(ytdl=False) 

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