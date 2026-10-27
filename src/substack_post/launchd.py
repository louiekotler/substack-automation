"""One-shot jobs for launchd, the macOS service manager.

launchd is the only scheduler a Mac reliably has: it arms a timer rather than
polling, runs a missed job on wake, and needs nothing left running. It has no
notion of "once", though. A calendar job with no year repeats every year, so
every job made here is expected to remove itself when it has done its work.

Jobs also run at load (`RunAtLoad`): when they are made, and at each login.
That is what makes a job missed while the Mac was off run at all, since
launchd does not catch up on a calendar time that passed while it wasn't
running. A job must therefore tolerate being started early, and decide from
the clock whether it is due.
"""

from __future__ import annotations

import os
import plistlib
import subprocess
from datetime import datetime
from pathlib import Path


class LaunchdError(Exception):
    """Raised when a job cannot be loaded."""


def agents_dir() -> Path:
    return Path.home() / "Library" / "LaunchAgents"


def plist_path(label: str) -> Path:
    return agents_dir() / f"{label}.plist"


def _domain() -> str:
    return f"gui/{os.getuid()}"


def install(label: str, arguments: list[str], at: datetime, log: Path) -> None:
    """Load a job that runs `arguments` at `at` (local time, to the minute)."""
    local = at.astimezone() if at.tzinfo else at
    log.parent.mkdir(parents=True, exist_ok=True)
    agents_dir().mkdir(parents=True, exist_ok=True)
    path = plist_path(label)
    with path.open("wb") as handle:
        plistlib.dump(
            {
                "Label": label,
                "ProgramArguments": arguments,
                "StartCalendarInterval": {
                    "Month": local.month,
                    "Day": local.day,
                    "Hour": local.hour,
                    "Minute": local.minute,
                },
                "RunAtLoad": True,
                "StandardOutPath": str(log),
                "StandardErrorPath": str(log),
            },
            handle,
        )
    result = subprocess.run(
        ["launchctl", "bootstrap", _domain(), str(path)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        path.unlink(missing_ok=True)
        raise LaunchdError(
            f"Could not load {label}: {(result.stderr or result.stdout).strip()}"
        )


def remove(label: str) -> None:
    """Unload a job and delete its plist.

    When the caller *is* that job, launchd stops it as part of unloading, so
    this must be the last thing it does.
    """
    plist_path(label).unlink(missing_ok=True)
    subprocess.run(
        ["launchctl", "bootout", f"{_domain()}/{label}"], capture_output=True
    )
