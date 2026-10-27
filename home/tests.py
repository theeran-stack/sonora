import os
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.test import TestCase, Client
from django.conf import settings
from home.models import Track
from home.scanner import (
    scan_music_library,
    is_safe_music_path,
    format_duration,
    extract_metadata,
    SCAN_STATE,
)


class MusicScannerAndStreamingTests(TestCase):
    def setUp(self):
        self.client = Client()
        SCAN_STATE.reset()

        self.temp_dir = tempfile.TemporaryDirectory()
        self.scan_root = Path(self.temp_dir.name).resolve()

        # Create dummy audio files
        self.mp3_file = self.scan_root / "test_song.mp3"
        self.wav_file = self.scan_root / "sample_audio.wav"
        self.flac_file = self.scan_root / "track3.flac"
        self.m4a_file = self.scan_root / "track4.m4a"
        self.ogg_file = self.scan_root / "track5.ogg"
        self.txt_file = self.scan_root / "ignored.txt"

        for f in [self.mp3_file, self.wav_file, self.flac_file, self.m4a_file, self.ogg_file]:
            f.write_bytes(b"\x00" * 512)
        self.txt_file.write_text("Hello world")

    def tearDown(self):
        SCAN_STATE.reset()
        self.temp_dir.cleanup()

    def test_format_duration(self):
        self.assertEqual(format_duration(0), "0:00")
        self.assertEqual(format_duration(65), "1:05")
        self.assertEqual(format_duration(215), "3:35")
        self.assertEqual(format_duration(3665), "1:01:05")

    def test_scanner_discovers_supported_formats(self):
        with patch.object(settings, "MUSIC_SCAN_DIRECTORIES", [self.scan_root]):
            result = scan_music_library(explicit_dirs=[self.scan_root])

            self.assertEqual(result["total_tracks"], 5)
            self.assertEqual(Track.objects.count(), 5)

            # Ensure txt file was ignored
            self.assertFalse(Track.objects.filter(file_path__endswith=".txt").exists())

            # Check track fields
            mp3_track = Track.objects.get(file_path=str(self.mp3_file))
            self.assertEqual(mp3_track.title, "test_song")
            self.assertEqual(mp3_track.format, ".mp3")
            self.assertEqual(mp3_track.source_type, "local")

    def test_scanner_idempotent_no_duplicates(self):
        with patch.object(settings, "MUSIC_SCAN_DIRECTORIES", [self.scan_root]):
            # First scan
            scan_music_library(explicit_dirs=[self.scan_root])
            first_count = Track.objects.count()
            self.assertEqual(first_count, 5)

            # Second scan immediately on same directory
            scan_music_library(explicit_dirs=[self.scan_root])
            second_count = Track.objects.count()

            # Count must remain exactly 5, zero duplicate records created
            self.assertEqual(second_count, first_count)

    def test_scanner_preserves_library_when_dir_inaccessible(self):
        # Create an existing track
        Track.objects.create(
            title="Preserved Song",
            artist="Artist",
            album="Album",
            file_path=str(self.mp3_file),
            format=".mp3",
        )
        self.assertEqual(Track.objects.count(), 1)

        # Scan a non-existent path
        missing_path = Path("C:/definitely/nonexistent/drive/music")
        scan_music_library(explicit_dirs=[missing_path])

        # Track must NOT be deleted
        self.assertEqual(Track.objects.count(), 1)
        preserved = Track.objects.first()
        self.assertEqual(preserved.title, "Preserved Song")

    def test_path_traversal_security(self):
        with patch.object(settings, "MUSIC_SCAN_DIRECTORIES", [self.scan_root]):
            # File inside allowed root
            self.assertTrue(is_safe_music_path(str(self.mp3_file)))

            # File outside allowed root
            forbidden_path = "C:/Windows/System32/drivers/etc/hosts"
            self.assertFalse(is_safe_music_path(forbidden_path))

    def test_http_range_audio_streaming(self):
        with patch("home.views.is_safe_music_path", return_value=True):
            test_content = b"ABCDEFGHIJ" * 100  # 1000 bytes
            dummy_file = self.scan_root / "stream_test.mp3"
            dummy_file.write_bytes(test_content)

            track = Track.objects.create(
                title="Stream Test",
                artist="Tester",
                album="Album",
                file_path=str(dummy_file),
                format=".mp3",
            )

            # 1. Full content request (no Range header)
            resp = self.client.get(f"/stream/{track.id}/")
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp["Accept-Ranges"], "bytes")
            self.assertEqual(resp["Content-Type"], "audio/mpeg")
            self.assertEqual(b"".join(resp.streaming_content), test_content)

            # 2. HTTP Range request (bytes=0-49)
            resp_range = self.client.get(f"/stream/{track.id}/", HTTP_RANGE="bytes=0-49")
            self.assertEqual(resp_range.status_code, 206)
            self.assertEqual(resp_range["Content-Range"], f"bytes 0-49/{len(test_content)}")
            self.assertEqual(resp_range["Content-Length"], "50")
            self.assertEqual(b"".join(resp_range.streaming_content), test_content[:50])

    def test_homepage_renders_tracks(self):
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "SONORA")
        self.assertContains(resp, "Scan music")

    @patch("home.views.start_background_scan", return_value=True)
    def test_scan_api_endpoints(self, mock_scan):
        # Scan status endpoint
        resp_status = self.client.get("/api/scan-status/")
        self.assertEqual(resp_status.status_code, 200)
        json_data = resp_status.json()
        self.assertIn("is_scanning", json_data)
        self.assertIn("total_tracks", json_data)

        # Trigger scan endpoint
        resp_scan = self.client.post("/api/scan/")
        self.assertEqual(resp_scan.status_code, 200)
        self.assertEqual(resp_scan.json()["status"], "started")
        mock_scan.assert_called_once()

    @patch("home.views.start_background_scan", return_value=True)
    def test_upload_api_endpoint(self, mock_scan):
        with patch.object(settings, "BASE_DIR", self.scan_root):
            from django.core.files.uploadedfile import SimpleUploadedFile
            fake_audio = SimpleUploadedFile("uploaded_song.mp3", b"\x00" * 256, content_type="audio/mpeg")
            resp = self.client.post("/api/upload/", {"audio_files": fake_audio})
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.json()["status"], "success")
            self.assertEqual(resp.json()["saved_count"], 1)

    def test_cover_art_folder_search_and_caching(self):
        with patch("home.views.is_safe_music_path", return_value=True):
            with patch("home.artwork.get_cache_dir", return_value=self.scan_root / "cache"):
                # Create a song in a folder with folder.jpg
                song_dir = self.scan_root / "album_folder"
                song_dir.mkdir(parents=True, exist_ok=True)
                audio_file = song_dir / "song1.mp3"
                audio_file.write_bytes(b"\x00" * 256)

                fake_jpeg = b"\xff\xd8\xff\xe0" + b"\x00" * 100
                cover_file = song_dir / "cover.jpg"
                cover_file.write_bytes(fake_jpeg)

                track = Track.objects.create(
                    title="Folder Art Song",
                    artist="Artist",
                    album="Album Folder",
                    file_path=str(audio_file),
                    format=".mp3",
                )

                # Request cover endpoint
                resp = self.client.get(f"/cover/{track.id}/")
                self.assertEqual(resp.status_code, 200)
                self.assertEqual(resp["Content-Type"], "image/jpeg")
                self.assertEqual(resp.content, fake_jpeg)
                self.assertIn("max-age=86400", resp["Cache-Control"])

                # Second request should be served from cache
                resp2 = self.client.get(f"/cover/{track.id}/")
                self.assertEqual(resp2.status_code, 200)
                self.assertEqual(resp2.content, fake_jpeg)

    def test_cover_art_not_found_returns_404(self):
        with patch("home.views.is_safe_music_path", return_value=True):
            with patch("home.artwork.fetch_online_artwork", return_value=(None, None)):
                with patch("home.artwork.get_cache_dir", return_value=self.scan_root / "cache"):
                    track = Track.objects.create(
                        title="Unknown Nothing",
                        artist="Unknown",
                        album="Unknown",
                        file_path=str(self.mp3_file),
                        format=".mp3",
                        has_cover=False,
                    )
                    resp = self.client.get(f"/cover/{track.id}/")
                    self.assertEqual(resp.status_code, 404)

    def test_http_range_suffix_and_seeking(self):
        with patch("home.views.is_safe_music_path", return_value=True):
            test_content = b"0123456789" * 100  # 1000 bytes
            dummy_file = self.scan_root / "suffix_test.mp3"
            dummy_file.write_bytes(test_content)

            track = Track.objects.create(
                title="Suffix Test",
                artist="Tester",
                album="Album",
                file_path=str(dummy_file),
                format=".mp3",
            )

            # Test suffix range (last 100 bytes: bytes=-100)
            resp = self.client.get(f"/stream/{track.id}/", HTTP_RANGE="bytes=-100")
            self.assertEqual(resp.status_code, 206)
            self.assertEqual(resp["Content-Range"], f"bytes 900-999/{len(test_content)}")
            self.assertEqual(b"".join(resp.streaming_content), test_content[900:1000])

            # Test seeking range: bytes=250-499
            resp_seek = self.client.get(f"/stream/{track.id}/", HTTP_RANGE="bytes=250-499")
            self.assertEqual(resp_seek.status_code, 206)
            self.assertEqual(resp_seek["Content-Length"], "250")
            self.assertEqual(b"".join(resp_seek.streaming_content), test_content[250:500])
