#accounts/utils.py

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from cryptography.fernet import Fernet, InvalidToken


def get_cipher_suite():
    key = getattr(settings, "CTMS_ENCRYPTION_KEY", None)
    if not key:
        raise ImproperlyConfigured("CTMS_ENCRYPTION_KEY is not configured")

    if isinstance(key, str):
        key = key.encode("utf-8")

    try:
        return Fernet(key)
    except Exception:
        raise ImproperlyConfigured("CTMS_ENCRYPTION_KEY must be a valid Fernet key")


def encrypt_value(data):
    if data in [None, ""]:
        return None

    cipher = get_cipher_suite()
    if isinstance(data, str):
        data = data.encode("utf-8")
    return cipher.encrypt(data).decode("utf-8")


def decrypt_value(encrypted_data):
    if encrypted_data in [None, ""]:
        return None

    cipher = get_cipher_suite()
    if isinstance(encrypted_data, str):
        encrypted_data = encrypted_data.encode("utf-8")

    try:
        return cipher.decrypt(encrypted_data).decode("utf-8")
    except InvalidToken:
        return None