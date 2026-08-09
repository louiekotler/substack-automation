from datetime import datetime
from pathlib import Path

from substack_post.photos import Photo
from substack_post.render import (
    SPACER_PLACEHOLDER,
    format_caption,
    photo_markdown,
    render_photos_block,
    render_post,
)


def _photo(taken_at=None, name="photo.jpg") -> Photo:
    return Photo(path=Path(name), taken_at=taken_at)


def test_format_caption_with_timestamp() -> None:
    photo = _photo(taken_at=datetime(2026, 8, 3, 12, 0, 0))
    assert format_caption(photo, "%B {day}, %Y") == "August 3, 2026"


def test_format_caption_without_timestamp_is_empty() -> None:
    assert format_caption(_photo(taken_at=None), "%B {day}, %Y") == ""


def test_photo_markdown_includes_caption_as_title() -> None:
    md = photo_markdown("https://cdn.example.com/a.jpg", "August 3, 2026")
    assert md == '![](https://cdn.example.com/a.jpg "August 3, 2026")'


def test_photo_markdown_without_caption_has_no_title_slot() -> None:
    md = photo_markdown("https://cdn.example.com/a.jpg", "")
    assert md == "![](https://cdn.example.com/a.jpg)"


def test_photo_markdown_escapes_quotes_in_caption() -> None:
    md = photo_markdown("https://cdn.example.com/a.jpg", 'A "big" day')
    assert md == '![](https://cdn.example.com/a.jpg "A \\"big\\" day")'


def test_render_photos_block_separates_with_a_spacer_paragraph() -> None:
    photos = [
        (_photo(taken_at=datetime(2026, 8, 1)), "https://cdn.example.com/1.jpg"),
        (_photo(taken_at=None), "https://cdn.example.com/2.jpg"),
    ]
    block = render_photos_block(photos, "%B {day}, %Y")
    assert block == (
        '![](https://cdn.example.com/1.jpg "August 1, 2026")\n\n'
        f"{SPACER_PLACEHOLDER}\n\n"
        "![](https://cdn.example.com/2.jpg)"
    )


def test_render_post_substitutes_placeholders() -> None:
    template = "{{photos}}\n\n{{signoff}}"
    photos = [(_photo(taken_at=None), "https://cdn.example.com/1.jpg")]
    rendered = render_post(template, photos, "%B {day}, %Y", "- Louie")
    assert rendered == "![](https://cdn.example.com/1.jpg)\n\n- Louie"


def test_render_post_resolves_spacer_placeholder_to_non_breaking_space() -> None:
    template = f"{{{{photos}}}}\n\n{SPACER_PLACEHOLDER}\n\n{{{{signoff}}}}"
    photos = [(_photo(taken_at=None), "https://cdn.example.com/1.jpg")]
    rendered = render_post(template, photos, "%B {day}, %Y", "- Louie")
    assert SPACER_PLACEHOLDER not in rendered
    assert " " in rendered
