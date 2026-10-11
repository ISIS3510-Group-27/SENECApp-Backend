"""Uploaded media (group cover photos).

Photos are re-encoded before being stored: that proves the file really is an
image, bounds its size, and drops its metadata (phone cameras embed the GPS
position where the photo was taken).

Storage is behind :class:`MediaStorage`. ``LocalMediaStorage`` writes to a
directory served by the API at ``/media``; on the server that directory is a
Docker volume on the EC2 disk.
"""

import io
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Protocol

from PIL import Image, ImageOps, UnidentifiedImageError

from app.core.config import get_settings
from app.services.errors import ServiceError, ValidationFailedError

URL_PREFIX = "/media"
ACCEPTED_FORMATS = {"JPEG", "PNG", "WEBP"}
MAX_SIDE = 1600
JPEG_QUALITY = 85


class UploadTooLargeError(ServiceError):
    status_code = 413


class MediaStorage(Protocol):
    def save(self, key: str, data: bytes) -> str:
        """Store ``data`` under ``key`` and return its public path."""

    def delete(self, public_path: str) -> None:
        """Delete a file previously returned by :meth:`save` (no-op if gone)."""


class LocalMediaStorage:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()

    def save(self, key: str, data: bytes) -> str:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return f"{URL_PREFIX}/{key}"

    def delete(self, public_path: str) -> None:
        if not public_path.startswith(f"{URL_PREFIX}/"):
            return  # not ours (e.g. an external image URL)
        self._path(public_path.removeprefix(f"{URL_PREFIX}/")).unlink(missing_ok=True)

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if not path.is_relative_to(self.root):  # never write outside the media root
            raise ValueError(f"Invalid media key: {key}")
        return path


@lru_cache
def get_media_storage() -> MediaStorage:
    return LocalMediaStorage(get_settings().media_root)


def prepare_photo(raw: bytes) -> bytes:
    """Validate an uploaded image and re-encode it as a metadata-free JPEG."""
    max_bytes = get_settings().max_upload_mb * 1024 * 1024
    if len(raw) > max_bytes:
        raise UploadTooLargeError(f"The photo is too large (max {get_settings().max_upload_mb} MB)")
    try:
        image = Image.open(io.BytesIO(raw))
        if image.format not in ACCEPTED_FORMATS:
            raise ValidationFailedError("Upload a JPEG, PNG or WebP photo")
        image = ImageOps.exif_transpose(image)  # keep the orientation the phone intended
        image = image.convert("RGB")
    except (UnidentifiedImageError, OSError) as exc:
        raise ValidationFailedError("That file isn't a valid image") from exc
    image.thumbnail((MAX_SIDE, MAX_SIDE))
    out = io.BytesIO()
    image.save(out, format="JPEG", quality=JPEG_QUALITY, optimize=True)  # no EXIF kept
    return out.getvalue()


def new_cover_key(group_id: int) -> str:
    return f"groups/{group_id}/cover-{uuid.uuid4().hex}.jpg"
