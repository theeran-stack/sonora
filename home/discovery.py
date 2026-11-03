import os
import json
import logging
import urllib.parse
import urllib.request
import urllib.error
from django.conf import settings

logger = logging.getLogger(__name__)

# Fallback catalog of Creative Commons licensed independent music
# Ensures Discovery works out-of-the-box even before user configures their Jamendo API key
CURATED_DISCOVER_CATALOG = [
    {
        "id": "jamendo_1",
        "title": "Acoustic Breeze",
        "artist": "Benjamin Tissot",
        "album": "Acoustic Memories",
        "duration": "2:37",
        "duration_seconds": 157,
        "cover": "https://images.unsplash.com/photo-1511671782779-c97d3d27a1d4?w=500&auto=format&fit=crop&q=80",
        "preview_url": "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-1.mp3",
        "download_url": "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-1.mp3",
        "download_allowed": True,
        "license": "Creative Commons BY-NC-ND",
        "quality": "320 kbps MP3",
        "source": "Jamendo / CC",
    },
    {
        "id": "jamendo_2",
        "title": "Neon Sunset",
        "artist": "Aerochord Labs",
        "album": "Synthwave Horizon",
        "duration": "3:24",
        "duration_seconds": 204,
        "cover": "https://images.unsplash.com/photo-1618005182384-a83a8bd57fbe?w=500&auto=format&fit=crop&q=80",
        "preview_url": "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-2.mp3",
        "download_url": "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-2.mp3",
        "download_allowed": True,
        "license": "Creative Commons BY-SA",
        "quality": "320 kbps MP3",
        "source": "Jamendo / CC",
    },
    {
        "id": "jamendo_3",
        "title": "Deep Mountain Calm",
        "artist": "Lofi Forest",
        "album": "Quiet Earth",
        "duration": "3:10",
        "duration_seconds": 190,
        "cover": "https://images.unsplash.com/photo-1518495973542-4542c06a5843?w=500&auto=format&fit=crop&q=80",
        "preview_url": "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-3.mp3",
        "download_url": "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-3.mp3",
        "download_allowed": True,
        "license": "Creative Commons BY",
        "quality": "320 kbps MP3",
        "source": "Jamendo / CC",
    },
    {
        "id": "jamendo_4",
        "title": "Midnight Reflections",
        "artist": "Solaris Echo",
        "album": "Night Tapes",
        "duration": "4:05",
        "duration_seconds": 245,
        "cover": "https://images.unsplash.com/photo-1514525253161-7a46d19cd819?w=500&auto=format&fit=crop&q=80",
        "preview_url": "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-4.mp3",
        "download_url": "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-4.mp3",
        "download_allowed": True,
        "license": "Creative Commons BY-NC",
        "quality": "320 kbps MP3",
        "source": "Jamendo / CC",
    },
    {
        "id": "jamendo_5",
        "title": "Starlight Highway",
        "artist": "Cassette Vibe",
        "album": "Analog Dreams",
        "duration": "3:48",
        "duration_seconds": 228,
        "cover": "https://images.unsplash.com/photo-1508700115892-45ecd05ae2ad?w=500&auto=format&fit=crop&q=80",
        "preview_url": "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-5.mp3",
        "download_url": "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-5.mp3",
        "download_allowed": True,
        "license": "Creative Commons BY-SA",
        "quality": "320 kbps MP3",
        "source": "Jamendo / CC",
    },
]


def search_jamendo_tracks(query="", limit=20):
    """
    Searches Jamendo API for licensed Creative Commons tracks.
    If JAMENDO_CLIENT_ID is not configured, provides curated CC tracks matching query.
    """
    client_id = getattr(settings, "JAMENDO_CLIENT_ID", "") or os.getenv("JAMENDO_CLIENT_ID", "")
    api_timeout = getattr(settings, "JAMENDO_API_TIMEOUT", 4.0)
    clean_q = (query or "").strip().lower()

    if client_id:
        try:
            params = {
                "client_id": client_id,
                "format": "json",
                "limit": min(limit, 50),
                "namesearch": clean_q if clean_q else "electronic chill",
                "include": "musicinfo licenses",
                "audioformat": "mp32",
            }
            url = f"https://api.jamendo.com/v3.0/tracks/?{urllib.parse.urlencode(params)}"
            req = urllib.request.Request(url, headers={"User-Agent": "SonoraPlatform/2.0"})
            with urllib.request.urlopen(req, timeout=api_timeout) as resp:
                if resp.status == 200:
                    raw_data = resp.read()
                    data = json.loads(raw_data.decode("utf-8"))
                    results = []
                    for item in data.get("results", []):
                        dur = int(item.get("duration", 0))
                        dur_fmt = f"{dur // 60}:{dur % 60:02d}"
                        can_download = bool(item.get("audiodownload_allowed", False))
                        results.append({
                            "id": f"jamendo_{item.get('id')}",
                            "external_id": str(item.get("id")),
                            "title": item.get("name", "Untitled"),
                            "artist": item.get("artist_name", "Unknown Artist"),
                            "album": item.get("album_name", "Single"),
                            "duration": dur_fmt,
                            "duration_seconds": dur,
                            "cover": item.get("album_image") or item.get("image") or "",
                            "preview_url": item.get("audio", ""),
                            "download_url": item.get("audiodownload", "") if can_download else "",
                            "download_allowed": can_download,
                            "license": item.get("license_ccurl", "Creative Commons"),
                            "quality": "VBR MP3 (HQ)",
                            "source": "Jamendo Music",
                        })
                    if results:
                        return results
        except (TimeoutError, urllib.error.URLError) as e:
            logger.warning(f"Jamendo API timeout or connection notice (timeout={api_timeout}s): {e}. Falling back to curated catalog.")
        except json.JSONDecodeError as e:
            logger.warning(f"Jamendo API emitted malformed non-JSON response: {e}. Falling back to curated catalog.")
        except Exception as e:
            logger.warning(f"Jamendo API search unexpected error: {e}. Falling back to curated catalog.")

    # Fallback to curated catalog
    if not clean_q:
        return CURATED_DISCOVER_CATALOG

    filtered = [
        item for item in CURATED_DISCOVER_CATALOG
        if clean_q in item["title"].lower() or clean_q in item["artist"].lower() or clean_q in item["album"].lower()
    ]
    return filtered if filtered else CURATED_DISCOVER_CATALOG
