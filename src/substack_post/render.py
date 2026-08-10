"""Render the post template: one captioned-image paragraph per photo."""

from __future__ import annotations

import importlib.resources
from pathlib import Path

from .photos import Photo

DEFAULT_CAPTION_FORMAT = "%B {day}, %Y"

SPACER_PLACEHOLDER = "{{spacer}}"
_SPACER_MARKDOWN = " "  # non-breaking space


def default_template() -> str:
    return _read_package_template("post.md")


def default_signoff() -> str:
    return _read_package_template("signoff.md").strip()


def default_notify_message_template() -> str:
    return _read_package_template("notify_message.md")


def _read_package_template(name: str) -> str:
    return (
        importlib.resources.files("substack_post.templates").joinpath(name).read_text()
    )


def format_caption(photo: Photo, caption_format: str) -> str:
    """Return the caption for a photo, or "" if it has no known timestamp."""
    if photo.taken_at is None:
        return ""
    # %-d (no leading zero) isn't portable across platforms, so the day number
    # is substituted by hand before strftime handles the rest of the format.
    fmt = caption_format.replace("{day}", str(photo.taken_at.day))
    return photo.taken_at.strftime(fmt)


def _escape_title(caption: str) -> str:
    return caption.replace("\\", "\\\\").replace('"', '\\"')


def photo_markdown(photo_url: str, caption: str) -> str:
    """One markdown paragraph for a single captioned image.

    The image must be alone in its paragraph for python-substack to treat it as
    a captioned image; a caption comes from the CommonMark title slot. An empty
    caption omits the title slot entirely, rather than emitting an empty one.
    """
    if caption:
        return f'![]({photo_url} "{_escape_title(caption)}")'
    return f"![]({photo_url})"


def render_photos_block(
    photo_urls: list[tuple[Photo, str]], caption_format: str
) -> str:
    """One markdown paragraph per photo, with a spacer paragraph between each."""
    paragraphs = [
        photo_markdown(url, format_caption(photo, caption_format))
        for photo, url in photo_urls
    ]
    separator = f"\n\n{SPACER_PLACEHOLDER}\n\n"
    return separator.join(paragraphs)


def render_post(
    template: str,
    photo_urls: list[tuple[Photo, str]],
    caption_format: str,
    signoff: str,
) -> str:
    rendered = template.replace(
        "{{photos}}", render_photos_block(photo_urls, caption_format)
    )
    rendered = rendered.replace("{{signoff}}", signoff)
    rendered = rendered.replace(SPACER_PLACEHOLDER, _SPACER_MARKDOWN)
    return rendered


def load_template(path: Path | None) -> str:
    if path is None:
        return default_template()
    return path.read_text()