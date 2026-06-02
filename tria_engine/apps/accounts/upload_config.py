# apps/accounts/upload_config.py

from django.conf import settings


def get_upload_settings():
    return getattr(settings, "TRIA_UPLOADS", {})


def get_document_max_size():
    return get_upload_settings().get("DOCUMENT_MAX_SIZE", 5 * 1024 * 1024)


def get_document_allowed_extensions():
    return get_upload_settings().get("DOCUMENT_ALLOWED_EXTENSIONS", ["pdf", "doc", "docx", "txt"])


def get_profile_photo_max_size():
    return get_upload_settings().get("PROFILE_PHOTO_MAX_SIZE", 5 * 1024 * 1024)


def get_profile_photo_allowed_extensions():
    return get_upload_settings().get("PROFILE_PHOTO_ALLOWED_EXTENSIONS", ["jpg", "jpeg", "png"])