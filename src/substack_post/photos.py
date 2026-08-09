"""Photo discovery, EXIF timestamps, and ordering.

A photo's caption comes from the moment it was taken, which lives in EXIF. We
deliberately do *not* fall back to the file's modification time: copying photos
off a camera or out of a phone rewrites mtime, so it would silently produce
confident-looking wrong dates. A photo with no EXIF timestamp simply gets no
caption.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageOps
from pillow_heif import register_heif_opener

# Lets Pillow open HEIC/HEIF (the iPhone default) so we can read its EXIF.
register_heif_opener()

SUPPORTED_EXTENSIONS = frozenset(
    {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff", ".heic", ".heif"}
)

# Formats Substack's uploader will not accept, so we transcode them to JPEG.
_CONVERT_TO_JPEG = frozenset({".heic", ".heif"})

# EXIF tag IDs, most-trustworthy first. DateTimeOriginal is when the shutter
# fired; DateTime is merely when the file was last written by some editor.
_EXIF_IFD = 0x8769
_DATETIME_ORIGINAL = 0x9003
_DATETIME_DIGITIZED = 0x9004
_DATETIME = 0x0132

_EXIF_DATETIME_FORMAT = "%Y:%m:%d %H:%M:%S"


class PhotoError(Exception):
    """Raised when a photo folder cannot be used."""


@dataclass(frozen=True)
class Photo:
    """A single photo on disk and the moment it was taken, if known."""

    path: Path
    taken_at: datetime | None

    @property
    def name(self) -> str:
        return self.path.name


def taken_at(path: Path) -> datetime | None:
    """Return when the photo was taken, or None if EXIF does not say."""
    try:
        with Image.open(path) as image:
            exif = image.getexif()
            if not exif:
                return None
            # DateTimeOriginal and DateTimeDigitized live in the Exif sub-IFD;
            # only the plain DateTime tag sits in the top-level IFD0.
            sub_ifd = exif.get_ifd(_EXIF_IFD) or {}
            candidates = (
                sub_ifd.get(_DATETIME_ORIGINAL),
                sub_ifd.get(_DATETIME_DIGITIZED),
                exif.get(_DATETIME),
            )
    except (OSError, ValueError, SyntaxError):
        # Unreadable or corrupt image metadata: treat as "no timestamp".
        return None

    for value in candidates:
        parsed = _parse_exif_datetime(value)
        if parsed is not None:
            return parsed
    return None


def _parse_exif_datetime(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value.strip(), _EXIF_DATETIME_FORMAT)
    except ValueError:
        # Cameras occasionally write placeholders like "0000:00:00 00:00:00".
        return None


def collect(directory: Path) -> list[Photo]:
    """Find every supported photo in `directory` and return them in post order.

    Photos with an EXIF timestamp come first, oldest to newest. Photos without
    one follow, ordered by filename, so the tool stays deterministic.
    """
    if not directory.exists():
        raise PhotoError(f"Photo folder does not exist: {directory}")
    if not directory.is_dir():
        raise PhotoError(f"Not a folder: {directory}")

    paths = [
        entry
        for entry in directory.iterdir()
        if entry.is_file()
        and not entry.name.startswith(".")
        and entry.suffix.lower() in SUPPORTED_EXTENSIONS
    ]
    if not paths:
        supported = ", ".join(sorted(SUPPORTED_EXTENSIONS))
        raise PhotoError(f"No photos found in {directory} (looking for: {supported})")

    photos = [Photo(path=path, taken_at=taken_at(path)) for path in paths]
    return sorted(photos, key=_order_key)


def _order_key(photo: Photo) -> tuple[bool, datetime, str]:
    return (
        photo.taken_at is None,
        photo.taken_at or datetime.min,
        photo.name.casefold(),
    )


def prepare_for_upload(photo: Photo, workdir: Path) -> Path:
    """Return a path Substack's uploader will accept.

    Formats Substack rejects are transcoded to JPEG inside `workdir`; everything
    else uploads its original bytes, so no photo is needlessly recompressed.
    """
    if photo.path.suffix.lower() not in _CONVERT_TO_JPEG:
        return photo.path

    destination = workdir / f"{photo.path.stem}.jpg"
    with Image.open(photo.path) as image:
        # exif_transpose bakes in the orientation tag, which is lost on convert.
        upright = ImageOps.exif_transpose(image)
        upright.convert("RGB").save(destination, format="JPEG", quality=95)
    return destination
