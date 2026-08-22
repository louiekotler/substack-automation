"""Report how far along a run is to a caller that cannot read stderr."""

from __future__ import annotations

from pathlib import Path


class Progress:
    """Where a run has got to, written somewhere a caller can poll.

    Stderr suits a terminal, but a caller with no terminal only sees output
    once the process has exited — by which point the work it wanted to track
    is over. So the position also goes to a file, in the same key=value form
    the results use. Each write replaces the last: it is a position, not a log.

    A run is measured in steps rather than photos because uploading is only
    part of the wait; drafting, publishing and texting take seconds each and
    are exactly the stretch that would otherwise look like nothing happening.
    """

    def __init__(self, path: Path | None, total: int) -> None:
        self._path = path
        self._total = max(total, 1)
        self._step = 0

    def step(self, label: str) -> None:
        """Start the next step. The label says what is being done, for a caption."""
        self._step = min(self._step + 1, self._total)
        self._write(label)

    def _write(self, label: str) -> None:
        if self._path is None:
            return
        try:
            self._path.write_text(
                f"step={self._step}\ntotal={self._total}\nlabel={label}\n"
            )
        except OSError:
            # Progress is a courtesy; losing it must never fail a post.
            pass
