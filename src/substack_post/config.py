"""Load Substack credentials from the environment / .env file."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv
import os


class ConfigError(Exception):
    """Raised when required credentials are missing."""


@dataclass(frozen=True)
class Credentials:
    email: str
    password: str
    publication_url: str


# Each key checks our own SUBSTACK_-prefixed name first, falling back to the
# python-substack library's own unprefixed name so a `.env` written for the
# library directly (per its README) also works here.
_VAR_ALIASES = {
    "email": ("SUBSTACK_EMAIL", "EMAIL"),
    "password": ("SUBSTACK_PASSWORD", "PASSWORD"),
    "publication_url": ("SUBSTACK_PUBLICATION_URL", "PUBLICATION_URL"),
}


def load_credentials(env_file: Path | None = None) -> Credentials:
    # A named file that isn't there is a typo, not an invitation to fall back
    # to the environment and then complain about missing settings.
    if env_file is not None and not env_file.expanduser().is_file():
        raise ConfigError(f"No such env file: {env_file}")
    load_dotenv(dotenv_path=env_file)

    values = {}
    missing = []
    for field, names in _VAR_ALIASES.items():
        value = next((os.environ[n] for n in names if os.environ.get(n)), None)
        if value is None:
            missing.append(" or ".join(names))
        values[field] = value

    if missing:
        raise ConfigError(
            "Missing required setting(s): "
            + ", ".join(missing)
            + ". Copy .env.example to .env and fill it in — see the README's "
            "Setup section."
        )

    return Credentials(**values)


DEFAULT_NOTIFY_CONTACTS_FILENAME = "notify_contacts.json"


def resolve_notify_contacts_path(
    cli_path: Path | None = None, env_file: Path | None = None
) -> Path:
    """Where to find the notify contacts file: --notify-list, then
    NOTIFY_CONTACTS_FILE (from .env), then the default filename beside a named
    --env-file, then in the current directory. Call after load_credentials()
    so .env is loaded.

    The step before last is for callers launched by something else: a GUI gives
    its subprocess an arbitrary working directory, so a bare filename resolves
    somewhere meaningless. Such a caller names the env file explicitly, and the
    contacts file conventionally sits beside it.
    """
    if cli_path is not None:
        return cli_path.expanduser()
    env_value = os.environ.get("NOTIFY_CONTACTS_FILE")
    if env_value:
        return Path(env_value).expanduser()
    if env_file is not None:
        beside = env_file.expanduser().parent / DEFAULT_NOTIFY_CONTACTS_FILENAME
        if beside.is_file():
            return beside
    return Path(DEFAULT_NOTIFY_CONTACTS_FILENAME)
