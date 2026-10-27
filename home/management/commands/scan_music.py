from pathlib import Path
from django.core.management.base import BaseCommand
from home.scanner import scan_music_library, get_scan_directories


class Command(BaseCommand):
    help = "Scan configured or specified music directories for local audio files."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dir",
            type=str,
            help="Custom directory path to scan (overrides configured defaults for this run).",
        )

    def handle(self, *args, **options):
        custom_dir = options.get("dir")
        if custom_dir:
            scan_dirs = [Path(custom_dir).resolve()]
            self.stdout.write(self.style.NOTICE(f"Scanning custom directory: {custom_dir}"))
        else:
            scan_dirs = get_scan_directories()
            self.stdout.write(self.style.NOTICE(f"Scanning configured directories: {[str(d) for d in scan_dirs]}"))

        self.stdout.write("Running scanner...")
        result = scan_music_library(explicit_dirs=scan_dirs)

        self.stdout.write(self.style.SUCCESS(
            f"Scan completed successfully.\n"
            f"- Total tracks in library: {result.get('total_tracks', 0)}\n"
            f"- Scanned in this pass: {result.get('scanned_count', 0)}"
        ))

        errors = result.get("errors", [])
        if errors:
            self.stdout.write(self.style.WARNING("Warnings/Errors encountered:"))
            for err in errors:
                self.stdout.write(self.style.WARNING(f"  * {err}"))
