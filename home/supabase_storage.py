"""
Supabase Storage Integration for SONORA.
Handles private bucket operations, presigned upload URLs, and presigned playback URLs.
All secret service-role credentials remain strictly server-side.
"""

import logging
import os
import posixpath
import re
import requests
from django.conf import settings

logger = logging.getLogger("home")


def get_bucket_name() -> str:
    """Returns the configured Supabase Storage bucket name."""
    return getattr(settings, "SUPABASE_STORAGE_BUCKET", "sonora-music") or "sonora-music"


def is_supabase_storage_configured() -> bool:
    """Checks whether the Supabase URL and service role key are configured."""
    url = getattr(settings, "SUPABASE_URL", "")
    key = getattr(settings, "SUPABASE_SERVICE_ROLE_KEY", "")
    return bool(url and key and url.startswith("http"))


def sanitize_storage_filename(filename: str) -> str:
    """Sanitizes user filename for safe storage keys in Supabase Storage."""
    name = re.sub(r"[^\w\s\.-]", "", filename).strip()
    name = re.sub(r"[\s]+", "_", name)
    return name or "uploaded_track.mp3"


def create_signed_upload_url(relative_path: str, expires_in: int = 900) -> dict:
    """
    Generates a secure presigned upload URL for direct browser-to-Supabase upload.
    This bypasses Vercel's 4.5 MB serverless request payload limit.
    """
    if not is_supabase_storage_configured():
        return {
            "success": False,
            "error": "ERR_STORAGE_UNCONFIGURED",
            "message": "Supabase Storage credentials are not configured on this server.",
        }

    supabase_url = getattr(settings, "SUPABASE_URL", "").rstrip("/")
    service_key = getattr(settings, "SUPABASE_SERVICE_ROLE_KEY", "")
    bucket = get_bucket_name()

    clean_path = posixpath.normpath(relative_path.lstrip("/"))
    endpoint = f"{supabase_url}/storage/v1/object/upload/sign/{bucket}/{clean_path}"

    headers = {
        "Authorization": f"Bearer {service_key}",
        "apiKey": service_key,
        "Content-Type": "application/json",
    }

    try:
        resp = requests.post(endpoint, json={"expiresIn": expires_in}, headers=headers, timeout=8)
        if resp.status_code in (200, 201):
            data = resp.json()
            # Supabase returns {"url": "/object/upload/sign/..."}
            raw_url = data.get("url", "")
            if raw_url.startswith("http"):
                upload_url = raw_url
            else:
                upload_url = f"{supabase_url}/storage/v1{raw_url}"

            return {
                "success": True,
                "upload_url": upload_url,
                "storage_path": clean_path,
                "bucket": bucket,
                "expires_in": expires_in,
            }
        else:
            logger.warning(f"Supabase Storage create_signed_upload_url returned {resp.status_code}: {resp.text}")
            return {
                "success": False,
                "error": "ERR_SIGN_UPLOAD_FAILED",
                "message": f"Supabase Storage API returned status {resp.status_code}",
                "details": resp.text,
            }
    except Exception as e:
        logger.error(f"Error requesting signed upload URL from Supabase: {e}")
        return {
            "success": False,
            "error": "ERR_STORAGE_CONNECTION",
            "message": str(e),
        }


def create_signed_playback_url(storage_path: str, expires_in: int = 3600) -> dict:
    """
    Generates a temporary signed playback URL for secure streaming from private bucket.
    Expires in 1 hour (3600s) by default.
    """
    if not is_supabase_storage_configured():
        return {
            "success": False,
            "error": "ERR_STORAGE_UNCONFIGURED",
            "message": "Supabase Storage credentials are not configured on this server.",
        }

    supabase_url = getattr(settings, "SUPABASE_URL", "").rstrip("/")
    service_key = getattr(settings, "SUPABASE_SERVICE_ROLE_KEY", "")
    bucket = get_bucket_name()

    # Extract clean path if prefixed with supabase://
    clean_path = storage_path
    if clean_path.startswith("supabase://"):
        parts = clean_path.replace("supabase://", "").split("/", 1)
        if len(parts) == 2:
            bucket = parts[0]
            clean_path = parts[1]
        else:
            clean_path = parts[0]

    clean_path = posixpath.normpath(clean_path.lstrip("/"))
    endpoint = f"{supabase_url}/storage/v1/object/sign/{bucket}/{clean_path}"

    headers = {
        "Authorization": f"Bearer {service_key}",
        "apiKey": service_key,
        "Content-Type": "application/json",
    }

    try:
        resp = requests.post(endpoint, json={"expiresIn": expires_in}, headers=headers, timeout=8)
        if resp.status_code in (200, 201):
            data = resp.json()
            raw_url = data.get("signedURL", "")
            if raw_url.startswith("http"):
                playback_url = raw_url
            else:
                playback_url = f"{supabase_url}/storage/v1{raw_url}"

            return {
                "success": True,
                "playback_url": playback_url,
                "storage_path": clean_path,
                "bucket": bucket,
                "expires_in": expires_in,
            }
        else:
            logger.warning(f"Supabase Storage create_signed_playback_url returned {resp.status_code}: {resp.text}")
            return {
                "success": False,
                "error": "ERR_SIGN_PLAYBACK_FAILED",
                "message": f"Supabase Storage API returned status {resp.status_code}",
            }
    except Exception as e:
        logger.error(f"Error requesting signed playback URL from Supabase: {e}")
        return {
            "success": False,
            "error": "ERR_STORAGE_CONNECTION",
            "message": str(e),
        }


def delete_storage_file(storage_path: str) -> bool:
    """Deletes an object from the Supabase Storage bucket."""
    if not is_supabase_storage_configured():
        return False

    supabase_url = getattr(settings, "SUPABASE_URL", "").rstrip("/")
    service_key = getattr(settings, "SUPABASE_SERVICE_ROLE_KEY", "")
    bucket = get_bucket_name()

    clean_path = storage_path
    if clean_path.startswith("supabase://"):
        parts = clean_path.replace("supabase://", "").split("/", 1)
        if len(parts) == 2:
            bucket = parts[0]
            clean_path = parts[1]

    endpoint = f"{supabase_url}/storage/v1/object/{bucket}"
    headers = {
        "Authorization": f"Bearer {service_key}",
        "apiKey": service_key,
        "Content-Type": "application/json",
    }

    try:
        resp = requests.delete(endpoint, json={"prefixes": [clean_path]}, headers=headers, timeout=8)
        return resp.status_code in (200, 204)
    except Exception as e:
        logger.error(f"Error deleting file from Supabase storage: {e}")
        return False

