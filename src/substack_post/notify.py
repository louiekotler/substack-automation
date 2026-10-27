"""Notify a personal contact list via macOS Messages once a post is live.

Uses the Messages app's AppleScript dictionary (through `osascript`) rather
than any messaging API or third-party service -- this is a personal tool for
one Mac, and Messages is already signed in there.
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from . import render


class NotifyError(Exception):
    """Raised when contacts can't be loaded or a text can't be sent."""


@dataclass(frozen=True)
class Contact:
    """One notify target: either a person (`phone`) or a group (`chat_name`), never both."""

    name: str
    phone: str | None = None
    chat_name: str | None = None

    @property
    def is_group(self) -> bool:
        return self.chat_name is not None


def check_platform() -> None:
    """Fail fast, before touching Substack, if Messages automation is impossible here."""
    if sys.platform != "darwin":
        raise NotifyError(
            "--notify requires macOS (it sends messages through the Messages "
            "app via osascript)."
        )


def load_contacts(path: Path) -> list[Contact]:
    """Load a JSON list of contacts.

    Each entry is either a person -- {"name": ..., "phone": ...}, where
    "phone" is any iMessage handle (a phone number or an email address) -- or
    a group -- {"name": ..., "chat_name": ...}, where "chat_name" is the
    group conversation's display name exactly as it appears in Messages (set
    by renaming the conversation there; unnamed groups can't be matched).
    """
    if not path.exists():
        raise NotifyError(
            f"Contacts file not found: {path}. Copy notify_contacts.example.json "
            "and fill it in, or pass --notify-list."
        )
    try:
        raw = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise NotifyError(f"Contacts file {path} is not valid JSON: {exc}") from exc

    if not isinstance(raw, list):
        raise NotifyError(f"Contacts file {path} must contain a JSON list.")
    if not raw:
        raise NotifyError(f"Contacts file {path} is empty -- add at least one contact.")

    contacts = []
    for index, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise NotifyError(f"Contacts file {path}: entry {index} is not an object.")
        name = str(entry.get("name") or "").strip()
        phone = str(entry.get("phone") or "").strip()
        chat_name = str(entry.get("chat_name") or "").strip()
        if not name:
            raise NotifyError(f"Contacts file {path}: entry {index} is missing \"name\".")
        if phone and chat_name:
            raise NotifyError(
                f"Contacts file {path}: entry {index} ({name}) has both \"phone\" "
                'and "chat_name" -- a contact is one or the other, not both.'
            )
        if not phone and not chat_name:
            raise NotifyError(
                f"Contacts file {path}: entry {index} ({name}) needs a \"phone\" "
                '(for a person) or "chat_name" (for a group).'
            )
        contacts.append(
            Contact(name=name, phone=phone or None, chat_name=chat_name or None)
        )
    return contacts


def render_message(template: str, *, title: str, link: str, name: str) -> str:
    """Fill in the notify template for one contact. Literal replacement, not str.format."""
    return (
        template.replace("{{title}}", title)
        .replace("{{link}}", link)
        .replace("{{name}}", name)
    )


def compose(contacts: list[Contact], title: str, link: str) -> list[tuple[Contact, str]]:
    """Pair each contact with their personalized text about the post."""
    template = render.default_notify_message_template()
    return [
        (contact, render_message(template, title=title, link=link, name=contact.name))
        for contact in contacts
    ]


def _applescript_string(value: str) -> str:
    """Escape a value for embedding in a double-quoted AppleScript string literal."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _run_osascript(script: str) -> None:
    result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    if result.returncode != 0:
        raise NotifyError((result.stderr or "osascript failed").strip())


def send_imessage(phone: str, message: str) -> None:
    """Send `message` to one person's iMessage handle (phone number or email)."""
    script = (
        'tell application "Messages"\n'
        "  set targetService to 1st service whose service type = iMessage\n"
        f'  set targetBuddy to buddy "{_applescript_string(phone)}" of targetService\n'
        f'  send "{_applescript_string(message)}" to targetBuddy\n'
        "end tell"
    )
    _run_osascript(script)


def send_to_group(chat_name: str, message: str) -> None:
    """Send `message` to an existing group conversation, matched by its display name.

    The group must already exist and have that name set in Messages -- this
    doesn't create new group chats or add participants.
    """
    script = (
        'tell application "Messages"\n'
        f'  set targetChat to first chat whose name is "{_applescript_string(chat_name)}"\n'
        f'  send "{_applescript_string(message)}" to targetChat\n'
        "end tell"
    )
    _run_osascript(script)


def check_messages() -> None:
    """Prove Messages can be scripted from this process, without sending anything.

    macOS asks permission per program the first time it scripts Messages, and
    the answer belongs to whichever program asked. A check run from one place
    says nothing about another, so this is run by the program that will send.
    """
    _run_osascript('tell application "Messages" to count chats')


def alert_owner(title: str, message: str) -> None:
    """Show a macOS notification. Best effort: there may be no one to see it."""
    script = (
        f'display notification "{_applescript_string(message)}" '
        f'with title "{_applescript_string(title)}"'
    )
    subprocess.run(["osascript", "-e", script], capture_output=True)


def notify_all(
    messages: list[tuple[Contact, str]], on_send=None
) -> list[tuple[Contact, str | None]]:
    """Text each (contact, personalized message) pair independently.

    One bad number/chat name doesn't stop the rest. `on_send` is called with
    each contact before texting them, for a caller tracking progress.
    """
    results = []
    for contact, message in messages:
        if on_send:
            on_send(contact)
        try:
            if contact.is_group:
                send_to_group(contact.chat_name, message)
            else:
                send_imessage(contact.phone, message)
            results.append((contact, None))
        except NotifyError as exc:
            results.append((contact, str(exc)))
    return results
