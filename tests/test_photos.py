from datetime import datetime
from pathlib import Path

import piexif
import pytest
from PIL import Image

from substack_post.photos import PhotoError, collect, taken_at


def _make_jpeg(path: Path, *, taken: datetime | None) -> None:
    image = Image.new("RGB", (4, 4), color="red")
    if taken is None:
        image.save(path, format="JPEG")
        return
    exif_bytes = piexif.dump(
        {
            "Exif": {
                piexif.ExifIFD.DateTimeOriginal: taken.strftime("%Y:%m:%d %H:%M:%S"),
            }
        }
    )
    image.save(path, format="JPEG", exif=exif_bytes)


def test_taken_at_reads_exif(tmp_path: Path) -> None:
    path = tmp_path / "dated.jpg"
    _make_jpeg(path, taken=datetime(2026, 8, 3, 14, 30, 0))
    assert taken_at(path) == datetime(2026, 8, 3, 14, 30, 0)


def test_taken_at_returns_none_without_exif(tmp_path: Path) -> None:
    path = tmp_path / "undated.jpg"
    _make_jpeg(path, taken=None)
    assert taken_at(path) is None


def test_collect_orders_dated_before_undated_and_alphabetically_within_undated(
    tmp_path: Path,
) -> None:
    _make_jpeg(tmp_path / "b_undated.jpg", taken=None)
    _make_jpeg(tmp_path / "a_undated.jpg", taken=None)
    _make_jpeg(tmp_path / "second.jpg", taken=datetime(2026, 8, 3, 10, 0, 0))
    _make_jpeg(tmp_path / "first.jpg", taken=datetime(2026, 8, 1, 9, 0, 0))

    photos = collect(tmp_path)

    assert [p.name for p in photos] == [
        "first.jpg",
        "second.jpg",
        "a_undated.jpg",
        "b_undated.jpg",
    ]


def test_collect_ignores_unsupported_files(tmp_path: Path) -> None:
    _make_jpeg(tmp_path / "photo.jpg", taken=datetime(2026, 8, 1, 9, 0, 0))
    (tmp_path / "notes.txt").write_text("not a photo")
    (tmp_path / ".hidden.jpg").write_bytes(b"")

    photos = collect(tmp_path)

    assert [p.name for p in photos] == ["photo.jpg"]


def test_collect_raises_on_empty_folder(tmp_path: Path) -> None:
    with pytest.raises(PhotoError):
        collect(tmp_path)


def test_collect_raises_on_missing_folder(tmp_path: Path) -> None:
    with pytest.raises(PhotoError):
        collect(tmp_path / "does-not-exist")
