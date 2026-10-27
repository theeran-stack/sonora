import os
import sys
import threading
import logging
from pathlib import Path
from datetime import datetime

import mutagen
from django.conf import settings
from django.db import transaction
from .models import Track
from .artwork import extract_embedded_cover, find_folder_artwork

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = getattr(settings, 'MUSIC_SUPPORTED_FORMATS', {'.mp3', '.wav', '.flac', '.m4a', '.ogg'})


class ScanState:
    def __init__(self):
        self.is_scanning = False
        self.scanned_count = 0
        self.total_tracks = 0
        self.last_scan = None
        self.errors = []
        self._lock = threading.Lock()

    def start(self):
        with self._lock:
            self.is_scanning = True
            self.scanned_count = 0
            self.errors = []

    def reset(self):
        with self._lock:
            self.is_scanning = False
            self.scanned_count = 0
            self.errors = []

    def update_progress(self, count):
        with self._lock:
            self.scanned_count = count

    def finish(self, total, errors=None):
        with self._lock:
            self.is_scanning = False
            self.total_tracks = total
            self.last_scan = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            if errors:
                self.errors.extend(errors)

    def to_dict(self):
        with self._lock:
            # Query actual DB count if not actively scanning
            current_total = self.total_tracks if self.is_scanning else Track.objects.count()
            return {
                "is_scanning": self.is_scanning,
                "scanned_count": self.scanned_count,
                "total_tracks": current_total,
                "last_scan": self.last_scan,
                "errors": self.errors[-5:] if self.errors else [],
            }


SCAN_STATE = ScanState()


def get_scan_directories():
    """Returns list of existing and configured Path objects for scanning."""
    configured = getattr(settings, 'MUSIC_SCAN_DIRECTORIES', [])
    valid_paths = []
    for p in configured:
        p_obj = Path(p).resolve()
        valid_paths.append(p_obj)
    return valid_paths


def is_safe_music_path(file_path):
    """
    Validates that a file_path is strictly inside one of the allowed MUSIC_SCAN_DIRECTORIES.
    Prevents path traversal attacks and arbitrary filesystem access.
    """
    try:
        resolved_file = Path(file_path).resolve()
        for root in get_scan_directories():
            try:
                # Check if file is relative to root
                resolved_file.relative_to(root)
                return True
            except ValueError:
                continue
    except Exception as e:
        logger.warning(f"Error checking path security for {file_path}: {e}")
    return False


def format_duration(seconds):
    """Formats seconds to M:SS or H:MM:SS string."""
    if not seconds or seconds < 0:
        return "0:00"
    secs = int(round(seconds))
    mins = secs // 60
    rem_secs = secs % 60
    if mins >= 60:
        hours = mins // 60
        rem_mins = mins % 60
        return f"{hours}:{rem_mins:02d}:{rem_secs:02d}"
    return f"{mins}:{rem_secs:02d}"


def extract_metadata(file_path):
    """
    Extracts title, artist, album, duration, genre tag, and cover status using Mutagen.
    Provides robust fallbacks for missing metadata or corrupted tags.
    """
    path_obj = Path(file_path)
    title = path_obj.stem
    artist = "Unknown Artist"
    album = "Unknown Album"
    genre = "Local"
    duration = 0.0
    has_cover = False
    fmt = path_obj.suffix.lower()

    try:
        audio = mutagen.File(str(path_obj))
        if audio is not None:
            # Duration
            if hasattr(audio, 'info') and hasattr(audio.info, 'length'):
                duration = float(audio.info.length or 0.0)

            # Metadata tags depending on container
            # 1. ID3 / EasyID3 / Vorbis / MP4 tags
            tags = getattr(audio, 'tags', None)
            if tags:
                # Title
                for t_key in ['title', 'TIT2', '\xa9nam']:
                    val = tags.get(t_key)
                    if val:
                        title = str(val[0] if isinstance(val, (list, tuple)) else val).strip()
                        if title:
                            break

                # Artist
                for a_key in ['artist', 'TPE1', '\xa9ART', 'aART']:
                    val = tags.get(a_key)
                    if val:
                        artist = str(val[0] if isinstance(val, (list, tuple)) else val).strip()
                        if artist:
                            break

                # Album
                for alb_key in ['album', 'TALB', '\xa9alb']:
                    val = tags.get(alb_key)
                    if val:
                        album = str(val[0] if isinstance(val, (list, tuple)) else val).strip()
                        if album:
                            break

                # Genre
                for g_key in ['genre', 'TCON', '\xa9gen']:
                    val = tags.get(g_key)
                    if val:
                        genre = str(val[0] if isinstance(val, (list, tuple)) else val).strip()
                        if genre:
                            break

            # Cover detection
            if fmt == '.mp3' and hasattr(audio, 'tags') and audio.tags:
                for k in audio.tags.keys():
                    if k.startswith('APIC'):
                        has_cover = True
                        break
            elif fmt == '.flac' and hasattr(audio, 'pictures') and audio.pictures:
                has_cover = True
            elif fmt == '.m4a' and hasattr(audio, 'tags') and audio.tags:
                if 'covr' in audio.tags and audio.tags['covr']:
                    has_cover = True
            elif fmt == '.ogg':
                if hasattr(audio, 'get') and (audio.get('metadata_block_picture') or audio.get('coverart')):
                    has_cover = True
    except Exception as e:
        logger.warning(f"Metadata extraction fallback for {file_path}: {e}")

    # If no embedded artwork, check if folder artwork exists (cover.jpg, folder.jpg, etc.)
    if not has_cover:
        try:
            folder_mime, folder_data = find_folder_artwork(file_path)
            if folder_data:
                has_cover = True
        except Exception:
            pass

    # Map genre into Sonora vibes if applicable
    g_lower = genre.lower()
    tag = "Local"
    if any(w in g_lower for w in ["chill", "ambient", "downtempo", "lo-fi", "lofi", "piano"]):
        tag = "Chill"
    elif any(w in g_lower for w in ["electronic", "dance", "synth", "edm", "techno", "house"]):
        tag = "Electronic"
    elif any(w in g_lower for w in ["acoustic", "folk", "guitar", "instrumental"]):
        tag = "Acoustic"
    elif any(w in g_lower for w in ["classic", "retro", "rock", "jazz", "blues"]):
        tag = "Classics"

    return {
        "title": title or path_obj.stem,
        "artist": artist or "Unknown Artist",
        "album": album or "Unknown Album",
        "duration_seconds": duration,
        "duration_formatted": format_duration(duration),
        "format": fmt,
        "tag": tag,
        "has_cover": has_cover,
    }


def extract_cover_bytes(file_path):
    """
    Extracts raw cover image data and mime-type from an audio file.
    Returns (mime_type, binary_data) or (None, None).
    """
    return extract_embedded_cover(file_path)


def scan_music_library(explicit_dirs=None):
    """
    Performs recursive, idempotent scan across configured music directories.
    - Updates changed files.
    - Avoids duplicate records.
    - Never deletes library when folders/drives are unavailable.
    - Non-blocking when invoked via background thread.
    """
    if SCAN_STATE.is_scanning:
        logger.info("Scan already in progress. Skipping duplicate invocation.")
        return SCAN_STATE.to_dict()

    SCAN_STATE.start()
    dirs_to_scan = explicit_dirs or get_scan_directories()
    discovered_files = []
    errors = []

    try:
        # Collect all valid audio files
        for scan_dir in dirs_to_scan:
            p = Path(scan_dir)
            if not p.exists():
                logger.info(f"Configured directory not currently accessible: {p}")
                continue
            if not p.is_dir():
                continue

            try:
                for root, _, files in os.walk(p):
                    for f in files:
                        ext = os.path.splitext(f)[1].lower()
                        if ext in SUPPORTED_EXTENSIONS:
                            discovered_files.append(os.path.join(root, f))
            except PermissionError as pe:
                errors.append(f"Permission denied on {scan_dir}: {pe}")
            except Exception as ex:
                errors.append(f"Error reading {scan_dir}: {ex}")

        # Index and update database idempotently
        count = 0
        for fpath in discovered_files:
            try:
                norm_path = os.path.abspath(fpath)
                try:
                    fstat = os.stat(norm_path)
                except OSError:
                    continue

                mtime = fstat.st_mtime
                size = fstat.st_size

                # Check if existing track matches
                existing = Track.objects.filter(file_path=norm_path).first()
                if existing:
                    # If file has not changed, skip re-extracting metadata
                    if abs(existing.file_mtime - mtime) < 0.01 and existing.file_size == size:
                        count += 1
                        SCAN_STATE.update_progress(count)
                        continue

                # Extract metadata
                meta = extract_metadata(norm_path)

                with transaction.atomic():
                    Track.objects.update_or_create(
                        file_path=norm_path,
                        defaults={
                            "title": meta["title"],
                            "artist": meta["artist"],
                            "album": meta["album"],
                            "duration_seconds": meta["duration_seconds"],
                            "duration_formatted": meta["duration_formatted"],
                            "file_size": size,
                            "file_mtime": mtime,
                            "format": meta["format"],
                            "tag": meta["tag"],
                            "has_cover": meta["has_cover"],
                            "source_type": "local",
                        }
                    )
                count += 1
                SCAN_STATE.update_progress(count)
            except Exception as e:
                errors.append(f"Error processing {fpath}: {e}")

    except Exception as general_err:
        errors.append(f"Scan failed: {general_err}")
    finally:
        total = Track.objects.count()
        SCAN_STATE.finish(total, errors)
        logger.info(f"Music scan finished. Total tracks in library: {total}. Discovered: {len(discovered_files)}")

    return SCAN_STATE.to_dict()


def start_background_scan():
    """Spawns scan in a background daemon thread."""
    if SCAN_STATE.is_scanning:
        return False
    t = threading.Thread(target=scan_music_library, daemon=True, name="SonoraMusicScanner")
    t.start()
    return True
