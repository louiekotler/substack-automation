"""Thin wrapper around python-substack for uploading photos and publishing."""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
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
    api: Api, photos: list[Photo], workdir: Path, on_photo=None
) -> list[tuple[Photo, str]]:
    """Upload each photo and return it paired with its hosted CDN URL.

    `on_photo` is called with each photo before it goes up, for a caller
    tracking how far along the upload is.
    """
    results = []
    total = len(photos)
    for index, photo in enumerate(photos, start=1):
        if on_photo:
            on_photo(photo)
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


def set_send_email(api: Api, draft_id: int, send: bool) -> None:
    """Choose whether the post emails subscribers when it goes live.

    For a scheduled post this is the only place the choice is made: it is a
    setting on the draft, and scheduling carries nothing but the time. So it is
    read back rather than trusted, since a wrong value here emails everyone.
    """
    try:
        api.put_draft(draft_id, should_send_email=send)
        saved = (api.get_draft(draft_id) or {}).get("should_send_email")
    except Exception as exc:  # noqa: BLE001
        raise PublishError(f"Could not set the email option on draft {draft_id}: {exc}") from exc
    if saved is not send:
        raise PublishError(
            f"Substack did not keep the email option on draft {draft_id}. "
            "Not scheduled."
        )


def scheduled_for(api: Api, draft_id: int) -> datetime | None:
    """When a draft is due to go live, or None if it is not scheduled.

    A draft does not carry its own release time; only Substack's list of
    scheduled posts does.
    """
    offset = 0
    while True:
        try:
            page = api._session.get(
                f"{api.publication_url}/post_management/scheduled",
                params={"offset": offset, "limit": 25,
                        "order_by": "trigger_at", "order_direction": "asc"},
            )
            page.raise_for_status()
            listing = page.json()
        except Exception as exc:  # noqa: BLE001
            raise PublishError(f"Could not read the scheduled posts: {exc}") from exc
        posts = listing.get("posts") or []
        for post in posts:
            if post.get("id") == draft_id and post.get("trigger_at"):
                return _parse_time(post["trigger_at"])
        offset += len(posts)
        if not posts or offset >= (listing.get("total") or 0):
            return None


def schedule(api: Api, draft_id: int, when: datetime) -> datetime:
    """Schedule a draft to go live at `when`, and return the time Substack kept."""
    try:
        api.prepublish_draft(draft_id)
        api.schedule_draft(draft_id, when.astimezone(timezone.utc))
    except Exception as exc:  # noqa: BLE001
        raise PublishError(f"Failed to schedule draft {draft_id}: {exc}") from exc
    kept = scheduled_for(api, draft_id)
    if kept is None:
        raise PublishError(
            f"Substack accepted the schedule for draft {draft_id} but does not "
            "list it as scheduled."
        )
    return kept


@dataclass(frozen=True)
class PostState:
    """Where a post stands, for deciding what a scheduled job should do."""

    state: str  # "published", "scheduled", "draft" or "gone"
    title: str = ""
    slug: str = ""
    published_at: datetime | None = None
    scheduled_for: datetime | None = None


def post_state(api: Api, draft_id: int) -> PostState:
    try:
        draft = api.get_draft(draft_id) or {}
    except Exception as exc:  # noqa: BLE001
        # A deleted draft is a 404, which is an answer, not a failure.
        if "404" in str(exc):
            return PostState("gone")
        raise PublishError(f"Could not read post {draft_id}: {exc}") from exc

    title = draft.get("title") or draft.get("draft_title") or ""
    slug = draft.get("slug") or ""
    if draft.get("is_published"):
        published_at = _parse_time(draft["post_date"]) if draft.get("post_date") else None
        return PostState("published", title, slug, published_at=published_at)
    when = scheduled_for(api, draft_id)
    if when is not None:
        return PostState("scheduled", title, slug, scheduled_for=when)
    return PostState("draft", title, slug)


def _parse_time(value: str) -> datetime:
    """Substack's timestamps, e.g. 2026-10-28T05:12:00.000Z, as aware datetimes."""
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
