# aktui

A lightweight Anki TUI client built on the official [Anki](https://github.com/ankitects/anki) core.

## Install

Requires Linux, Python 3.11+, curses, and a terminal editor. Tested on Linux x86_64 with Python 3.12; use Debian 12 / Ubuntu 24.04 or newer.

Anki is pinned to `anki==25.9`: both the supported minimum and maximum are **25.9**.

```bash
cd aktui
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --no-cache-dir -r requirements.lock
python -m pip install --no-cache-dir --no-deps .
```

## Login and sync

```bash
aktui login    # Prompts for email and password
aktui sync     # Sync collection changes
aktui          # Open the terminal interface
aktui logout   # Remove the local token; keep the collection
```

If a full sync is required, open `aktui` and select **Sync**. Choose **download** to replace the local collection or **upload** to replace the remote collection, then type `DOWNLOAD` or `UPLOAD` to confirm. The CLI returns exit code 2 when this choice is needed.

Sync again after reviewing or editing. Use a separate profile for each account:

```bash
aktui --profile personal login
aktui --profile personal sync
aktui --profile personal
```

Data defaults to `~/.local/share/aktui/default`, respecting `XDG_DATA_HOME`. Override it with `--data-dir PATH`.

## Daily use

Choose **Open deck > Study**. Use arrows or `j/k` in menus, Space to reveal answers, `1-4` to grade, `e` to edit, and `q` to return.

Use **Add note** or **Browse / edit notes** for editing. Set `EDITOR=nano` to choose an editor. Notes are edited as JSON containing fields and tags; field values retain their original HTML. Cloze syntax is `{{c1::text}}`.

## Media sync

```bash
aktui media-on     # Enable media transfer on the next sync
aktui media-off    # Disable media transfer (default)
```

When disabled, aktui does not upload, download, or delete media files. Existing files and note references are preserved; phone sync is unaffected. When enabled, Anki merges media changes, including deletions. The terminal shows media placeholders without playback.

## Implementation

- Official Anki collection, scheduler, and sync; a single-process curses interface.
- One account per profile, an exclusive profile lock, and token storage with `0600` permissions. Passwords are not saved; tokens are not encrypted.
- Official collection backups, at most three retained. Full replacement stops if backup fails. Backups exclude media.

For development, install with `python -m pip install --no-deps -e .`, then run `python -m unittest discover -s tests -v`. Code lives in `src/aktui/`. Local sync-server tests pass; live account testing has not been completed.

License: [AGPL-3.0-or-later](LICENSE).
