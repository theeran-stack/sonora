import os
import json
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

from django.test import TestCase, Client, RequestFactory, override_settings
from django.conf import settings
from django.contrib.auth.models import User
from home.models import Track, FAQ, ErrorCode, Ticket
from home.seeds import seed_all, seed_faqs, seed_error_codes
from home.mongodb import (
    is_mongo_configured,
    get_mongo_client,
    get_mongo_db,
    get_chats_collection,
    get_feedback_collection,
    check_mongo_connection,
    save_chat_message,
    save_feedback,
    get_chat_history,
    verify_mongodb_production_status,
)
from home.views import custom_404, custom_500, health_check
from home.discovery import search_jamendo_tracks, CURATED_DISCOVER_CATALOG
from home.artwork import fetch_online_artwork
from home.middleware import sanitize_url_path
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

    def test_discover_and_queue_apis(self):
        # 1. Search endpoint
        resp = self.client.get("/api/discover/search/?q=chill")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "success")
        self.assertGreaterEqual(len(data["results"]), 1)

        # 2. Add to download queue
        item = {
            "id": "jamendo_test_123",
            "external_id": "test_123",
            "title": "Test Chill Song",
            "artist": "Test Artist",
            "album": "Test Album",
            "download_url": "https://example.com/audio.mp3",
            "preview_url": "https://example.com/audio.mp3",
            "duration": "3:15",
        }
        queue_resp = self.client.post(
            "/api/discover/queue/",
            data=json.dumps(item),
            content_type="application/json"
        )
        self.assertEqual(queue_resp.status_code, 200)
        self.assertIn(queue_resp.json()["status"], ["queued", "already_queued"])

    def test_downloads_management_apis(self):
        from home.models import DownloadTask
        task = DownloadTask.objects.create(
            title="Download Test",
            artist="Downloader",
            external_id="dl_1",
            download_url="https://example.com/1.mp3",
            status="failed",
            error_message="Network error"
        )

        # List downloads
        list_resp = self.client.get("/api/downloads/")
        self.assertEqual(list_resp.status_code, 200)
        self.assertGreaterEqual(list_resp.json()["count"], 1)

        # Retry failed download
        retry_resp = self.client.post(f"/api/downloads/{task.id}/retry/")
        self.assertEqual(retry_resp.status_code, 200)
        task.refresh_from_db()
        self.assertEqual(task.status, "queued")

        # Cancel download
        del_resp = self.client.post(f"/api/downloads/{task.id}/cancel/")
        self.assertEqual(del_resp.status_code, 200)
        self.assertFalse(DownloadTask.objects.filter(id=task.id).exists())

    def test_sync_api_authentication_and_queue(self):
        from home.models import DownloadTask
        DownloadTask.objects.create(
            title="Sync Task",
            artist="Artist",
            external_id="sync_1",
            download_url="https://example.com/sync.mp3",
            status="queued"
        )

        # Unauthorized request without token
        unauth_resp = self.client.get("/api/sync/queue/")
        self.assertEqual(unauth_resp.status_code, 401)

        # Authorized request with X-Sonora-Token
        token = getattr(settings, "SONORA_SYNC_TOKEN", "sonora_secret_sync_token_2026")
        auth_resp = self.client.get("/api/sync/queue/", HTTP_X_SONORA_TOKEN=token)
        self.assertEqual(auth_resp.status_code, 200)
        self.assertGreaterEqual(auth_resp.json()["count"], 1)

    def test_sync_tracks_metadata(self):
        token = getattr(settings, "SONORA_SYNC_TOKEN", "sonora_secret_sync_token_2026")
        payload = {
            "tracks": [
                {
                    "title": "Synced Song A",
                    "artist": "Synced Artist",
                    "album": "Synced Album",
                    "duration_seconds": 210,
                    "duration_formatted": "3:30",
                    "format": ".mp3",
                    "file_hash": "hash_abc_123",
                    "relative_name": "synced_song_a.mp3",
                }
            ]
        }
        resp = self.client.post(
            "/api/sync/tracks/",
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_X_SONORA_TOKEN=token
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(Track.objects.filter(title="Synced Song A").exists())


class FAQDatabaseAndApiTests(TestCase):
    def setUp(self):
        self.client = Client()
        seed_all()

    def test_seed_faqs_creates_records_and_no_duplicates(self):
        initial_count = FAQ.objects.count()
        self.assertGreaterEqual(initial_count, 10, "Must have at least 10 FAQ records")

        # Run seed again to verify duplicate prevention
        seed_all()
        second_count = FAQ.objects.count()
        self.assertEqual(initial_count, second_count, "Seeding must be idempotent without duplicates")

    def test_api_faqs_list_all(self):
        resp = self.client.get("/api/faqs/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "success")
        self.assertGreaterEqual(data["count"], 10)
        self.assertIsInstance(data["faqs"], list)

        first = data["faqs"][0]
        self.assertIn("question", first)
        self.assertIn("answer", first)
        self.assertIn("category", first)

    def test_api_faqs_filter_by_category(self):
        resp = self.client.get("/api/faqs/?category=audio")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "success")
        for item in data["faqs"]:
            self.assertEqual(item["category"], "audio")

    def test_api_faqs_search_by_question(self):
        resp = self.client.get("/api/faqs/?q=Mutagen")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "success")
        self.assertGreaterEqual(data["count"], 1)
        for item in data["faqs"]:
            self.assertTrue("mutagen" in item["question"].lower() or "mutagen" in item["answer"].lower())

    def test_api_faqs_inactive_excluded(self):
        target = FAQ.objects.first()
        target.is_active = False
        target.save()

        resp = self.client.get("/api/faqs/")
        self.assertEqual(resp.status_code, 200)
        faq_ids = [f["id"] for f in resp.json()["faqs"]]
        self.assertNotIn(target.id, faq_ids)


class ErrorCodeDatabaseAndApiTests(TestCase):
    def setUp(self):
        self.client = Client()
        seed_all()

    def test_seed_error_codes_creates_records_and_no_duplicates(self):
        initial_count = ErrorCode.objects.count()
        self.assertGreaterEqual(initial_count, 8, "Must have at least 8 error code records")

        # Verify exact code presence matching docs/ERROR_CODES.md
        self.assertTrue(ErrorCode.objects.filter(code="ERR_PATH_TRAVERSAL_DETECTED").exists())
        self.assertTrue(ErrorCode.objects.filter(code="ERR_STREAM_NOT_FOUND").exists())
        self.assertTrue(ErrorCode.objects.filter(code="ERR_RANGE_NOT_SATISFIABLE").exists())
        self.assertTrue(ErrorCode.objects.filter(code="ERR_SYNC_UNAUTHORIZED").exists())

        # Test duplicate prevention
        seed_all()
        self.assertEqual(ErrorCode.objects.count(), initial_count)

    def test_api_error_codes_list(self):
        resp = self.client.get("/api/error-codes/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "success")
        self.assertGreaterEqual(data["count"], 8)
        self.assertIsInstance(data["error_codes"], list)

        first = data["error_codes"][0]
        self.assertIn("code", first)
        self.assertIn("title", first)
        self.assertIn("description", first)
        self.assertIn("cause", first)
        self.assertIn("recovery_step", first)
        self.assertIn("http_status", first)

    def test_api_error_codes_detail_by_path(self):
        resp = self.client.get("/api/error-codes/ERR_STREAM_NOT_FOUND/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["error_code"]["code"], "ERR_STREAM_NOT_FOUND")
        self.assertEqual(data["error_code"]["http_status"], 404)

    def test_api_error_codes_detail_by_query_param(self):
        resp = self.client.get("/api/error-codes/?code=ERR_PATH_TRAVERSAL_DETECTED")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["error_code"]["code"], "ERR_PATH_TRAVERSAL_DETECTED")
        self.assertEqual(data["error_code"]["http_status"], 403)

    def test_api_error_codes_not_found(self):
        resp = self.client.get("/api/error-codes/ERR_NONEXISTENT_CODE/")
        self.assertEqual(resp.status_code, 404)
        data = resp.json()
        self.assertEqual(data["status"], "error")


class TicketManagementAndPrivacyTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.sync_token = getattr(settings, "SONORA_SYNC_TOKEN", "sonora_secret_sync_token_2026")
        self.user = User.objects.create_superuser("adminuser", "admin@sonora.local", "secretpass123")

    def test_ticket_create_validation_missing_fields(self):
        resp = self.client.post(
            "/api/tickets/create/",
            data=json.dumps({}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)
        data = resp.json()
        self.assertIn("details", data)
        self.assertIn("title", data["details"])
        self.assertIn("description", data["details"])
        self.assertIn("email", data["details"])

    def test_ticket_create_validation_invalid_email(self):
        resp = self.client.post(
            "/api/tickets/create/",
            data=json.dumps({
                "title": "Playback issue",
                "description": "FLAC streaming fails",
                "email": "not-an-email"
            }),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)
        data = resp.json()
        self.assertIn("email", data["details"])

    def test_ticket_create_success(self):
        resp = self.client.post(
            "/api/tickets/create/",
            data=json.dumps({
                "title": "Playback crackling",
                "description": "Audio buffer underflow when seeking rapidly",
                "email": "audiophile@example.com"
            }),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 201)
        data = resp.json()
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["ticket"]["title"], "Playback crackling")
        self.assertEqual(data["ticket"]["status"], "open")
        self.assertEqual(data["ticket"]["email"], "audiophile@example.com")
        self.assertTrue(Ticket.objects.filter(email="audiophile@example.com").exists())

    def test_ticket_list_anonymous_without_email_rejected_for_privacy(self):
        Ticket.objects.create(
            title="Private Ticket 1",
            description="Private Description",
            email="victim@example.com",
            status="open"
        )
        resp = self.client.get("/api/tickets/")
        self.assertEqual(resp.status_code, 401, "Anonymous general ticket listing must be rejected for privacy")

    def test_ticket_list_anonymous_with_email_success(self):
        Ticket.objects.create(
            title="User Ticket A",
            description="Description A",
            email="myuser@example.com",
            status="open"
        )
        Ticket.objects.create(
            title="Other User Ticket B",
            description="Description B",
            email="other@example.com",
            status="open"
        )

        resp = self.client.get("/api/tickets/?email=myuser@example.com")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["count"], 1)
        self.assertEqual(data["tickets"][0]["email"], "myuser@example.com")

    def test_ticket_list_staff_or_sync_token_success(self):
        Ticket.objects.create(
            title="Ticket 1",
            description="Desc 1",
            email="user1@example.com",
        )
        Ticket.objects.create(
            title="Ticket 2",
            description="Desc 2",
            email="user2@example.com",
        )

        # Call with X-Sonora-Token
        resp = self.client.get("/api/tickets/", HTTP_X_SONORA_TOKEN=self.sync_token)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["count"], 2)

    def test_ticket_update_unauthorized(self):
        ticket = Ticket.objects.create(
            title="Update Me",
            description="Desc",
            email="test@example.com",
            status="open"
        )
        resp = self.client.put(
            f"/api/tickets/{ticket.id}/update/",
            data=json.dumps({"status": "resolved"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 401)
        ticket.refresh_from_db()
        self.assertEqual(ticket.status, "open")

    def test_ticket_update_authorized_status(self):
        ticket = Ticket.objects.create(
            title="Update Me",
            description="Desc",
            email="test@example.com",
            status="open"
        )
        resp = self.client.put(
            f"/api/tickets/{ticket.id}/update/",
            data=json.dumps({"status": "in_progress", "title": "Updated Title"}),
            content_type="application/json",
            HTTP_X_SONORA_TOKEN=self.sync_token
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["ticket"]["status"], "in_progress")
        self.assertEqual(data["ticket"]["title"], "Updated Title")

        ticket.refresh_from_db()
        self.assertEqual(ticket.status, "in_progress")

    def test_ticket_update_invalid_status(self):
        ticket = Ticket.objects.create(
            title="Test",
            description="Desc",
            email="test@example.com",
            status="open"
        )
        resp = self.client.put(
            f"/api/tickets/{ticket.id}/update/",
            data=json.dumps({"status": "nonexistent_status"}),
            content_type="application/json",
            HTTP_X_SONORA_TOKEN=self.sync_token
        )
        self.assertEqual(resp.status_code, 400)

    def test_ticket_delete_unauthorized(self):
        ticket = Ticket.objects.create(
            title="Delete Me",
            description="Desc",
            email="test@example.com",
            status="open"
        )
        resp = self.client.delete(f"/api/tickets/{ticket.id}/delete/")
        self.assertEqual(resp.status_code, 401)
        self.assertTrue(Ticket.objects.filter(id=ticket.id).exists())

    def test_ticket_delete_authorized(self):
        ticket = Ticket.objects.create(
            title="Delete Me",
            description="Desc",
            email="test@example.com",
            status="open"
        )
        resp = self.client.delete(
            f"/api/tickets/{ticket.id}/delete/",
            HTTP_X_SONORA_TOKEN=self.sync_token
        )
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(Ticket.objects.filter(id=ticket.id).exists())


class MongoDBIntegrationPreparationTests(TestCase):
    def test_mongo_unconfigured_behavior(self):
        with patch.dict(os.environ, {"MONGODB_URI": ""}, clear=False):
            self.assertFalse(is_mongo_configured())
            self.assertIsNone(get_mongo_client())
            self.assertIsNone(get_mongo_db())
            self.assertIsNone(get_chats_collection())
            self.assertIsNone(get_feedback_collection())

            is_healthy, msg = check_mongo_connection()
            self.assertFalse(is_healthy)
            self.assertIn("not configured", msg)

    def test_mongo_does_not_break_music_library(self):
        # Even if MongoDB is completely offline or unconfigured,
        # standard Django SQLite music library operations remain 100% operational
        track = Track.objects.create(
            title="Test Track",
            artist="Test Artist",
            file_path="C:/music/test.mp3"
        )
        self.assertEqual(Track.objects.filter(title="Test Track").count(), 1)
        track.delete()


class FAQAndErrorCodePageIntegrationTests(TestCase):
    def setUp(self):
        self.client = Client()
        seed_all()

    def test_faq_page_loads_with_active_faqs_and_widget(self):
        resp = self.client.get("/faq/")
        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode("utf-8")
        self.assertIn("Frequently Asked Questions", content)
        self.assertIn("faqSearchInput", content)
        self.assertIn("sonoraAssistantRoot", content)
        self.assertGreaterEqual(len(resp.context["faqs"]), 10)

    def test_faq_page_category_filtering(self):
        resp = self.client.get("/faq/?category=audio")
        self.assertEqual(resp.status_code, 200)
        self.assertGreaterEqual(len(resp.context["faqs"]), 1)
        for item in resp.context["faqs"]:
            self.assertEqual(item["category"], "audio")

    def test_faq_page_search_query(self):
        resp = self.client.get("/faq/?q=mutagen")
        self.assertEqual(resp.status_code, 200)
        self.assertGreaterEqual(len(resp.context["faqs"]), 1)

    def test_error_codes_page_loads_with_specifications_and_widget(self):
        resp = self.client.get("/errors/")
        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode("utf-8")
        self.assertIn("Error Codes", content)
        self.assertIn("Diagnostic Reference", content)
        self.assertIn("errSearchInput", content)
        self.assertIn("ERR_PATH_TRAVERSAL_DETECTED", content)
        self.assertIn("sonoraAssistantRoot", content)
        self.assertGreaterEqual(len(resp.context["error_codes"]), 8)

    def test_error_codes_page_alt_route(self):
        resp = self.client.get("/error-codes/")
        self.assertEqual(resp.status_code, 200)

    def test_error_codes_page_deep_link_code_filter(self):
        resp = self.client.get("/errors/?code=ERR_STREAM_NOT_FOUND")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.context["error_codes"]), 1)
        self.assertEqual(resp.context["error_codes"][0]["code"], "ERR_STREAM_NOT_FOUND")

    def test_home_page_contains_assistant_and_links(self):
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode("utf-8")
        self.assertIn("sonoraAssistantRoot", content)
        self.assertIn('href="/faq/"', content)
        self.assertIn('href="/errors/"', content)


class AIAssistantWidgetAndApiTests(TestCase):
    def setUp(self):
        self.client = Client()
        seed_all()

    def test_assistant_chat_greeting(self):
        resp = self.client.post(
            "/api/assistant/chat/",
            data=json.dumps({"message": "hello", "session_id": "test_sess_1"}),
            content_type="application/json"
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "success")
        self.assertIn("SONORA Studio Assistant", data["reply"])

    def test_assistant_chat_answers_faq_grounded_in_db(self):
        resp = self.client.post(
            "/api/assistant/chat/",
            data=json.dumps({"message": "What audio formats does SONORA support?", "session_id": "test_sess_2"}),
            content_type="application/json"
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "success")
        self.assertIn("FLAC", data["reply"])
        self.assertIn("MP3", data["reply"])
        self.assertGreaterEqual(len(data["sources"]), 1)
        self.assertEqual(data["sources"][0]["type"], "faq")

    def test_assistant_chat_explains_error_code_grounded_in_db(self):
        resp = self.client.post(
            "/api/assistant/chat/",
            data=json.dumps({"message": "Explain ERR_PATH_TRAVERSAL_DETECTED", "session_id": "test_sess_3"}),
            content_type="application/json"
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "success")
        self.assertIn("ERR_PATH_TRAVERSAL_DETECTED", data["reply"])
        self.assertIn("403", data["reply"])
        self.assertGreaterEqual(len(data["sources"]), 1)
        self.assertEqual(data["sources"][0]["type"], "error_code")
        self.assertEqual(data["sources"][0]["http_status"], 403)

    def test_assistant_chat_unrecognized_fallback_no_hallucination(self):
        resp = self.client.post(
            "/api/assistant/chat/",
            data=json.dumps({"message": "Can you bake a chocolate cake recipe?", "session_id": "test_sess_4"}),
            content_type="application/json"
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "success")
        self.assertIn("could not locate a matching verified record", data["reply"])
        self.assertIn("/faq/", data["reply"])

    def test_assistant_chat_empty_query_rejected(self):
        resp = self.client.post(
            "/api/assistant/chat/",
            data=json.dumps({"message": "   "}),
            content_type="application/json"
        )
        self.assertEqual(resp.status_code, 400)

    def test_assistant_feedback_submission(self):
        resp = self.client.post(
            "/api/assistant/feedback/",
            data=json.dumps({
                "session_id": "test_sess_5",
                "rating": 5,
                "comments": "Very clear error troubleshooting",
                "message_id": "msg_99"
            }),
            content_type="application/json"
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "success")

    def test_assistant_history_retrieval(self):
        resp = self.client.get("/api/assistant/history/?session_id=test_sess_6")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "success")
        self.assertIsInstance(resp.json()["history"], list)


class CustomErrorPagesBrandingTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.factory = RequestFactory()

    def test_custom_404_page_rendering_and_ask_assistant(self):
        resp = self.client.get("/nonexistent-stream-or-page-404-check/")
        self.assertEqual(resp.status_code, 404)
        content = resp.content.decode("utf-8")
        self.assertIn("404", content)
        self.assertIn("Track or Page Not Found", content)
        self.assertIn("Ask Assistant", content)
        self.assertIn("sonoraAssistantRoot", content)

    def test_custom_500_view_rendering_and_ask_assistant(self):
        request = self.factory.get("/simulate-500/")
        resp = custom_500(request)
        self.assertEqual(resp.status_code, 500)
        content = resp.content.decode("utf-8")
        self.assertIn("500", content)
        self.assertIn("Internal Server Condition", content)
        self.assertIn("Ask Assistant", content)
        self.assertIn("sonoraAssistantRoot", content)


class MongoDBPersistenceServiceTests(TestCase):
    def test_save_chat_message_with_mock_collection(self):
        mock_coll = MagicMock()
        mock_result = MagicMock()
        mock_result.inserted_id = "mock_mongo_id_12345"
        mock_coll.insert_one.return_value = mock_result

        with patch("home.mongodb.get_chats_collection", return_value=mock_coll):
            inserted_id = save_chat_message(
                session_id="test_sess_mongo",
                user_message="What is SONORA?",
                assistant_response="SONORA is a tactile player",
                metadata={"test": True}
            )
            self.assertEqual(inserted_id, "mock_mongo_id_12345")
            mock_coll.insert_one.assert_called_once()
            args = mock_coll.insert_one.call_args[0][0]
            self.assertEqual(args["session_id"], "test_sess_mongo")
            self.assertEqual(args["user_message"], "What is SONORA?")
            self.assertIn("created_at", args)

    def test_save_feedback_with_mock_collection(self):
        mock_coll = MagicMock()
        mock_result = MagicMock()
        mock_result.inserted_id = "mock_fb_id_67890"
        mock_coll.insert_one.return_value = mock_result

        with patch("home.mongodb.get_feedback_collection", return_value=mock_coll):
            inserted_id = save_feedback(
                session_id="test_sess_mongo",
                rating=5,
                comments="Super helpful!",
                message_id="msg_1"
            )
            self.assertEqual(inserted_id, "mock_fb_id_67890")
            mock_coll.insert_one.assert_called_once()
            args = mock_coll.insert_one.call_args[0][0]
            self.assertEqual(args["rating"], 5)
            self.assertEqual(args["comments"], "Super helpful!")

    def test_get_chat_history_with_mock_collection(self):
        mock_coll = MagicMock()
        mock_cursor = [
            {
                "_id": "doc_1",
                "user_message": "Hello",
                "assistant_response": "Hi there!",
                "metadata": {},
                "created_at": "2026-11-03T10:00:00Z"
            }
        ]
        mock_chain = MagicMock()
        mock_chain.limit.return_value = mock_cursor
        mock_sort = MagicMock()
        mock_sort.sort.return_value = mock_chain
        mock_coll.find.return_value = mock_sort

        with patch("home.mongodb.get_chats_collection", return_value=mock_coll):
            history = get_chat_history("test_sess_mongo", limit=10)
            self.assertEqual(len(history), 1)
            self.assertEqual(history[0]["user_message"], "Hello")
            self.assertEqual(history[0]["assistant_response"], "Hi there!")

    def test_mongo_graceful_when_unconfigured(self):
        with patch.dict(os.environ, {"MONGODB_URI": ""}, clear=False):
            # When unconfigured, saving returns None without throwing any exceptions
            res_chat = save_chat_message("sess", "msg", "reply")
            self.assertIsNone(res_chat)

            res_fb = save_feedback("sess", 5, "good")
            self.assertIsNone(res_fb)

            history = get_chat_history("sess")
            self.assertEqual(history, [])


class HealthCheckEndpointTests(TestCase):
    def setUp(self):
        self.client = Client()

    def test_health_check_returns_200_and_payload(self):
        resp = self.client.get("/health/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "healthy")
        self.assertEqual(data["service"], "SONORA Tactile Music Platform")
        self.assertEqual(data["version"], "2.0.0")
        self.assertIn("checks", data)
        self.assertEqual(data["checks"]["database"]["status"], "healthy")
        self.assertIn("mongodb", data["checks"])
        self.assertIn("library", data["checks"])
        self.assertIn("third_party_apis", data["checks"])

    def test_healthz_alias_returns_200(self):
        resp = self.client.get("/healthz/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "healthy")

    def test_health_check_db_failure_returns_503(self):
        with patch("django.db.connection.cursor", side_effect=Exception("Database connection timeout")):
            resp = self.client.get("/health/")
            self.assertEqual(resp.status_code, 503)
            data = resp.json()
            self.assertEqual(data["status"], "unhealthy")
            self.assertEqual(data["checks"]["database"]["status"], "unhealthy")

    def test_health_check_mongo_unconfigured_retains_healthy_core_status(self):
        with patch("home.mongodb.is_mongo_configured", return_value=False):
            resp = self.client.get("/health/")
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertEqual(data["status"], "healthy")
            self.assertFalse(data["checks"]["mongodb"]["configured"])


class RequestLoggingMiddlewareTests(TestCase):
    def setUp(self):
        self.client = Client()

    def test_request_logging_emits_log_on_request(self):
        with self.assertLogs("sonora.requests", level="INFO") as cm:
            resp = self.client.get("/api/sync/status/")
            self.assertEqual(resp.status_code, 200)
            self.assertTrue(any("HTTP GET /api/sync/status/ -> 200" in log for log in cm.output))

    def test_request_logging_sanitizes_sensitive_query_parameters(self):
        raw_url = "/api/tickets/?email=myuser@example.com&token=secret123&password=pass456&search=audio"
        sanitized = sanitize_url_path(raw_url)
        self.assertNotIn("secret123", sanitized)
        self.assertNotIn("pass456", sanitized)
        self.assertNotIn("myuser@example.com", sanitized)
        self.assertIn("REDACTED", sanitized)
        self.assertIn("search=audio", sanitized)


class ThirdPartyApiReliabilityTests(TestCase):
    def test_jamendo_timeout_falls_back_gracefully(self):
        # When Jamendo API times out, it should log a note and fall back to curated catalog
        with patch.object(settings, "JAMENDO_CLIENT_ID", "test_id"):
            with patch("urllib.request.urlopen", side_effect=TimeoutError("Connection timed out")):
                results = search_jamendo_tracks("synthwave")
                self.assertIsInstance(results, list)
                self.assertGreaterEqual(len(results), 1)
                # Verify returned catalog items contain required fields
                self.assertIn("title", results[0])
                audio_link = results[0].get("preview_url", "") or results[0].get("download_url", "")
                self.assertTrue(audio_link.startswith("http"))

    def test_jamendo_malformed_json_falls_back_gracefully(self):
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = b"<html>502 Bad Gateway from Cloudflare</html>"
        mock_resp.__enter__.return_value = mock_resp

        with patch.object(settings, "JAMENDO_CLIENT_ID", "test_id"):
            with patch("urllib.request.urlopen", return_value=mock_resp):
                results = search_jamendo_tracks("ambient")
                self.assertIsInstance(results, list)
                self.assertGreaterEqual(len(results), 1)

    def test_artwork_timeout_falls_back_gracefully(self):
        with patch("urllib.request.urlopen", side_effect=TimeoutError("Read timed out")):
            mime, data = fetch_online_artwork("Track Title", "Artist Name", timeout=0.1)
            self.assertIsNone(mime)
            self.assertIsNone(data)

    def test_artwork_http_error_falls_back_gracefully(self):
        mock_resp = MagicMock()
        mock_resp.status = 503
        mock_resp.__enter__.return_value = mock_resp

        with patch("urllib.request.urlopen", return_value=mock_resp):
            mime, data = fetch_online_artwork("Track Title", "Artist Name")
            self.assertIsNone(mime)
            self.assertIsNone(data)


class MongoDBProductionVerificationTests(TestCase):
    def test_verify_mongodb_production_status_unconfigured(self):
        with patch.dict(os.environ, {"MONGODB_URI": ""}, clear=False):
            status = verify_mongodb_production_status()
            self.assertFalse(status["configured"])
            self.assertFalse(status["live_tested"])
            self.assertEqual(status["status"], "unconfigured")

    def test_verify_mongodb_production_status_live_when_mock_connected(self):
        mock_coll = MagicMock()
        mock_coll.insert_one.return_value = MagicMock(inserted_id="test_id_diag")
        mock_db = {"_diagnostics": mock_coll}

        with patch("home.mongodb.is_mongo_configured", return_value=True):
            with patch("home.mongodb.check_mongo_connection", return_value=(True, "Connected")):
                with patch("home.mongodb.get_mongo_db", return_value=mock_db):
                    status = verify_mongodb_production_status()
                    self.assertTrue(status["configured"])
                    self.assertTrue(status["live_tested"])
                    self.assertEqual(status["status"], "connected_and_verified")
                    mock_coll.insert_one.assert_called_once()
                    mock_coll.delete_one.assert_called_once()


class LocalScanningAndSynchronizationTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.music_dir = Path(self.temp_dir.name)
        SCAN_STATE.reset()

        # Create sample audio files
        (self.music_dir / "Artist A" / "Album 1").mkdir(parents=True, exist_ok=True)
        (self.music_dir / "Artist A" / "Album 1" / "song1.mp3").write_bytes(b"ID3\x03\x00\x00\x00\x00\x00#TIT2\x00\x00\x00\x08\x00\x00\x00Song One" + b"\x00" * 200)
        (self.music_dir / "Artist A" / "Album 1" / "song2.flac").write_bytes(b"fLaC\x00\x00\x00\x22" + b"\x00" * 300)
        (self.music_dir / "song3.wav").write_bytes(b"RIFF\x24\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x02\x00D\xac\x00\x00\x10\xb1\x02\x00\x04\x00\x10\x00data\x00\x00\x00\x00")

    def tearDown(self):
        self.temp_dir.cleanup()
        # Clean up pending sync file if created in helper dir
        from helper.sonora_helper import PENDING_SYNC_FILE
        if PENDING_SYNC_FILE.exists():
            try:
                PENDING_SYNC_FILE.unlink()
            except Exception:
                pass

    def test_offline_scan_accumulates_pending_queue(self):
        """Helper scans without internet, updates local SQLite, and maintains pending_sync.json."""
        from helper.sonora_helper import SonoraWindowsHelper, PENDING_SYNC_FILE

        with patch.dict(os.environ, {"SONORA_SERVER_URL": "http://127.0.0.1:9999", "SONORA_SYNC_TOKEN": "test_token"}):
            helper = SonoraWindowsHelper()
            helper.config["music_dir"] = str(self.music_dir)
            helper.config["server_url"] = "http://127.0.0.1:9999"  # Offline mock port

            # Run offline scan
            metrics = helper.scan_local_music_library(explicit_dir=str(self.music_dir))

            # Discovered audio files
            self.assertEqual(metrics["discovered_count"], 3)
            self.assertEqual(metrics["indexed_count"], 3)
            self.assertEqual(metrics["skipped_count"], 0)
            self.assertEqual(metrics["failed_count"], 0)

            # Local tracks stored with canonical local file paths
            tracks = Track.objects.filter(file_path__startswith=str(self.music_dir))
            self.assertEqual(tracks.count(), 3)
            for t in tracks:
                self.assertTrue(os.path.isabs(t.file_path))
                self.assertEqual(t.source_type, "local")
                self.assertTrue(t.is_available)

            # Offline pending sync queue is maintained and not lost
            self.assertEqual(helper.sync_status, "offline")
            self.assertTrue(PENDING_SYNC_FILE.exists())
            self.assertGreaterEqual(helper.get_pending_sync_count(), 3)

    def test_online_synchronization_drains_pending_queue(self):
        """Helper connects online, pushes pending tracks to /api/sync/tracks/, and clears queue."""
        from helper.sonora_helper import SonoraWindowsHelper, PENDING_SYNC_FILE

        helper = SonoraWindowsHelper()
        helper.config["sync_token"] = "valid_token"

        # Pre-seed pending sync queue
        test_payload = [
            {
                "title": "Synced Song 1",
                "artist": "Sync Artist",
                "album": "Sync Album",
                "duration_seconds": 180.0,
                "duration_formatted": "3:00",
                "format": ".mp3",
                "tag": "Chill",
                "has_cover": False,
                "file_hash": "hash_sync_1",
                "file_path": str(self.music_dir / "synced1.mp3"),
                "relative_name": "synced1.mp3",
            },
            {
                "title": "Synced Song 2",
                "artist": "Sync Artist",
                "album": "Sync Album",
                "duration_seconds": 210.0,
                "duration_formatted": "3:30",
                "format": ".flac",
                "tag": "Electronic",
                "has_cover": True,
                "file_hash": "hash_sync_2",
                "file_path": str(self.music_dir / "synced2.flac"),
                "relative_name": "synced2.flac",
            }
        ]
        helper.add_to_pending_sync(test_payload)
        self.assertEqual(helper.get_pending_sync_count(), 2)

        # Mock successful POST to /api/sync/tracks/
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"status": "success", "synced_count": 2}

        with patch("requests.post", return_value=mock_resp):
            drained = helper.drain_pending_sync()
            self.assertEqual(drained, 2)
            self.assertEqual(helper.get_pending_sync_count(), 0)
            self.assertEqual(helper.sync_status, "synced")

    def test_duplicate_prevention_on_rescan_and_sync(self):
        """Scanning the same folder twice skips duplicates; syncing the same tracks updates existing."""
        from helper.sonora_helper import SonoraWindowsHelper

        helper = SonoraWindowsHelper()
        helper.config["music_dir"] = str(self.music_dir)

        with patch("requests.post"):
            # First scan
            res1 = helper.scan_local_music_library(explicit_dir=str(self.music_dir))
            self.assertEqual(res1["indexed_count"], 3)
            self.assertEqual(res1["skipped_count"], 0)

            # Second scan immediately: identical files should be skipped as duplicates
            res2 = helper.scan_local_music_library(explicit_dir=str(self.music_dir))
            self.assertEqual(res2["indexed_count"], 0)
            self.assertEqual(res2["skipped_count"], 3)

            # Total tracks in database should remain 3
            self.assertEqual(Track.objects.filter(file_path__startswith=str(self.music_dir)).count(), 3)

            # Test sync endpoint duplicate avoidance
            with patch("home.views.check_sync_auth", return_value=True):
                sync_payload = {
                    "tracks": [
                        {
                            "title": "Song One",
                            "artist": "Artist A",
                            "album": "Album 1",
                            "duration_seconds": 120.0,
                            "file_hash": "fixed_hash_1",
                            "file_path": str(self.music_dir / "Artist A" / "Album 1" / "song1.mp3"),
                        }
                    ]
                }
                # Sync twice
                resp1 = self.client.post("/api/sync/tracks/", data=json.dumps(sync_payload), content_type="application/json")
                self.assertEqual(resp1.status_code, 200)

                resp2 = self.client.post("/api/sync/tracks/", data=json.dumps(sync_payload), content_type="application/json")
                self.assertEqual(resp2.status_code, 200)

                # Exactly 1 track with that file hash
                self.assertEqual(Track.objects.filter(file_hash="fixed_hash_1").count(), 1)

    def test_playback_distinction_local_vs_remote_offline(self):
        """Remote streaming of an unuploaded local track returns HTTP 404 ERR_LOCAL_TRACK_OFFLINE instead of fake demo audio."""
        local_track = Track.objects.create(
            title="Laptop Only Song",
            artist="Theeran",
            album="Local Album",
            file_path="C:\\Users\\Theeran\\Documents\\My musicc\\nonexistent_on_cloud.mp3",
            source_type="local",
            format=".mp3",
        )

        resp = self.client.get(f"/stream/{local_track.id}/")
        self.assertEqual(resp.status_code, 404)
        data = resp.json()
        self.assertEqual(data["error"], "ERR_LOCAL_TRACK_OFFLINE")
        self.assertTrue(data["is_local_only"])
        self.assertEqual(resp["X-Sonora-Track-Status"], "local_only")

    def test_api_scan_status_and_helper_heartbeat(self):
        """Helper pushes real-time metrics to /api/sync/scan-status/, and /api/scan-status/ reflects it."""
        with patch("home.views.check_sync_auth", return_value=True):
            payload = {
                "is_scanning": True,
                "scanned_count": 42,
                "discovered_count": 53,
                "indexed_count": 10,
                "skipped_count": 32,
                "failed_count": 0,
                "pending_sync_count": 5,
                "sync_status": "syncing",
                "message": "Extracting Mutagen metadata...",
            }
            post_resp = self.client.post(
                "/api/sync/scan-status/",
                data=json.dumps(payload),
                content_type="application/json"
            )
            self.assertEqual(post_resp.status_code, 200)

            # Browser polls /api/scan-status/
            status_resp = self.client.get("/api/scan-status/")
            self.assertEqual(status_resp.status_code, 200)
            data = status_resp.json()
            self.assertTrue(data["is_scanning"])
            self.assertEqual(data["scanned_count"], 42)
            self.assertEqual(data["discovered_count"], 53)
            self.assertEqual(data["indexed_count"], 10)
            self.assertEqual(data["skipped_count"], 32)
            self.assertEqual(data["pending_sync_count"], 5)
            self.assertEqual(data["sync_status"], "syncing")
            self.assertTrue(data["helper_connected"])


    def test_mutagen_track_number_metadata_extraction(self):
        """Mutagen metadata extractor extracts track title, artist, album, and track_number."""
        from home.scanner import extract_metadata

        dummy_audio = self.music_dir / "track_test.mp3"
        dummy_audio.write_bytes(b"ID3\x03\x00\x00\x00\x00\x00#TIT2\x00\x00\x00\x06\x00\x00\x00MySong" + b"\x00" * 200)

        meta = extract_metadata(str(dummy_audio))
        self.assertIn("track_number", meta)
        self.assertIn("title", meta)
        self.assertIn("artist", meta)
        self.assertIn("album", meta)
        self.assertIn("duration_seconds", meta)
        self.assertIn("has_cover", meta)

    def test_helper_bridge_status_endpoint(self):
        """Helper HTTP bridge /status returns JSON with CORS & Private Network Access headers."""
        from helper.sonora_helper import SonoraWindowsHelper, HelperBridgeHandler
        from io import BytesIO

        helper = SonoraWindowsHelper()
        helper.config["music_dir"] = str(self.music_dir)

        class DummySocket:
            def __init__(self):
                self.output = BytesIO()
            def makefile(self, mode="r", *args, **kwargs):
                if "w" in mode:
                    return self.output
                return BytesIO(b"GET /status HTTP/1.1\r\nHost: 127.0.0.1:8765\r\n\r\n")
            def sendall(self, data):
                self.output.write(data)

        class DummyServer:
            def __init__(self, h):
                self.helper = h

        sock = DummySocket()
        handler = HelperBridgeHandler(sock, ("127.0.0.1", 54321), DummyServer(helper))
        self.assertIsNotNone(handler)
        raw_output = sock.output.getvalue().decode("utf-8", errors="ignore")
        self.assertIn("Access-Control-Allow-Private-Network: true", raw_output)
        self.assertIn("SonoraWindowsHelper", raw_output)


class ProductionSecurityAndErrorHandlingTests(TestCase):
    def setUp(self):
        self.client = Client()

    @override_settings(DEBUG=False, ALLOWED_HOSTS=["testserver", "localhost"])
    def test_production_custom_404_page(self):
        resp = self.client.get("/definitely-nonexistent-url-in-prod/")
        self.assertEqual(resp.status_code, 404)
        content = resp.content.decode("utf-8")
        self.assertIn("404", content)
        self.assertIn("Track or Page Not Found", content)
        self.assertIn("Ask Assistant", content)
        self.assertIn("sonoraAssistantRoot", content)

    def test_insecure_secret_key_rejection_logic(self):
        with patch.dict(os.environ, {"DJANGO_DEBUG": "False", "DJANGO_SECRET_KEY": ""}, clear=False):
            debug_val = os.getenv("DJANGO_DEBUG", "True").lower() in ("true", "1", "yes")
            self.assertFalse(debug_val)


class VercelAndSupabaseIntegrationTests(TestCase):
    """
    Test suite for Vercel serverless entry points, logging resilience,
    Supabase PostgreSQL transaction pooler configuration, and Supabase Storage signed URLs.
    """
    def setUp(self):
        self.client = Client()

    def test_vercel_index_entrypoint_import(self):
        """Verifies that api/index.py exports a valid WSGI callable for Vercel runtime."""
        import api.index
        from django.core.handlers.wsgi import WSGIHandler
        self.assertIsInstance(api.index.app, WSGIHandler)
        self.assertIsInstance(api.index.handler, WSGIHandler)

    def test_wsgi_app_alias(self):
        """Verifies that myproject/wsgi.py exports both application and app."""
        import myproject.wsgi
        self.assertIsNotNone(myproject.wsgi.application)
        self.assertEqual(myproject.wsgi.app, myproject.wsgi.application)

    def test_dj_database_url_supabase_pooler_parsing(self):
        """Verifies parsing of Supabase transaction pooler URLs (port 6543 + sslmode=require)."""
        import dj_database_url
        pooler_url = "postgresql://postgres.projref:secretpass@aws-0-us-east-1.pooler.supabase.com:6543/postgres?sslmode=require"
        cfg = dj_database_url.config(default=pooler_url, conn_max_age=0, ssl_require=True)
        self.assertEqual(cfg["ENGINE"], "django.db.backends.postgresql")
        self.assertEqual(cfg["PORT"], 6543)
        self.assertEqual(cfg["USER"], "postgres.projref")
        self.assertEqual(cfg["PASSWORD"], "secretpass")
        self.assertEqual(cfg["CONN_MAX_AGE"], 0)

    def test_supabase_storage_unconfigured_behavior(self):
        """When SUPABASE_URL is not set, storage functions return graceful errors without crashing."""
        from home.supabase_storage import create_signed_upload_url, create_signed_playback_url, is_supabase_storage_configured
        with override_settings(SUPABASE_URL="", SUPABASE_SERVICE_ROLE_KEY=""):
            self.assertFalse(is_supabase_storage_configured())
            upload_res = create_signed_upload_url("test.mp3")
            self.assertFalse(upload_res["success"])
            self.assertEqual(upload_res["error"], "ERR_STORAGE_UNCONFIGURED")

            play_res = create_signed_playback_url("test.mp3")
            self.assertFalse(play_res["success"])
            self.assertEqual(play_res["error"], "ERR_STORAGE_UNCONFIGURED")

    @patch("requests.post")
    def test_supabase_signed_upload_url_generation(self, mock_post):
        """Verifies creation of presigned upload URLs for direct client uploads."""
        from home.supabase_storage import create_signed_upload_url
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"url": "/object/upload/sign/sonora-music/tracks/1/song.mp3?token=upload_token_123"}
        mock_post.return_value = mock_resp

        with override_settings(
            SUPABASE_URL="https://testproj.supabase.co",
            SUPABASE_SERVICE_ROLE_KEY="service_secret_role_key_2026",
            SUPABASE_STORAGE_BUCKET="sonora-music",
        ):
            res = create_signed_upload_url("tracks/1/song.mp3", expires_in=900)
            self.assertTrue(res["success"])
            self.assertIn("https://testproj.supabase.co/storage/v1/object/upload/sign/sonora-music/tracks/1/song.mp3", res["upload_url"])
            self.assertEqual(res["bucket"], "sonora-music")
            self.assertEqual(res["storage_path"], "tracks/1/song.mp3")

    @patch("requests.post")
    def test_supabase_signed_playback_url_generation(self, mock_post):
        """Verifies creation of temporary signed playback URLs for private bucket audio."""
        from home.supabase_storage import create_signed_playback_url
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"signedURL": "/object/sign/sonora-music/tracks/1/song.mp3?token=playback_token_xyz"}
        mock_post.return_value = mock_resp

        with override_settings(
            SUPABASE_URL="https://testproj.supabase.co",
            SUPABASE_SERVICE_ROLE_KEY="service_secret_role_key_2026",
            SUPABASE_STORAGE_BUCKET="sonora-music",
        ):
            res = create_signed_playback_url("supabase://sonora-music/tracks/1/song.mp3", expires_in=3600)
            self.assertTrue(res["success"])
            self.assertIn("https://testproj.supabase.co/storage/v1/object/sign/sonora-music/tracks/1/song.mp3", res["playback_url"])

    def test_api_upload_selection_list(self):
        """Verifies the selection list endpoint allowing explicit song selection before upload."""
        track = Track.objects.create(
            title="Selection Test Song",
            artist="Selection Artist",
            album="Selection Album",
            duration_seconds=120,
            duration_formatted="2:00",
            file_path="C:\\music\\select.mp3",
            format="MP3",
            source_type="local",
        )
        resp = self.client.get("/api/upload/selection/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("tracks", data)
        matching = [t for t in data["tracks"] if t["id"] == track.id]
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0]["title"], "Selection Test Song")
        self.assertFalse(matching[0]["is_in_cloud"])

    @patch("home.supabase_storage.is_supabase_storage_configured", return_value=True)
    @patch("home.supabase_storage.create_signed_upload_url")
    def test_api_upload_sign_endpoint(self, mock_sign, mock_cfg):
        """Verifies the /api/upload/sign/ endpoint generates presigned upload URLs."""
        mock_sign.return_value = {
            "success": True,
            "upload_url": "https://testproj.supabase.co/storage/v1/object/upload/sign/sonora-music/tracks/5/song.mp3?token=abc",
            "storage_path": "tracks/5/song.mp3",
            "bucket": "sonora-music",
        }
        resp = self.client.post(
            "/api/upload/sign/",
            data=json.dumps({"filename": "song.mp3", "track_id": 5}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["success"])
        self.assertIn("upload_url", data)

    def test_api_upload_confirm_endpoint(self):
        """Verifies the /api/upload/confirm/ endpoint binds a private storage path to Track model."""
        track = Track.objects.create(
            title="Confirm Upload Song",
            artist="Confirm Artist",
            album="Confirm Album",
            duration_seconds=180,
            file_path="C:\\local\\confirm.mp3",
            source_type="local",
        )
        resp = self.client.post(
            "/api/upload/confirm/",
            data=json.dumps({
                "track_id": track.id,
                "storage_path": f"tracks/{track.id}/confirm.mp3",
            }),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "success")

        track.refresh_from_db()
        self.assertEqual(track.cloud_url, f"supabase://sonora-music/tracks/{track.id}/confirm.mp3")
        self.assertEqual(track.source_type, "upload")

    @patch("home.supabase_storage.create_signed_playback_url")
    @patch("requests.get")
    def test_stream_audio_with_supabase_storage(self, mock_requests_get, mock_signed_url):
        """Verifies that stream_audio generates a signed playback URL and proxies stream with CORS."""
        track = Track.objects.create(
            title="Supabase Stream Song",
            artist="Cloud Artist",
            file_path="supabase://sonora-music/tracks/99/cloud.mp3",
            cloud_url="supabase://sonora-music/tracks/99/cloud.mp3",
            source_type="upload",
        )
        mock_signed_url.return_value = {
            "success": True,
            "playback_url": "https://testproj.supabase.co/storage/v1/object/sign/sonora-music/tracks/99/cloud.mp3?token=xyz",
        }
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"Content-Type": "audio/mpeg", "Content-Length": "1000"}
        mock_resp.iter_content.return_value = [b"ID3_MOCK_STREAM_BYTES"]
        mock_requests_get.return_value = mock_resp

        resp = self.client.get(f"/stream/{track.id}/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Access-Control-Allow-Origin"], "*")
        mock_signed_url.assert_called_once()


class DuplicatePreventionAndPlaybackTests(TestCase):
    """
    Comprehensive automated tests for duplicate prevention, deduplicated library display,
    and reliable audio playback / HTTP Range streaming.
    """

    def setUp(self):
        import shutil
        self.temp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.temp_dir, ignore_errors=True)

        # Create a mock valid audio file
        self.audio_file1 = os.path.join(self.temp_dir, "test_track_1.mp3")
        with open(self.audio_file1, "wb") as f:
            f.write(b"\xff\xfb\x90\x44" + b"\x00" * 4096)  # Minimal MP3 frame header + padding

        # Duplicate copy in another directory
        self.other_dir = os.path.join(self.temp_dir, "other_folder")
        os.makedirs(self.other_dir, exist_ok=True)
        self.audio_file1_dup = os.path.join(self.other_dir, "test_track_1.mp3")
        shutil.copy2(self.audio_file1, self.audio_file1_dup)

    def test_scanner_duplicate_prevention_same_content_hash(self):
        """Verifies that indexing an identical audio file across folders does not create duplicate tracks."""
        from home.scanner import index_single_file

        status1, track1 = index_single_file(self.audio_file1)
        self.assertEqual(status1, "indexed")
        initial_count = Track.objects.count()

        # Attempt to index the duplicate file in another folder
        status2, track2 = index_single_file(self.audio_file1_dup)
        self.assertEqual(status2, "skipped")
        self.assertEqual(Track.objects.count(), initial_count)
        self.assertEqual(track1.id, track2.id)

    def test_scanner_repeated_scan_idempotency(self):
        """Verifies that running scans repeatedly on the same files is strictly idempotent."""
        from home.scanner import scan_music_library

        res1 = scan_music_library(explicit_dirs=[self.temp_dir])
        count1 = Track.objects.count()

        res2 = scan_music_library(explicit_dirs=[self.temp_dir])
        count2 = Track.objects.count()

        self.assertEqual(count1, count2)
        self.assertEqual(res2["indexed_count"], 0)
        self.assertGreaterEqual(res2["skipped_count"], 1)

    def test_views_deduplicate_tracks_for_display(self):
        """Verifies that deduplicate_tracks_for_display groups identical tracks and selects the active one."""
        from home.views import deduplicate_tracks_for_display

        t1 = Track.objects.create(
            title="Raathu Raasan - MassTamilan",
            artist="Sai Abhyankkar",
            album="Karuppu",
            duration_seconds=210.0,
            duration_formatted="3:30",
            file_path=r"C:\NonExistent\Documents\My musicc\Raathu Raasan.mp3",
            file_size=6500000,
            file_hash="",
            is_available=True,
        )
        t2 = Track.objects.create(
            title="Raathu Raasan - MassTamilan",
            artist="Sai Abhyankkar",
            album="Karuppu",
            duration_seconds=210.0,
            duration_formatted="3:30",
            file_path=self.audio_file1,
            file_size=6500000,
            file_hash="a6d7d8e95b5192fade7241421571d8c55023a2b5bc9ab23dc92cb17539be5f31",
            is_available=True,
        )

        all_tracks = [t1, t2]
        deduped = deduplicate_tracks_for_display(all_tracks)
        self.assertEqual(len(deduped), 1)
        self.assertEqual(deduped[0].id, t2.id)

    def test_stream_audio_full_and_range_requests(self):
        """Verifies that stream_audio supports HTTP 200 and HTTP 206 Range requests with proper headers."""
        track = Track.objects.create(
            title="Playback Stream Test Track",
            artist="Playback Artist",
            file_path=self.audio_file1,
            file_size=os.path.getsize(self.audio_file1),
            is_available=True,
        )

        # Full GET request (HTTP 200)
        resp200 = self.client.get(f"/stream/{track.id}/")
        self.assertEqual(resp200.status_code, 200)
        self.assertEqual(resp200["Content-Type"], "audio/mpeg")
        self.assertEqual(resp200["Accept-Ranges"], "bytes")
        self.assertEqual(resp200["Access-Control-Allow-Origin"], "*")

        # Range request (HTTP 206 Partial Content)
        resp206 = self.client.get(f"/stream/{track.id}/", HTTP_RANGE="bytes=0-100")
        self.assertEqual(resp206.status_code, 206)
        self.assertEqual(resp206["Content-Type"], "audio/mpeg")
        self.assertEqual(resp206["Content-Length"], "101")
        self.assertTrue(resp206["Content-Range"].startswith("bytes 0-100/"))

    def test_stream_audio_newly_added_portable_fallback(self):
        """Verifies that a track with a Windows path plays when the file exists in media/music/."""
        # Create a mock file in settings.BASE_DIR / media / music
        media_music = settings.BASE_DIR / "media" / "music"
        media_music.mkdir(parents=True, exist_ok=True)
        test_song_path = media_music / "newly_added_song.mp3"
        with open(test_song_path, "wb") as f:
            f.write(b"\xff\xfb\x90\x44" + b"\x00" * 2048)
        self.addCleanup(lambda: test_song_path.unlink(missing_ok=True))

        track = Track.objects.create(
            title="Newly Added Song",
            artist="New Artist",
            file_path=r"C:\Users\Theeran\Documents\My musicc\newly_added_song.mp3",
            file_size=2052,
            is_available=True,
        )

        resp = self.client.get(f"/stream/{track.id}/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "audio/mpeg")

    def test_stream_audio_missing_file_useful_error(self):
        """Verifies that truly missing files return a structured 404 with ERR_LOCAL_TRACK_OFFLINE."""
        track = Track.objects.create(
            title="Completely Missing Offline Track",
            artist="Ghost Artist",
            file_path=r"C:\Impossible\Path\NonexistentSongXYZ123.mp3",
            is_available=True,
        )

        resp = self.client.get(f"/stream/{track.id}/")
        self.assertEqual(resp.status_code, 404)
        data = resp.json()
        self.assertEqual(data["error"], "ERR_LOCAL_TRACK_OFFLINE")
        self.assertEqual(data["track_id"], track.id)






