import json
from pathlib import Path

import pytest

from substack_post import notify
from substack_post.config import resolve_notify_contacts_path
from substack_post.notify import (
    Contact,
    NotifyError,
    _applescript_string,
    load_contacts,
    notify_all,
    render_message,
)


def _write(path: Path, data) -> Path:
    path.write_text(json.dumps(data))
    return path


def test_load_contacts_valid_file(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "contacts.json",
        [{"name": "Mom", "phone": "+15551234567"}, {"name": "Dan", "phone": "+15559876543"}],
    )
    assert load_contacts(path) == [
        Contact(name="Mom", phone="+15551234567"),
        Contact(name="Dan", phone="+15559876543"),
    ]


def test_load_contacts_missing_file(tmp_path: Path) -> None:
    with pytest.raises(NotifyError):
        load_contacts(tmp_path / "does-not-exist.json")


def test_load_contacts_invalid_json(tmp_path: Path) -> None:
    path = tmp_path / "contacts.json"
    path.write_text("{not valid json")
    with pytest.raises(NotifyError):
        load_contacts(path)


def test_load_contacts_must_be_a_list(tmp_path: Path) -> None:
    path = _write(tmp_path / "contacts.json", {"name": "Mom", "phone": "+15551234567"})
    with pytest.raises(NotifyError):
        load_contacts(path)


def test_load_contacts_empty_list(tmp_path: Path) -> None:
    path = _write(tmp_path / "contacts.json", [])
    with pytest.raises(NotifyError):
        load_contacts(path)


@pytest.mark.parametrize(
    "entry",
    [
        {"phone": "+15551234567"},
        {"name": "Mom"},
        {"name": "", "phone": "+15551234567"},
        {"name": "Mom", "phone": ""},
        {"name": "Mom", "phone": "  "},
        {"name": "Family", "chat_name": ""},
        # Ambiguous: a contact is a person or a group, never both.
        {"name": "Mom", "phone": "+15551234567", "chat_name": "Family"},
    ],
)
def test_load_contacts_rejects_incomplete_entries(tmp_path: Path, entry) -> None:
    path = _write(tmp_path / "contacts.json", [entry])
    with pytest.raises(NotifyError):
        load_contacts(path)


def test_load_contacts_accepts_group_entries(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "contacts.json",
        [
            {"name": "Mom", "phone": "+15551234567"},
            {"name": "Family", "chat_name": "Family Group Chat Name"},
        ],
    )
    contacts = load_contacts(path)
    assert contacts[0] == Contact(name="Mom", phone="+15551234567")
    assert contacts[1] == Contact(name="Family", chat_name="Family Group Chat Name")
    assert contacts[0].is_group is False
    assert contacts[1].is_group is True


def test_render_message_substitutes_placeholders() -> None:
    rendered = render_message(
        "Hi {{name}}, new post: {{title}}\n\n{{link}}",
        title="Olympic Peninsula",
        link="https://example.substack.com/p/olympic-peninsula",
        name="Mom",
    )
    assert rendered == (
        "Hi Mom, new post: Olympic Peninsula\n\n"
        "https://example.substack.com/p/olympic-peninsula"
    )


def test_render_message_is_personalized_per_contact() -> None:
    template = "Hi {{name}}!"
    assert render_message(template, title="T", link="L", name="Mom") == "Hi Mom!"
    assert render_message(template, title="T", link="L", name="Dan") == "Hi Dan!"


def test_render_message_leaves_stray_braces_alone() -> None:
    rendered = render_message("{{title}} {curly} {{link}} {{name}}", title="A {thing}", link="L", name="N")
    assert rendered == "A {thing} {curly} L N"


def test_applescript_string_escapes_quotes_and_backslashes() -> None:
    escaped = _applescript_string('say "hi" \\ bye')
    assert escaped == 'say \\"hi\\" \\\\ bye'
    # And the escaped form, embedded in a script, keeps the string literal intact.
    script = f'send "{escaped}" to targetBuddy'
    assert script.count('"') == 4


def test_notify_all_dispatches_people_and_groups_separately(monkeypatch) -> None:
    sent_to_people = []
    sent_to_groups = []
    monkeypatch.setattr(
        notify, "send_imessage", lambda phone, message: sent_to_people.append(phone)
    )
    monkeypatch.setattr(
        notify, "send_to_group", lambda chat_name, message: sent_to_groups.append(chat_name)
    )

    mom = Contact(name="Mom", phone="+15551234567")
    family = Contact(name="Family", chat_name="Family Group Chat Name")
    results = notify_all([(mom, "hi mom"), (family, "hi family")])

    assert sent_to_people == ["+15551234567"]
    assert sent_to_groups == ["Family Group Chat Name"]
    assert results == [(mom, None), (family, None)]


def test_notify_all_continues_after_one_failure(monkeypatch) -> None:
    def fail(phone, message):
        raise NotifyError("boom")

    monkeypatch.setattr(notify, "send_imessage", fail)
    contacts = [Contact(name="Bad", phone="+1"), ]
    results = notify_all([(contacts[0], "msg")])
    assert results == [(contacts[0], "boom")]


class TestResolveNotifyContactsPath:
    """Where the contacts file is looked for, and in what order."""

    def test_cli_path_wins(self, tmp_path, monkeypatch):
        monkeypatch.setenv("NOTIFY_CONTACTS_FILE", str(tmp_path / "from-env.json"))
        chosen = resolve_notify_contacts_path(tmp_path / "explicit.json", tmp_path / ".env")
        assert chosen == tmp_path / "explicit.json"

    def test_env_var_beats_the_file_beside_the_env_file(self, tmp_path, monkeypatch):
        (tmp_path / "notify_contacts.json").write_text("[]")
        monkeypatch.setenv("NOTIFY_CONTACTS_FILE", str(tmp_path / "from-env.json"))
        chosen = resolve_notify_contacts_path(None, tmp_path / ".env")
        assert chosen == tmp_path / "from-env.json"

    def test_falls_back_to_the_file_beside_a_named_env_file(self, tmp_path, monkeypatch):
        """A GUI-launched run has an arbitrary working directory, so a bare
        filename is meaningless; the env file it names is the only anchor."""
        monkeypatch.delenv("NOTIFY_CONTACTS_FILE", raising=False)
        beside = tmp_path / "notify_contacts.json"
        beside.write_text("[]")
        assert resolve_notify_contacts_path(None, tmp_path / ".env") == beside

    def test_ignores_a_neighbour_that_is_not_there(self, tmp_path, monkeypatch):
        monkeypatch.delenv("NOTIFY_CONTACTS_FILE", raising=False)
        chosen = resolve_notify_contacts_path(None, tmp_path / ".env")
        assert chosen == Path("notify_contacts.json")

    def test_without_an_env_file_the_old_default_stands(self, monkeypatch):
        monkeypatch.delenv("NOTIFY_CONTACTS_FILE", raising=False)
        assert resolve_notify_contacts_path() == Path("notify_contacts.json")
