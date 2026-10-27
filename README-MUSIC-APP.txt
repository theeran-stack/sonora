SONORA — Django music app starter

RUN IT IN VS CODE (Windows PowerShell)
1. Open the extracted "Web App" folder in VS Code.
2. Open Terminal > New Terminal.
3. Activate the existing virtual environment:
   .\.venv\Scripts\Activate.ps1
   If the .venv folder was not included, create it and install Django:
   py -3.12 -m venv .venv
   .\.venv\Scripts\Activate.ps1
   python -m pip install django
4. Start Django:
   python manage.py runserver
5. Open http://127.0.0.1:8000/

WHAT IS INCLUDED
- Django homepage view and URL route
- Responsive frontend (HTML, CSS, JavaScript)
- Search/filter tracks, play/pause, next/previous, shuffle, repeat, volume and progress
- Like buttons and session-only playlist creation demo

NOTES
- Audio examples are remote sample MP3 files, so internet access is needed for playback.
- This is a frontend/demo starter, not a production streaming service. Likes/playlists are not saved to a database yet; login, persistent playlists, licensed music and real track data can be added next.
