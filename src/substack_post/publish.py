"""Thin wrapper around python-substack for uploading photos and publishing."""

from __future__ import annotations

import sys
from pathlib import Path

from substack import Api
from substack.post import Post

from .photos import Photo, prepare_for_upload


class PublishError(Exception):
    """Raised when talking to Substack fails."""


def login(email: str, password: str, publication_url: str) -> Api:
    try:
        return Api(email=email, password=password, publication_url=publication_url)
    except Exception as exc:  # noqa: BLE001 - library raises plain Exception/requests errors
        raise PublishError(f"Could not sign in to Substack: {exc}") from exc


def whoami(api: Api) -> dict:
    """Confirm the session actually works, and say who it belongs to.

    Constructing the Api signs in, but only a real request proves the resulting
    session is usable — which is the whole point of a configuration check.
    """
    try:
        user_id = api.get_user_id()
    except Exception as exc:  # noqa: BLE001
        raise PublishError(f"Signed in, but Substack rejected a request: {exc}") from exc

    name = ""
    try:
        name = (api.get_user_profile() or {}).get("name") or ""
    except Exception:  # noqa: BLE001 - cosmetic; the user id already proved the session
        pass
    return {"user_id": user_id, "user": name}


def upload_photos(
    api: Api, photos: list[Photo], workdir: Path
) -> list[tuple[Photo, str]]:
    """Upload each photo and return it paired with its hosted CDN URL."""
    results = []
    total = len(photos)
    for index, photo in enumerate(photos, start=1):
        print(f"  [{index}/{total}] uploading {photo.name}...", file=sys.stderr)
        upload_path = prepare_for_upload(photo, workdir)
        try:
            response = api.get_image(str(upload_path))
        except Exception as exc:  # noqa: BLE001
            raise PublishError(f"Failed to upload {photo.name}: {exc}") from exc
        url = response.get("url")
        if not url:
            raise PublishError(f"Substack did not return a URL for {photo.name}")
        results.append((photo, url))
    return results


def create_draft(api: Api, title: str, subtitle: str, markdown: str) -> dict:
    post = Post(title=title, subtitle=subtitle or "", user_id=api.get_user_id())
    post.from_markdown(markdown)
    try:
        return api.post_draft(post.get_draft())
    except Exception as exc:  # noqa: BLE001
        raise PublishError(f"Failed to create draft: {exc}") from exc


def publish_draft(api: Api, draft_id: int, send: bool) -> dict:
    try:
        api.prepublish_draft(draft_id)
        return api.publish_draft(draft_id, send=send, share_automatically=False)
    except Exception as exc:  # noqa: BLE001
        raise PublishError(f"Failed to publish draft {draft_id}: {exc}") from exc


def site_base_url(configured_publication_url: str) -> str:
    return configured_publication_url.rstrip("/")


def draft_url(base: str, draft_id: int) -> str:
    return f"{site_base_url(base)}/publish/post/{draft_id}"


def post_url(base: str, slug: str) -> str:
    return f"{site_base_url(base)}/p/{slug}"
