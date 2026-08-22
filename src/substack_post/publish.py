"""Thin wrapper around python-substack for uploading photos and publishing."""

from __future__ import annotations

import re
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


def published_slug(api: Api, draft_id: int, published: dict) -> str:
    """The live post's slug, which exists only once the post is published.

    Creating a draft returns a null slug — Substack derives one from the title
    at publish time — so it has to come from the publish response, or from the
    post itself if that response leaves it out.
    """
    slug = (published or {}).get("slug")
    if slug:
        return slug
    try:
        slug = (api.get_draft(draft_id) or {}).get("slug")
    except Exception:  # noqa: BLE001 - the post is live either way; this is a lookup
        slug = None
    if not slug:
        raise PublishError(
            f"Post {draft_id} is published, but Substack did not report its "
            "slug, so the address to link to is unknown."
        )
    return slug


def expected_slug(title: str) -> str:
    """Approximate the slug Substack derives from a title.

    Good enough to show someone what a link will look like before the post
    exists; the real slug always comes back from Substack.
    """
    stripped = re.sub(r"['\u2019]", "", title.lower())
    return re.sub(r"[^a-z0-9]+", "-", stripped).strip("-")


def site_base_url(configured_publication_url: str) -> str:
    return configured_publication_url.rstrip("/")


def draft_url(base: str, draft_id: int) -> str:
    return f"{site_base_url(base)}/publish/post/{draft_id}"


def post_url(base: str, slug: str) -> str:
    # A missing slug must never be interpolated as it comes: that turns into a
    # link to /p/None that looks real enough to send to people.
    if not slug:
        raise PublishError("Cannot build a post address without a slug.")
    return f"{site_base_url(base)}/p/{slug}"


def preview_post_url(base: str, title: str) -> str:
    """The address the post will most likely get, for showing before it exists."""
    slug = expected_slug(title)
    return post_url(base, slug) if slug else site_base_url(base)
