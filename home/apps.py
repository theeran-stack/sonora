import os
import sys
import logging
from django.apps import AppConfig

logger = logging.getLogger(__name__)

SKIP_COMMANDS = {
    "makemigrations",
    "migrate",
    "test",
    "collectstatic",
    "shell",
    "showmigrations",
    "dumpdata",
    "loaddata",
    "check",
    "scan_music",
}


class HomeConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "home"

    def ready(self):
        # Prevent scanner running during management commands, tests, or migrations
        args = set(sys.argv)
        if any(cmd in args for cmd in SKIP_COMMANDS):
            return

        # Ensure startup scanning runs only in the active runserver child process
        # to avoid double execution from Django's auto-reloader or during imports
        is_runserver = any("runserver" in arg for arg in sys.argv)
        if not is_runserver:
            return

        if os.environ.get("RUN_MAIN") != "true":
            return

        # Trigger startup scan non-blockingly in background daemon thread
        try:
            from .scanner import start_background_scan
            start_background_scan()
            logger.info("Sonora startup background scanner initiated.")
        except Exception as e:
            logger.warning(f"Could not trigger startup scanner: {e}")
