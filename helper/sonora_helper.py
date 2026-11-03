#!/usr/bin/env python
"""
SONORA Windows Background Helper Service
-----------------------------------------
- Real-time filesystem folder monitoring via Watchdog
- Automatic background download processor for Jamendo and discovered tracks
- Local-to-Hosted library metadata and audio synchronization
- Single-instance lock and graceful shutdown support
"""

import os
import sys
import time
import json
import signal
import atexit
import logging
import threading
from pathlib import Path

# Setup logging
LOG_DIR = Path(__file__).resolve().parent / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "helper.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(threadName)s] %(message)s",
    handlers=[
        logging.FileHandler(str(LOG_FILE), encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("SonoraHelper")

# Setup Django Environment if running from Sonora repository
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "myproject.settings")

try:
    import django
    django.setup()
    from home.models import Track, DownloadTask
    from home.scanner import index_single_file, scan_music_library, compute_file_hash
    HAS_DJANGO = True
    logger.info("Django runtime successfully initialized in helper process.")
except Exception as e:
    HAS_DJANGO = False
    logger.warning(f"Django direct setup unavailable: {e}. Running in HTTP client mode.")

import requests
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

PID_FILE = Path(__file__).resolve().parent / "sonora_helper.pid"
CONFIG_FILE = Path(__file__).resolve().parent / "config.json"
CONFIG_LOCAL_FILE = Path(__file__).resolve().parent / "config.local.json"


def load_config():
    # Dynamic music directory resolution: environment var -> user's Documents/My musicc (if exists) -> ~/Music -> fallback
    default_music_dir = os.getenv("SONORA_MUSIC_DIR")
    if not default_music_dir:
        cand_doc = Path.home() / "Documents" / "My musicc"
        if cand_doc.exists():
            default_music_dir = str(cand_doc)
        else:
            default_music_dir = str(Path.home() / "Music")

    config = {
        "music_dir": default_music_dir,
        "server_url": os.getenv("SONORA_SERVER_URL", "http://127.0.0.1:8000"),
        "sync_token": os.getenv("SONORA_SYNC_TOKEN", ""),
        "poll_interval_seconds": int(os.getenv("SONORA_POLL_INTERVAL", "15")),
        "sync_enabled": True,
        "auto_download": True,
    }

    # 1. Base shareable configuration (config.json)
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    for k, v in data.items():
                        if v != "" and v is not None:
                            config[k] = v
        except Exception as e:
            logger.warning(f"Error loading config.json: {e}")

    # 2. Local private overrides (config.local.json - takes precedence over config.json)
    if CONFIG_LOCAL_FILE.exists():
        try:
            with open(CONFIG_LOCAL_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    for k, v in data.items():
                        if v != "" and v is not None:
                            config[k] = v
        except Exception as e:
            logger.warning(f"Error loading config.local.json: {e}")

    # 3. Environment variable fallback for sync_token if still empty
    if not config.get("sync_token"):
        env_token = os.getenv("SONORA_SYNC_TOKEN", "")
        if env_token:
            config["sync_token"] = env_token

    # 4. Authentication token presence check (never log actual token)
    if not config.get("sync_token"):
        logger.warning(
            "Authentication Notice: 'sync_token' is not configured. "
            "Helper requests requiring X-Sonora-Token will receive 401 Unauthorized."
        )
    else:
        logger.info("Authentication configured: sync_token is loaded (token masked for security).")

    return config


# ==============================================================================
# SINGLE-INSTANCE PID LOCK
# ==============================================================================

def acquire_pid_lock():
    if PID_FILE.exists():
        try:
            old_pid = int(PID_FILE.read_text().strip())
            # Check if process is still active on Windows
            if os.name == "nt":
                import ctypes
                PROCESS_QUERY_INFORMATON = 0x1000
                processHandle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_INFORMATON, 0, old_pid)
                if processHandle != 0:
                    ctypes.windll.kernel32.CloseHandle(processHandle)
                    logger.error(f"Another Sonora Helper instance (PID {old_pid}) is already running. Exiting.")
                    sys.exit(0)
            else:
                os.kill(old_pid, 0)
                logger.error(f"Another Sonora Helper instance (PID {old_pid}) is running. Exiting.")
                sys.exit(0)
        except (ValueError, OSError):
            pass

    PID_FILE.write_text(str(os.getpid()))
    atexit.register(release_pid_lock)


def release_pid_lock():
    try:
        if PID_FILE.exists():
            PID_FILE.unlink()
    except Exception:
        pass


# ==============================================================================
# WATCHDOG MUSIC FOLDER MONITOR
# ==============================================================================

SUPPORTED_EXTS = {".mp3", ".wav", ".flac", ".m4a", ".ogg"}

class MusicFolderHandler(FileSystemEventHandler):
    def __init__(self, helper):
        super().__init__()
        self.helper = helper
        self._last_events = {}
        self._lock = threading.Lock()

    def _debounce(self, path):
        with self._lock:
            now = time.time()
            if path in self._last_events and (now - self._last_events[path]) < 1.0:
                return False
            self._last_events[path] = now
            return True

    def on_created(self, event):
        if event.is_directory:
            return
        ext = os.path.splitext(event.src_path)[1].lower()
        if ext in SUPPORTED_EXTS and self._debounce(event.src_path):
            logger.info(f"[Watchdog] New audio file detected: {event.src_path}")
            threading.Thread(target=self._process_file, args=(event.src_path,)).start()

    def on_modified(self, event):
        if event.is_directory:
            return
        ext = os.path.splitext(event.src_path)[1].lower()
        if ext in SUPPORTED_EXTS and self._debounce(event.src_path):
            logger.info(f"[Watchdog] Audio file modified: {event.src_path}")
            threading.Thread(target=self._process_file, args=(event.src_path,)).start()

    def on_deleted(self, event):
        if event.is_directory:
            return
        ext = os.path.splitext(event.src_path)[1].lower()
        if ext in SUPPORTED_EXTS:
            logger.info(f"[Watchdog] Audio file removed: {event.src_path}")
            if HAS_DJANGO:
                try:
                    norm = os.path.abspath(event.src_path)
                    track = Track.objects.filter(file_path=norm).first()
                    if track and track.is_available:
                        track.is_available = False
                        track.save(update_fields=["is_available"])
                        logger.info(f"[Watchdog] Marked track id={track.id} as missing safely.")
                except Exception as e:
                    logger.warning(f"Error updating missing track state: {e}")

    def _process_file(self, fpath):
        time.sleep(1.0)  # Wait for file write completion
        if not os.path.exists(fpath):
            return
        if HAS_DJANGO:
            status, track = index_single_file(fpath)
            logger.info(f"[Watchdog] Indexed {fpath}: status={status}")
            if status in ("indexed", "renamed") and hasattr(track, "id"):
                self.helper.sync_single_track_to_hosted(track)


# ==============================================================================
# MAIN HELPER SERVICE
# ==============================================================================

class SonoraWindowsHelper:
    def __init__(self):
        self.config = load_config()
        self.running = True
        self.observer = None
        self.download_thread = None
        self.sync_thread = None

    def start(self):
        acquire_pid_lock()
        logger.info("==================================================")
        logger.info("SONORA Windows Background Helper Service Started")
        logger.info(f"Target Music Directory: {self.config['music_dir']}")
        logger.info(f"Server URL: {self.config['server_url']}")
        logger.info("==================================================")

        # Setup Signal Handlers
        signal.signal(signal.SIGINT, self.stop)
        signal.signal(signal.SIGTERM, self.stop)

        # 1. Start Watchdog Folder Monitoring
        music_path = Path(self.config["music_dir"])
        if music_path.exists() and music_path.is_dir():
            self.observer = Observer()
            handler = MusicFolderHandler(self)
            self.observer.schedule(handler, str(music_path), recursive=True)
            self.observer.start()
            logger.info(f"Watchdog active: Monitoring {music_path} recursively.")
        else:
            logger.warning(f"Configured music directory does not exist: {music_path}")

        # 2. Start Download Processor Thread
        if self.config.get("auto_download", True):
            self.download_thread = threading.Thread(
                target=self._download_loop, name="DownloadWorker", daemon=True
            )
            self.download_thread.start()

        # 3. Start Hosted Synchronizer Thread
        if self.config.get("sync_enabled", True):
            self.sync_thread = threading.Thread(
                target=self._sync_loop, name="SyncWorker", daemon=True
            )
            self.sync_thread.start()

        # Keep alive in main thread
        try:
            while self.running:
                time.sleep(1)
        except (KeyboardInterrupt, SystemExit):
            self.stop()

    def stop(self, *args):
        if not self.running:
            return
        logger.info("Stopping SONORA Windows Helper gracefully...")
        self.running = False
        if self.observer:
            self.observer.stop()
            self.observer.join(timeout=3)
        release_pid_lock()
        logger.info("Helper service stopped.")
        sys.exit(0)

    # ==========================================================================
    # DOWNLOAD QUEUE PROCESSOR
    # ==========================================================================

    def _download_loop(self):
        while self.running:
            try:
                self.process_pending_downloads()
            except Exception as e:
                logger.error(f"Error in download loop: {e}", exc_info=True)
            time.sleep(self.config.get("poll_interval_seconds", 15))

    def process_pending_downloads(self):
        server_url = self.config["server_url"].rstrip("/")
        sync_token = self.config["sync_token"]
        headers = {"X-Sonora-Token": sync_token, "User-Agent": "SonoraHelper/2.0"}

        # 1. Fetch pending tasks from server
        try:
            resp = requests.get(f"{server_url}/api/sync/queue/", headers=headers, timeout=8)
            if resp.status_code != 200:
                return
            data = resp.json()
            tasks = data.get("tasks", [])
        except Exception as e:
            logger.debug(f"Could not reach server {server_url}: {e}")
            return

        for task in tasks:
            if not self.running:
                break
            self._download_single_task(task, server_url, headers)

    def _download_single_task(self, task, server_url, headers):
        task_id = task["id"]
        title = task["title"]
        artist = task["artist"]
        album = task.get("album") or "Single"
        download_url = task["download_url"]

        logger.info(f"[DownloadManager] Processing task #{task_id}: {title} - {artist}")

        # Update status: downloading
        self._update_task_status(server_url, headers, task_id, "downloading", 15)

        # Sanitize folder and file names
        def sanitize(s):
            import re
            return re.sub(r'[\\/*?:"<>|]', "", str(s)).strip() or "Unknown"

        safe_artist = sanitize(artist)
        safe_album = sanitize(album)
        safe_title = sanitize(title)

        music_root = Path(self.config["music_dir"])
        target_dir = music_root / safe_artist / safe_album
        target_dir.mkdir(parents=True, exist_ok=True)
        target_file = target_dir / f"{safe_title}.mp3"

        if target_file.exists():
            logger.info(f"Target file already exists: {target_file}. Skipping download.")
            if HAS_DJANGO:
                index_single_file(str(target_file))
            self._update_task_status(server_url, headers, task_id, "completed", 100, str(target_file))
            return

        # Download stream with progress
        try:
            r = requests.get(download_url, stream=True, timeout=20)
            if r.status_code != 200:
                raise Exception(f"HTTP {r.status_code} downloading audio stream")

            total_size = int(r.headers.get("content-length", 0))
            downloaded = 0
            temp_file = target_dir / f"{safe_title}.mp3.tmp"

            with open(temp_file, "wb") as f:
                for chunk in r.iter_content(chunk_size=64 * 1024):
                    if not self.running:
                        temp_file.unlink(missing_ok=True)
                        return
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total_size > 0:
                            pct = min(90, 20 + int((downloaded / total_size) * 70))
                            self._update_task_status(server_url, headers, task_id, "downloading", pct)

            temp_file.rename(target_file)
            logger.info(f"Successfully downloaded to: {target_file}")

            # Embed Mutagen tags
            self._tag_downloaded_file(target_file, title, artist, album, task.get("cover_url"))

            # Index into local database
            if HAS_DJANGO:
                index_single_file(str(target_file))

            # Mark completed
            self._update_task_status(server_url, headers, task_id, "completed", 100, str(target_file))

        except Exception as ex:
            logger.error(f"Download failed for task #{task_id}: {ex}")
            self._update_task_status(server_url, headers, task_id, "failed", 0, "", str(ex))

    def _tag_downloaded_file(self, fpath, title, artist, album, cover_url=None):
        try:
            import mutagen
            from mutagen.easyid3 import EasyID3
            from mutagen.id3 import ID3, APIC
            try:
                audio = EasyID3(str(fpath))
            except Exception:
                audio = mutagen.File(str(fpath), easy=True)
                audio.add_tags()
            audio["title"] = title
            audio["artist"] = artist
            audio["album"] = album
            audio.save()

            if cover_url:
                c_resp = requests.get(cover_url, timeout=5)
                if c_resp.status_code == 200:
                    id3 = ID3(str(fpath))
                    id3.add(APIC(
                        encoding=3,
                        mime="image/jpeg",
                        type=3,
                        desc="Cover",
                        data=c_resp.content
                    ))
                    id3.save()
        except Exception as e:
            logger.warning(f"Could not apply ID3 tags to {fpath}: {e}")

    def _update_task_status(self, server_url, headers, task_id, status, progress, file_path="", error_message=""):
        payload = {
            "task_id": task_id,
            "status": status,
            "progress": progress,
            "file_path": file_path,
            "error_message": error_message,
        }
        try:
            requests.post(f"{server_url}/api/sync/queue/update/", json=payload, headers=headers, timeout=5)
        except Exception:
            pass

        if HAS_DJANGO:
            try:
                task = DownloadTask.objects.filter(id=task_id).first()
                if task:
                    task.status = status
                    task.progress = progress
                    if file_path:
                        task.file_path = file_path
                    if error_message:
                        task.error_message = error_message
                    task.save()
            except Exception:
                pass

    # ==========================================================================
    # HOSTED SYNCHRONIZER
    # ==========================================================================

    def _sync_loop(self):
        while self.running:
            try:
                self.sync_library_to_hosted()
            except Exception as e:
                logger.error(f"Error in sync loop: {e}", exc_info=True)
            time.sleep(60)

    def sync_library_to_hosted(self):
        server_url = self.config["server_url"].rstrip("/")
        # If server_url is localhost, no remote sync needed
        if "127.0.0.1" in server_url or "localhost" in server_url:
            return

        if not HAS_DJANGO:
            return

        headers = {"X-Sonora-Token": self.config["sync_token"]}
        available_tracks = Track.objects.filter(is_available=True)
        if not available_tracks.exists():
            return

        payload = []
        for t in available_tracks[:100]:
            rel_name = Path(t.file_path).name if t.file_path else f"{t.title}.mp3"
            payload.append({
                "title": t.title,
                "artist": t.artist,
                "album": t.album,
                "duration_seconds": t.duration_seconds,
                "duration_formatted": t.duration_formatted,
                "format": t.format,
                "tag": t.tag,
                "has_cover": t.has_cover,
                "file_hash": t.file_hash,
                "relative_name": rel_name,
            })

        try:
            r = requests.post(f"{server_url}/api/sync/tracks/", json={"tracks": payload}, headers=headers, timeout=10)
            if r.status_code == 200:
                logger.info(f"Successfully synchronized {len(payload)} tracks to hosted server.")
        except Exception as e:
            logger.debug(f"Could not sync with hosted server: {e}")

    def sync_single_track_to_hosted(self, track):
        server_url = self.config["server_url"].rstrip("/")
        if "127.0.0.1" in server_url or "localhost" in server_url:
            return
        headers = {"X-Sonora-Token": self.config["sync_token"]}
        rel_name = Path(track.file_path).name
        payload = [{
            "title": track.title,
            "artist": track.artist,
            "album": track.album,
            "duration_seconds": track.duration_seconds,
            "duration_formatted": track.duration_formatted,
            "format": track.format,
            "tag": track.tag,
            "has_cover": track.has_cover,
            "file_hash": track.file_hash,
            "relative_name": rel_name,
        }]
        try:
            requests.post(f"{server_url}/api/sync/tracks/", json={"tracks": payload}, headers=headers, timeout=5)
        except Exception:
            pass


if __name__ == "__main__":
    helper = SonoraWindowsHelper()
    helper.start()
