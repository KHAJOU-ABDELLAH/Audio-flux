import yt_dlp
import mpv

video_url = "https://youtu.be/62TrmUvQGjo?si=Fa-c-y-Xj3jxu9QN"

ydl_opts = {
    'format': 'bestaudio/best',
    'quiet': True,
}

with yt_dlp.YoutubeDL(ydl_opts) as ydl:
    info_dict = ydl.extract_info(video_url, download=False)

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