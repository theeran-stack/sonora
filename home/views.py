import os
import re
import json
import logging
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
from django.core.validators import validate_email
from django.core.exceptions import ValidationError
from django.db.models import Q

from .models import Track, DownloadTask, SyncLog, FAQ, ErrorCode, Ticket
from .assistant import process_assistant_query
from .mongodb import save_feedback, get_chat_history
from .scanner import (
    SCAN_STATE,
    start_background_scan,
    is_safe_music_path,
    extract_cover_bytes,
    index_single_file,
)
from .artwork import get_or_create_track_artwork
from .discovery import search_jamendo_tracks

logger = logging.getLogger(__name__)

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


def check_sync_auth(request):
    """Verifies X-Sonora-Token authentication header."""
    token = request.headers.get("X-Sonora-Token") or request.META.get("HTTP_X_SONORA_TOKEN")
    expected = getattr(settings, "SONORA_SYNC_TOKEN", "sonora_secret_sync_token_2026")
    return bool(token and token == expected)


def stream_remote_audio_with_cors(url, request):
    """
    Proxies remote audio streams (Jamendo / preview) with strict CORS headers.
    Prevents Web Audio API MediaElementAudioSourceNode from muting audio due to missing CORS headers.
    """
    import requests
    headers = {"User-Agent": "SonoraStreamProxy/2.0"}
    range_header = request.META.get("HTTP_RANGE")
    if range_header:
        headers["Range"] = range_header

    try:
        r = requests.get(url, headers=headers, stream=True, timeout=12)
        def chunk_iter():
            for chunk in r.iter_content(chunk_size=64 * 1024):
                if chunk:
                    yield chunk

        resp = StreamingHttpResponse(
            chunk_iter(),
            status=r.status_code,
            content_type=r.headers.get("Content-Type", "audio/mpeg")
        )
        resp["Access-Control-Allow-Origin"] = "*"
        resp["Access-Control-Allow-Methods"] = "GET, HEAD, OPTIONS"
        resp["Access-Control-Allow-Headers"] = "Range, Content-Type, Accept"
        resp["Access-Control-Expose-Headers"] = "Content-Range, Content-Length, Accept-Ranges"
        resp["Accept-Ranges"] = "bytes"
        if "Content-Range" in r.headers:
            resp["Content-Range"] = r.headers["Content-Range"]
        if "Content-Length" in r.headers:
            resp["Content-Length"] = r.headers["Content-Length"]
        return resp
    except Exception as e:
        logger.warning(f"Error proxying remote stream {url}: {e}")
        return HttpResponse(status=404)


def home_page(request):
    """
    Renders the SONORA tactile music workstation.
    Passes database tracks, environment diagnostics, and initial state.
    """
    db_tracks = Track.objects.filter(is_available=True)
    if not db_tracks.exists():
        db_tracks = Track.objects.all()

    if db_tracks.exists():
        tracks = [t.to_dict() for t in db_tracks]
    else:
        tracks = DEMO_TRACKS

    scan_dirs = getattr(settings, "MUSIC_SCAN_DIRECTORIES", [])
    primary_dir = str(scan_dirs[-1]) if scan_dirs else str(Path.home() / "Music")
    is_local_host = os.name == 'nt' and Path(primary_dir).exists()

    pending_count = DownloadTask.objects.filter(status__in=["queued", "downloading"]).count()

    return render(request, "home/index.html", {
        "tracks": tracks,
        "local_count": len(tracks) if db_tracks.exists() else 0,
        "music_dir": primary_dir,
        "is_hosted": not is_local_host,
        "environment_badge": "Local Windows" if is_local_host else "Cloud Hosted",
        "pending_downloads": pending_count,
        "jamendo_configured": bool(getattr(settings, "JAMENDO_CLIENT_ID", "")),
    })


def stream_audio(request, track_id):
    """
    Streams audio with full RFC 7233 Range request support (HTTP 206 Partial Content).
    Enforces path security, provides CORS headers, and eliminates CORS muting.
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

    # If track points directly to a remote URL, proxy it with CORS
    if track.cloud_url and track.cloud_url.startswith("http"):
        return stream_remote_audio_with_cors(track.cloud_url, request)

    path_obj = None

    # 1. Local path if safe and exists
    if file_path and is_safe_music_path(file_path):
        cand = Path(file_path)
        if cand.exists() and cand.is_file():
            path_obj = cand

    # 2. Bundled repository media/music/ or persistent storage
    if not path_obj and file_path:
        fname = Path(file_path).name
        bundled = settings.BASE_DIR / "media" / "music" / fname
        if bundled.exists() and bundled.is_file():
            path_obj = bundled

    # 3. Persistent media root fallback
    if not path_obj and file_path:
        fname = Path(file_path).name
        persistent = settings.MEDIA_ROOT / "music" / fname
        if persistent.exists() and persistent.is_file():
            path_obj = persistent

    # 4. Fallback title match in media/music/
    if not path_obj:
        media_music = settings.BASE_DIR / "media" / "music"
        if media_music.exists():
            clean_title = (track.title or "").split(" - ")[0].strip().lower()
            for cand in media_music.glob("*.*"):
                if clean_title and clean_title in cand.stem.lower():
                    path_obj = cand
                    break

    # 5. Remote fallback with CORS proxying
    if not path_obj or not path_obj.exists() or not path_obj.is_file():
        demo_idx = (track.id - 1) % len(DEMO_TRACKS)
        fallback_url = DEMO_TRACKS[demo_idx]["audio"]
        return stream_remote_audio_with_cors(fallback_url, request)

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
    1. Cached local image
    2. Embedded tags (MP3 APIC, FLAC, M4A, OGG, WAV)
    3. Directory image (cover.jpg, folder.jpg, album.jpg)
    4. Online high-res artwork search (iTunes Search API)
    5. HTTP 404 (client uses Aurora CSS gradient)
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
    """Triggers non-blocking background library scan."""
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
    """Returns real-time scan progress, metrics, and discovered counts."""
    return JsonResponse(SCAN_STATE.to_dict())


@csrf_exempt
@require_POST
def api_upload_music(request):
    """Saves uploaded audio files and indexes them into database."""
    files = request.FILES.getlist("audio_files") or request.FILES.getlist("files")
    if not files:
        return JsonResponse({"error": "No files provided"}, status=400)

    # Store uploaded music under persistent media mount when available, or local music_library fallback
    persistent_mount = getattr(settings, "PERSISTENT_DIR", None)
    if persistent_mount and Path(persistent_mount).exists() and hasattr(settings, "MEDIA_ROOT"):
        upload_dir = settings.MEDIA_ROOT / "music"
    else:
        upload_dir = settings.BASE_DIR / "music_library"
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
            index_single_file(str(target_path))
            saved.append(f.name)

    if saved:
        start_background_scan()

    return JsonResponse({
        "status": "success",
        "saved_count": len(saved),
        "files": saved,
        "message": f"Successfully stored {len(saved)} file(s)."
    })


# ==============================================================================
# DISCOVERY & JAMENDO API
# ==============================================================================

@require_GET
def api_discover_search(request):
    """Searches Jamendo licensed catalog with query parameter 'q'."""
    q = request.GET.get("q", "").strip()
    results = search_jamendo_tracks(query=q, limit=25)
    return JsonResponse({
        "status": "success",
        "count": len(results),
        "query": q,
        "results": results,
    })


@csrf_exempt
@require_POST
def api_discover_queue(request):
    """Adds a discovered track to the DownloadTask queue."""
    try:
        body = json.loads(request.body.decode("utf-8"))
    except Exception:
        body = request.POST

    external_id = str(body.get("external_id") or body.get("id") or "").strip()
    title = str(body.get("title") or "Untitled").strip()
    artist = str(body.get("artist") or "Unknown Artist").strip()
    album = str(body.get("album") or "Single").strip()
    download_url = str(body.get("download_url") or "").strip()
    preview_url = str(body.get("preview_url") or "").strip()
    cover_url = str(body.get("cover_url") or body.get("cover") or "").strip()
    duration = str(body.get("duration") or "0:00").strip()

    if not download_url and not preview_url:
        return JsonResponse({"error": "No download or preview audio URL provided."}, status=400)

    url_to_download = download_url or preview_url

    # Check if duplicate already exists in queue
    existing = DownloadTask.objects.filter(external_id=external_id).first()
    if existing:
        if existing.status == "failed":
            existing.status = "queued"
            existing.progress = 0
            existing.error_message = ""
            existing.save()
            return JsonResponse({"status": "re_queued", "task": existing.to_dict()})
        return JsonResponse({"status": "already_queued", "task": existing.to_dict()})

    task = DownloadTask.objects.create(
        external_id=external_id,
        title=title,
        artist=artist,
        album=album,
        provider="jamendo",
        download_url=url_to_download,
        preview_url=preview_url,
        cover_url=cover_url,
        duration=duration,
        status="queued",
        progress=0,
    )

    return JsonResponse({
        "status": "queued",
        "message": f"Added '{title}' to download queue.",
        "task": task.to_dict()
    })


# ==============================================================================
# DOWNLOAD MANAGER APIS
# ==============================================================================

@require_GET
def api_downloads_list(request):
    """Returns list of active and recent download tasks."""
    tasks = DownloadTask.objects.all()[:50]
    return JsonResponse({
        "status": "success",
        "count": tasks.count(),
        "downloads": [t.to_dict() for t in tasks],
    })


@csrf_exempt
@require_POST
def api_download_retry(request, task_id):
    """Retries a failed download task."""
    task = get_object_or_404(DownloadTask, id=task_id)
    task.status = "queued"
    task.progress = 0
    task.error_message = ""
    task.save()
    return JsonResponse({"status": "retrying", "task": task.to_dict()})


@csrf_exempt
@require_POST
def api_download_cancel(request, task_id):
    """Cancels/deletes a download task."""
    task = get_object_or_404(DownloadTask, id=task_id)
    task.delete()
    return JsonResponse({"status": "cancelled", "task_id": task_id})


# ==============================================================================
# LOCAL <-> HOSTED SYNCHRONIZATION APIS
# ==============================================================================

@require_GET
def api_sync_status(request):
    """Returns synchronization telemetry and server mode."""
    scan_dirs = getattr(settings, "MUSIC_SCAN_DIRECTORIES", [])
    primary_dir = str(scan_dirs[-1]) if scan_dirs else str(Path.home() / "Music")
    is_local = os.name == 'nt' and Path(primary_dir).exists()

    return JsonResponse({
        "status": "online",
        "mode": "local" if is_local else "hosted",
        "total_tracks": Track.objects.count(),
        "available_tracks": Track.objects.filter(is_available=True).count(),
        "pending_downloads": DownloadTask.objects.filter(status="queued").count(),
        "active_downloads": DownloadTask.objects.filter(status="downloading").count(),
        "completed_downloads": DownloadTask.objects.filter(status="completed").count(),
        "music_dir": primary_dir if is_local else "[Hosted Storage]",
        "media_root": str(settings.MEDIA_ROOT),
    })


@require_GET
def api_sync_queue(request):
    """Helper pulls pending download tasks (Requires X-Sonora-Token)."""
    if not check_sync_auth(request):
        return JsonResponse({"error": "Unauthorized. Invalid or missing X-Sonora-Token."}, status=401)

    pending = DownloadTask.objects.filter(status__in=["queued", "downloading"]).order_by("created_at")
    return JsonResponse({
        "status": "success",
        "count": pending.count(),
        "tasks": [t.to_dict() for t in pending]
    })


@csrf_exempt
@require_POST
def api_sync_queue_update(request):
    """Helper updates status of a download task (Requires X-Sonora-Token)."""
    if not check_sync_auth(request):
        return JsonResponse({"error": "Unauthorized"}, status=401)

    try:
        data = json.loads(request.body.decode("utf-8"))
    except Exception:
        return JsonResponse({"error": "Malformed JSON payload"}, status=400)

    task_id = data.get("task_id")
    status = data.get("status")
    progress = data.get("progress", 0)
    file_path = data.get("file_path", "")
    err = data.get("error_message", "")

    task = get_object_or_404(DownloadTask, id=task_id)
    if status:
        task.status = status
    if progress is not None:
        task.progress = progress
    if file_path:
        task.file_path = file_path
    if err:
        task.error_message = err
    task.save()

    # If completed, log sync action
    if status == "completed":
        SyncLog.objects.create(
            action="download_completed",
            details=f"Task {task_id}: {task.title} downloaded to {file_path}"
        )

    return JsonResponse({"status": "updated", "task": task.to_dict()})


@csrf_exempt
@require_POST
def api_sync_tracks(request):
    """Helper synchronizes local track metadata to hosted server (Requires X-Sonora-Token)."""
    if not check_sync_auth(request):
        return JsonResponse({"error": "Unauthorized"}, status=401)

    try:
        data = json.loads(request.body.decode("utf-8"))
    except Exception:
        return JsonResponse({"error": "Malformed JSON"}, status=400)

    tracks_payload = data.get("tracks", [])
    synced_count = 0

    for t in tracks_payload:
        title = t.get("title")
        artist = t.get("artist", "Unknown Artist")
        album = t.get("album", "Unknown Album")
        dur_sec = t.get("duration_seconds", 0)
        dur_fmt = t.get("duration_formatted", "0:00")
        f_hash = t.get("file_hash", "")
        fmt = t.get("format", ".mp3")
        tag = t.get("tag", "Local")
        has_cov = t.get("has_cover", False)
        rel_path = t.get("relative_name", "")

        # Look up by file hash or title/artist
        existing = None
        if f_hash:
            existing = Track.objects.filter(file_hash=f_hash).first()
        if not existing and title:
            existing = Track.objects.filter(title=title, artist=artist).first()

        hosted_file_path = str(settings.MEDIA_ROOT / "music" / rel_path) if rel_path else f"hosted_{title}"

        if existing:
            existing.title = title
            existing.artist = artist
            existing.album = album
            existing.duration_seconds = dur_sec
            existing.duration_formatted = dur_fmt
            existing.has_cover = has_cov
            existing.file_hash = f_hash
            existing.is_available = True
            existing.save()
        else:
            Track.objects.create(
                title=title,
                artist=artist,
                album=album,
                duration_seconds=dur_sec,
                duration_formatted=dur_fmt,
                file_path=hosted_file_path,
                format=fmt,
                tag=tag,
                has_cover=has_cov,
                source_type="local",
                is_available=True,
                file_hash=f_hash,
            )
        synced_count += 1

    SyncLog.objects.create(
        action="metadata_sync",
        details=f"Synchronized {synced_count} tracks from helper"
    )

    return JsonResponse({
        "status": "success",
        "synced_count": synced_count,
        "total_tracks": Track.objects.count()
    })


@csrf_exempt
@require_POST
def api_sync_upload(request):
    """Helper uploads an audio file to hosted storage (Requires X-Sonora-Token)."""
    if not check_sync_auth(request):
        return JsonResponse({"error": "Unauthorized"}, status=401)

    file_obj = request.FILES.get("audio_file") or request.FILES.get("file")
    if not file_obj:
        return JsonResponse({"error": "No file uploaded"}, status=400)

    dest_dir = settings.MEDIA_ROOT / "music"
    dest_dir.mkdir(parents=True, exist_ok=True)
    target_path = dest_dir / file_obj.name

    with open(target_path, "wb+") as f:
        for chunk in file_obj.chunks():
            f.write(chunk)

    # Immediately index the uploaded file
    status, track = index_single_file(str(target_path))

    return JsonResponse({
        "status": "success",
        "file": file_obj.name,
        "indexed_status": status,
        "track_id": track.id if hasattr(track, "id") else None,
    })


# ==============================================================================
# FAQ APIS
# ==============================================================================

@require_GET
def api_faqs_list(request):
    """
    GET /api/faqs/
    Lists active FAQs.
    Query parameters:
      - category: filter by category (e.g. general, audio, library, sync, security)
      - q or search: case-insensitive search in question or answer
    """
    category = request.GET.get("category", "").strip().lower()
    search = (request.GET.get("q") or request.GET.get("search") or "").strip()

    qs = FAQ.objects.filter(is_active=True)
    if category:
        qs = qs.filter(category__iexact=category)
    if search:
        qs = qs.filter(Q(question__icontains=search) | Q(answer__icontains=search))

    faqs = [f.to_dict() for f in qs]
    return JsonResponse({
        "status": "success",
        "count": len(faqs),
        "category": category or "all",
        "search": search,
        "faqs": faqs,
    })


# ==============================================================================
# ERROR CODE APIS
# ==============================================================================

@require_GET
def api_error_codes_list(request):
    """
    GET /api/error-codes/
    Lists active ErrorCodes.
    Supports ?code=<code_string> as an optional query param.
    """
    code_filter = request.GET.get("code", "").strip().upper()
    qs = ErrorCode.objects.filter(is_active=True)
    if code_filter:
        item = qs.filter(code=code_filter).first()
        if not item:
            return JsonResponse({
                "status": "error",
                "error": f"Error code '{code_filter}' not found."
            }, status=404)
        return JsonResponse({"status": "success", "error_code": item.to_dict()})

    items = [e.to_dict() for e in qs]
    return JsonResponse({
        "status": "success",
        "count": len(items),
        "error_codes": items,
    })


@require_GET
def api_error_code_detail(request, code):
    """
    GET /api/error-codes/<str:code>/
    Returns individual error code details based on docs/ERROR_CODES.md.
    """
    code_normalized = code.strip().upper()
    item = ErrorCode.objects.filter(code=code_normalized, is_active=True).first()
    if not item:
        return JsonResponse({
            "status": "error",
            "error": f"Error code '{code_normalized}' not found."
        }, status=404)

    return JsonResponse({
        "status": "success",
        "error_code": item.to_dict(),
    })


# ==============================================================================
# TICKET MANAGEMENT APIS
# ==============================================================================

def is_authorized_staff(request):
    """Checks if request has admin privileges via staff user session or X-Sonora-Token."""
    if hasattr(request, "user") and request.user.is_authenticated and request.user.is_staff:
        return True
    return check_sync_auth(request)


@require_GET
def api_tickets_list(request):
    """
    GET /api/tickets/
    Lists tickets.
    Permissions & Privacy:
      - Staff or sync-token authenticated callers can view all tickets.
      - Anonymous callers MUST provide an ?email= parameter to view only their own tickets.
      - Anonymous requests without an email parameter are rejected with 401 Unauthorized to prevent data leaks.
    """
    email_query = request.GET.get("email", "").strip()
    status_query = request.GET.get("status", "").strip().lower()

    is_admin = is_authorized_staff(request)

    if not is_admin and not email_query:
        return JsonResponse({
            "error": "Authentication required to view all tickets. Specify an ?email= filter or provide admin credentials."
        }, status=401)

    qs = Ticket.objects.all()
    if email_query:
        qs = qs.filter(email__iexact=email_query)
    if status_query:
        qs = qs.filter(status=status_query)

    tickets = [t.to_dict(include_email=True) for t in qs]
    return JsonResponse({
        "status": "success",
        "count": len(tickets),
        "tickets": tickets,
    })


@csrf_exempt
@require_POST
def api_ticket_create(request):
    """
    POST /api/tickets/create/
    Creates a new support ticket.
    Payload: {"title": "...", "description": "...", "email": "..."}
    """
    try:
        data = json.loads(request.body.decode("utf-8")) if request.body else request.POST.dict()
    except Exception:
        return JsonResponse({"error": "Malformed JSON payload"}, status=400)

    title = (data.get("title") or "").strip()
    description = (data.get("description") or "").strip()
    email = (data.get("email") or "").strip()

    errors = {}
    if not title:
        errors["title"] = "Title is required."
    elif len(title) > 200:
        errors["title"] = "Title must be at most 200 characters."

    if not description:
        errors["description"] = "Description is required."

    if not email:
        errors["email"] = "Email address is required."
    else:
        try:
            validate_email(email)
        except ValidationError:
            errors["email"] = "Invalid email address format."

    if errors:
        return JsonResponse({
            "status": "error",
            "error": "Validation failed",
            "details": errors,
        }, status=400)

    ticket = Ticket.objects.create(
        title=title,
        description=description,
        email=email,
        status="open",
    )

    return JsonResponse({
        "status": "success",
        "message": "Ticket created successfully.",
        "ticket": ticket.to_dict(include_email=True),
    }, status=201)


@csrf_exempt
def api_ticket_update(request, ticket_id):
    """
    PUT /api/tickets/<id>/update/
    Updates ticket status, title, or description.
    Requires staff authentication or X-Sonora-Token.
    """
    if request.method not in ["PUT", "PATCH", "POST"]:
        return JsonResponse({"error": f"Method {request.method} not allowed"}, status=405)

    if not is_authorized_staff(request):
        return JsonResponse({"error": "Unauthorized. Staff or token authentication required."}, status=401)

    ticket = Ticket.objects.filter(id=ticket_id).first()
    if not ticket:
        return JsonResponse({"error": f"Ticket #{ticket_id} not found."}, status=404)

    try:
        data = json.loads(request.body.decode("utf-8")) if request.body else request.POST.dict()
    except Exception:
        return JsonResponse({"error": "Malformed JSON payload"}, status=400)

    valid_statuses = [choice[0] for choice in Ticket.STATUS_CHOICES]

    if "status" in data:
        new_status = str(data["status"]).strip().lower()
        if new_status not in valid_statuses:
            return JsonResponse({
                "error": f"Invalid status '{new_status}'. Allowed choices: {valid_statuses}"
            }, status=400)
        ticket.status = new_status

    if "title" in data:
        new_title = str(data["title"]).strip()
        if new_title:
            ticket.title = new_title[:200]

    if "description" in data:
        new_desc = str(data["description"]).strip()
        if new_desc:
            ticket.description = new_desc

    ticket.save()

    return JsonResponse({
        "status": "success",
        "message": f"Ticket #{ticket.id} updated successfully.",
        "ticket": ticket.to_dict(include_email=True),
    })


@csrf_exempt
def api_ticket_delete(request, ticket_id):
    """
    DELETE /api/tickets/<id>/delete/
    Deletes a support ticket.
    Requires staff authentication or X-Sonora-Token.
    """
    if request.method not in ["DELETE", "POST"]:
        return JsonResponse({"error": f"Method {request.method} not allowed"}, status=405)

    if not is_authorized_staff(request):
        return JsonResponse({"error": "Unauthorized. Staff or token authentication required."}, status=401)

    ticket = Ticket.objects.filter(id=ticket_id).first()
    if not ticket:
        return JsonResponse({"error": f"Ticket #{ticket_id} not found."}, status=404)

    deleted_id = ticket.id
    ticket.delete()

    return JsonResponse({
        "status": "success",
        "message": f"Ticket #{deleted_id} deleted successfully.",
        "deleted_id": deleted_id,
    })


# ==============================================================================
# FAQ & ERROR CODE PAGES
# ==============================================================================

def faq_page(request):
    """
    Renders the dedicated FAQ knowledge page.
    Passes initial active FAQs for server-side rendering while supporting dynamic AJAX filtering.
    """
    category = request.GET.get("category", "").strip().lower()
    search = (request.GET.get("q") or request.GET.get("search") or "").strip()

    qs = FAQ.objects.filter(is_active=True)
    if category:
        qs = qs.filter(category__iexact=category)
    if search:
        qs = qs.filter(Q(question__icontains=search) | Q(answer__icontains=search))

    categories = list(FAQ.objects.filter(is_active=True).values_list("category", flat=True).distinct())

    return render(request, "home/faq.html", {
        "faqs": [f.to_dict() for f in qs],
        "categories": sorted(categories),
        "selected_category": category,
        "search_query": search,
        "total_count": FAQ.objects.filter(is_active=True).count(),
    })


def error_codes_page(request):
    """
    Renders the dedicated Error Codes & Troubleshooting page.
    Passes initial ErrorCode records for instant rendering and deep-link code lookup.
    """
    code_filter = request.GET.get("code", "").strip().upper()
    search = (request.GET.get("q") or request.GET.get("search") or "").strip()

    qs = ErrorCode.objects.filter(is_active=True)
    if code_filter:
        qs = qs.filter(code__icontains=code_filter)
    if search:
        qs = qs.filter(
            Q(code__icontains=search)
            | Q(title__icontains=search)
            | Q(description__icontains=search)
            | Q(cause__icontains=search)
        )

    return render(request, "home/error_codes.html", {
        "error_codes": [e.to_dict() for e in qs],
        "selected_code": code_filter,
        "search_query": search,
        "total_count": ErrorCode.objects.filter(is_active=True).count(),
    })


# ==============================================================================
# AI ASSISTANT APIS
# ==============================================================================

@csrf_exempt
@require_POST
def api_assistant_chat(request):
    """
    POST /api/assistant/chat/
    Processes natural language queries against SQLite FAQ and ErrorCode models.
    Persists interaction turn in MongoDB if configured.
    Payload: {"message": "...", "session_id": "..."}
    """
    try:
        data = json.loads(request.body.decode("utf-8")) if request.body else request.POST.dict()
    except Exception:
        return JsonResponse({"status": "error", "error": "Malformed JSON payload"}, status=400)

    message = (data.get("message") or data.get("query") or "").strip()
    session_id = (data.get("session_id") or "anonymous_session").strip()

    if not message:
        return JsonResponse({"status": "error", "error": "Message is required."}, status=400)

    result = process_assistant_query(message, session_id=session_id)
    return JsonResponse(result)


@csrf_exempt
@require_POST
def api_assistant_feedback(request):
    """
    POST /api/assistant/feedback/
    Records user rating and comments on assistant responses into MongoDB.
    Payload: {"session_id": "...", "rating": 5, "comments": "...", "message_id": "..."}
    """
    try:
        data = json.loads(request.body.decode("utf-8")) if request.body else request.POST.dict()
    except Exception:
        return JsonResponse({"status": "error", "error": "Malformed JSON payload"}, status=400)

    session_id = (data.get("session_id") or "anonymous_session").strip()
    rating = data.get("rating", 5)
    comments = (data.get("comments") or "").strip()
    message_id = (data.get("message_id") or "").strip()

    try:
        rating_int = int(rating)
    except (ValueError, TypeError):
        rating_int = 5

    mongo_id = save_feedback(session_id, rating=rating_int, comments=comments, message_id=message_id)

    return JsonResponse({
        "status": "success",
        "message": "Feedback submitted successfully.",
        "mongo_saved": bool(mongo_id),
    })


@require_GET
def api_assistant_history(request):
    """
    GET /api/assistant/history/?session_id=...
    Retrieves previous chat turns from MongoDB.
    """
    session_id = request.GET.get("session_id", "anonymous_session").strip()
    history = get_chat_history(session_id)
    return JsonResponse({
        "status": "success",
        "session_id": session_id,
        "count": len(history),
        "history": history,
    })


# ==============================================================================
# BRANDED CUSTOM ERROR HANDLERS (404 & 500)
# ==============================================================================

def custom_404(request, exception=None):
    """Custom 404 handler with brand styling and Ask Assistant trigger."""
    return render(request, "404.html", {
        "request_path": getattr(request, "path", "/"),
        "error_code": "ERR_STREAM_NOT_FOUND",
    }, status=404)


def custom_500(request):
    """Custom 500 handler with brand styling and Ask Assistant trigger."""
    return render(request, "500.html", {
        "request_path": getattr(request, "path", "/"),
        "error_code": "ERR_500_SERVER_ERROR",
    }, status=500)


# ==============================================================================
# APPLICATION HEALTH CHECK APIS
# ==============================================================================

@require_GET
def health_check(request):
    """
    GET /health/ and GET /healthz/
    Comprehensive health check validating:
      - Core Django web application runtime
      - SQLite primary database connectivity
      - Optional MongoDB integration status (offline mode does NOT degrade core platform)
      - Audio library directory accessibility and track indexing count
    Returns HTTP 200 when healthy, or HTTP 503 if primary database is unreachable.
    """
    from django.db import connection
    import datetime

    timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
    checks = {}
    is_healthy = True

    # 1. Primary Database Check
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        checks["database"] = {
            "status": "healthy",
            "engine": "django.db.backends.sqlite3",
            "writable": True,
        }
    except Exception as exc:
        is_healthy = False
        checks["database"] = {
            "status": "unhealthy",
            "error": str(exc),
        }

    # 2. Tracks and Storage
    try:
        track_count = Track.objects.count()
        scan_dirs = getattr(settings, "MUSIC_SCAN_DIRECTORIES", [])
        primary_dir = str(scan_dirs[-1]) if scan_dirs else str(Path.home() / "Music")
        checks["library"] = {
            "status": "accessible" if Path(primary_dir).exists() or not os.name == "nt" else "offline_or_remote",
            "tracks_indexed": track_count,
            "primary_dir": primary_dir,
        }
    except Exception as exc:
        checks["library"] = {
            "status": "error",
            "error": str(exc),
        }

    # 3. Optional MongoDB Check (decoupled: unconfigured is not a fatal failure)
    from .mongodb import is_mongo_configured, check_mongo_connection
    if is_mongo_configured():
        m_ok, m_msg = check_mongo_connection()
        checks["mongodb"] = {
            "status": "connected" if m_ok else "unreachable",
            "configured": True,
            "message": m_msg,
        }
    else:
        checks["mongodb"] = {
            "status": "unconfigured",
            "configured": False,
            "message": "Running in resilient standalone mode without external MongoDB Atlas.",
        }

    # 4. Third-Party API Configuration
    checks["third_party_apis"] = {
        "jamendo": {
            "configured": bool(getattr(settings, "JAMENDO_CLIENT_ID", "")),
            "timeout_seconds": getattr(settings, "JAMENDO_API_TIMEOUT", 4.0),
            "fallback": "curated_catalog",
        },
        "itunes_artwork": {
            "timeout_seconds": getattr(settings, "ARTWORK_API_TIMEOUT", 2.5),
            "fallback": "aurora_gradient",
        },
    }

    payload = {
        "status": "healthy" if is_healthy else "unhealthy",
        "service": "SONORA Tactile Music Platform",
        "version": "2.0.0",
        "timestamp": timestamp,
        "environment": "Production" if not settings.DEBUG else "Development",
        "checks": checks,
    }

    status_code = 200 if is_healthy else 503
    return JsonResponse(payload, status=status_code)
