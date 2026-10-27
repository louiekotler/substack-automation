"""Text contacts when a scheduled post goes live.

Substack publishes a scheduled post and emails subscribers by itself, with or
without this Mac. Texts can only come from this Mac, and Messages has no way
to schedule one from a script, so each scheduled post with --notify gets a
one-shot launchd job that wakes up just after the post's release time.

Substack stays the source of truth. The job sends nothing on the strength of
having been scheduled: it asks Substack where the post stands and acts on that.

- Live: text everyone, unless it went live more than LATE_TEXT_LIMIT ago (the
  Mac slept or was off through the release), in which case a "new post" text
  would arrive days late.
- Moved later in Substack's editor: wait for the new time.
- Due but not yet live: Substack is behind; look again shortly, for a while.
- Unscheduled or deleted: send nothing.

Every ending removes the job and shows the owner a notification, since there
is usually nobody watching when it runs.

Run by launchd as `python -m substack_post.scheduled <job file>`.
"""

from __future__ import annotations

import fcntl
import json
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import launchd, notify, publish
from .config import ConfigError, load_credentials

LATE_TEXT_LIMIT = timedelta(hours=24)
# Substack releases a post up to a few minutes after its time (3.5 minutes
# late on the first real one), so look once it is most likely live.
RELEASE_GRACE = timedelta(minutes=5)
# How long a post may stay unreleased past its time before giving up on it.
RELEASE_LAG_LIMIT = timedelta(minutes=30)
RETRY_AFTER_LAG = timedelta(minutes=2)
RETRY_AFTER_ERROR = timedelta(minutes=5)
# launchd fires on the minute; a start this close before it is that firing.
EARLY_SLACK = timedelta(seconds=30)

ALERT_TITLE = "Substack Post"
LABEL_PREFIX = "substack-post.notify"


def state_dir() -> Path:
    return Path.home() / "Library" / "Application Support" / "substack-post" / "scheduled"


def log_path(draft_id: int) -> Path:
    return Path.home() / "Library" / "Logs" / "substack-post" / f"notify-{draft_id}.log"


@dataclass
class Job:
    draft_id: int
    env_file: str
    contacts_file: str
    release: str  # when the post is due to go live, as last known (ISO, aware)
    due: str  # when this job is set to run (ISO, aware)
    label: str
    # As it was scheduled, for naming the post in a notification once Substack
    # can no longer say (it was deleted).
    title: str = ""

    @property
    def path(self) -> Path:
        return state_dir() / f"{self.draft_id}.json"

    @property
    def check_path(self) -> Path:
        return state_dir() / f"{self.draft_id}.messages-check"

    @property
    def lock_path(self) -> Path:
        return state_dir() / f"{self.draft_id}.lock"

    def forget(self) -> None:
        for path in (self.path, self.check_path, self.lock_path):
            path.unlink(missing_ok=True)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        partial = self.path.with_suffix(".tmp")
        partial.write_text(json.dumps(asdict(self), indent=2))
        partial.replace(self.path)

    @classmethod
    def load(cls, path: Path) -> "Job":
        return cls(**json.loads(path.read_text()))


def _minute_on_or_after(moment: datetime) -> datetime:
    """launchd counts in whole minutes, so round up to one."""
    rounded = moment.replace(second=0, microsecond=0)
    return rounded if rounded == moment else rounded + timedelta(minutes=1)


def _label(draft_id: int, due: datetime) -> str:
    # The time is part of the label so a job can load its successor before
    # unloading itself; two jobs never share one.
    return f"{LABEL_PREFIX}.{draft_id}.{due.astimezone(timezone.utc):%Y%m%d%H%M}"


def _load(job: Job, due: datetime) -> None:
    launchd.install(
        job.label,
        [sys.executable, "-m", "substack_post.scheduled", str(job.path)],
        due,
        log_path(job.draft_id),
    )


def arm(draft_id: int, release: datetime, env_file: Path, contacts_file: Path,
        title: str = "") -> Job:
    """Set up the texts for a post due to go live at `release`."""
    due = _minute_on_or_after(release + RELEASE_GRACE)
    job = Job(
        draft_id=draft_id,
        env_file=str(env_file.expanduser().resolve()),
        contacts_file=str(contacts_file.expanduser().resolve()),
        release=release.isoformat(),
        due=due.isoformat(),
        label=_label(draft_id, due),
        title=title,
    )
    job.check_path.unlink(missing_ok=True)
    job.save()
    try:
        _load(job, due)
    except launchd.LaunchdError:
        job.path.unlink(missing_ok=True)
        raise
    return job


def disarm(job: Job) -> None:
    """Undo `arm`, for a post that ended up not being scheduled."""
    job.forget()
    launchd.remove(job.label)


def messages_check(job: Job) -> str | None:
    """What the job found when it tried Messages: "ok", an error, or None yet.

    The job tries as soon as it is loaded, because macOS may ask permission
    the first time, and that should happen now, with someone at the screen,
    not at 8am to nobody.
    """
    try:
        return job.check_path.read_text().strip() or None
    except FileNotFoundError:
        return None


def _say(message: str) -> None:
    print(f"{datetime.now():%Y-%m-%d %H:%M:%S} {message}", flush=True)


def _finish(job: Job, message: str) -> None:
    """Tell the owner how it ended, and remove the job. Must come last: removing
    the job stops this process."""
    _say(message)
    notify.alert_owner(ALERT_TITLE, message)
    job.forget()
    launchd.remove(job.label)


def _rearm(job: Job, due: datetime, why: str) -> None:
    """Run again at `due`: load a successor, then remove this job, last."""
    _say(f"{why} Running again at {due.astimezone():%Y-%m-%d %H:%M}.")
    previous = job.label
    job.due = due.isoformat()
    job.label = _label(job.draft_id, due)
    job.save()
    _load(job, due)
    launchd.remove(previous)


def _check_messages(job: Job) -> None:
    try:
        notify.check_messages()
        result = "ok"
    except notify.NotifyError as exc:
        result = f"failed: {exc}"
    _say(f"Messages check: {result}")
    job.check_path.write_text(result + "\n")


def _send(job: Job, post: publish.PostState, publication_url: str) -> str:
    contacts = notify.load_contacts(Path(job.contacts_file))
    link = publish.post_url(publication_url, post.slug)
    results = notify.notify_all(notify.compose(contacts, post.title, link))
    failed = [contact.name for contact, error in results if error is not None]
    for contact, error in results:
        _say(f"  {'ok' if error is None else 'FAILED'} {contact.name}"
             + (f": {error}" if error else ""))
    sent = len(results) - len(failed)
    summary = (f'Text notifications for "{post.title}" sent to {sent} of '
               f"{len(results)} contacts.")
    if failed:
        summary += " Failed: " + ", ".join(failed) + "."
    return summary


def _cancelled(title: str) -> str:
    return f'Scheduled text notifications for "{title}" are cancelled.'


def _act(job: Job, now: datetime) -> None:
    release = datetime.fromisoformat(job.release)
    try:
        credentials = load_credentials(Path(job.env_file))
        api = publish.login(
            credentials.email, credentials.password, credentials.publication_url
        )
        post = publish.post_state(api, job.draft_id)
    except (ConfigError, publish.PublishError) as exc:
        if now - release < LATE_TEXT_LIMIT:
            _rearm(job, _minute_on_or_after(now + RETRY_AFTER_ERROR),
                   f"Could not reach Substack ({exc}).")
        else:
            _finish(job, f"{_cancelled(job.title)} Could not reach Substack: {exc}")
        return

    title = post.title or job.title

    if post.state == "published":
        live_since = post.published_at or release
        if now - live_since > LATE_TEXT_LIMIT:
            hours = int((now - live_since).total_seconds() // 3600)
            _finish(job, f"{_cancelled(title)} It went live {hours} hours ago.")
            return
        try:
            summary = _send(job, post, credentials.publication_url)
        except (notify.NotifyError, publish.PublishError) as exc:
            summary = f'Text notifications for "{title}" failed: {exc}'
        _finish(job, summary)
        return

    if post.state == "scheduled" and post.scheduled_for is not None:
        if post.scheduled_for > now:
            job.release = post.scheduled_for.isoformat()
            _rearm(job, _minute_on_or_after(post.scheduled_for + RELEASE_GRACE),
                   f'"{title}" was moved to {post.scheduled_for.astimezone():%Y-%m-%d %H:%M}.')
        elif now - post.scheduled_for < RELEASE_LAG_LIMIT:
            _rearm(job, _minute_on_or_after(now + RETRY_AFTER_LAG),
                   f'"{title}" is due but not live yet.')
        else:
            late = int((now - post.scheduled_for).total_seconds() // 60)
            _finish(job, f"{_cancelled(title)} It is {late} minutes late going live.")
        return

    # Unscheduled, or deleted: the owner called it off in Substack.
    _finish(job, _cancelled(title))


def run(job_file: Path, now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    if not job_file.exists():
        return 0
    # launchd can start a job twice at once (at login on the minute it is due,
    # or a successor loading while its predecessor finishes). Whoever holds the
    # lock does the work; the other has nothing to add. The lock goes when the
    # process does, however it ends.
    lock = open(job_file.with_suffix(".lock"), "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return 0
    if not job_file.exists():
        return 0
    job = Job.load(job_file)
    if now < datetime.fromisoformat(job.due) - EARLY_SLACK:
        # Started at load or at login, before its time: only make sure
        # Messages will answer when it is.
        if messages_check(job) is None:
            _check_messages(job)
        return 0
    _act(job, now)
    return 0


def main() -> None:
    if len(sys.argv) != 2:
        print("usage: python -m substack_post.scheduled <job file>", file=sys.stderr)
        raise SystemExit(2)
    raise SystemExit(run(Path(sys.argv[1])))


if __name__ == "__main__":
    main()
