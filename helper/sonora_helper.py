#!/usr/bin/env python
"""
SONORA Windows Background Helper Service
-----------------------------------------
- Real-time filesystem folder monitoring via Watchdog
- On-demand and server-relayed recursive music folder scanning
- Mutagen audio metadata extraction (title, artist, album, duration, genre, track #, cover)
- Offline-resilient pending synchronization queue (pending_sync.json)
- Embedded localhost HTTP streaming bridge with RFC 7233 byte-range support & PNA headers
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
import http.server
import urllib.parse
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
    from home.models import Track, DownloadTask, SyncLog
    from home.scanner import index_single_file, scan_music_library, compute_file_hash, SCAN_STATE
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
PENDING_SYNC_FILE = Path(__file__).resolve().parent / "pending_sync.json"


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
        "bridge_port": int(os.getenv("SONORA_BRIDGE_PORT", "8765")),
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

    # 3. Environment variable overrides (if explicitly set in environment)
    env_server_url = os.getenv("SONORA_SERVER_URL")
    if env_server_url:
        config["server_url"] = env_server_url

    env_music_dir = os.getenv("SONORA_MUSIC_DIR")
    if env_music_dir:
        config["music_dir"] = env_music_dir

    env_token = os.getenv("SONORA_SYNC_TOKEN")
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
# LOCAL HTTP BRIDGE SERVER (RFC 7233 STREAMING + PNA HEADERS)
# ==============================================================================

class HelperBridgeHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Silence routine poll spam
        pass

    def _set_cors_headers(self, status=200, content_type="application/json"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS, HEAD")
        self.send_header("Access-Control-Allow-Headers", "Range, Content-Type, Authorization, X-Sonora-Token, Access-Control-Request-Private-Network")
        self.send_header("Access-Control-Expose-Headers", "Content-Range, Content-Length, Accept-Ranges")
        self.send_header("Access-Control-Allow-Private-Network", "true")
        self.send_header("Accept-Ranges", "bytes")

    def do_OPTIONS(self):
        self._set_cors_headers(204)
        self.send_header("Access-Control-Max-Age", "86400")
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"

        if path in ("/status", ""):
            self._set_cors_headers(200)
            self.end_headers()
            data = {
                "status": "online",
                "service": "SonoraWindowsHelper",
                "version": "2.0",
                "music_dir": self.server.helper.config.get("music_dir", ""),
                "server_url": self.server.helper.config.get("server_url", ""),
                "is_scanning": self.server.helper.is_scanning,
                "scan_metrics": self.server.helper.last_scan_metrics,
                "pending_sync_count": self.server.helper.get_pending_sync_count(),
                "sync_status": self.server.helper.sync_status,
            }
            self.wfile.write(json.dumps(data).encode("utf-8"))
            return

        if path == "/health":
            self._set_cors_headers(200)
            self.end_headers()
            self.wfile.write(json.dumps({"status": "healthy"}).encode("utf-8"))
            return

        if path.startswith("/stream/"):
            parts = path.split("/")
            try:
                track_id = int(parts[2])
            except (IndexError, ValueError):
                self._set_cors_headers(400)
                self.end_headers()
                self.wfile.write(json.dumps({"error": "Invalid track ID"}).encode("utf-8"))
                return

            self._stream_local_track(track_id)
            return

        self._set_cors_headers(404)
        self.end_headers()
        self.wfile.write(json.dumps({"error": "Not Found"}).encode("utf-8"))

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path == "/scan":
            self._set_cors_headers(200)
            self.end_headers()
            started = self.server.helper.start_background_scan()
            resp = {
                "status": "started" if started else "in_progress",
                "message": "Local library scan initiated" if started else "Scan already in progress",
                "is_scanning": self.server.helper.is_scanning,
            }
            self.wfile.write(json.dumps(resp).encode("utf-8"))
            return

        self._set_cors_headers(404)
        self.end_headers()
        self.wfile.write(json.dumps({"error": "Not Found"}).encode("utf-8"))

    def _stream_local_track(self, track_id):
        if not HAS_DJANGO:
            self._set_cors_headers(503)
            self.end_headers()
            self.wfile.write(json.dumps({"error": "Django not loaded in helper process"}).encode("utf-8"))
            return

        try:
            track = Track.objects.get(id=track_id)
        except Track.DoesNotExist:
            self._set_cors_headers(404)
            self.end_headers()
            self.wfile.write(json.dumps({"error": "ERR_STREAM_NOT_FOUND", "message": f"Track {track_id} not found"}).encode("utf-8"))
            return

        file_path = track.file_path
        if not file_path or not os.path.isfile(file_path):
            self._set_cors_headers(404)
            self.end_headers()
            self.wfile.write(json.dumps({"error": "ERR_STREAM_NOT_FOUND", "message": f"File missing: {file_path}"}).encode("utf-8"))
            return

        file_size = os.path.getsize(file_path)
        ext = os.path.splitext(file_path)[1].lower()
        mime_types = {
            ".mp3": "audio/mpeg",
            ".wav": "audio/wav",
            ".flac": "audio/flac",
            ".m4a": "audio/mp4",
            ".ogg": "audio/ogg",
        }
        content_type = mime_types.get(ext, "application/octet-stream")

        range_header = self.headers.get("Range", "").strip()
        import re
        range_match = re.match(r"bytes=(?:(\d+)-(\d*)|-(\d+))", range_header) if range_header else None

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
                self._set_cors_headers(416)
                self.send_header("Content-Range", f"bytes */{file_size}")
                self.end_headers()
                return

            last_byte = min(last_byte, file_size - 1)
            length = last_byte - first_byte + 1

            self._set_cors_headers(206, content_type)
            self.send_header("Content-Range", f"bytes {first_byte}-{last_byte}/{file_size}")
            self.send_header("Content-Length", str(length))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()

            try:
                with open(file_path, "rb") as f:
                    f.seek(first_byte)
                    remaining = length
                    while remaining > 0:
                        chunk = f.read(min(remaining, 64 * 1024))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        remaining -= len(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass
        else:
            self._set_cors_headers(200, content_type)
            self.send_header("Content-Length", str(file_size))
            self.send_header("Cache-Control", "public, max-age=3600")
            self.end_headers()

            try:
                with open(file_path, "rb") as f:
                    while True:
                        chunk = f.read(64 * 1024)
                        if not chunk:
                            break
                        self.wfile.write(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass


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
        self.bridge_server = None
        self.bridge_thread = None

        self.is_scanning = False
        self.last_scan_metrics = {
            "discovered_count": 0,
            "indexed_count": 0,
            "skipped_count": 0,
            "failed_count": 0,
            "missing_count": 0,
            "total_tracks": 0,
            "message": "Ready to scan",
        }
        self.sync_status = "idle"  # idle, syncing, synced, offline, error, unauthorized

        self._scan_lock = threading.Lock()
        self._queue_lock = threading.Lock()

    def start(self):
        acquire_pid_lock()
        logger.info("==================================================")
        logger.info("SONORA Windows Background Helper Service Started")
        logger.info(f"Target Music Directory: {self.config['music_dir']}")
        logger.info(f"Server URL: {self.config['server_url']}")
        logger.info("==================================================")

        # Setup Signal Handlers if in main thread
        try:
            signal.signal(signal.SIGINT, self.stop)
            signal.signal(signal.SIGTERM, self.stop)
        except (ValueError, AttributeError):
            pass

        # 1. Start Local HTTP Bridge
        self._start_bridge()

        # 2. Start Watchdog Folder Monitoring
        music_path = Path(self.config["music_dir"])
        if music_path.exists() and music_path.is_dir():
            self.observer = Observer()
            handler = MusicFolderHandler(self)
            self.observer.schedule(handler, str(music_path), recursive=True)
            self.observer.start()
            logger.info(f"Watchdog active: Monitoring {music_path} recursively.")
        else:
            logger.warning(f"Configured music directory does not exist or is not a directory: {music_path}")

        # 3. Start Download Processor Thread
        if self.config.get("auto_download", True):
            self.download_thread = threading.Thread(
                target=self._download_loop, name="DownloadWorker", daemon=True
            )
            self.download_thread.start()

        # 4. Start Hosted Synchronizer Thread
        if self.config.get("sync_enabled", True):
            self.sync_thread = threading.Thread(
                target=self._sync_loop, name="SyncWorker", daemon=True
            )
            self.sync_thread.start()

        # Drain any initial pending items left from previous runs
        threading.Thread(target=self.drain_pending_sync, name="InitialDrain", daemon=True).start()

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
            try:
                self.observer.stop()
                self.observer.join(timeout=3)
            except Exception:
                pass
        if self.bridge_server:
            try:
                self.bridge_server.shutdown()
                self.bridge_server.server_close()
            except Exception:
                pass
        release_pid_lock()
        logger.info("Helper service stopped.")
        sys.exit(0)

    def _start_bridge(self):
        port = self.config.get("bridge_port", 8765)
        try:
            self.bridge_server = http.server.ThreadingHTTPServer(("127.0.0.1", port), HelperBridgeHandler)
            self.bridge_server.helper = self
            self.bridge_thread = threading.Thread(
                target=self.bridge_server.serve_forever,
                name="BridgeServer",
                daemon=True,
            )
            self.bridge_thread.start()
            logger.info(f"Local HTTP Bridge listening on http://127.0.0.1:{port} (CORS & PNA enabled)")
        except OSError as e:
            logger.warning(f"Could not bind bridge server to port {port}: {e}. Local HTTP bridge disabled.")

    # ==========================================================================
    # PENDING SYNCHRONIZATION QUEUE (OFFLINE RESILIENCE)
    # ==========================================================================

    def _load_pending_sync(self):
        with self._queue_lock:
            if PENDING_SYNC_FILE.exists():
                try:
                    with open(PENDING_SYNC_FILE, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        if isinstance(data, dict) and "pending_tracks" in data:
                            return data
                except Exception as e:
                    logger.warning(f"Error reading pending_sync.json: {e}")
            return {
                "pending_tracks": [],
                "last_sync_attempt": None,
                "last_sync_status": "idle",
                "last_sync_error": "",
            }

    def _save_pending_sync(self, data):
        with self._queue_lock:
            try:
                tmp_file = PENDING_SYNC_FILE.with_suffix(".tmp")
                with open(tmp_file, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2)
                tmp_file.replace(PENDING_SYNC_FILE)
            except Exception as e:
                logger.error(f"Error saving pending_sync.json: {e}")

    def add_to_pending_sync(self, tracks):
        """Adds tracks to pending queue, deduplicated by file_hash / file_path."""
        if not tracks:
            return 0
        data = self._load_pending_sync()
        existing_keys = {
            t.get("file_hash") or t.get("file_path") or t.get("title")
            for t in data["pending_tracks"]
        }
        added_count = 0
        for track in tracks:
            key = track.get("file_hash") or track.get("file_path") or track.get("title")
            if key and key not in existing_keys:
                data["pending_tracks"].append(track)
                existing_keys.add(key)
                added_count += 1
        if added_count > 0:
            self._save_pending_sync(data)
            logger.info(f"Added {added_count} tracks to pending sync queue (total pending: {len(data['pending_tracks'])}).")
        return added_count

    def get_pending_sync_count(self):
        data = self._load_pending_sync()
        return len(data.get("pending_tracks", []))

    def drain_pending_sync(self):
        """
        Attempts to push pending tracks to the hosted backend.
        Preserves queue if offline or unreachable.
        """
        data = self._load_pending_sync()
        pending = data.get("pending_tracks", [])
        if not pending:
            self.sync_status = "synced"
            return 0

        server_url = self.config["server_url"].rstrip("/")
        headers = {
            "X-Sonora-Token": self.config.get("sync_token", ""),
            "Content-Type": "application/json",
            "User-Agent": "SonoraHelper/2.0",
        }

        try:
            self.sync_status = "syncing"
            batch = pending[:100]
            resp = requests.post(
                f"{server_url}/api/sync/tracks/",
                json={"tracks": batch},
                headers=headers,
                timeout=8,
            )
            if resp.status_code == 200:
                synced_hashes = {t.get("file_hash") or t.get("file_path") for t in batch}
                data["pending_tracks"] = [
                    t for t in pending if (t.get("file_hash") or t.get("file_path")) not in synced_hashes
                ]
                data["last_sync_status"] = "synced"
                data["last_sync_attempt"] = time.strftime("%Y-%m-%d %H:%M:%S")
                data["last_sync_error"] = ""
                self._save_pending_sync(data)
                self.sync_status = "synced" if not data["pending_tracks"] else "pending"
                logger.info(f"Successfully synchronized {len(batch)} tracks to server. Remaining pending: {len(data['pending_tracks'])}.")
                return len(batch)
            elif resp.status_code == 401:
                data["last_sync_status"] = "unauthorized"
                data["last_sync_error"] = "X-Sonora-Token authentication rejected (401 Unauthorized)"
                self.sync_status = "unauthorized"
                self._save_pending_sync(data)
                logger.warning("Sync failed: 401 Unauthorized. Check sync_token configuration.")
                return 0
            else:
                data["last_sync_status"] = "error"
                data["last_sync_error"] = f"Server returned HTTP {resp.status_code}"
                self.sync_status = "error"
                self._save_pending_sync(data)
                logger.warning(f"Server returned HTTP {resp.status_code} during sync.")
                return 0
        except Exception as e:
            # Offline or unreachable: safely preserve all pending scan results
            data["last_sync_status"] = "offline"
            data["last_sync_error"] = str(e)
            data["last_sync_attempt"] = time.strftime("%Y-%m-%d %H:%M:%S")
            self.sync_status = "offline"
            self._save_pending_sync(data)
            logger.info(f"Server {server_url} unreachable ({e}). Helper operating in OFFLINE mode; pending sync queue preserved.")
            return 0

    # ==========================================================================
    # ON-DEMAND AND RELAYED LOCAL SCANNING
    # ==========================================================================

    def start_background_scan(self, explicit_dir=None):
        if self.is_scanning:
            return False
        t = threading.Thread(
            target=self.scan_local_music_library,
            args=(explicit_dir,),
            name="HelperScanner",
            daemon=True,
        )
        t.start()
        return True

    def scan_local_music_library(self, explicit_dir=None):
        """
        Recursively scans the local music directory:
        1. Discovers all supported audio files.
        2. Mutagen metadata extraction.
        3. Updates local library (preserves db.sqlite3 & existing 53 tracks).
        4. Detects duplicates (via content hashes and canonical paths).
        5. Accumulates results into pending sync queue (pending_sync.json).
        6. If online, flushes sync queue and reports progress to server.
        """
        with self._scan_lock:
            self.is_scanning = True
            target_dir = explicit_dir or self.config["music_dir"]
            logger.info(f"[HelperScan] Starting recursive scan on: {target_dir}")

            # Notify server scan has started if reachable
            self._report_scan_status({
                "is_scanning": True,
                "message": f"Scanning {target_dir}...",
            })

            metrics = {
                "discovered_count": 0,
                "indexed_count": 0,
                "skipped_count": 0,
                "failed_count": 0,
                "missing_count": 0,
                "total_tracks": 0,
                "message": "",
                "errors": [],
            }

            if HAS_DJANGO:
                scan_res = scan_music_library(explicit_dirs=[target_dir])
                metrics["discovered_count"] = scan_res.get("discovered_count", 0)
                metrics["indexed_count"] = scan_res.get("indexed_count", 0)
                metrics["skipped_count"] = scan_res.get("skipped_count", 0)
                metrics["failed_count"] = scan_res.get("failed_count", 0)
                metrics["missing_count"] = scan_res.get("missing_count", 0)
                metrics["total_tracks"] = scan_res.get("total_tracks", 0)
                metrics["message"] = scan_res.get("message", "Scan complete")
                metrics["errors"] = scan_res.get("errors", [])

                # Prepare tracks from the scanned directory for synchronization
                try:
                    target_root = str(Path(target_dir).resolve())
                    local_tracks = Track.objects.filter(
                        file_path__istartswith=target_root,
                        is_available=True,
                        source_type="local"
                    )
                    payload = []
                    for t in local_tracks:
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
                            "file_path": t.file_path,
                        })
                    self.add_to_pending_sync(payload)
                except Exception as e:
                    logger.warning(f"Error queuing tracks to pending sync: {e}")
            else:
                # Standalone fallback without direct Django access
                discovered = []
                p = Path(target_dir)
                if p.exists() and p.is_dir():
                    for root, _, files in os.walk(p):
                        for f in files:
                            ext = os.path.splitext(f)[1].lower()
                            if ext in SUPPORTED_EXTS:
                                discovered.append(os.path.join(root, f))
                metrics["discovered_count"] = len(discovered)
                metrics["total_tracks"] = len(discovered)
                metrics["message"] = f"Discovered {len(discovered)} audio files in client mode."

            # Attempt synchronization
            self.drain_pending_sync()

            self.is_scanning = False
            self.last_scan_metrics = metrics

            # Report completion to server
            self._report_scan_status({
                "is_scanning": False,
                "scanned_count": metrics["discovered_count"],
                "discovered_count": metrics["discovered_count"],
                "indexed_count": metrics["indexed_count"],
                "skipped_count": metrics["skipped_count"],
                "failed_count": metrics["failed_count"],
                "missing_count": metrics["missing_count"],
                "total_tracks": metrics["total_tracks"],
                "message": metrics["message"],
                "errors": metrics.get("errors", []),
            })

            logger.info(
                f"[HelperScan] Completed: {metrics['discovered_count']} discovered, "
                f"{metrics['indexed_count']} indexed, {metrics['skipped_count']} skipped, "
                f"{metrics['failed_count']} failed. Sync status: {self.sync_status}."
            )
            return metrics

    def _report_scan_status(self, update_dict):
        server_url = self.config["server_url"].rstrip("/")
        headers = {
            "X-Sonora-Token": self.config.get("sync_token", ""),
            "Content-Type": "application/json",
            "User-Agent": "SonoraHelper/2.0",
        }
        payload = {
            "is_scanning": update_dict.get("is_scanning", self.is_scanning),
            "scanned_count": update_dict.get("scanned_count", 0),
            "discovered_count": update_dict.get("discovered_count", 0),
            "indexed_count": update_dict.get("indexed_count", 0),
            "skipped_count": update_dict.get("skipped_count", 0),
            "failed_count": update_dict.get("failed_count", 0),
            "missing_count": update_dict.get("missing_count", 0),
            "total_tracks": update_dict.get("total_tracks", 0),
            "pending_sync_count": self.get_pending_sync_count(),
            "sync_status": self.sync_status,
            "message": update_dict.get("message", ""),
            "errors": update_dict.get("errors", []),
        }
        try:
            requests.post(f"{server_url}/api/sync/scan-status/", json=payload, headers=headers, timeout=4)
        except Exception as e:
            logger.debug(f"Could not push scan status update to {server_url}: {e}")

    # ==========================================================================
    # DOWNLOAD QUEUE PROCESSOR & TASK RELAY
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

            # Check if this task is a server-relayed system command (e.g. scan_music)
            if task.get("provider") == "system_command" and task.get("title") == "scan_music":
                task_id = task["id"]
                logger.info(f"[HelperRelay] Processing scan_music command task #{task_id}")
                self._update_task_status(server_url, headers, task_id, "downloading", 20, "", "Scanning local music library...")
                metrics = self.scan_local_music_library()
                self._update_task_status(server_url, headers, task_id, "completed", 100, "", metrics.get("message", "Scan completed successfully."))
                continue

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
                self.drain_pending_sync()
            except Exception as e:
                logger.error(f"Error in sync loop: {e}", exc_info=True)
            time.sleep(30)

    def sync_single_track_to_hosted(self, track):
        rel_name = Path(track.file_path).name if track.file_path else f"{track.title}.mp3"
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
            "file_path": track.file_path,
        }]
        self.add_to_pending_sync(payload)
        self.drain_pending_sync()


if __name__ == "__main__":
    helper = SonoraWindowsHelper()

    # CLI flag support: python helper/sonora_helper.py --scan
    if "--scan" in sys.argv:
        print("Starting on-demand Sonora local music scan...")
        metrics = helper.scan_local_music_library()
        print(f"Discovered: {metrics['discovered_count']}")
        print(f"Indexed:    {metrics['indexed_count']}")
        print(f"Skipped:    {metrics['skipped_count']}")
        print(f"Failed:     {metrics['failed_count']}")
        print(f"Pending Sync: {helper.get_pending_sync_count()}")
        print(f"Sync Status:  {helper.sync_status}")
        print("Done.")
        sys.exit(0)

    if "--status" in sys.argv:
        print(f"Service:      SonoraWindowsHelper")
        print(f"Music Dir:    {helper.config['music_dir']}")
        print(f"Server URL:   {helper.config['server_url']}")
        print(f"Pending Sync: {helper.get_pending_sync_count()}")
        print(f"Sync Status:  {helper.sync_status}")
        sys.exit(0)

    helper.start()
