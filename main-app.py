import flet as ft
import threading 
import os

# Import your finished backend components
from poc_stream import download_audio, audio_player, play_audio

def main(page: ft.Page):
  page.title = "My First App"
  page.theme_mode = ft.ThemeMode.DARK

  # 1. Inputs
  url_input = ft.TextField(label="URL or Local Path", width=400, autofocus=True)
  path_input = ft.TextField(label="Download Path (Optional)", width=400)

  # Added your new visual controls right here
  album_art = ft.Image(src="placeholder.png", width=200, height=200, border_radius=10)
  artist_label = ft.Text("Artist: No Track Loaded", size=16, weight=ft.FontWeight.BOLD)
  status_label = ft.Text("Source: Idle", size=14, color="grey")

  # Thread-safe UI update and track checking function
  def update_player_ui(track_info):
      artist_label.value = f"Artist: {track_info['artist']}"
      album_art.src = track_info['cover']
      
      # Track checking state logic: evaluates if it's streamed or local
      if track_info.get('audio_source') and track_info['audio_source'].startswith("http"):
          status_label.value = "Source: Live Web Stream"
          status_label.color = "blue"
      else:
          status_label.value = "Source: Offline Local File"
          status_label.color = "green"
          
      page.update()

  # 2. Handlers
  def handle_submit(e):
    user_url = url_input.value.strip()
    if user_url:
      print(f"URL/Path submitted: {user_url}")
      # Fixed to use mode checking and your new audio_player manager function
      mode = "online" if user_url.startswith("http") else "offline"
      
      def playback_worker():
          track_info = audio_player(mode, user_url)
          if track_info and track_info.get('audio_source'):
              update_player_ui(track_info)
              play_audio(track_info['audio_source'])
              
      threading.Thread(target=playback_worker, daemon=True).start()
    else:
      print("The URL field is empty!")

  def handle_download(e):
    user_url = url_input.value.strip()
    user_path = path_input.value.strip()
    
    if user_url:
        print(f"Downloading audio from: {user_url}")
        actual_path = user_path if user_path else None
        threading.Thread(target=download_audio, args=(user_url, actual_path), daemon=True).start()
    else:
        print("Provide a URL to download!")
  
  # 3. Layout (Your exact layout format)
  page.add(
      ft.Text("Enter your media details below:", size=15, color="grey"),
      url_input,
      path_input,
      ft.Row([
          ft.Button(content=ft.Text("Stream/Play Audio"), on_click=handle_submit),
          ft.Button(content=ft.Text("Download Audio"), on_click=handle_download)
      ]),
      ft.Divider(height=10, color="transparent"),
      album_art,
      artist_label,
      status_label
  )
  
if __name__ == "__main__":
    ft.run(main)