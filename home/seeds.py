"""
Seed data definitions and seeding functions for SONORA.
Provides idempotent insertion of FAQs and ErrorCodes into the database.
"""

from .models import FAQ, ErrorCode

SEED_FAQS = [
    {
        "question": "What is SONORA?",
        "category": "general",
        "answer": (
            "SONORA is a tactile studio personal music platform built with Django, Web Audio API, and Mutagen. "
            "It streams, indexes, and manages local and hosted audio collections with rich metadata and artwork."
        ),
    },
    {
        "question": "What audio formats does SONORA support?",
        "category": "audio",
        "answer": (
            "SONORA natively supports MP3 (.mp3), WAV (.wav), FLAC (.flac), M4A (.m4a), and OGG (.ogg) audio formats. "
            "Files are scanned recursively and cataloged with ID3/Vorbis/FLAC metadata."
        ),
    },
    {
        "question": "How does SONORA scan my local music library?",
        "category": "library",
        "answer": (
            "SONORA scans configured directories recursively (such as your local Music folder or custom paths), "
            "extracting metadata and embedded album art via Mutagen. Duplicate records are prevented by matching "
            "canonical file paths and SHA-256 audio file content hashes."
        ),
    },
    {
        "question": "How does seek and scrubbing work in the music player?",
        "category": "audio",
        "answer": (
            "SONORA implements RFC 7233 HTTP Range requests (HTTP 206 Partial Content). When scrubbing or seeking, "
            "the browser sends byte range requests (e.g., bytes=1048576-2097151), enabling instantaneous seeking "
            "without having to wait for the entire audio file to download."
        ),
    },
    {
        "question": "What happens if an audio track has no album artwork?",
        "category": "audio",
        "answer": (
            "SONORA employs a 4-tier artwork resolution pipeline: 1) embedded ID3 APIC / Vorbis cover frames, "
            "2) directory image files (cover.jpg/folder.jpg), 3) web cover cache via iTunes Search API fallback, "
            "and 4) procedurally generated CSS Aurora gradients based on track ID hashing so the UI never displays broken image placeholders."
        ),
    },
    {
        "question": "Can I discover and download online tracks with SONORA?",
        "category": "general",
        "answer": (
            "Yes. SONORA integrates with the Jamendo Music API for searching Creative Commons music. "
            "Tracks can be previewed directly in the browser or added to the download queue to be saved into your local library."
        ),
    },
    {
        "question": "How does local-to-cloud synchronization work?",
        "category": "sync",
        "answer": (
            "A lightweight Windows background helper (helper/sonora_helper.py) monitors your local music folder, "
            "processes queued download tasks, and synchronizes metadata and audio tracks with your hosted SONORA instance "
            "using secure X-Sonora-Token authentication."
        ),
    },
    {
        "question": "How does SONORA protect against directory traversal attacks?",
        "category": "security",
        "answer": (
            "SONORA utilizes strict path normalization (os.path.realpath) and verifies that every requested audio file "
            "resolves strictly within configured whitelist directories (MUSIC_SCAN_DIRECTORIES). Any traversal attempt "
            "outside these boundaries is blocked with HTTP 403 Forbidden."
        ),
    },
    {
        "question": "What audio processing features are available in the player?",
        "category": "audio",
        "answer": (
            "SONORA utilizes the Web Audio API to provide real-time audio analysis, dynamic gain compression, "
            "a stereo panner, and reactive spectrum visualizers with ultra-smooth 60fps rendering."
        ),
    },
    {
        "question": "What keyboard shortcuts can I use in SONORA?",
        "category": "general",
        "answer": (
            "Keyboard shortcuts include: Spacebar (Play/Pause), Left/Right Arrows (Seek 5s backward/forward), "
            "Up/Down Arrows (Volume up/down), M (Mute/Unmute), and F (Toggle fullscreen visualizer)."
        ),
    },
    {
        "question": "Why is a track showing as unavailable?",
        "category": "troubleshooting",
        "answer": (
            "If an audio file was moved, renamed, or deleted from disk, SONORA flags is_available=False in the database "
            "rather than deleting the record. This preserves playlists, play count, and metadata history."
        ),
    },
    {
        "question": "How do I troubleshoot audio decoding errors in the browser?",
        "category": "troubleshooting",
        "answer": (
            "Check the browser console for MEDIA_ERR_DECODE or Web Audio state. Ensure the file has valid audio headers "
            "and that the sample rate and codec are supported by your browser."
        ),
    },
]

SEED_ERROR_CODES = [
    {
        "code": "ERR_PATH_TRAVERSAL_DETECTED",
        "title": "Path Traversal Detected",
        "description": "Requested audio file path resolves outside configured music whitelist directories.",
        "cause": "Client requested an audio file or stream path resolving outside allowed MUSIC_SCAN_DIRECTORIES.",
        "recovery_step": "Verify the requested track path is located within configured music directories in settings.py.",
        "http_status": 403,
    },
    {
        "code": "ERR_STREAM_NOT_FOUND",
        "title": "Audio Stream Not Found",
        "description": "The requested track ID does not exist in the database or the underlying file is missing.",
        "cause": "Invalid track ID requested or physical audio file was moved, renamed, or deleted.",
        "recovery_step": "Run library scan to update file paths or verify that the track ID exists in the library.",
        "http_status": 404,
    },
    {
        "code": "ERR_RANGE_NOT_SATISFIABLE",
        "title": "Requested Range Not Satisfiable",
        "description": "The requested byte offset exceeds the physical audio file boundaries.",
        "cause": "HTTP Range header first_byte >= file_size or first_byte > last_byte.",
        "recovery_step": "Client must reset playback position or request byte offsets within valid file size.",
        "http_status": 416,
    },
    {
        "code": "ERR_SYNC_UNAUTHORIZED",
        "title": "Sync Authentication Failed",
        "description": "Synchronization request was rejected due to missing or invalid authentication token.",
        "cause": "Missing or incorrect X-Sonora-Token in HTTP request headers.",
        "recovery_step": "Configure SONORA_SYNC_TOKEN in environment variables and update helper configuration.",
        "http_status": 401,
    },
    {
        "code": "ERR_SCAN_CONCURRENT_LOCK",
        "title": "Scan Already In Progress",
        "description": "A library scan is already active in the background.",
        "cause": "User or API triggered scan while a background scan thread is actively running.",
        "recovery_step": "Wait for the active scan thread to complete before initiating another scan.",
        "http_status": 409,
    },
    {
        "code": "ERR_DIR_INACCESSIBLE",
        "title": "Music Directory Inaccessible",
        "description": "Configured library folder or network drive is offline or inaccessible.",
        "cause": "Target directory does not exist or lacks read permissions.",
        "recovery_step": "Check drive connection, mount point, or update MUSIC_SCAN_DIRECTORIES in settings.py.",
        "http_status": 503,
    },
    {
        "code": "ERR_MUTAGEN_PARSE_FAIL",
        "title": "Metadata Extraction Failed",
        "description": "Unable to parse audio metadata tags due to invalid or corrupt audio headers.",
        "cause": "Corrupt audio headers, non-sync MPEG frames, or unsupported tag format.",
        "recovery_step": "SONORA automatically falls back to filename extraction; re-encode file if playback fails.",
        "http_status": 422,
    },
    {
        "code": "ERR_ARTWORK_API_TIMEOUT",
        "title": "Artwork Fetch Timeout",
        "description": "Remote album art search query timed out.",
        "cause": "Network disconnection or remote iTunes search API slow response (exceeded 2.5s).",
        "recovery_step": "Procedural CSS Aurora gradient used automatically; embed artwork locally or retry with network access.",
        "http_status": 504,
    },
    {
        "code": "ERR_UPLOAD_UNSUPPORTED_EXT",
        "title": "Unsupported Upload Format",
        "description": "Uploaded file format is not supported by SONORA.",
        "cause": "File extension does not match .mp3, .wav, .flac, .m4a, or .ogg.",
        "recovery_step": "Upload only audio files with supported extensions: MP3, WAV, FLAC, M4A, OGG.",
        "http_status": 400,
    },
    {
        "code": "ERR_QUEUE_PAYLOAD_INVALID",
        "title": "Malformed Queue Payload",
        "description": "Download queue request is missing required fields or has invalid JSON.",
        "cause": "Missing download_url or title in JSON body.",
        "recovery_step": "Ensure request body contains valid JSON with title, artist, and download_url.",
        "http_status": 400,
    },
]


def seed_faqs():
    """Idempotently seed FAQ records into the database without creating duplicates."""
    created_count = 0
    updated_count = 0
    for item in SEED_FAQS:
        obj, created = FAQ.objects.update_or_create(
            question=item["question"],
            defaults={
                "category": item["category"],
                "answer": item["answer"],
                "is_active": True,
            },
        )
        if created:
            created_count += 1
        else:
            updated_count += 1
    return created_count, updated_count, len(SEED_FAQS)


def seed_error_codes():
    """Idempotently seed ErrorCode records into the database without creating duplicates."""
    created_count = 0
    updated_count = 0
    for item in SEED_ERROR_CODES:
        obj, created = ErrorCode.objects.update_or_create(
            code=item["code"],
            defaults={
                "title": item["title"],
                "description": item["description"],
                "cause": item["cause"],
                "recovery_step": item["recovery_step"],
                "http_status": item["http_status"],
                "is_active": True,
            },
        )
        if created:
            created_count += 1
        else:
            updated_count += 1
    return created_count, updated_count, len(SEED_ERROR_CODES)


def seed_all():
    """Seeds both FAQs and ErrorCodes."""
    faq_created, faq_updated, faq_total = seed_faqs()
    err_created, err_updated, err_total = seed_error_codes()
    return {
        "faqs": {"created": faq_created, "updated": faq_updated, "total": faq_total},
        "error_codes": {"created": err_created, "updated": err_updated, "total": err_total},
    }

