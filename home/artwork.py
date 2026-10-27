import os
import json
import logging
import mimetypes
import urllib.parse
import urllib.request
from pathlib import Path

import mutagen
from django.conf import settings

logger = logging.getLogger(__name__)

COMMON_COVER_NAMES = {
    "cover", "folder", "album", "front", "artwork", "default"
}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


def get_cache_dir() -> Path:
    """Returns directory used to store extracted and downloaded album artwork."""
    cache_dir = Path(getattr(settings, "COVER_CACHE_DIR", settings.BASE_DIR / ".cover_cache"))
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def extract_embedded_cover(file_path: str):
    """
    Extracts raw embedded cover image data and MIME type from an audio file.
    Supports MP3 (APIC), FLAC (Picture), M4A/MP4 (covr), OGG (Vorbis metadata_block_picture),
    and WAV/AIFF (ID3 tags).
    """
    path_obj = Path(file_path)
    if not path_obj.exists() or not path_obj.is_file():
        return None, None

    fmt = path_obj.suffix.lower()
    try:
        audio = mutagen.File(str(path_obj))
        if not audio:
            return None, None

        # 1. MP3 / ID3
        if fmt == ".mp3" and hasattr(audio, "tags") and audio.tags:
            # Check APIC frames (type 3 = cover front preferred)
            best_tag = None
            for k, tag in audio.tags.items():
                if k.startswith("APIC"):
                    if getattr(tag, "type", None) == 3:
                        best_tag = tag
                        break
                    if not best_tag:
                        best_tag = tag
            if best_tag and hasattr(best_tag, "data"):
                mime = getattr(best_tag, "mime", "image/jpeg")
                return mime, best_tag.data

        # 2. FLAC
        elif fmt == ".flac" and hasattr(audio, "pictures") and audio.pictures:
            best_pic = None
            for pic in audio.pictures:
                if pic.type == 3:  # Front cover
                    best_pic = pic
                    break
                if not best_pic:
                    best_pic = pic
            if best_pic:
                return best_pic.mime or "image/jpeg", best_pic.data

        # 3. M4A / MP4
        elif fmt in [".m4a", ".mp4", ".aac"] and hasattr(audio, "tags") and audio.tags:
            covr = audio.tags.get("covr")
            if covr and len(covr) > 0:
                data = bytes(covr[0])
                mime = "image/png" if data.startswith(b"\x89PNG") else "image/jpeg"
                return mime, data

        # 4. OGG / Vorbis
        elif fmt in [".ogg", ".opus"] and hasattr(audio, "get"):
            import base64
            pics = audio.get("metadata_block_picture")
            if pics:
                raw = base64.b64decode(pics[0])
                from mutagen.flac import Picture
                pic = Picture(raw)
                return pic.mime or "image/jpeg", pic.data
            coverart = audio.get("coverart")
            if coverart:
                raw = base64.b64decode(coverart[0])
                mime = "image/png" if raw.startswith(b"\x89PNG") else "image/jpeg"
                return mime, raw

        # 5. WAV / AIFF with ID3 tags
        elif fmt in [".wav", ".aiff"] and hasattr(audio, "tags") and audio.tags:
            for k, tag in audio.tags.items():
                if k.startswith("APIC") and hasattr(tag, "data"):
                    mime = getattr(tag, "mime", "image/jpeg")
                    return mime, tag.data

    except Exception as e:
        logger.warning(f"Error extracting embedded cover from {file_path}: {e}")

    return None, None


def find_folder_artwork(file_path: str, is_safe_path_fn=None):
    """
    Searches the audio file's directory for local artwork files (cover.jpg, folder.jpg, etc.).
    """
    path_obj = Path(file_path)
    parent_dir = path_obj.parent
    if not parent_dir.exists() or not parent_dir.is_dir():
        return None, None

    # Check common artwork names
    candidates = []
    for ext in IMAGE_EXTENSIONS:
        for name in COMMON_COVER_NAMES:
            candidates.append(parent_dir / f"{name}{ext}")
            candidates.append(parent_dir / f"{name.upper()}{ext}")

    # Also check album name match
    album_name = path_obj.stem
    for ext in IMAGE_EXTENSIONS:
        candidates.append(parent_dir / f"{album_name}{ext}")

    for cand in candidates:
        if cand.exists() and cand.is_file():
            if is_safe_path_fn and not is_safe_path_fn(str(cand)):
                continue
            try:
                data = cand.read_bytes()
                if len(data) > 0:
                    mime = mimetypes.guess_type(str(cand))[0] or "image/jpeg"
                    return mime, data
            except Exception as e:
                logger.warning(f"Error reading folder artwork {cand}: {e}")

    return None, None


def fetch_online_artwork(title: str, artist: str, album: str = None, timeout: float = 2.5):
    """
    Queries iTunes Search API for song/album artwork with a strict timeout.
    Returns (mime_type, binary_data) or (None, None).
    """
    clean_title = (title or "").strip()
    clean_artist = (artist or "").strip()
    if not clean_title or clean_title.lower() in ["untitled", "track"]:
        return None, None

    # Strip MassTamilan or download tags for clean search query
    clean_title = clean_title.split(" - MassTamilan")[0].split(" - ")[0].strip()
    clean_artist = clean_artist.split(" - MassTamilan")[0].split(" - ")[0].strip()
    if clean_artist.lower() in ["unknown artist", "unknown", ""]:
        query = clean_title
    else:
        query = f"{clean_title} {clean_artist}".strip()

    try:
        url = f"https://itunes.apple.com/search?term={urllib.parse.quote(query)}&media=music&entity=song&limit=1"
        req = urllib.request.Request(url, headers={"User-Agent": "SonoraMusicPlayer/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as response:
            if response.status != 200:
                return None, None
            payload = json.loads(response.read().decode("utf-8"))

        results = payload.get("results", [])
        if not results:
            return None, None

        item = results[0]
        art_url = item.get("artworkUrl100")
        if not art_url:
            return None, None

        # Upgrade resolution to 600x600 for sharp artwork
        high_res_url = art_url.replace("100x100bb.jpg", "600x600bb.jpg").replace("100x100bb.png", "600x600bb.png")

        img_req = urllib.request.Request(high_res_url, headers={"User-Agent": "SonoraMusicPlayer/1.0"})
        with urllib.request.urlopen(img_req, timeout=timeout) as img_resp:
            if img_resp.status == 200:
                data = img_resp.read()
                # Verify magic bytes
                if data.startswith(b"\xff\xd8"):
                    return "image/jpeg", data
                elif data.startswith(b"\x89PNG"):
                    return "image/png", data
                elif data.startswith(b"RIFF") and b"WEBP" in data[:16]:
                    return "image/webp", data
                return "image/jpeg", data

    except Exception as e:
        logger.info(f"Online artwork lookup note for '{query}': {e}")

    return None, None


def get_or_create_track_artwork(track, is_safe_path_fn=None):
    """
    Artwork priority resolver with persistent caching:
    1. Cache hit (checks file mtime to refresh if source changed)
    2. Embedded artwork via Mutagen (MP3/FLAC/M4A/OGG)
    3. Folder artwork (cover.jpg, folder.jpg, etc.)
    4. Online search (iTunes Search API)
    5. Fallback: (None, None)
    """
    cache_dir = get_cache_dir()
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_img = cache_dir / f"track_{track.id}.img"
    cache_mime = cache_dir / f"track_{track.id}.mime"

    audio_path = Path(track.file_path)

    # 1. Check existing valid cache
    if cache_img.exists() and cache_mime.exists():
        try:
            cache_mtime = cache_img.stat().st_mtime
            audio_mtime = audio_path.stat().st_mtime if audio_path.exists() else 0
            if cache_mtime >= audio_mtime:
                mime = cache_mime.read_text(encoding="utf-8").strip()
                data = cache_img.read_bytes()
                if len(data) > 0 and mime:
                    return mime, data
        except Exception as e:
            logger.warning(f"Error reading artwork cache for track {track.id}: {e}")

    # 2. Priority 1: Embedded artwork
    mime, data = extract_embedded_cover(track.file_path)

    # 3. Priority 2: Local folder artwork
    if not data:
        mime, data = find_folder_artwork(track.file_path, is_safe_path_fn=is_safe_path_fn)

    # 4. Priority 3: Online artwork lookup
    if not data:
        mime, data = fetch_online_artwork(track.title, track.artist, track.album)

    # If found, persist into cache
    if data and mime:
        try:
            cache_img.write_bytes(data)
            cache_mime.write_text(mime, encoding="utf-8")
            if not track.has_cover:
                from home.models import Track
                Track.objects.filter(id=track.id).update(has_cover=True)
                track.has_cover = True
        except Exception as e:
            logger.warning(f"Error caching artwork for track {track.id}: {e}")
        return mime, data

    return None, None
