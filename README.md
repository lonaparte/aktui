# aktui

A lightweight Anki TUI client built on the official [Anki](https://github.com/ankitects/anki) core.

## Install

Requires Python 3.11+. Anki package version is pinned to `anki==25.9`.

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

## Collection backup versions

Sync checks the collection before and after synchronization, saving a new
`.colpkg` only when its logical content changes. The three most recent distinct
versions are retained locally. Notes, cards, review history, decks, note types,
templates, and collection preferences contribute to the comparison. Sync counters,
timestamps, selected decks, daily counters, and SQLite file layout do not.
Reverting to an older content version makes it the newest version without keeping
two copies of the same content. Existing packages are inspected and deduplicated
on first use. A new package must finish successfully before older versions can be
removed. Packages exclude media and login credentials.

## Implementation

- Official Anki collection, scheduler, and sync; a single-process curses interface.
- One account per profile, an exclusive profile lock, and token storage with `0600` permissions. Passwords are not saved; tokens are not encrypted.
- Anki collection packages, retaining the three most recent distinct content versions. Full replacement stops if backup fails. Backups exclude media.

For development, install with `python -m pip install --no-deps -e .`, then run `python -m unittest discover -s tests -v`. Code lives in `src/aktui/`. Local sync-server tests pass; live account testing has not been completed.

License: [AGPL-3.0-or-later](LICENSE).
