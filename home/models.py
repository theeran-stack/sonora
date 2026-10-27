import os
from pathlib import Path
from django.db import models


AURORA_GRADIENTS = [
    "linear-gradient(135deg, #8B7CFF, #55E6C1)",
    "linear-gradient(135deg, #55E6C1, #38BDF8)",
    "linear-gradient(135deg, #F472B6, #8B7CFF)",
    "linear-gradient(135deg, #FB923C, #F43F5E)",
    "linear-gradient(135deg, #38BDF8, #818CF8)",
    "linear-gradient(135deg, #A78BFA, #34D399)",
]


class Track(models.Model):
    title = models.CharField(max_length=255)
    artist = models.CharField(max_length=255, default="Unknown Artist")
    album = models.CharField(max_length=255, default="Unknown Album")
    duration_seconds = models.FloatField(default=0.0)
    duration_formatted = models.CharField(max_length=20, default="0:00")
    file_path = models.CharField(max_length=1000, unique=True, db_index=True)
    file_size = models.BigIntegerField(default=0)
    file_mtime = models.FloatField(default=0.0)
    format = models.CharField(max_length=20, default="")
    tag = models.CharField(max_length=50, default="Local")
    has_cover = models.BooleanField(default=False)
    source_type = models.CharField(max_length=20, default="local")  # local, demo, upload
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["title", "artist"]

    def __str__(self):
        return f"{self.title} - {self.artist}"

    def get_default_gradient(self):
        idx = (self.id or 0) % len(AURORA_GRADIENTS)
        return AURORA_GRADIENTS[idx]

    def to_dict(self):
        if self.source_type == "demo" and self.file_path.startswith("http"):
            audio_url = self.file_path
            cover_val = self.get_default_gradient()
        else:
            audio_url = f"/stream/{self.id}/"
            cover_val = f"/cover/{self.id}/"

        return {
            "id": self.id,
            "title": self.title,
            "artist": self.artist,
            "album": self.album,
            "duration": self.duration_formatted,
            "cover": cover_val,
            "cover_fallback": self.get_default_gradient(),
            "has_cover": self.has_cover,
            "audio": audio_url,
            "tag": self.tag,
            "format": self.format.upper().lstrip("."),
            "source_type": self.source_type,
        }
