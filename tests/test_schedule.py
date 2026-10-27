"""Scheduling a post, and the job that texts contacts when it goes live.

Nothing here contacts Substack, sends a text, or loads anything into launchd:
the API, Messages and launchctl are all stubbed, and HOME points at a
temporary folder so no file lands in the real ~/Library.
"""

import json
import plistlib
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from PIL import Image

from substack_post import cli, launchd, notify, publish, scheduled
from substack_post.cli import parse_when, run
from substack_post.config import Credentials
from substack_post.publish import PostState, PublishError

NOW = datetime(2026, 10, 27, 5, 0, tzinfo=timezone.utc)
RELEASE = datetime(2026, 10, 27, 15, 0, tzinfo=timezone.utc)
DUE = RELEASE + scheduled.RELEASE_GRACE


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    return tmp_path / "home"


@pytest.fixture
def launchctl(monkeypatch):
    """Record launchctl calls instead of making them."""
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(launchd.subprocess, "run", fake_run)
    return calls


# --- when ------------------------------------------------------------------

def test_a_naive_time_is_local_with_that_dates_offset() -> None:
    now = datetime(2026, 10, 27, 12, 0).astimezone()
    when = parse_when("2026-12-01T08:00", now=now)
    # December's offset, not October's: the DST change in between counts.
    assert when.utcoffset() == datetime(2026, 12, 1, 8, 0).astimezone().utcoffset()
    assert (when.hour, when.minute) == (8, 0)


def test_an_explicit_offset_is_kept() -> None:
    when = parse_when("2026-12-01T08:00+00:00", now=NOW)
    assert when == datetime(2026, 12, 1, 8, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize("value", ["2026-10-27T04:00+00:00", "2026-10-27T05:01+00:00"])
def test_a_time_not_far_enough_ahead_is_refused(value) -> None:
    with pytest.raises(ValueError, match="too soon"):
        parse_when(value, now=NOW)


def test_an_unreadable_time_says_how_to_write_one() -> None:
    with pytest.raises(ValueError, match="2026-10-28T08:00"):
        parse_when("tomorrow morning", now=NOW)


# --- the CLI ---------------------------------------------------------------

def _jpeg(path) -> None:
    Image.new("RGB", (8, 8), "white").save(path, format="JPEG")


@pytest.fixture
def photo_dir(tmp_path):
    directory = tmp_path / "photos"
    directory.mkdir()
    _jpeg(directory / "a.jpg")
    return directory


@pytest.fixture
def env_file(tmp_path):
    path = tmp_path / "config" / ".env"
    path.parent.mkdir()
    path.write_text("SUBSTACK_EMAIL=e@x.test\n")
    (path.parent / "notify_contacts.json").write_text(
        json.dumps([{"name": "Mom", "phone": "+15551234567"}])
    )
    return path


@pytest.fixture
def fake_run(monkeypatch):
    """Stub Substack and the job, and keep a log of what happened, in order."""
    log = []
    state = {"schedule_fails": False, "check": "ok"}

    monkeypatch.setattr(cli, "load_credentials", lambda env_file=None: Credentials(
        email="e@x.test", password="p", publication_url="https://x.test"))
    monkeypatch.setattr(publish, "login", lambda *a: object())
    monkeypatch.setattr(publish, "upload_photos",
                        lambda api, photos, workdir, on_photo=None:
                        [(p, "https://cdn.test/" + p.name) for p in photos])
    monkeypatch.setattr(publish, "create_draft", lambda *a: {"id": 42})
    monkeypatch.setattr(publish, "set_send_email",
                        lambda api, draft_id, send: log.append(("email", send)))

    def schedule(api, draft_id, when):
        log.append(("schedule", when))
        if state["schedule_fails"]:
            raise PublishError("Failed to schedule draft 42: nope")
        return when

    monkeypatch.setattr(publish, "schedule", schedule)
    monkeypatch.setattr(publish, "post_state",
                        lambda api, draft_id: PostState("scheduled", "T", "olympic"))
    monkeypatch.setattr(publish, "publish_draft",
                        lambda *a, **k: pytest.fail("a scheduled run must not publish"))
    monkeypatch.setattr(notify, "check_platform", lambda: None)

    job = scheduled.Job(42, "", "", "", "", "label")

    def arm(draft_id, release, env, contacts, title):
        assert title == "Olympic"
        log.append(("arm", release, Path(contacts).name))
        return job

    monkeypatch.setattr(scheduled, "arm", arm)
    monkeypatch.setattr(scheduled, "disarm", lambda j: log.append(("disarm",)))
    monkeypatch.setattr(scheduled, "messages_check", lambda j: state["check"])
    return log, state


def _argv(photo_dir, env_file, *extra):
    return [str(photo_dir), "--title", "Olympic", "--schedule", "2099-01-01T08:00",
            "--env-file", str(env_file), "--yes", "--format", "kv", *extra]


def _kv(out):
    return dict(line.split("=", 1) for line in out.splitlines())


def test_schedule_sets_email_then_arms_texts_then_schedules(
    photo_dir, env_file, fake_run, capsys
) -> None:
    log, _ = fake_run
    assert run(_argv(photo_dir, env_file, "--notify")) == 0
    assert [entry[0] for entry in log] == ["email", "arm", "schedule"]
    assert log[0] == ("email", True)
    # The contacts file is found beside the env file, as for --publish.
    assert log[1][2] == "notify_contacts.json"
    result = _kv(capsys.readouterr().out)
    assert result["status"] == "scheduled"
    assert result["url"] == "https://x.test/publish/post/42"
    assert result["slug"] == "olympic"
    assert result["emailed"] == "1"
    assert result["scheduled_for"].startswith("2099-01-01T08:00:00")
    assert result["messages_check"] == "ok"


def test_schedule_without_email(photo_dir, env_file, fake_run, capsys) -> None:
    log, _ = fake_run
    assert run(_argv(photo_dir, env_file, "--no-email")) == 0
    assert log[0] == ("email", False)
    assert "arm" not in [entry[0] for entry in log]
    result = _kv(capsys.readouterr().out)
    assert result["emailed"] == "0"
    assert result["messages_check"] == ""


def test_a_failed_schedule_takes_the_texts_back(
    photo_dir, env_file, fake_run, capsys
) -> None:
    log, state = fake_run
    state["schedule_fails"] = True
    assert run(_argv(photo_dir, env_file, "--notify")) == 1
    assert [entry[0] for entry in log] == ["email", "arm", "schedule", "disarm"]
    assert "error: Failed to schedule" in capsys.readouterr().err


def test_a_time_that_comes_too_close_while_uploading_is_not_scheduled(
    photo_dir, env_file, fake_run, capsys, monkeypatch
) -> None:
    log, _ = fake_run
    # Uploading took until 20 seconds before the chosen time.
    soon = datetime.now().astimezone() + timedelta(minutes=10)
    monkeypatch.setattr(cli, "parse_when", lambda value: soon)
    real_upload = publish.upload_photos

    def slow_upload(*args, **kwargs):
        monkeypatch.setattr(cli, "MIN_LEAD_AT_SCHEDULING", timedelta(minutes=11))
        return real_upload(*args, **kwargs)

    monkeypatch.setattr(publish, "upload_photos", slow_upload)
    assert run(_argv(photo_dir, env_file, "--notify")) == 1
    assert log == []  # no email change, no texts arranged, nothing scheduled
    err = capsys.readouterr().err
    assert "Left as a draft" in err and "/publish/post/42" in err


def test_texts_need_credentials_in_a_file(photo_dir, tmp_path, fake_run, capsys,
                                          monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)  # no .env here
    contacts = tmp_path / "c.json"
    contacts.write_text(json.dumps([{"name": "Mom", "phone": "+1"}]))
    code = run([str(photo_dir), "--title", "T", "--schedule", "2099-01-01T08:00",
                "--notify", "--notify-list", str(contacts), "--yes"])
    assert code == 2
    assert "--env-file" in capsys.readouterr().err


def test_schedule_and_publish_are_one_or_the_other(photo_dir, capsys) -> None:
    with pytest.raises(SystemExit):
        run([str(photo_dir), "--title", "T", "--publish", "--schedule", "2099-01-01T08:00"])
    assert "not allowed with" in capsys.readouterr().err


def test_schedule_asks_first_and_refuses_without_a_terminal(
    photo_dir, env_file, fake_run, capsys
) -> None:
    log, _ = fake_run
    argv = [a for a in _argv(photo_dir, env_file) if a != "--yes"]
    assert run(argv) == 1
    assert log == []  # nothing set, nothing scheduled
    captured = capsys.readouterr()
    assert "About to schedule:" in captured.out
    assert "Goes live:" in captured.out
    assert _kv(captured.out.split("\n\n")[-1])["status"] == "aborted"


def test_a_past_time_fails_before_anything_is_uploaded(
    photo_dir, env_file, fake_run, capsys
) -> None:
    log, _ = fake_run
    argv = _argv(photo_dir, env_file)
    argv[argv.index("2099-01-01T08:00")] = "2001-01-01T08:00"
    assert run(argv) == 2
    assert log == []
    assert "too soon" in capsys.readouterr().err


# --- publish helpers -------------------------------------------------------

class _Response:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _Api:
    publication_url = "https://x.test/api/v1"

    def __init__(self, draft=None, pages=(), saved_email=None, draft_error=None):
        self.draft = draft or {}
        self.pages = list(pages)
        self.saved_email = saved_email
        self.draft_error = draft_error
        self.calls = []
        api = self

        class Session:
            def get(self, url, params=None):
                api.calls.append(("list", params["offset"]))
                return _Response(api.pages.pop(0))

        self._session = Session()

    def put_draft(self, draft_id, **kwargs):
        self.calls.append(("put", kwargs))

    def get_draft(self, draft_id):
        if self.draft_error:
            raise self.draft_error
        if self.saved_email is not None:
            return {"should_send_email": self.saved_email}
        return self.draft


def test_the_email_option_is_read_back() -> None:
    publish.set_send_email(_Api(saved_email=False), 42, False)
    with pytest.raises(PublishError, match="did not keep the email option"):
        publish.set_send_email(_Api(saved_email=True), 42, False)


def test_scheduled_for_reads_every_page() -> None:
    first = {"posts": [{"id": i, "trigger_at": "2026-10-28T05:12:00.000Z"}
                       for i in range(25)], "total": 26}
    second = {"posts": [{"id": 42, "trigger_at": "2026-10-28T05:12:00.000Z"}], "total": 26}
    api = _Api(pages=[first, second])
    assert publish.scheduled_for(api, 42) == datetime(2026, 10, 28, 5, 12, tzinfo=timezone.utc)
    assert api.calls == [("list", 0), ("list", 25)]


def test_scheduled_for_is_none_when_not_listed() -> None:
    assert publish.scheduled_for(_Api(pages=[{"posts": [], "total": 0}]), 42) is None


def test_post_state_of_a_live_post() -> None:
    api = _Api(draft={"is_published": True, "post_date": "2026-10-17T02:50:36.493Z",
                      "slug": "s", "title": "T"})
    post = publish.post_state(api, 42)
    assert post.state == "published"
    assert post.published_at == datetime(2026, 10, 17, 2, 50, 36, 493000, tzinfo=timezone.utc)


def test_post_state_of_a_scheduled_and_an_unscheduled_draft() -> None:
    listed = {"posts": [{"id": 42, "trigger_at": "2026-10-28T05:12:00.000Z"}], "total": 1}
    assert publish.post_state(_Api(draft={"slug": "s"}, pages=[listed]), 42).state == "scheduled"
    unlisted = {"posts": [], "total": 0}
    assert publish.post_state(_Api(draft={"slug": "s"}, pages=[unlisted]), 42).state == "draft"


def test_a_deleted_draft_is_gone_not_an_error() -> None:
    error = Exception("APIError(code=404): Draft not found")
    assert publish.post_state(_Api(draft_error=error), 42).state == "gone"
    with pytest.raises(PublishError):
        publish.post_state(_Api(draft_error=Exception("APIError(code=500)")), 42)


# --- launchd ---------------------------------------------------------------

def test_install_writes_a_calendar_job_that_also_runs_at_load(home, launchctl) -> None:
    at = datetime(2026, 10, 27, 8, 1).astimezone()
    launchd.install("x.label", ["/bin/echo", "hi"], at, home / "logs" / "x.log")
    plist = plistlib.loads(launchd.plist_path("x.label").read_bytes())
    assert plist["StartCalendarInterval"] == {"Month": 10, "Day": 27, "Hour": 8, "Minute": 1}
    assert plist["RunAtLoad"] is True
    assert plist["ProgramArguments"] == ["/bin/echo", "hi"]
    assert launchctl[0][:2] == ["launchctl", "bootstrap"]


def test_a_job_that_will_not_load_leaves_no_plist(home, monkeypatch) -> None:
    monkeypatch.setattr(launchd.subprocess, "run", lambda argv, **k:
                        subprocess.CompletedProcess(argv, 5, "", "Bootstrap failed"))
    with pytest.raises(launchd.LaunchdError, match="Bootstrap failed"):
        launchd.install("x.label", ["/bin/true"], NOW, home / "x.log")
    assert not launchd.plist_path("x.label").exists()


# --- the texting job -------------------------------------------------------

@pytest.fixture
def job_world(home, tmp_path, launchctl, monkeypatch):
    """An armed job, with Substack and Messages stubbed. Returns a dict to
    steer the stubs and read back what happened."""
    world = {"post": PostState("published", "Olympic", "olympic",
                               published_at=RELEASE),
             "error": None, "sent": [], "alerts": [], "checks": 0}
    env = tmp_path / ".env"
    env.write_text("")
    contacts = tmp_path / "contacts.json"
    contacts.write_text(json.dumps([{"name": "Mom", "phone": "+15551234567"},
                                    {"name": "Dad", "phone": "+15557654321"}]))

    monkeypatch.setattr(scheduled, "load_credentials", lambda path: Credentials(
        email="e", password="p", publication_url="https://x.test"))
    monkeypatch.setattr(publish, "login", lambda *a: object())

    def post_state(api, draft_id):
        if world["error"]:
            raise PublishError(world["error"])
        return world["post"]

    monkeypatch.setattr(publish, "post_state", post_state)

    def notify_all(messages, on_send=None):
        world["sent"].extend(messages)
        return [(contact, None) for contact, _ in messages]

    monkeypatch.setattr(notify, "notify_all", notify_all)
    monkeypatch.setattr(notify, "alert_owner",
                        lambda title, message: world["alerts"].append(message))

    def check_messages():
        world["checks"] += 1

    monkeypatch.setattr(notify, "check_messages", check_messages)
    world["job"] = scheduled.arm(42, RELEASE, env, contacts, "Olympic")
    world["launchctl"] = launchctl
    return world


def _agents():
    return sorted(p.name for p in launchd.agents_dir().glob("*.plist"))


def test_arming_due_a_little_after_release(job_world) -> None:
    job = job_world["job"]
    assert datetime.fromisoformat(job.due) == RELEASE + timedelta(minutes=5)
    assert _agents() == [f"{job.label}.plist"]
    assert job.label == "substack-post.notify.42.202610271505"


def test_an_early_start_only_checks_messages_once(job_world) -> None:
    job = job_world["job"]
    assert scheduled.run(job.path, now=NOW) == 0
    assert scheduled.run(job.path, now=NOW) == 0
    assert job_world["checks"] == 1
    assert scheduled.messages_check(job) == "ok"
    assert job_world["sent"] == []
    assert job.path.exists()


def test_a_live_post_texts_everyone_and_removes_the_job(job_world) -> None:
    job = job_world["job"]
    scheduled.run(job.path, now=DUE)
    assert [c.name for c, _ in job_world["sent"]] == ["Mom", "Dad"]
    assert "https://x.test/p/olympic" in job_world["sent"][0][1]
    assert job_world["alerts"] == ['Text notifications for "Olympic" sent to 2 of 2 contacts.']
    assert _agents() == []
    assert not job.path.exists()
    assert job_world["launchctl"][-1][:2] == ["launchctl", "bootout"]


def test_texts_still_go_a_few_hours_late(job_world) -> None:
    job = job_world["job"]
    scheduled.run(job.path, now=RELEASE + timedelta(hours=23))
    assert len(job_world["sent"]) == 2


def test_no_texts_more_than_a_day_after_going_live(job_world) -> None:
    job = job_world["job"]
    scheduled.run(job.path, now=RELEASE + timedelta(hours=25))
    assert job_world["sent"] == []
    assert job_world["alerts"] == [
        'Scheduled text notifications for "Olympic" are cancelled. '
        "It went live 25 hours ago."]
    assert _agents() == []


def test_the_day_counts_from_when_it_went_live_not_when_it_was_due(job_world) -> None:
    # Released 3 hours late; 22 hours after that is still within the day.
    job_world["post"] = PostState("published", "Olympic", "olympic",
                                  published_at=RELEASE + timedelta(hours=3))
    scheduled.run(job_world["job"].path, now=RELEASE + timedelta(hours=25))
    assert len(job_world["sent"]) == 2


def test_a_post_moved_later_waits_for_its_new_time(job_world) -> None:
    job = job_world["job"]
    old_label = job.label
    later = RELEASE + timedelta(hours=2)
    job_world["post"] = PostState("scheduled", "Olympic", "olympic", scheduled_for=later)
    scheduled.run(job.path, now=DUE)
    moved = scheduled.Job.load(job.path)
    assert datetime.fromisoformat(moved.due) == later + scheduled.RELEASE_GRACE
    assert datetime.fromisoformat(moved.release) == later
    assert _agents() == [f"{moved.label}.plist"]
    assert ["launchctl", "bootout", f"gui/{__import__('os').getuid()}/{old_label}"] \
        in job_world["launchctl"]
    assert job_world["sent"] == [] and job_world["alerts"] == []


def test_a_post_not_yet_released_is_looked_at_again_soon(job_world) -> None:
    job = job_world["job"]
    job_world["post"] = PostState("scheduled", "Olympic", "olympic", scheduled_for=RELEASE)
    now = DUE
    scheduled.run(job.path, now=now)
    assert datetime.fromisoformat(scheduled.Job.load(job.path).due) == now + timedelta(minutes=2)
    assert job_world["sent"] == []


def test_a_post_that_never_releases_is_given_up_on(job_world) -> None:
    job_world["post"] = PostState("scheduled", "Olympic", "olympic", scheduled_for=RELEASE)
    scheduled.run(job_world["job"].path, now=RELEASE + timedelta(minutes=45))
    assert "45 minutes late" in job_world["alerts"][0]
    assert _agents() == []


@pytest.mark.parametrize("state", ["draft", "gone"])
def test_an_unscheduled_or_deleted_post_sends_nothing(job_world, state) -> None:
    # A deleted post has no title to report, so the one it was scheduled with
    # names it.
    job_world["post"] = PostState(state, "Olympic" if state == "draft" else "")
    scheduled.run(job_world["job"].path, now=DUE)
    assert job_world["sent"] == []
    assert job_world["alerts"] == [
        'Scheduled text notifications for "Olympic" are cancelled.']
    assert _agents() == []


def test_substack_unreachable_tries_again_later(job_world) -> None:
    job = job_world["job"]
    job_world["error"] = "network down"
    now = DUE
    scheduled.run(job.path, now=now)
    assert datetime.fromisoformat(scheduled.Job.load(job.path).due) == now + timedelta(minutes=5)
    assert job_world["alerts"] == []


def test_substack_unreachable_for_a_day_gives_up(job_world) -> None:
    job_world["error"] = "network down"
    scheduled.run(job_world["job"].path, now=RELEASE + timedelta(hours=25))
    assert job_world["alerts"][0].startswith(
        'Scheduled text notifications for "Olympic" are cancelled. Could not reach')
    assert _agents() == []


def test_a_second_copy_running_at_once_does_nothing(job_world) -> None:
    import fcntl

    job = job_world["job"]
    with open(job.lock_path, "w") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        scheduled.run(job.path, now=DUE)
    assert job_world["sent"] == []
    assert job.path.exists()


def test_disarm_removes_everything(job_world) -> None:
    job = job_world["job"]
    scheduled.disarm(job)
    assert _agents() == [] and not job.path.exists()
