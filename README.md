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

## Requirements

- Python 3.10+
- A Substack publication you can post to
- Password sign-in enabled on your Substack account (see [Setup](#setup))

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
| `photo_dir` | Folder of photos to post (required, positional). |
| `--title` | Post title (required). |
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

## Sample output

```
$ substack-post ./photos --title "Olympic Peninsula" --subtitle "August 2026" --publish
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

Publish? [y/N] y
Publishing...
Published:
https://yourpub.substack.com/p/olympic-peninsula
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