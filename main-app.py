import flet as ft
# 1. Top-level imports
from poc_stream import stream_audio_from_youtube


def main(page: ft.Page):
  page.title = "My First App"
  page.theme_mode = ft.ThemeMode.DARK

 
  url_input = ft.TextField(label="URL", width=400, autofocus=True)

  def handle_submit(e):
    user_url = url_input.value
    if user_url:
      print(f"URL submitted: {user_url}")
      stream_audio_from_youtube(user_url)
    else:
      print("The URL field is empty!")

  # 4. Layout
  page.add(
      ft.Text("Hello! I am building a music app.", size=24, weight="bold"),
      ft.Text("Phase 1: Core Engine starts next.", size=15, color="grey"),
      ft.Button(
          content=ft.Text("Click me!"), on_click=lambda e: print("Clicked!")
      ),
  )

  page.add(
      ft.Text("Put your url here:", size=15, color="grey"),
      url_input,
      ft.Button(
          content=ft.Text("Submit"), on_click=handle_submit
      ),  
  )

ft.run(main)
