import flet as ft
import threading 
from poc_stream import stream_audio_from_youtube, download_audio

def main(page: ft.Page):
  page.title = "My First App"
  page.theme_mode = ft.ThemeMode.DARK

  # 1. Inputs
  url_input = ft.TextField(label="URL", width=400, autofocus=True)
  path_input = ft.TextField(label="Download Path (Optional)", width=400)

  # 2. Handlers
  def handle_submit(e):
    user_url = url_input.value
    if user_url:
      print(f"URL submitted: {user_url}")
      threading.Thread(target=stream_audio_from_youtube, args=(user_url,), daemon=True).start()
    else:
      print("The URL field is empty!")


  def handle_download(e):
    user_url = url_input.value
    user_path = path_input.value
    
    if user_url:
        print(f"Downloading audio from: {user_url}")
        actual_path = user_path.strip() if user_path.strip() else None
        threading.Thread(target=download_audio, args=(user_url, actual_path), daemon=True).start()
    else:
        print("Provide a URL to download!")
  
  page.add(
      ft.Text("Enter your media details below:", size=15, color="grey"),
      url_input,
      path_input,
      ft.Row([
          ft.Button(content=ft.Text("Stream Audio"), on_click=handle_submit),
          ft.Button(content=ft.Text("Download Audio"), on_click=handle_download)
      ])
  )

ft.run(main)
