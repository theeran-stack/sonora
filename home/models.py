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
    source_type = models.CharField(max_length=20, default="local")  # local, demo, upload, jamendo
    is_available = models.BooleanField(default=True, db_index=True)
    file_hash = models.CharField(max_length=64, blank=True, default="", db_index=True)
    external_id = models.CharField(max_length=100, blank=True, default="", db_index=True)
    cloud_url = models.CharField(max_length=1000, blank=True, default="")
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
            "is_available": self.is_available,
            "file_hash": self.file_hash,
            "external_id": self.external_id,
        }


class DownloadTask(models.Model):
    STATUS_CHOICES = [
        ("queued", "Queued"),
        ("downloading", "Downloading"),
        ("completed", "Completed"),
        ("failed", "Failed"),
    ]

    title = models.CharField(max_length=255)
    artist = models.CharField(max_length=255, default="Unknown Artist")
    album = models.CharField(max_length=255, default="Single")
    provider = models.CharField(max_length=50, default="jamendo")
    external_id = models.CharField(max_length=100, db_index=True)
    download_url = models.CharField(max_length=1000)
    preview_url = models.CharField(max_length=1000, blank=True, default="")
    cover_url = models.CharField(max_length=1000, blank=True, default="")
    duration = models.CharField(max_length=20, default="0:00")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="queued", db_index=True)
    progress = models.IntegerField(default=0)  # 0 to 100
    file_path = models.CharField(max_length=1000, blank=True, default="")
    error_message = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"[{self.status}] {self.title} - {self.artist}"

    def to_dict(self):
        return {
            "id": self.id,
            "title": self.title,
            "artist": self.artist,
            "album": self.album,
            "provider": self.provider,
            "external_id": self.external_id,
            "download_url": self.download_url,
            "preview_url": self.preview_url,
            "cover_url": self.cover_url,
            "duration": self.duration,
            "status": self.status,
            "progress": self.progress,
            "file_path": self.file_path,
            "error_message": self.error_message,
            "created_at": self.created_at.strftime("%Y-%m-%d %H:%M:%S") if self.created_at else "",
        }


class SyncLog(models.Model):
    action = models.CharField(max_length=50)
    details = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.created_at} - {self.action}"


class FAQ(models.Model):
    CATEGORY_CHOICES = [
        ("general", "General"),
        ("audio", "Audio & Streaming"),
        ("library", "Library & Scanning"),
        ("sync", "Cloud & Sync"),
        ("security", "Security"),
        ("troubleshooting", "Troubleshooting"),
    ]

    question = models.CharField(max_length=500, unique=True, db_index=True)
    answer = models.TextField()
    category = models.CharField(max_length=100, default="general", db_index=True)
    is_active = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["category", "id"]
        verbose_name = "FAQ"
        verbose_name_plural = "FAQs"

    def __str__(self):
        return self.question

    def to_dict(self):
        return {
            "id": self.id,
            "question": self.question,
            "answer": self.answer,
            "category": self.category,
            "is_active": self.is_active,
            "created_at": self.created_at.strftime("%Y-%m-%d %H:%M:%S") if self.created_at else "",
            "updated_at": self.updated_at.strftime("%Y-%m-%d %H:%M:%S") if self.updated_at else "",
        }


class ErrorCode(models.Model):
    code = models.CharField(max_length=50, unique=True, db_index=True)
    title = models.CharField(max_length=200)
    description = models.TextField()
    cause = models.TextField()
    recovery_step = models.TextField()
    http_status = models.IntegerField(default=400)
    is_active = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["code"]
        verbose_name = "Error Code"
        verbose_name_plural = "Error Codes"

    def __str__(self):
        return f"{self.code} ({self.http_status}) - {self.title}"

    def to_dict(self):
        return {
            "id": self.id,
            "code": self.code,
            "title": self.title,
            "description": self.description,
            "cause": self.cause,
            "recovery_step": self.recovery_step,
            "http_status": self.http_status,
            "is_active": self.is_active,
            "created_at": self.created_at.strftime("%Y-%m-%d %H:%M:%S") if self.created_at else "",
            "updated_at": self.updated_at.strftime("%Y-%m-%d %H:%M:%S") if self.updated_at else "",
        }


class Ticket(models.Model):
    STATUS_CHOICES = [
        ("open", "Open"),
        ("in_progress", "In Progress"),
        ("resolved", "Resolved"),
        ("closed", "Closed"),
    ]

    title = models.CharField(max_length=200)
    description = models.TextField()
    email = models.EmailField(db_index=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="open", db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Support Ticket"
        verbose_name_plural = "Support Tickets"

    def __str__(self):
        return f"Ticket #{self.id} [{self.status}]: {self.title}"

    def to_dict(self, include_email=False):
        data = {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "status": self.status,
            "created_at": self.created_at.strftime("%Y-%m-%d %H:%M:%S") if self.created_at else "",
            "updated_at": self.updated_at.strftime("%Y-%m-%d %H:%M:%S") if self.updated_at else "",
        }
        if include_email:
            data["email"] = self.email
        return data
