from django.contrib import admin
from django.urls import path
from home.views import (
    home_page,
    stream_audio,
    get_cover_art,
    api_scan_music,
    api_scan_status,
    api_upload_music,
)

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", home_page, name="home"),
    path("stream/<int:track_id>/", stream_audio, name="stream_audio"),
    path("cover/<int:track_id>/", get_cover_art, name="cover_art"),
    path("api/scan/", api_scan_music, name="api_scan_music"),
    path("api/scan-status/", api_scan_status, name="api_scan_status"),
    path("api/upload/", api_upload_music, name="api_upload_music"),
]
