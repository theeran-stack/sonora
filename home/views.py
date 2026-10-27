import os
import re
import mimetypes
from pathlib import Path

from django.conf import settings
from django.shortcuts import render, get_object_or_404
from django.http import (
    HttpResponse,
    StreamingHttpResponse,
    JsonResponse,
    Http404,
    HttpResponseForbidden,
)
from django.views.decorators.http import require_GET, require_POST
from django.views.decorators.csrf import csrf_exempt

from .models import Track
from .scanner import (
    SCAN_STATE,
    start_background_scan,
    is_safe_music_path,
    extract_cover_bytes,
)
from .artwork import get_or_create_track_artwork

AUDIO_MIME_TYPES = {
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".flac": "audio/flac",
    ".m4a": "audio/mp4",
    ".ogg": "audio/ogg",
}

DEMO_TRACKS = [
    {
        "id": 1,
        "title": "Midnight City",
        "artist": "M83",
        "album": "Night Drive",
        "duration": "4:03",
        "cover": "linear-gradient(135deg, #f97316, #7c3aed)",
        "audio": "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-1.mp3",
        "tag": "Electronic",
        "format": "MP3",
        "source_type": "demo",
    },
    {
        "id": 2,
        "title": "Golden Hour",
        "artist": "JVKE",
        "album": "Golden",
        "duration": "3:28",
        "cover": "linear-gradient(135deg, #fbbf24, #fb7185)",
        "audio": "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-2.mp3",
        "tag": "Pop",
        "format": "MP3",
        "source_type": "demo",
    },
    {
        "id": 3,
        "title": "Ocean Eyes",
        "artist": "Billie Eilish",
        "album": "Blue Room",
        "duration": "3:20",
        "cover": "linear-gradient(135deg, #38bdf8, #1e3a8a)",
        "audio": "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-3.mp3",
        "tag": "Chill",
        "format": "MP3",
        "source_type": "demo",
    },
    {
        "id": 4,
        "title": "Afterglow",
        "artist": "Ed Sheeran",
        "album": "Late Night",
        "duration": "3:45",
        "cover": "linear-gradient(135deg, #34d399, #065f46)",
        "audio": "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-4.mp3",
        "tag": "Acoustic",
        "format": "MP3",
        "source_type": "demo",
    },
    {
        "id": 5,
        "title": "Dreams",
        "artist": "Fleetwood Mac",
        "album": "Soft Focus",
        "duration": "4:17",
        "cover": "linear-gradient(135deg, #c084fc, #db2777)",
        "audio": "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-5.mp3",
        "tag": "Classics",
        "format": "MP3",
        "source_type": "demo",
    },
    {
        "id": 6,
        "title": "Sunset Lover",
        "artist": "Petit Biscuit",
        "album": "Golden Skies",
        "duration": "3:58",
        "cover": "linear-gradient(135deg, #fb7185, #f59e0b)",
        "audio": "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-6.mp3",
        "tag": "Electronic",
        "format": "MP3",
        "source_type": "demo",
    },
]


def home_page(request):
    """
    Renders the SONORA homepage using Claude's tactile studio interface.
    Passes ONLY actual database tracks if they exist, with real /stream/<id>/ URLs.
    Falls back to demo tracks only when the library is completely empty.
    """
    from django.conf import settings
    db_tracks = Track.objects.all()
    if db_tracks.exists():
        tracks = [t.to_dict() for t in db_tracks]
    else:
        tracks = DEMO_TRACKS

    scan_dirs = getattr(settings, "MUSIC_SCAN_DIRECTORIES", [])
    primary_dir = str(scan_dirs[-1]) if scan_dirs else r"C:\Users\Theeran\Documents\My musicc"

    return render(request, "home/index.html", {
        "tracks": tracks,
        "local_count": len(tracks) if db_tracks.exists() else 0,
        "music_dir": primary_dir,
    })


def stream_audio(request, track_id):
    """
    Securely streams audio with full HTTP Range request support (206 Partial Content)
    for seamless seeking, pause, and resume.
    Ensures file is strictly within allowed music roots before streaming.
    Adds CORS headers so modern browsers decode media elements cleanly.
    """
    if request.method == "OPTIONS":
        response = HttpResponse(status=204)
        response["Access-Control-Allow-Origin"] = "*"
        response["Access-Control-Allow-Methods"] = "GET, HEAD, OPTIONS"
        response["Access-Control-Allow-Headers"] = "Range, Content-Type, Accept"
        response["Access-Control-Expose-Headers"] = "Content-Range, Content-Length, Accept-Ranges"
        return response

    track = get_object_or_404(Track, id=track_id)
    file_path = track.file_path

    path_obj = None
    # 1. Local path if safe and exists (for local development)
    if file_path and is_safe_music_path(file_path):
        cand = Path(file_path)
        if cand.exists() and cand.is_file():
            path_obj = cand

    # 2. Bundled repository media/music/ (for cloud hosting like Vercel / Render)
    if not path_obj and file_path:
        fname = Path(file_path).name
        bundled = settings.BASE_DIR / "media" / "music" / fname
        if bundled.exists() and bundled.is_file():
            path_obj = bundled

    # 3. Fallback title match in media/music/
    if not path_obj:
        media_music = settings.BASE_DIR / "media" / "music"
        if media_music.exists():
            clean_title = (track.title or "").split(" - ")[0].strip().lower()
            for cand in media_music.glob("*.*"):
                if clean_title and clean_title in cand.stem.lower():
                    path_obj = cand
                    break

    # 4. Fallback to demo audio only if no audio file is available
    if not path_obj or not path_obj.exists() or not path_obj.is_file():
        from django.shortcuts import redirect
        demo_idx = (track.id - 1) % len(DEMO_TRACKS)
        return redirect(DEMO_TRACKS[demo_idx]["audio"])

    file_size = path_obj.stat().st_size
    content_type = AUDIO_MIME_TYPES.get(path_obj.suffix.lower(), "application/octet-stream")

    range_header = request.META.get("HTTP_RANGE", "").strip()
    range_match = re.match(r"bytes=(?:(\d+)-(\d*)|-(\d+))", range_header)

    chunk_size = 64 * 1024  # 64KB chunks

    def add_cors_headers(resp):
        resp["Accept-Ranges"] = "bytes"
        resp["Access-Control-Allow-Origin"] = "*"
        resp["Access-Control-Allow-Methods"] = "GET, HEAD, OPTIONS"
        resp["Access-Control-Allow-Headers"] = "Range, Content-Type, Accept"
        resp["Access-Control-Expose-Headers"] = "Content-Range, Content-Length, Accept-Ranges"
        return resp

    if range_match:
        if range_match.group(3):
            # Suffix byte range: bytes=-500 (last 500 bytes)
            suffix_len = int(range_match.group(3))
            first_byte = max(0, file_size - suffix_len)
            last_byte = file_size - 1
        else:
            first_byte = int(range_match.group(1))
            last_byte_str = range_match.group(2)
            last_byte = int(last_byte_str) if last_byte_str else file_size - 1

        if first_byte >= file_size or first_byte > last_byte:
            response = HttpResponse(status=416)
            response["Content-Range"] = f"bytes */{file_size}"
            return add_cors_headers(response)

        last_byte = min(last_byte, file_size - 1)
        length = last_byte - first_byte + 1

        def file_iterator(start, end, block_size=chunk_size):
            f = open(path_obj, "rb")
            try:
                f.seek(start)
                remaining = end - start + 1
                while remaining > 0:
                    read_len = min(remaining, block_size)
                    data = f.read(read_len)
                    if not data:
                        break
                    remaining -= len(data)
                    yield data
            finally:
                f.close()

        response = StreamingHttpResponse(
            file_iterator(first_byte, last_byte),
            status=206,
            content_type=content_type,
        )
        response["Content-Range"] = f"bytes {first_byte}-{last_byte}/{file_size}"
        response["Content-Length"] = str(length)
        response["Cache-Control"] = "no-cache"
        return add_cors_headers(response)

    else:
        # Full content response
        def full_iterator(block_size=chunk_size):
            f = open(path_obj, "rb")
            try:
                while True:
                    data = f.read(block_size)
                    if not data:
                        break
                    yield data
            finally:
                f.close()

        response = StreamingHttpResponse(full_iterator(), status=200, content_type=content_type)
        response["Content-Length"] = str(file_size)
        response["Cache-Control"] = "public, max-age=3600"
        return add_cors_headers(response)


def get_cover_art(request, track_id):
    """
    Returns real album artwork with priority:
    1. Cached local image (fastest, refreshed on file mtime update)
    2. Embedded tags (MP3 APIC, FLAC, M4A, OGG, WAV)
    3. Directory image (cover.jpg, folder.jpg, album.jpg, etc.)
    4. Online high-res artwork search (iTunes Search API)
    5. HTTP 404 (triggers graceful subtle CSS fallback on client)
    """
    track = get_object_or_404(Track, id=track_id)

    mime_type, data = get_or_create_track_artwork(track, is_safe_path_fn=is_safe_music_path)
    if data and mime_type:
        response = HttpResponse(data, content_type=mime_type)
        response["Access-Control-Allow-Origin"] = "*"
        response["Cache-Control"] = "public, max-age=86400, immutable"
        response["Content-Length"] = str(len(data))
        return response

    raise Http404("Cover art not found.")


@csrf_exempt
@require_POST
def api_scan_music(request):
    """
    API endpoint to trigger a non-blocking background library scan.
    """
    started = start_background_scan()
    if started:
        return JsonResponse({"status": "started", "message": "Local music scan started in background."})
    else:
        return JsonResponse({
            "status": "in_progress",
            "message": "A scan is already in progress.",
            "data": SCAN_STATE.to_dict()
        })


@require_GET
def api_scan_status(request):
    """
    API endpoint returning real-time scan progress, count, and status.
    """
    return JsonResponse(SCAN_STATE.to_dict())


@csrf_exempt
@require_POST
def api_upload_music(request):
    """
    Accepts uploaded audio files, saves them into the configured music folder,
    and indexes them into the database.
    """
    files = request.FILES.getlist("audio_files") or request.FILES.getlist("files")
    if not files:
        return JsonResponse({"error": "No files provided"}, status=400)

    from django.conf import settings
    upload_dir = getattr(settings, "BASE_DIR") / "music_library"
    upload_dir.mkdir(parents=True, exist_ok=True)

    supported = getattr(settings, "MUSIC_SUPPORTED_FORMATS", {".mp3", ".wav", ".flac", ".m4a", ".ogg"})
    saved = []
    for f in files:
        ext = os.path.splitext(f.name)[1].lower()
        if ext in supported:
            target_path = upload_dir / f.name
            with open(target_path, "wb+") as dest:
                for chunk in f.chunks():
                    dest.write(chunk)
            saved.append(f.name)

    if saved:
        start_background_scan()

    return JsonResponse({
        "status": "success",
        "saved_count": len(saved),
        "files": saved,
        "message": f"Successfully stored {len(saved)} file(s). Indexing started in background."
    })
