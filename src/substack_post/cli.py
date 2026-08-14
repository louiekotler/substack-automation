"""substack-post: publish a folder of photos to Substack as one post."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

from . import notify, publish, render
from .config import ConfigError, load_credentials, resolve_notify_contacts_path
from .notify import NotifyError
from .photos import PhotoError, collect
from .render import DEFAULT_CAPTION_FORMAT
from .publish import PublishError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="substack-post",
        description="Publish a folder of photos to Substack as a single post, "
        "in timestamp order, captioned by the date each photo was taken.",
    )
    # Optional at the parser level, then checked by hand, so --check can run
    # without naming photos or a title. Everything else still errors out with
    # argparse's own wording.
    parser.add_argument(
        "photo_dir",
        type=Path,
        nargs="?",
        default=None,
        help="Folder containing the photos to post.",
    )
    parser.add_argument("--title", default=None, help="Post title.")
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
    parser.add_argument(
        "--notify",
        action="store_true",
        help="After publishing, text everyone in the contacts file via macOS "
        "Messages that the post is live. Requires --publish.",
    )
    parser.add_argument(
        "--notify-list",
        type=Path,
        default=None,
        help="Path to the notify contacts JSON file (default: "
        "NOTIFY_CONTACTS_FILE from .env, or notify_contacts.json).",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Sign in, report the publication, and exit without posting.",
    )
    parser.add_argument(
        "--format",
        choices=("text", "kv", "json"),
        default="text",
        help="Result format. 'kv' emits key=value lines for callers without a "
        "JSON parser (default: %(default)s).",
    )
    return parser


def _emit(payload: dict, fmt: str) -> None:
    """Print the result of a run in machine-readable form.

    The kv form exists for callers without a JSON parser — notably the
    Lightroom plug-in, where avoiding one keeps a third-party Lua file out of
    the bundle.
    """
    if fmt == "json":
        print(json.dumps(payload, indent=2))
        return
    for key, value in payload.items():
        if isinstance(value, bool):
            value = 1 if value else 0
        print(f"{key}={'' if value is None else value}")


def _result(status: str, *, url: str = "", draft_id=None, slug: str = "",
            photos: int = 0, emailed: bool = False,
            notified_ok: int = 0, notified_failed: int = 0) -> dict:
    """One shape for every outcome, so a caller can parse without branching."""
    return {
        "status": status,
        "url": url,
        "draft_id": draft_id,
        "slug": slug,
        "photos": photos,
        "emailed": emailed,
        "notified_ok": notified_ok,
        "notified_failed": notified_failed,
    }


def _check(env_file: Path | None, fmt: str) -> int:
    """Verify the configuration by signing in, without posting anything."""
    try:
        credentials = load_credentials(env_file)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    try:
        api = publish.login(
            credentials.email, credentials.password, credentials.publication_url
        )
        identity = publish.whoami(api)
    except PublishError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    payload = {
        "ok": True,
        "publication": credentials.publication_url,
        "user": identity.get("user") or "",
    }
    if fmt == "text":
        who = payload["user"] or credentials.email
        print(f"Signed in as {who}")
        print(f"Publication: {credentials.publication_url}")
    else:
        _emit(payload, fmt)
    return 0


def _confirm_publish(
    *,
    title: str,
    photo_count: int,
    publication_url: str,
    send: bool,
    notify_messages: list[tuple[notify.Contact, str]] | None = None,
) -> bool:
    print()
    print("About to publish:")
    print(f"  Title:       {title}")
    print(f"  Photos:      {photo_count}")
    print(f"  Publication: {publication_url}")
    print(f"  Email:       {'yes, all subscribers' if send else 'no (web only)'}")
    if notify_messages is not None:
        names = ", ".join(contact.name for contact, _ in notify_messages)
        print(f"  Notify:      {len(notify_messages)} people via Messages ({names})")
        example_contact, example_message = notify_messages[0]
        indented = example_message.replace("\n", "\n               ")
        print(f"               each gets a personalized message, e.g. for {example_contact.name}:")
        print(f'               "{indented}"')
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
    machine = args.format != "text"

    if args.check:
        return _check(args.env_file, args.format)

    if args.photo_dir is None:
        parser.error("the following arguments are required: photo_dir")
    if args.title is None:
        parser.error("the following arguments are required: --title")
    if args.notify and not args.publish:
        parser.error("--notify requires --publish.")

    try:
        photos = collect(args.photo_dir)
    except PhotoError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    signoff = args.signoff if args.signoff is not None else render.default_signoff()
    try:
        template = render.load_template(args.template)
    except OSError as exc:
        print(f"error: could not read the template {args.template}: {exc}",
              file=sys.stderr)
        return 2

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
        # The preview is for a person; a machine-readable run keeps stdout clean
        # for the result and puts the markdown on stderr with the other notes.
        stream = sys.stderr if machine else sys.stdout
        print(file=stream)
        print("--- dry run: rendered markdown ---", file=stream)
        print(rendered, file=stream)
        if machine:
            _emit(_result("dry_run", photos=len(photos)), args.format)
        return 0

    try:
        credentials = load_credentials(args.env_file)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    # Loaded before touching Substack: a broken contacts file should abort
    # before anything irreversible happens, not after publishing.
    notify_contacts = None
    if args.notify:
        try:
            notify.check_platform()
            contacts_path = resolve_notify_contacts_path(args.notify_list)
            notify_contacts = notify.load_contacts(contacts_path)
        except NotifyError as exc:
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

            notify_messages = None
            if args.notify:
                link = publish.post_url(credentials.publication_url, slug)
                notify_template = render.default_notify_message_template()
                notify_messages = [
                    (
                        contact,
                        notify.render_message(
                            notify_template, title=args.title, link=link, name=contact.name
                        ),
                    )
                    for contact in notify_contacts
                ]

            if not args.yes and not _confirm_publish(
                title=args.title,
                photo_count=len(photos),
                publication_url=credentials.publication_url,
                send=send,
                notify_messages=notify_messages,
            ):
                url = publish.draft_url(credentials.publication_url, draft_id)
                print("Aborted. Draft was created but not published:", file=sys.stderr)
                if machine:
                    _emit(
                        _result("aborted", url=url, draft_id=draft_id,
                                slug=slug or "", photos=len(photos)),
                        args.format,
                    )
                else:
                    print(url)
                return 1

            print("Publishing...", file=sys.stderr)
            publish.publish_draft(api, draft_id, send=send)
            url = publish.post_url(credentials.publication_url, slug)
            if not machine:
                print("Published:")
                print(url)

            notified_ok = notified_failed = 0
            if args.notify:
                print("Notifying...", file=sys.stderr)
                # Fail-soft: a bad number shouldn't hide that the post itself
                # published successfully, so failures are reported, not fatal.
                for contact, error in notify.notify_all(notify_messages):
                    if error is None:
                        notified_ok += 1
                        line = f"  ok {contact.name}"
                    else:
                        notified_failed += 1
                        line = f"  FAILED {contact.name}: {error}"
                    # Per-contact lines are for a person; the counts carry the
                    # same news to a machine-readable caller.
                    print(line, file=sys.stderr if machine else sys.stdout)

            if machine:
                _emit(
                    _result("published", url=url, draft_id=draft_id, slug=slug or "",
                            photos=len(photos), emailed=send,
                            notified_ok=notified_ok, notified_failed=notified_failed),
                    args.format,
                )
        else:
            url = publish.draft_url(credentials.publication_url, draft_id)
            if machine:
                _emit(
                    _result("draft", url=url, draft_id=draft_id, slug=slug or "",
                            photos=len(photos)),
                    args.format,
                )
            else:
                print("Draft created:")
                print(url)

    except PublishError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    return 0


def main() -> None:
    try:
        raise SystemExit(run())
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        raise SystemExit(130)


if __name__ == "__main__":
    main()
