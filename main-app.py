import flet as ft

def main(page: ft.Page):
    page.title = "My First App"
    page.theme_mode = ft.ThemeMode.DARK
    
    page.add(
        ft.Text("Hello! I am building a music app.", size=24, weight="bold"),
        ft.Text("Phase 1: Core Engine starts next.", size=15, color="grey"),
        ft.Button(content="Click me!", on_click=lambda e: print("Button clicked!"))
    )
ft.run(main)