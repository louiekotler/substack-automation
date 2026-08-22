"""Building the addresses a post is linked by.

Nothing here contacts Substack: the API is stubbed where a response matters.
"""

import pytest

from substack_post.publish import (
    PublishError,
    expected_slug,
    post_url,
    preview_post_url,
    published_slug,
)

BASE = "https://x.test"


class _Api:
    """Stands in for python-substack's Api, for the one lookup that can happen."""

    def __init__(self, draft=None, fails=False):
        self._draft = draft
        self._fails = fails

    def get_draft(self, draft_id):
        if self._fails:
            raise RuntimeError("network")
        return self._draft


def test_post_url_joins_the_publication_and_the_slug() -> None:
    assert post_url(BASE + "/", "olympic-peninsula") == f"{BASE}/p/olympic-peninsula"


@pytest.mark.parametrize("slug", ["", None])
def test_post_url_refuses_a_missing_slug(slug) -> None:
    with pytest.raises(PublishError):
        post_url(BASE, slug)


def test_published_slug_prefers_what_publishing_reported() -> None:
    api = _Api(draft={"slug": "stale"})
    assert published_slug(api, 42, {"slug": "olympic-peninsula"}) == "olympic-peninsula"


@pytest.mark.parametrize("published", [{}, {"slug": None}, None])
def test_published_slug_reads_the_post_when_publishing_reports_none(published) -> None:
    api = _Api(draft={"slug": "olympic-peninsula"})
    assert published_slug(api, 42, published) == "olympic-peninsula"


def test_published_slug_raises_when_no_slug_can_be_found() -> None:
    with pytest.raises(PublishError, match="42"):
        published_slug(_Api(draft={"slug": None}), 42, {})


def test_published_slug_raises_when_the_lookup_fails() -> None:
    with pytest.raises(PublishError):
        published_slug(_Api(fails=True), 42, {})


@pytest.mark.parametrize(
    ("title", "slug"),
    [
        ("Olympic Peninsula", "olympic-peninsula"),
        ("  A Weekend: Part 2!  ", "a-weekend-part-2"),
        ("Louie's Frame", "louies-frame"),
        ("Louie’s Frame", "louies-frame"),
        ("...", ""),
    ],
)
def test_expected_slug_approximates_substacks_own(title, slug) -> None:
    assert expected_slug(title) == slug


def test_preview_post_url_guesses_from_the_title() -> None:
    assert preview_post_url(BASE, "Olympic Peninsula") == f"{BASE}/p/olympic-peninsula"


def test_preview_post_url_falls_back_to_the_site_for_an_unsluggable_title() -> None:
    assert preview_post_url(BASE, "...") == BASE
