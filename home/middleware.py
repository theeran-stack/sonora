"""
Structured Request Logging Middleware for SONORA.

Records incoming HTTP requests, response status codes, latency durations, and timestamps.
Defensively sanitizes sensitive parameters (tokens, passwords, API keys, email contents)
to ensure privacy compliance and prevent credential leakage in audit logs.
"""

import time
import re
import logging
from datetime import datetime, timezone
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

logger = logging.getLogger("sonora.requests")

SENSITIVE_PARAM_NAMES = {
    "token",
    "secret",
    "password",
    "key",
    "api_key",
    "apikey",
    "auth",
    "authorization",
    "email",
    "access_token",
    "refresh_token",
    "x-sonora-token",
    "sync_token",
}


def sanitize_url_path(url_string: str) -> str:
    """
    Redacts sensitive parameter values from query strings.
    E.g., /api/tickets/?email=secret@test.com -> /api/tickets/?email=[REDACTED]
    """
    if not url_string:
        return "/"

    try:
        parsed = urlparse(url_string)
        if not parsed.query:
            return parsed.path

        query_params = parse_qs(parsed.query, keep_blank_values=True)
        sanitized_params = {}

        for k, vals in query_params.items():
            if k.lower() in SENSITIVE_PARAM_NAMES or any(s in k.lower() for s in ["token", "secret", "pass"]):
                sanitized_params[k] = ["[REDACTED]"]
            else:
                sanitized_params[k] = vals

        new_query = urlencode(sanitized_params, doseq=True)
        return urlunparse(("", "", parsed.path, "", new_query, ""))
    except Exception:
        return url_string.split("?")[0]


def get_client_ip(request) -> str:
    """Extracts client IP address safely from request headers."""
    x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
    if x_forwarded_for:
        # First IP in proxy chain is the client
        return x_forwarded_for.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "127.0.0.1")


class RequestLoggingMiddleware:
    """
    Django middleware that records structured telemetry for every HTTP request.
    Includes duration profiling, response status, and sanitized path.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        start_time = time.perf_counter()
        timestamp = datetime.now(timezone.utc).isoformat()

        response = self.get_response(request)

        duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
        client_ip = get_client_ip(request)
        sanitized_path = sanitize_url_path(request.get_full_path())
        status_code = getattr(response, "status_code", 200)

        # Structured log record
        log_entry = (
            f"[{timestamp}] HTTP {request.method} {sanitized_path} -> "
            f"{status_code} ({duration_ms}ms) [IP: {client_ip}]"
        )

        if status_code >= 500:
            logger.error(log_entry)
        elif status_code >= 400:
            logger.warning(log_entry)
        else:
            logger.info(log_entry)

        return response

