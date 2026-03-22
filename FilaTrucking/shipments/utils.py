import base64
import hashlib
from django.conf import settings
from cryptography.fernet import Fernet

def get_cipher():
    """Derive a consistent key from SECRET_KEY."""
    # Fernet requires a 32-byte base64-encoded key
    key = hashlib.sha256(settings.SECRET_KEY.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))

def encrypt_value(value: str) -> str:
    if not value:
        return ""
    cipher = get_cipher()
    return cipher.encrypt(value.encode()).decode()

def decrypt_value(encrypted_value: str) -> str:
    if not encrypted_value:
        return ""
    try:
        cipher = get_cipher()
        return cipher.decrypt(encrypted_value.encode()).decode()
    except Exception:
        # Fallback for unencrypted or corrupted data
        return ""
