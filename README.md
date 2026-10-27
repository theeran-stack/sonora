# SONORA — Tactile Studio Music Player

SONORA is a local music player and streaming application built with Django and modern Web Audio APIs. Designed with warm tactile aesthetics, dynamic vinyl turntable visualization, reactive lighting aura, and real-time audio effects.

---

## Features

- **Local Music Library Scanner**: Recursively scans and indexes MP3, WAV, FLAC, M4A, and OGG files with Mutagen metadata extraction.
- **Embedded & Cloud Album Artwork**:
  1. Embedded cover art extracted directly from ID3 APIC, FLAC, M4A, and Vorbis picture blocks.
  2. Local directory image detection (`cover.jpg`, `folder.jpg`, `album.jpg`).
  3. Online high-resolution artwork fallback via iTunes Search API.
  4. Local disk caching (`.cover_cache/`) for instant sub-millisecond retrieval.
- **HTTP 206 Range Streaming**: Full support for seeking, pausing, and resuming with byte-range requests and CORS headers.
- **Audio Processing**:
  - **8D Audio**: Binaural HRTF 3D spatial panning circling listener position.
  - **10-Band Graphic Equalizer**: Custom biquad peaking filters with presets (Flat, Bass Boost, Rock, Pop, Vocal, Treble, Classical).
  - **Dynamic Compressor & Mono toggle**: Smooth dynamics and stereo-to-mono downmixing.
  - **Playback Speed**: Variable rate playback from 0.25× to 4.0×.
  - **A–B Loop**: Precise loop points for practice or repetition.
- **Tactile UI Design**:
  - Spinning vinyl turntable with real center record label artwork.
  - Reactive ambient glow responding in real-time to audio frequencies via AnalyserNode.
  - Folder and album horizontal slider carousels.
  - Playlists, favorites, and playback history persisted in browser localStorage.
  - Responsive layout for desktop and mobile devices.

---

## Keyboard Shortcuts

| Shortcut | Action |
| :--- | :--- |
| `Space` | Play / Pause |
| `←` / `→` | Jump back / forward 10 seconds |
| `↑` / `↓` | Increase / decrease volume |
| `M` | Toggle mute |
| `N` / `P` | Next / previous track |
| `S` | Toggle shuffle |
| `L` | Toggle repeat |
| `A` | Set A–B loop points |
| `[` / `]` | Decrease / increase playback speed |
| `Escape` | Close audio effects drawer |

---

## Local Setup

### 1. Clone the repository
```bash
git clone https://github.com/theeran-stack/sonora.git
cd sonora
```

### 2. Set up virtual environment
```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# Linux / macOS
source .venv/bin/activate
```

### 3. Install dependencies
```bash
pip install -r requirements.txt
```

### 4. Run migrations and scan music
```bash
python manage.py migrate
python manage.py scan_music
```

### 5. Start development server
```bash
python manage.py runserver
```
Open [http://127.0.0.1:8000/](http://127.0.0.1:8000/) in your browser.

---

## Deployment & Hosting

### Option 1: Render (Recommended)
1. Push this repository to your GitHub account.
2. Sign in to [Render](https://render.com) and click **New Web Service**.
3. Select your GitHub repository.
4. Render automatically detects `render.yaml` or you can specify:
   - **Build Command**: `pip install -r requirements.txt && python manage.py collectstatic --no-input && python manage.py migrate`
   - **Start Command**: `gunicorn myproject.wsgi:application`

### Option 2: Docker / Container Platforms (Railway, Fly.io)
Deploy using the included `Dockerfile`:
```bash
docker build -t sonora .
docker run -p 8000:8000 sonora
```

---

## License
MIT License. Built with Django and Web Audio API.
