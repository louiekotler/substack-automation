"""CLI argument handling and machine-readable output.

Nothing here contacts Substack: the tests cover argument validation and the
shape of the result the Lightroom plug-in parses.
"""

import json

import pytest
from PIL import Image

from substack_post import cli, notify, publish
from substack_post.cli import _emit, _result, build_parser, run
from substack_post.config import Credentials


def _jpeg(path, size=(8, 8)) -> None:
    Image.new("RGB", size, "white").save(path, format="JPEG")


@pytest.fixture
def photo_dir(tmp_path):
    directory = tmp_path / "photos"
    directory.mkdir()
    _jpeg(directory / "a.jpg")
    _jpeg(directory / "b.jpg")
    return directory


def test_result_has_every_key_whatever_the_outcome() -> None:
    keys = set(_result("draft")) | set(_result("published", url="u", emailed=True))
    assert keys == {
        "status", "url", "draft_id", "slug", "photos", "emailed",
        "notified_ok", "notified_failed",
    }


def test_emit_kv_writes_one_line_per_key(capsys) -> None:
    _emit(_result("draft", url="https://x.test/p/1", draft_id=7, photos=3), "kv")
    lines = dict(line.split("=", 1) for line in capsys.readouterr().out.splitlines())
    assert lines["status"] == "draft"
    assert lines["url"] == "https://x.test/p/1"
    assert lines["draft_id"] == "7"
    assert lines["photos"] == "3"


def test_emit_kv_renders_booleans_and_none_for_a_parserless_caller(capsys) -> None:
    _emit({"emailed": True, "notify": False, "slug": None}, "kv")
    assert capsys.readouterr().out == "emailed=1\nnotify=0\nslug=\n"


def test_emit_json_round_trips(capsys) -> None:
    _emit(_result("published", url="https://x.test/p/1", emailed=True), "json")
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "published"
    assert payload["emailed"] is True


def test_format_defaults_to_text() -> None:
    args = build_parser().parse_args(["photos", "--title", "T"])
    assert args.format == "text"


def test_check_needs_no_photo_dir_or_title() -> None:
    args = build_parser().parse_args(["--check"])
    assert args.check and args.photo_dir is None and args.title is None


def test_photo_dir_is_still_required_without_check(capsys) -> None:
    with pytest.raises(SystemExit) as exit_info:
        run(["--title", "T"])
    assert exit_info.value.code == 2
    assert "photo_dir" in capsys.readouterr().err


def test_title_is_still_required_without_check(capsys) -> None:
    with pytest.raises(SystemExit) as exit_info:
        run(["photos"])
    assert exit_info.value.code == 2
    assert "--title" in capsys.readouterr().err


def test_dry_run_kv_reports_the_photo_count_and_nothing_else_on_stdout(
    photo_dir, capsys
) -> None:
    assert run([str(photo_dir), "--title", "T", "--dry-run", "--format", "kv"]) == 0
    captured = capsys.readouterr()
    assert dict(l.split("=", 1) for l in captured.out.splitlines()) == {
        "status": "dry_run", "url": "", "draft_id": "", "slug": "",
        "photos": "2", "emailed": "0", "notified_ok": "0", "notified_failed": "0",
    }
    # The preview itself is still available, just out of the result's way.
    assert "dry run: rendered markdown" in captured.err


def test_dry_run_text_output_is_unchanged(photo_dir, capsys) -> None:
    assert run([str(photo_dir), "--title", "T", "--dry-run"]) == 0
    assert "dry run: rendered markdown" in capsys.readouterr().out


def test_missing_photo_folder_reports_an_error_line(tmp_path, capsys) -> None:
    assert run([str(tmp_path / "nope"), "--title", "T", "--dry-run"]) == 2
    assert capsys.readouterr().err.startswith("error: ")


def test_unreadable_template_reports_an_error_line(photo_dir, tmp_path, capsys) -> None:
    missing = tmp_path / "missing.md"
    code = run([str(photo_dir), "--title", "T", "--dry-run", "--template", str(missing)])
    assert code == 2
    assert capsys.readouterr().err.startswith("error: ")


def test_notify_still_requires_publish(photo_dir, capsys) -> None:
    with pytest.raises(SystemExit) as exit_info:
        run([str(photo_dir), "--title", "T", "--notify"])
    assert exit_info.value.code == 2
    assert "--notify requires --publish" in capsys.readouterr().err


def test_named_env_file_that_is_missing_says_so(photo_dir, tmp_path, capsys) -> None:
    code = run([str(photo_dir), "--title", "T", "--env-file", str(tmp_path / "nope.env")])
    assert code == 2
    assert "No such env file" in capsys.readouterr().err


class _Api:
    """Stands in for python-substack's Api; only the slug lookup is reached."""

    def __init__(self, draft):
        self._draft = draft

    def get_draft(self, draft_id):
        return self._draft


@pytest.fixture
def contacts_file(tmp_path):
    path = tmp_path / "contacts.json"
    path.write_text(json.dumps([{"name": "Mom", "phone": "+15551234567"}]))
    return path


@pytest.fixture
def fake_substack(monkeypatch):
    """Stub out Substack and Messages, and collect the texts that would go out.

    Returns a setup callable taking the publish response and the post as it
    would read back afterwards; the list it returns fills with (contact,
    message) pairs.
    """
    sent = []

    def setup(publish_response, post=None):
        monkeypatch.setattr(
            cli,
            "load_credentials",
            lambda env_file=None: Credentials(
                email="e@x.test", password="p", publication_url="https://x.test"
            ),
        )
        monkeypatch.setattr(publish, "login", lambda *args: _Api(post or {}))
        monkeypatch.setattr(
            publish,
            "upload_photos",
            lambda api, photos, workdir: [
                (photo, f"https://cdn.test/{photo.name}") for photo in photos
            ],
        )
        monkeypatch.setattr(publish, "create_draft", lambda *args: {"id": 42})
        monkeypatch.setattr(
            publish, "publish_draft", lambda api, draft_id, send: publish_response
        )
        monkeypatch.setattr(notify, "check_platform", lambda: None)

        def notify_all(messages):
            sent.extend(messages)
            return [(contact, None) for contact, _ in messages]

        monkeypatch.setattr(notify, "notify_all", notify_all)
        return sent

    return setup


def _publish_argv(photo_dir, contacts_file):
    return [
        str(photo_dir), "--title", "Olympic Peninsula", "--publish", "--notify",
        "--notify-list", str(contacts_file), "--yes", "--format", "kv",
    ]


def test_notify_links_to_the_slug_publishing_reported(
    photo_dir, contacts_file, fake_substack, capsys
) -> None:
    sent = fake_substack({"slug": "olympic-peninsula"})
    assert run(_publish_argv(photo_dir, contacts_file)) == 0
    [(contact, message)] = sent
    assert contact.name == "Mom"
    assert "https://x.test/p/olympic-peninsula" in message
    result = dict(l.split("=", 1) for l in capsys.readouterr().out.splitlines())
    assert result["url"] == "https://x.test/p/olympic-peninsula"
    assert result["slug"] == "olympic-peninsula"
    assert result["notified_ok"] == "1"


def test_notify_reads_the_slug_back_when_publishing_omits_it(
    photo_dir, contacts_file, fake_substack
) -> None:
    sent = fake_substack({}, post={"slug": "olympic-peninsula"})
    assert run(_publish_argv(photo_dir, contacts_file)) == 0
    assert "https://x.test/p/olympic-peninsula" in sent[0][1]


def test_no_slug_anywhere_errors_instead_of_texting_a_dead_link(
    photo_dir, contacts_file, fake_substack, capsys
) -> None:
    sent = fake_substack({}, post={})
    assert run(_publish_argv(photo_dir, contacts_file)) == 1
    assert sent == []
    captured = capsys.readouterr()
    assert "error: " in captured.err
    assert "/p/None" not in captured.err + captured.out


def test_confirmation_previews_the_link_the_post_will_get(
    photo_dir, contacts_file, fake_substack, monkeypatch, capsys
) -> None:
    sent = fake_substack({"slug": "olympic-peninsula"})
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda *args: "n")
    argv = [a for a in _publish_argv(photo_dir, contacts_file)
            if a not in ("--yes", "--format", "kv")]

    assert run(argv) == 1
    assert sent == []
    out = capsys.readouterr().out
    assert "https://x.test/p/olympic-peninsula" in out
    assert "/p/None" not in out
