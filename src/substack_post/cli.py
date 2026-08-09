"""substack-post: publish a folder of photos to Substack as one post."""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

from . import publish, render
from .config import ConfigError, load_credentials
from .photos import PhotoError, collect
from .render import DEFAULT_CAPTION_FORMAT
from .publish import PublishError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="substack-post",
        description="Publish a folder of photos to Substack as a single post, "
        "in timestamp order, captioned by the date each photo was taken.",
    )
    parser.add_argument("photo_dir", type=Path, help="Folder containing the photos to post.")
    parser.add_argument("--title", required=True, help="Post title.")
    parser.add_argument("--subtitle", default=None, help="Post subtitle (optional).")

    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--publish",
        action="store_true",
        help="Publish the post. Without this flag, a draft is created (the default).",
    )
    mode.add_argument(
        "--draft",
        action="store_true",
        help="Create a draft only. This is the default; the flag exists for clarity.",
    )

    parser.add_argument(
        "--no-email",
        action="store_true",
        help="With --publish, post to the site without emailing subscribers.",
    )
    parser.add_argument(
        "--signoff",
        default=None,
        help="Override the sign-off text at the end of the post.",
    )
    parser.add_argument(
        "--caption-format",
        default=DEFAULT_CAPTION_FORMAT,
        help="strftime format for photo captions, with {day} for a "
        f"leading-zero-free day number (default: %(default)r).",
    )
    parser.add_argument(
        "--template",
        type=Path,
        default=None,
        help="Path to a custom markdown template (default: the built-in template).",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=None,
        help="Path to a .env file (default: .env in the current directory).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Render the post and print it without uploading or contacting Substack.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip the confirmation prompt before publishing.",
    )
    return parser


def _confirm_publish(*, title: str, photo_count: int, publication_url: str, send: bool) -> bool:
    print()
    print("About to publish:")
    print(f"  Title:       {title}")
    print(f"  Photos:      {photo_count}")
    print(f"  Publication: {publication_url}")
    print(f"  Email:       {'yes, all subscribers' if send else 'no (web only)'}")
    print()
    if not sys.stdin.isatty():
        print(
            "error: refusing to publish without confirmation in a non-interactive "
            "session; pass --yes.",
            file=sys.stderr,
        )
        return False
    response = input("Publish? [y/N] ").strip().lower()
    return response == "y"


def run(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        photos = collect(args.photo_dir)
    except PhotoError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    signoff = args.signoff if args.signoff is not None else render.default_signoff()
    template = render.load_template(args.template)

    print(f"Found {len(photos)} photo(s) in {args.photo_dir}:", file=sys.stderr)
    for photo in photos:
        stamp = photo.taken_at.strftime("%Y-%m-%d %H:%M") if photo.taken_at else "no timestamp"
        print(f"  - {photo.name} ({stamp})", file=sys.stderr)

    if args.dry_run:
        # Preview with local paths as stand-ins for the eventual CDN URLs, so
        # nothing is uploaded and no Substack credentials are required.
        placeholder_urls = [(photo, str(photo.path)) for photo in photos]
        rendered = render.render_post(
            template, placeholder_urls, args.caption_format, signoff
        )
        print()
        print("--- dry run: rendered markdown ---")
        print(rendered)
        return 0

    try:
        credentials = load_credentials(args.env_file)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    try:
        print("Signing in to Substack...", file=sys.stderr)
        api = publish.login(
            credentials.email, credentials.password, credentials.publication_url
        )

        print("Uploading photos...", file=sys.stderr)
        with tempfile.TemporaryDirectory(prefix="substack-post-") as tmp:
            photo_urls = publish.upload_photos(api, photos, Path(tmp))

        rendered = render.render_post(
            template, photo_urls, args.caption_format, signoff
        )

        print("Creating draft...", file=sys.stderr)
        draft = publish.create_draft(api, args.title, args.subtitle, rendered)
        draft_id = draft.get("id")
        slug = draft.get("slug")

        if args.publish:
            send = not args.no_email
            if not args.yes and not _confirm_publish(
                title=args.title,
                photo_count=len(photos),
                publication_url=credentials.publication_url,
                send=send,
            ):
                print("Aborted. Draft was created but not published:", file=sys.stderr)
                print(publish.draft_url(credentials.publication_url, draft_id))
                return 1

            print("Publishing...", file=sys.stderr)
            publish.publish_draft(api, draft_id, send=send)
            print("Published:")
            print(publish.post_url(credentials.publication_url, slug))
        else:
            print("Draft created:")
            print(publish.draft_url(credentials.publication_url, draft_id))

    except PublishError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    return 0


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":
    main()
