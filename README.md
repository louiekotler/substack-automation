# substack-automation

A small CLI, `substack-post`, that publishes a folder of photos to
[Substack](https://substack.com) as a single post — no writing required beyond
a title. Photos are ordered by the moment they were taken (from EXIF) and each
gets a caption of its date, generated automatically.

```bash
substack-post ./photos \
    --title "Olympic Peninsula" \
    --subtitle "August 2026" \
    --publish
```

Built on [python-substack](https://github.com/ma2za/python-substack).

## What it does

1. Scans a folder for photos (JPEG, PNG, WebP, TIFF, HEIC/HEIF).
2. Reads each photo's EXIF capture timestamp and sorts them chronologically.
   Photos with no EXIF timestamp are placed last, in filename order.
3. Uploads every photo to Substack and builds a post: one image per paragraph,
   captioned with the date it was taken (`August 3, 2026`).
4. Fills in a simple markdown template (title/subtitle stay in Substack's own
   fields, not the body) and creates a draft.
5. With `--publish`, publishes it. Otherwise it stops at a draft.
6. Prints the resulting URL.
7. Optionally (`--notify`, publish only), texts a personal contact list via
   macOS Messages to let them know the post is up.

## Requirements

- Python 3.10+
- A Substack publication you can post to
- Password sign-in enabled on your Substack account (see [Setup](#setup))
- For `--notify`: macOS, with Messages signed in (see
  [Notify contacts on publish](#notify-contacts-on-publish))

## Setup

```bash
git clone https://github.com/<you>/substack-automation.git
cd substack-automation
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

Copy the example environment file and fill in your details:

```bash
cp .env.example .env
```

```env
SUBSTACK_EMAIL=you@example.com
SUBSTACK_PASSWORD=your-substack-password
SUBSTACK_PUBLICATION_URL=https://yourpublication.substack.com
```

`.env` is gitignored — never commit it.

> [!IMPORTANT]
> **Substack accounts sign in with a magic link by default, not a password.**
> This tool authenticates with email + password, so you must enable password
> sign-in manually first:
>
> 1. Go to Substack and sign out.
> 2. On the sign-in screen, choose **"Sign in with password"**.
> 3. Choose **"Set a new password"** and follow the prompts.
> 4. Use that password in `.env`.

## Usage

Create a draft (the default — nothing is published):

```bash
substack-post ./photos --title "Olympic Peninsula" --subtitle "August 2026"
```

Publish immediately, emailing subscribers (Substack's normal publish behavior):

```bash
substack-post ./photos --title "Olympic Peninsula" --publish
```

Publish to the site without emailing subscribers:

```bash
substack-post ./photos --title "Olympic Peninsula" --publish --no-email
```

Preview the rendered post — photo order and captions — without uploading
anything or contacting Substack:

```bash
substack-post ./photos --title "Olympic Peninsula" --dry-run
```

Skip the interactive confirmation (e.g. for a scripted/cron use):

```bash
substack-post ./photos --title "Olympic Peninsula" --publish --yes
```

`--publish` always prints a summary (title, photo count, publication, whether
subscribers will be emailed) and asks for confirmation before sending,
because publishing can't be undone. `--yes` skips the prompt.

### All options

| Flag | Description |
| --- | --- |
| `photo_dir` | Folder of photos to post (positional; required unless `--check`). |
| `--title` | Post title (required unless `--check`). |
| `--subtitle` | Post subtitle (optional). |
| `--publish` | Publish instead of leaving a draft. |
| `--draft` | Create a draft only (the default; exists for clarity). |
| `--no-email` | With `--publish`, don't email subscribers. |
| `--signoff` | Override the default sign-off text. |
| `--caption-format` | strftime format for captions. Use `{day}` for a day number with no leading zero. Default: `%B {day}, %Y` → `August 3, 2026`. |
| `--template` | Path to a custom markdown template, overriding the built-in one. |
| `--env-file` | Path to a `.env` file (default: `.env` in the current directory). |
| `--dry-run` | Render and print the post; no uploads, no Substack calls. |
| `--yes` | Skip the confirmation prompt before publishing. |
| `--notify` | After publishing, text everyone in the contacts file via macOS Messages. Requires `--publish`. |
| `--notify-list` | Path to the notify contacts JSON file. See [Notify contacts on publish](#notify-contacts-on-publish) for where it's looked for by default. |
| `--check` | Sign in, report the publication, and exit without posting. `photo_dir` and `--title` aren't needed. |
| `--format` | `text` (default), `kv`, or `json` — see [Machine-readable output](#machine-readable-output). |

## Checking your setup

```bash
substack-post --check
```

Loads `.env`, signs in, and prints who you are and which publication you'd be
posting to. Nothing is uploaded, drafted, or published. This is the quickest way
to tell a wrong password from a wrong publication URL, and it's what the
Lightroom plug-in's **Test** button calls.

## Machine-readable output

`--format kv` prints one `key=value` line per field on stdout, and `--format
json` prints the same fields as one object. Progress notes stay on stderr either
way, so stdout holds nothing but the result.

```bash
$ substack-post ./photos --title "Olympic Peninsula" --format kv
status=draft
url=https://yourpub.substack.com/publish/post/12345
draft_id=12345
slug=
photos=14
emailed=0
notified_ok=0
notified_failed=0
```

`status` is `draft`, `published`, `dry_run`, or `aborted`. Every key is always
present, so a caller never has to branch on which fields exist. Booleans are
`1`/`0` and unknown values are empty.

The `kv` form exists for callers without a JSON parser — it's what the companion
Lightroom plug-in (`lightroom-substack-publisher`) uses, which keeps a
third-party Lua JSON library out of that plug-in's bundle.

On failure the tool exits non-zero and prints `error: <message>` to stderr.
Exit codes: `1` if Substack rejected something, `2` for bad input or
configuration, `130` if interrupted.

## Ordering and captions

Order and captions both come from each photo's EXIF `DateTimeOriginal` (with
`DateTimeDigitized`, then `DateTime`, as fallbacks) — the moment the shutter
fired, not the file's modification time. File mtime is deliberately **not**
used as a fallback: it changes whenever a photo is copied or synced, so it
would produce a confident-looking but wrong date. A photo with no EXIF
timestamp is placed after all dated photos (sorted by filename) and gets no
caption at all.

HEIC/HEIF photos (the default format for iPhone photos) are converted to JPEG
before upload, since Substack's uploader doesn't accept HEIC directly. Every
other format uploads unmodified.

## Customizing the template

The post body comes from a small markdown template
([`src/substack_post/templates/post.md`](src/substack_post/templates/post.md)).

`{{photos}}` expands to one captioned-image paragraph per photo, in order,
with a `{{spacer}}` between each pair for visual breathing room. `{{signoff}}`
expands to the sign-off text (from
[`src/substack_post/templates/signoff.md`](src/substack_post/templates/signoff.md)
by default, or `--signoff` on the command line). Title and subtitle are set
via Substack's own fields, not the template, so they aren't duplicated inside
the post body.

Pass `--template path/to/file.md` to use a different template — it must
contain a `{{photos}}` placeholder to be useful.

## Notify contacts on publish

With `--publish --notify`, once the post is live it texts a personal contact
list via the macOS Messages app, so you don't have to separately let people
know. It only ever fires after an actual publish — never for a draft.

Create your contact list from the example:

```bash
cp notify_contacts.example.json notify_contacts.json
```

```json
[
  {"name": "Mom", "phone": "+15551234567"},
  {"name": "Dad", "phone": "+15559876543"},
  {"name": "Family", "chat_name": "Family Group Chat Name"}
]
```

Each entry is a person (`phone`) or an existing group conversation
(`chat_name`) — never both. `name` is never looked up against your macOS
Contacts — it's just what this tool uses to personalize the message (see
below) and to label the confirmation prompt and send results.

- **People:** `phone` is passed straight to Messages as the recipient's
  iMessage handle, so despite the field name it can just as well be an
  iCloud email address (`"phone": "mom@icloud.com"`) — whatever that person
  actually has registered with iMessage. Use [E.164
  format](https://en.wikipedia.org/wiki/E.164) (`+1` followed by the full
  number, no spaces or dashes) for phone numbers, for reliable matching.
- **Groups:** `chat_name` must match the group conversation's display name
  *exactly* as it appears in Messages. Unnamed groups (Messages just lists
  participants) can't be matched this way — open the conversation in
  Messages and give it a name first.

`notify_contacts.json` is gitignored — like `.env`, it holds personal data
and should never be committed.

The contacts file is looked for in this order:

1. `--notify-list path/to/file.json`
2. `NOTIFY_CONTACTS_FILE` in `.env`
3. `notify_contacts.json` sitting beside the `--env-file` you named
4. `notify_contacts.json` in the current directory

Step 3 exists for callers that don't control their working directory — a GUI
hands its subprocess an arbitrary one, so a bare filename would resolve
somewhere meaningless. Such a caller names the `.env` explicitly, and keeping
the contacts file next to it is enough.

The message itself comes from
[`src/substack_post/templates/notify_message.md`](src/substack_post/templates/notify_message.md),
rendered separately for each contact:

```markdown
Hi {{name}}, I have made a new Substack post: {{title}}

{{link}}
```

Edit that file to change the wording; `{{name}}`, `{{title}}`, and `{{link}}`
are filled in automatically per recipient. Before anything is sent, the usual
publish confirmation prompt (see [Usage](#usage)) grows a `Notify:` line
showing who will be texted and an example of the personalized message, so you
see it before it goes out — `--yes` skips this the same way it skips the rest
of the prompt.

> [!IMPORTANT]
> The first time you run `--notify`, macOS will prompt you to grant your
> terminal app **Automation** permission to control Messages — approve it, or
> texts won't send. You can review this later under **System Settings →
> Privacy & Security → Automation**.

If a particular text fails to send (a bad number, or a contact not reachable
via iMessage), it's reported as `FAILED <name>: <reason>` and the rest of the
list still gets sent — one bad number doesn't block everyone else, and it
never un-publishes the post, which has already gone out by that point.

## Sample output

```
$ substack-post ./photos --title "Olympic Peninsula" --subtitle "August 2026" --publish --notify
Found 14 photo(s) in ./photos:
  - IMG_0401.HEIC (2026-08-02 09:14)
  - IMG_0402.HEIC (2026-08-02 09:16)
  ...
Signing in to Substack...
Uploading photos...
  [1/14] uploading IMG_0401.HEIC...
  ...

About to publish:
  Title:       Olympic Peninsula
  Photos:      14
  Publication: https://yourpub.substack.com
  Email:       yes, all subscribers
  Notify:      2 people via Messages (Mom, Dad)
               each gets a personalized message, e.g. for Mom:
               "Hi Mom, I have made a new Substack post: Olympic Peninsula

               https://yourpub.substack.com/p/olympic-peninsula"

Publish? [y/N] y
Publishing...
Published:
https://yourpub.substack.com/p/olympic-peninsula
Notifying...
  ok Mom
  ok Dad
```

## Troubleshooting

- **Login fails with a captcha or magic-link error.** Substack occasionally
  requires interactive verification for password logins from a new location.
  Sign in normally in a browser first, then retry; the underlying library
  also supports cookie-based auth if this persists (see
  [python-substack's authentication docs](https://github.com/ma2za/python-substack/blob/main/docs/authentication.md)).
- **A photo has no caption.** It has no readable EXIF timestamp — check with
  `--dry-run` first. Screenshots and downloaded/edited images commonly lack
  EXIF data entirely.
- **HEIC photos fail to open.** Make sure `pillow-heif` installed correctly
  (`pip install -e .` should handle this); it's what lets Pillow read HEIC
  files at all.
- **A `--notify` text fails to send.** Most often either the terminal app
  hasn't been granted Automation permission for Messages yet (**System
  Settings → Privacy & Security → Automation**), a phone number isn't in
  E.164 format, or that contact's phone/email isn't reachable via iMessage.
  The failure is reported per-contact and doesn't affect the rest of the list
  or the post, which is already published by that point.
- **A `--notify` group message fails with "Invalid index" or "can't get
  chat."** The `chat_name` doesn't exactly match an existing group
  conversation's display name in Messages. Open the group in Messages,
  confirm (or set) its name there, and match it exactly, including case and
  punctuation.

## Running tests

```bash
pip install -e ".[dev]"
pytest
```

## Disclaimer

This tool uses [python-substack](https://github.com/ma2za/python-substack),
which relies on Substack's undocumented internal API. It is not affiliated
with or endorsed by Substack, and the underlying API may change without
notice.