from django.contrib import admin
from django.urls import path
from home.views import (
    home_page,
    stream_audio,
    get_cover_art,
    api_scan_music,
    api_scan_status,
    api_upload_music,
    api_upload_selection_list,
    api_upload_sign,
    api_upload_confirm,
    api_discover_search,
    api_discover_queue,
    api_downloads_list,
    api_download_retry,
    api_download_cancel,
    api_sync_status,
    api_sync_queue,
    api_sync_queue_update,
    api_sync_tracks,
    api_sync_upload,
    api_sync_scan_status,
    api_faqs_list,
    api_error_codes_list,
    api_error_code_detail,
    api_tickets_list,
    api_ticket_create,
    api_ticket_update,
    api_ticket_delete,
    faq_page,
    error_codes_page,
    api_assistant_chat,
    api_assistant_feedback,
    api_assistant_history,
    custom_404,
    custom_500,
    health_check,
)

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", home_page, name="home"),
    path("faq/", faq_page, name="faq_page"),
    path("errors/", error_codes_page, name="error_codes_page"),
    path("error-codes/", error_codes_page, name="error_codes_page_alt"),
    path("stream/<int:track_id>/", stream_audio, name="stream_audio"),
    path("cover/<int:track_id>/", get_cover_art, name="cover_art"),
    path("api/scan/", api_scan_music, name="api_scan_music"),
    path("api/scan-status/", api_scan_status, name="api_scan_status"),
    path("api/upload/", api_upload_music, name="api_upload_music"),
    path("api/upload/selection/", api_upload_selection_list, name="api_upload_selection_list"),
    path("api/upload/sign/", api_upload_sign, name="api_upload_sign"),
    path("api/upload/confirm/", api_upload_confirm, name="api_upload_confirm"),
    # Discovery
    path("api/discover/search/", api_discover_search, name="api_discover_search"),
    path("api/discover/queue/", api_discover_queue, name="api_discover_queue"),
    # Downloads
    path("api/downloads/", api_downloads_list, name="api_downloads_list"),
    path("api/downloads/<int:task_id>/retry/", api_download_retry, name="api_download_retry"),
    path("api/downloads/<int:task_id>/cancel/", api_download_cancel, name="api_download_cancel"),
    # Synchronization
    path("api/sync/status/", api_sync_status, name="api_sync_status"),
    path("api/sync/queue/", api_sync_queue, name="api_sync_queue"),
    path("api/sync/queue/update/", api_sync_queue_update, name="api_sync_queue_update"),
    path("api/sync/tracks/", api_sync_tracks, name="api_sync_tracks"),
    path("api/sync/upload/", api_sync_upload, name="api_sync_upload"),
    path("api/sync/scan-status/", api_sync_scan_status, name="api_sync_scan_status"),
    # FAQs
    path("api/faqs/", api_faqs_list, name="api_faqs_list"),
    # Error Codes
    path("api/error-codes/", api_error_codes_list, name="api_error_codes_list"),
    path("api/error-codes/<str:code>/", api_error_code_detail, name="api_error_code_detail"),
    # Ticket Management
    path("api/tickets/", api_tickets_list, name="api_tickets_list"),
    path("api/tickets/create/", api_ticket_create, name="api_ticket_create"),
    path("api/tickets/<int:ticket_id>/update/", api_ticket_update, name="api_ticket_update"),
    path("api/tickets/<int:ticket_id>/delete/", api_ticket_delete, name="api_ticket_delete"),
    # AI Assistant
    path("api/assistant/chat/", api_assistant_chat, name="api_assistant_chat"),
    path("api/assistant/feedback/", api_assistant_feedback, name="api_assistant_feedback"),
    path("api/assistant/history/", api_assistant_history, name="api_assistant_history"),
    # Health Checks
    path("health/", health_check, name="health_check"),
    path("healthz/", health_check, name="healthz"),
]

handler404 = "home.views.custom_404"
handler500 = "home.views.custom_500"
