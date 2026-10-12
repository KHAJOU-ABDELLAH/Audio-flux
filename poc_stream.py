import yt_dlp
import mpv

def stream_audio_from_youtube(video_url):
    video = video_url
    ydl_opts = {
        'format': 'bestaudio/best',
        'quiet': True,
        'noplaylist': True,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info_dict = ydl.extract_info(video, download=False)

        direct_audio_url = info_dict.get('url')

        audio_extension = info_dict.get('ext')
        audio_bitrate = info_dict.get('abr')

        print(f"Format Extension: {audio_extension}")
        print(f"Bitrate: {audio_bitrate} kbps")
        print(f"\nDirect Audio Link:\n{direct_audio_url}")
        if direct_audio_url:
            print("Streaming audio via mpv...")
            player = mpv.MPV()
            player.play(direct_audio_url)
            player.wait_for_playback()
        else:
            print("Failed to retrieve direct audio link.")
