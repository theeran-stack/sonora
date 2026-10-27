from django.contrib import admin
from .models import Track


@admin.register(Track)
class TrackAdmin(admin.ModelAdmin):
    list_display = ("title", "artist", "album", "duration_formatted", "format", "tag", "source_type")
    search_fields = ("title", "artist", "album", "file_path")
    list_filter = ("format", "tag", "source_type", "has_cover")
