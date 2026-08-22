"""The position file a caller polls while a post is being made."""

from pathlib import Path

from substack_post.progress import Progress


def _read(path: Path) -> dict:
    return dict(line.split("=", 1) for line in path.read_text().splitlines())


def test_each_step_replaces_the_last(tmp_path: Path) -> None:
    path = tmp_path / "position"
    progress = Progress(path, 3)

    progress.step("Signing in to Substack")
    assert _read(path) == {"step": "1", "total": "3", "label": "Signing in to Substack"}

    progress.step("Uploading a.jpg")
    assert _read(path) == {"step": "2", "total": "3", "label": "Uploading a.jpg"}
    # A position, not a log: nothing accumulates.
    assert len(path.read_text().splitlines()) == 3


def test_the_step_never_runs_past_the_total(tmp_path: Path) -> None:
    path = tmp_path / "position"
    progress = Progress(path, 1)
    for _ in range(3):
        progress.step("Texting someone")
    assert _read(path)["step"] == "1"


def test_no_file_means_no_writing(tmp_path: Path) -> None:
    Progress(None, 2).step("Signing in to Substack")
    assert list(tmp_path.iterdir()) == []


def test_an_unwritable_file_does_not_stop_the_run(tmp_path: Path) -> None:
    # Nothing about the post depends on the position being recorded.
    unwritable = tmp_path / "no-such-directory" / "position"
    Progress(unwritable, 2).step("Signing in to Substack")
