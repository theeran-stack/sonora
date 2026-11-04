import os
import sys
import hashlib
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
        self.discovered_count = 0
        self.indexed_count = 0
        self.skipped_count = 0
        self.missing_count = 0
        self.failed_count = 0
        self.total_tracks = 0
        self.pending_sync_count = 0
        self.sync_status = "synced"  # synced, syncing, offline, pending
        self.helper_connected = False
        self.helper_last_seen = 0.0
        self.last_scan = None
        self.message = ""
        self.errors = []
        self._lock = threading.Lock()

    def start(self):
        with self._lock:
            self.is_scanning = True
            self.scanned_count = 0
            self.discovered_count = 0
            self.indexed_count = 0
            self.skipped_count = 0
            self.missing_count = 0
            self.failed_count = 0
            self.message = "Scan started..."
            self.errors = []

    def reset(self):
        with self._lock:
            self.is_scanning = False
            self.scanned_count = 0
            self.discovered_count = 0
            self.indexed_count = 0
            self.skipped_count = 0
            self.missing_count = 0
            self.failed_count = 0
            self.message = ""
            self.errors = []

    def update_progress(self, count, discovered=0):
        with self._lock:
            self.scanned_count = count
            if discovered:
                self.discovered_count = discovered

    def update_sync_state(self, sync_status=None, pending_count=None, helper_connected=None):
        with self._lock:
            if sync_status is not None:
                self.sync_status = sync_status
            if pending_count is not None:
                self.pending_sync_count = pending_count
            if helper_connected is not None:
                self.helper_connected = helper_connected

    def touch_helper(self):
        with self._lock:
            import time
            self.helper_last_seen = time.time()
            self.helper_connected = True

    def finish(self, total, indexed=0, skipped=0, missing=0, failed=0, message="", errors=None):
        with self._lock:
            self.is_scanning = False
            self.total_tracks = total
            self.indexed_count = indexed
            self.skipped_count = skipped
            self.missing_count = missing
            self.failed_count = failed
            self.last_scan = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self.message = message
            if errors:
                self.errors.extend(errors)

    def to_dict(self):
        with self._lock:
            import time
            current_total = self.total_tracks if self.is_scanning else Track.objects.count()
            # If helper hasn't been seen in > 60s and we're not scanning, check connected flag
            is_helper_alive = self.helper_connected
            if self.helper_last_seen > 0:
                is_helper_alive = (time.time() - self.helper_last_seen) < 65

            return {
                "is_scanning": self.is_scanning,
                "scanned_count": self.scanned_count,
                "discovered_count": self.discovered_count,
                "indexed_count": self.indexed_count,
                "skipped_count": self.skipped_count,
                "missing_count": self.missing_count,
                "failed_count": self.failed_count,
                "total_tracks": current_total,
                "pending_sync_count": self.pending_sync_count,
                "sync_status": self.sync_status,
                "helper_connected": is_helper_alive,
                "last_scan": self.last_scan,
                "message": self.message,
                "errors": self.errors[-5:] if self.errors else [],
            }


SCAN_STATE = ScanState()


def get_scan_directories():
    """Returns list of existing and configured Path objects for scanning."""
    configured = getattr(settings, 'MUSIC_SCAN_DIRECTORIES', [])
    valid_paths = []
    for p in configured:
        try:
            p_obj = Path(p).resolve()
            valid_paths.append(p_obj)
        except Exception:
            continue
    return valid_paths


def is_safe_music_path(file_path):
    """
    Validates that a file_path is strictly inside one of the allowed MUSIC_SCAN_DIRECTORIES.
    Prevents path traversal attacks and arbitrary filesystem access.
    """
    if not file_path:
        return False
    try:
        resolved_file = Path(file_path).resolve()
        for root in get_scan_directories():
            try:
                resolved_file.relative_to(root)
                return True
            except (ValueError, RuntimeError):
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


def compute_file_hash(file_path):
    """Calculates quick SHA-256 fingerprint from size and sample chunks."""
    h = hashlib.sha256()
    try:
        size = os.path.getsize(file_path)
        h.update(str(size).encode("utf-8"))
        with open(file_path, "rb") as f:
            if size <= 1024 * 1024:
                h.update(f.read())
            else:
                h.update(f.read(65536))
                f.seek(max(0, size - 65536))
                h.update(f.read(65536))
        return h.hexdigest()
    except Exception:
        return ""


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

    track_number = ""
    try:
        audio = mutagen.File(str(path_obj))
        if audio is not None:
            if hasattr(audio, 'info') and hasattr(audio.info, 'length'):
                duration = float(audio.info.length or 0.0)

            tags = getattr(audio, 'tags', None)
            if tags:
                for t_key in ['title', 'TIT2', '\xa9nam', 'TITLE']:
                    val = tags.get(t_key)
                    if val:
                        title = str(val[0] if isinstance(val, (list, tuple)) else val).strip()
                        if title:
                            break

                for a_key in ['artist', 'TPE1', '\xa9ART', 'aART', 'ARTIST']:
                    val = tags.get(a_key)
                    if val:
                        artist = str(val[0] if isinstance(val, (list, tuple)) else val).strip()
                        if artist:
                            break

                for alb_key in ['album', 'TALB', '\xa9alb', 'ALBUM']:
                    val = tags.get(alb_key)
                    if val:
                        album = str(val[0] if isinstance(val, (list, tuple)) else val).strip()
                        if album:
                            break

                for g_key in ['genre', 'TCON', '\xa9gen', 'GENRE']:
                    val = tags.get(g_key)
                    if val:
                        genre = str(val[0] if isinstance(val, (list, tuple)) else val).strip()
                        if genre:
                            break

                for num_key in ['tracknumber', 'TRCK', 'trkn', 'TRACKNUMBER', 'track']:
                    val = tags.get(num_key)
                    if val is not None:
                        if isinstance(val, (list, tuple)) and len(val) > 0:
                            val = val[0]
                        if isinstance(val, tuple) and len(val) > 0:
                            val = val[0]
                        num_str = str(val).split('/')[0].strip()
                        if num_str.isdigit():
                            track_number = num_str
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

    if not has_cover:
        try:
            folder_mime, folder_data = find_folder_artwork(file_path)
            if folder_data:
                has_cover = True
        except Exception:
            pass

    # Map genre into Sonora vibes
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
        "track_number": track_number,
    }


def extract_cover_bytes(file_path):
    """Extracts embedded cover bytes and MIME type from audio file."""
    return extract_embedded_cover(file_path)


def index_single_file(file_path):
    """
    Indexes or updates a single audio file.
    Detects modifications, renames, and new files.
    Returns: ('indexed' | 'skipped' | 'renamed' | 'error', track_instance_or_error)
    """
    try:
        norm_path = os.path.abspath(file_path)
        if not os.path.isfile(norm_path):
            return "error", "File does not exist"

        ext = os.path.splitext(norm_path)[1].lower()
        if ext not in SUPPORTED_EXTENSIONS:
            return "skipped", "Unsupported format"

        fstat = os.stat(norm_path)
        mtime = fstat.st_mtime
        size = fstat.st_size

        # 1. Exact or normalized path match check
        existing = Track.objects.filter(file_path=norm_path).first()
        if not existing:
            # Check case-insensitive / normalized slash match
            for t in Track.objects.filter(file_size=size):
                if t.file_path and os.path.normcase(os.path.abspath(t.file_path)) == os.path.normcase(norm_path):
                    existing = t
                    break

        if existing:
            # If unchanged in size and mtime, ensure available and skip
            if abs(existing.file_mtime - mtime) < 0.01 and existing.file_size == size:
                if not existing.is_available:
                    existing.is_available = True
                    existing.save(update_fields=["is_available"])
                return "skipped", existing

            # File modified: re-extract metadata
            meta = extract_metadata(norm_path)
            f_hash = compute_file_hash(norm_path)
            with transaction.atomic():
                existing.title = meta["title"]
                existing.artist = meta["artist"]
                existing.album = meta["album"]
                existing.duration_seconds = meta["duration_seconds"]
                existing.duration_formatted = meta["duration_formatted"]
                existing.file_size = size
                existing.file_mtime = mtime
                existing.format = meta["format"]
                existing.tag = meta["tag"]
                existing.has_cover = meta["has_cover"]
                existing.is_available = True
                existing.file_hash = f_hash
                existing.save()
            return "indexed", existing

        # 2. Check for duplicate content hash or rename
        f_hash = compute_file_hash(norm_path)
        if f_hash:
            same_hash_track = Track.objects.filter(file_hash=f_hash).first()
            if same_hash_track:
                # If old path is missing or inaccessible, treat as rename / relocation
                if not os.path.exists(same_hash_track.file_path):
                    with transaction.atomic():
                        same_hash_track.file_path = norm_path
                        same_hash_track.file_size = size
                        same_hash_track.file_mtime = mtime
                        same_hash_track.is_available = True
                        same_hash_track.save()
                    return "renamed", same_hash_track
                else:
                    # Content hash matches an existing track.
                    # Verify if filename, stem, or metadata confirms it is the same track across directories
                    norm_fname = Path(norm_path).name.lower()
                    existing_fname = Path(same_hash_track.file_path).name.lower()
                    meta = extract_metadata(norm_path)

                    if (
                        norm_fname == existing_fname
                        or Path(norm_path).stem.lower() == Path(same_hash_track.file_path).stem.lower()
                        or (meta["title"].lower() == same_hash_track.title.lower() and meta["artist"].lower() == same_hash_track.artist.lower())
                    ):
                        if not same_hash_track.is_available:
                            same_hash_track.is_available = True
                            same_hash_track.save(update_fields=["is_available"])
                        return "skipped", same_hash_track

        # 3. Check for matching metadata and file size (prevents duplicating unhashed existing records)
        meta = extract_metadata(norm_path)
        candidates = Track.objects.filter(
            file_size=size,
            title__iexact=meta["title"],
            artist__iexact=meta["artist"],
        )
        if candidates.exists():
            matched_track = candidates.first()
            if f_hash and not matched_track.file_hash:
                matched_track.file_hash = f_hash
                matched_track.save(update_fields=["file_hash"])
            if not os.path.exists(matched_track.file_path):
                matched_track.file_path = norm_path
                matched_track.is_available = True
                matched_track.save(update_fields=["file_path", "is_available"])
                return "renamed", matched_track
            return "skipped", matched_track

        # 4. Truly new file: extract and create
        with transaction.atomic():
            track = Track.objects.create(
                file_path=norm_path,
                title=meta["title"],
                artist=meta["artist"],
                album=meta["album"],
                duration_seconds=meta["duration_seconds"],
                duration_formatted=meta["duration_formatted"],
                file_size=size,
                file_mtime=mtime,
                format=meta["format"],
                tag=meta["tag"],
                has_cover=meta["has_cover"],
                source_type="local",
                is_available=True,
                file_hash=f_hash,
            )
        return "indexed", track

    except Exception as e:
        logger.warning(f"Error indexing file {file_path}: {e}")
        return "error", str(e)


def scan_music_library(explicit_dirs=None):
    """
    Performs recursive, idempotent scan across configured music directories.
    - Discovers all supported audio files.
    - Accurately tracks discovered, indexed, skipped, renamed, and missing files.
    - Marks missing files safely without deleting history.
    - Non-blocking when invoked via background thread.
    """
    if SCAN_STATE.is_scanning:
        logger.info("Scan already in progress. Skipping duplicate invocation.")
        return SCAN_STATE.to_dict()

    SCAN_STATE.start()
    dirs_to_scan = explicit_dirs or get_scan_directories()
    discovered_files = []
    scanned_roots = []
    errors = []

    indexed_count = 0
    skipped_count = 0
    missing_count = 0
    failed_count = 0

    try:
        # Collect all valid audio files
        for scan_dir in dirs_to_scan:
            p = Path(scan_dir)
            if not p.exists():
                logger.info(f"Configured directory not currently accessible: {p}")
                continue
            if not p.is_dir():
                continue

            scanned_roots.append(str(p.resolve()))

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

        SCAN_STATE.discovered_count = len(discovered_files)

        if not discovered_files:
            msg = f"Zero audio files discovered across scanned locations: {[str(d) for d in dirs_to_scan]}."
            if not scanned_roots:
                msg += " (No configured directories were accessible on this host)."
            logger.info(msg)
            total = Track.objects.count()
            SCAN_STATE.finish(total=total, indexed=0, skipped=0, missing=0, failed=0, message=msg, errors=errors)
            return SCAN_STATE.to_dict()

        # Index each discovered file
        count = 0
        discovered_norm_paths = set()
        seen_file_keys = set()
        for fpath in discovered_files:
            norm_path = os.path.abspath(fpath)
            norm_key = os.path.normcase(norm_path)
            if norm_key in seen_file_keys:
                continue
            seen_file_keys.add(norm_key)
            discovered_norm_paths.add(norm_path)
            discovered_norm_paths.add(norm_key)

            status, _ = index_single_file(norm_path)
            if status in ("indexed", "renamed"):
                indexed_count += 1
            elif status == "skipped":
                skipped_count += 1
            elif status == "error":
                failed_count += 1
                errors.append(f"Failed to index {fpath}")

            count += 1
            SCAN_STATE.update_progress(count, len(discovered_files))

        # Check for missing files in scanned roots (soft availability update, no deletion)
        for root_str in scanned_roots:
            root_tracks = Track.objects.filter(file_path__startswith=root_str, source_type="local")
            for t in root_tracks:
                if t.file_path not in discovered_norm_paths:
                    if t.is_available:
                        t.is_available = False
                        t.save(update_fields=["is_available"])
                        missing_count += 1

        total = Track.objects.filter(is_available=True).count()
        success_msg = f"Scan complete: {len(discovered_files)} found, {indexed_count} indexed/updated, {skipped_count} skipped, {failed_count} failed, {missing_count} marked missing."
        SCAN_STATE.finish(
            total=total,
            indexed=indexed_count,
            skipped=skipped_count,
            missing=missing_count,
            failed=failed_count,
            message=success_msg,
            errors=errors,
        )
        logger.info(success_msg)

    except Exception as general_err:
        err_msg = f"Scan failed: {general_err}"
        errors.append(err_msg)
        logger.error(err_msg, exc_info=True)
        total = Track.objects.count()
        SCAN_STATE.finish(total=total, indexed=indexed_count, skipped=skipped_count, missing=missing_count, failed=failed_count, message=err_msg, errors=errors)

    return SCAN_STATE.to_dict()


def start_background_scan():
    """Spawns scan in a background daemon thread."""
    if SCAN_STATE.is_scanning:
        return False
    t = threading.Thread(target=scan_music_library, daemon=True, name="SonoraMusicScanner")
    t.start()
    return True
