"""Upload helpers shared by every model with a file field (QA-023)."""
import os
import uuid

from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator
from django.utils.deconstruct import deconstructible

MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # nginx client_max_body_size is 10M as well
DOCUMENT_EXTENSIONS = ["pdf", "jpg", "jpeg", "png", "webp", "doc", "docx", "xls", "xlsx"]
IMAGE_EXTENSIONS = ["jpg", "jpeg", "png", "webp"]


@deconstructible
class RandomUploadPath:
    """upload_to that stores the file as <folder>/<random hex><original extension>.

    The original file name ("license_john_smith.pdf") is both personal data and guessable; a 128-bit random name is
    not. The user-visible name can still be kept in a separate model field (DriverDocument.name).
    """

    def __init__(self, folder):
        self.folder = folder.strip("/")

    def __call__(self, instance, filename):
        extension = os.path.splitext(filename)[1].lower()[:10]
        return f"{self.folder}/{uuid.uuid4().hex}{extension}"

    def __eq__(self, other):
        return isinstance(other, RandomUploadPath) and other.folder == self.folder


def validate_upload_size(file):
    if file.size > MAX_UPLOAD_BYTES:
        raise ValidationError(f"File too large ({file.size // 1024 // 1024} MB). The limit is {MAX_UPLOAD_BYTES // 1024 // 1024} MB.")


document_validators = [FileExtensionValidator(DOCUMENT_EXTENSIONS), validate_upload_size]
image_validators = [FileExtensionValidator(IMAGE_EXTENSIONS), validate_upload_size]
