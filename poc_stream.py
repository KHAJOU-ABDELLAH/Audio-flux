import yt_dlp
import mpv
import os

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
def download_audio(video_url, path=None):
    if path is None:
        ydl_opts = {'format': 'bestaudio'}
    else:
        if not os.path.exists(path): 
            os.makedirs(path) 
        output_template = os.path.join(path, '%(title)s.%(ext)s')
        ydl_opts = {
            'quiet': True,
            'noplaylist': True,
            'format': 'bestaudio',
            'outtmpl': output_template}
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([video_url])



                        

            
                
