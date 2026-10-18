from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import curses
import json
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import textwrap

from .render import plain, safe_text
from .multiselect import parse_multiselect
from anki.sync_pb2 import SyncCollectionResponse as SyncResult


class UI:
    def __init__(self, screen, service):
        self.win = screen
        self.s = service
        self.did = 1
        self.message = ""
        curses.curs_set(0)
        screen.keypad(True)

    def draw(self, title, lines, footer="Up/Down move | Enter select | q back", offset=0):
        self.win.erase()
        h, w = self.win.getmaxyx()
        def line(y, text, style=0):
            if 0 <= y < h:
                try:
                    self.win.addnstr(y, 0, safe_text(str(text)).replace("\n", " "), max(0, w - 1), style)
                except curses.error:
                    pass
        line(0, title, curses.A_BOLD)
        for y, text in enumerate(lines[offset:offset + max(0, h - 4)], 2):
            line(y, text)
        line(h - 1, footer, curses.A_REVERSE)
        self.win.refresh()

    def menu(self, title, labels):
        index = 0
        while True:
            height = max(1, self.win.getmaxyx()[0] - 4)
            start = (index // height) * height
            self.draw(title, [("> " if i == index else "  ") + str(v) for i, v in enumerate(labels)], offset=start)
            key = self.win.getch()
            if key in (ord('q'), 27):
                return None
            if key in (curses.KEY_UP, ord('k')) and labels:
                index = (index - 1) % len(labels)
            if key in (curses.KEY_DOWN, ord('j')) and labels:
                index = (index + 1) % len(labels)
            if key in (10, 13) and labels:
                return index

    def prompt(self, title, secret=False):
        value = ""
        while True:
            self.draw(title, ["*" * len(value) if secret else value], "Enter submit | Esc cancel")
            key = self.win.get_wch()
            if key == '\x1b':
                return None
            if key in ('\n', '\r'):
                return value
            if key in ('\x7f', '\b', curses.KEY_BACKSPACE):
                value = value[:-1]
            elif isinstance(key, str) and key.isprintable():
                value += key

    def notice(self, text):
        offset = 0
        while True:
            width = max(10, self.win.getmaxyx()[1] - 2)
            lines = []
            for part in safe_text(str(text)).splitlines():
                lines.extend(textwrap.wrap(part, width) or [''])
            self.draw("aktui", lines, "Up/Down scroll | Enter/q close", offset)
            key = self.win.getch()
            if key in (10, 13, ord('q'), 27):
                return
            if key == curses.KEY_DOWN:
                offset = min(max(0, len(lines) - 1), offset + 1)
            if key == curses.KEY_UP:
                offset = max(0, offset - 1)

    def busy(self, title, fn):
        # Exactly one operation owns the collection; main thread only draws.
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(fn)
            self.win.timeout(150)
            tick = 0
            cancelling = False
            try:
                while not future.done():
                    self.draw(title, ["Cancelling; waiting for backend..." if cancelling else "Working " + "|/-\\"[tick % 4]], "q cancel | do not terminate during database writes")
                    tick += 1
                    if self.win.getch() == ord('q'):
                        cancelling = True
                        self.s.col.abort_sync()
                        self.s.col.abort_media_sync()
                return future.result()
            finally:
                self.win.timeout(-1)

    def sync(self):
        result = self.busy("Syncing AnkiWeb", self.s.sync)
        if result.server_message:
            self.notice(result.server_message)
        if self.s.pending:
            choices = []
            if result.required != SyncResult.FULL_UPLOAD:
                choices.append("download")
            if result.required != SyncResult.FULL_DOWNLOAD:
                choices.append("upload")
            index = self.menu("Full sync required: choose the authoritative copy", ["Cancel"] + choices)
            if index is None or index == 0:
                return
            direction = choices[index - 1]
            target = "LOCAL collection" if direction == "download" else "ANKIWEB collection"
            confirmation = self.prompt(f"Replaces {target}. Type {direction.upper()} to confirm")
            if confirmation != direction.upper():
                return
            self.busy("Full " + direction, lambda: self.s.full_sync(direction))
        self.notice("Collection sync complete. " + ("Media synced." if self.s.profile.settings.get('media') else "Media sync is disabled."))

    def login(self):
        username = self.prompt("AnkiWeb email (new account: https://ankiweb.net/account/register)")
        if username is None:
            return
        password = self.prompt("AnkiWeb password", secret=True)
        if password is None:
            return
        try:
            self.busy("Signing in", lambda: self.s.login(username, password))
        finally:
            password = ""
        self.notice("Signed in. Select Sync to download or merge your collection.")

    def edit(self, note=None):
        creating = note is None
        if creating:
            models = self.s.col.models.all_names_and_ids()
            index = self.menu("Choose note type", [m.name for m in models])
            if index is None:
                return
            note = self.s.col.new_note(self.s.col.models.get(models[index].id))
        original = {"fields": dict(note.items()), "tags": list(note.tags)}
        fd, name = tempfile.mkstemp(suffix=".json", prefix="edit-", dir=self.s.profile.path)
        path = Path(name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as out:
                json.dump(original, out, ensure_ascii=False, indent=2)
            curses.def_prog_mode()
            curses.endwin()
            try:
                editor = shlex.split(os.environ.get("EDITOR", "vi"))
                result = subprocess.run(editor + [str(path)], check=False)
            finally:
                curses.reset_prog_mode()
                self.win.refresh()
            if result.returncode:
                self.notice("Editor failed; note unchanged")
                return
            changed = json.loads(path.read_text(encoding="utf-8"))
            fields = changed.get("fields")
            tags = changed.get("tags")
            if not isinstance(fields, dict) or set(fields) != set(original["fields"]) or not all(isinstance(v, str) for v in fields.values()):
                raise ValueError("Keep the exact field names; field values must be strings")
            if not isinstance(tags, list) or not all(isinstance(v, str) and not any(c.isspace() for c in v) for v in tags):
                raise ValueError("tags must be a list of strings without spaces")
            if not any(v.strip() for v in fields.values()):
                raise ValueError("An empty note cannot be saved")
            if self.menu("Save note?", ["Cancel", "Save"]) != 1:
                return
            for field, value in fields.items():
                note[field] = value
            note.tags = tags
            if creating:
                if self.s.col.decks.is_filtered(self.did):
                    raise ValueError("Add notes to a regular deck, not a filtered deck")
                self.s.col.add_note(note, self.did)
            else:
                self.s.col.update_note(note)
        finally:
            path.unlink(missing_ok=True)

    def browse(self):
        cursors = [0]
        query = ""
        while True:
            rows = self.s.note_page(self.did, after=cursors[-1], query=query)
            labels = [f"{nid}: {plain(str(label))[:100]}" for nid, label in rows]
            index = self.menu("Notes | " + (query or "all"), labels + ["Next page", "Previous page", "Search", "Back"])
            if index is None or index == len(rows) + 3:
                return
            if index < len(rows):
                self.edit(self.s.col.get_note(rows[index][0]))
            elif index == len(rows):
                if len(rows) == 30:
                    cursors.append(rows[-1][0])
            elif index == len(rows) + 1:
                if len(cursors) > 1:
                    cursors.pop()
            else:
                value = self.prompt("Search literal note text (empty = all)")
                if value is not None:
                    query, cursors = value, [0]

    def multiselect(self, form):
        selected = set()
        focus = 0
        message = ""
        while True:
            lines = [form.prompt, ""]
            for index, choice in enumerate(form.choices):
                lines.append(("> " if focus == index else "  ") +
                             ("[x] " if choice.value in selected else "[ ] ") + choice.label)
            lines.append(("> " if focus == len(form.choices) else "  ") + "[" + form.submit_label + "]")
            if message:
                lines += ["", message]
            height = max(1, self.win.getmaxyx()[0] - 4)
            self.draw(self.s.col.decks.name(self.did), lines,
                      "Up/Down move | Space/Enter select or submit | 1-9 toggle | q back",
                      offset=max(0, focus + 3 - height))
            key = self.win.getch()
            if key in (ord('q'), 27):
                return None
            if key in (curses.KEY_UP, ord('k')):
                focus = (focus - 1) % (len(form.choices) + 1)
            elif key in (curses.KEY_DOWN, ord('j'), 9):
                focus = (focus + 1) % (len(form.choices) + 1)
            elif ord('1') <= key <= ord('9') and key - ord('1') < len(form.choices):
                focus = key - ord('1')
                form.toggle(selected, focus)
                message = ""
            elif key in (32, 10, 13):
                if focus < len(form.choices):
                    form.toggle(selected, focus)
                    message = ""
                elif selected:
                    return selected
                else:
                    message = "Choose at least one option before submitting."

    def study(self):
        self.s.select_deck(self.did)
        while True:
            item = self.s.next_card()
            if item is None:
                self.notice("No cards currently due. Return later for learning cards, or check daily limits in Anki.")
                return
            card, states = item
            form = parse_multiselect(card.question())
            feedback = []
            if form:
                selected = self.multiselect(form)
                if selected is None:
                    return
                feedback = form.feedback(selected) + [""]
            shown = form is not None
            offset = 0
            while True:
                html = card.answer() if shown else card.question()
                width = max(10, self.win.getmaxyx()[1] - 2)
                lines = list(feedback)
                for part in plain(html).splitlines():
                    lines.extend(textwrap.wrap(part, width) or [''])
                footer = "Space reveal | Up/Down scroll | e edit | q back"
                if shown:
                    intervals = self.s.col.sched.describe_next_states(states)
                    footer = " | ".join(f"{i + 1} {name} {intervals[i]}" for i, name in enumerate(["Again", "Hard", "Good", "Easy"])) + " | q back"
                self.draw(self.s.col.decks.name(self.did), lines, footer, offset)
                key = self.win.getch()
                if key in (ord('q'), 27):
                    return
                if key == curses.KEY_DOWN:
                    offset = min(max(0, len(lines) - 1), offset + 1)
                if key == curses.KEY_UP:
                    offset = max(0, offset - 1)
                if key in (32, 10) and not shown:
                    shown, offset = True, 0
                elif shown and key in tuple(map(ord, "1234")):
                    self.s.answer(card, states, key - ord('0'))
                    break
                elif key == ord('e'):
                    self.edit(card.note())
                    break  # reload queue and scheduling state after editing

    def run(self):
        while True:
            account = self.s.profile.account()
            media = bool(self.s.profile.settings.get("media"))
            title = f"aktui | {self.s.profile.path.name} | {account.get('username', 'offline')} | media sync {'on' if media else 'off'}"
            action = self.menu(title, ["Open deck", "New deck", "AnkiWeb login", "Sync", f"Media file sync: {'ON' if media else 'OFF'} (toggle)", "Log out", "Quit"])
            if action is None or action == 6:
                return
            try:
                if action == 0:
                    decks = self.s.decks()
                    index = self.menu("Decks", [d.name for d in decks])
                    if index is not None:
                        self.did = decks[index].id
                        while True:
                            choice = self.menu(decks[index].name, ["Study", "Add note", "Browse / edit notes", "Back"])
                            if choice is None or choice == 3:
                                break
                            [self.study, self.edit, self.browse][choice]()
                elif action == 1:
                    name = self.prompt("New deck name (Parent::Child supported)")
                    if name and name.strip():
                        self.s.col.decks.id(name.strip())
                elif action == 2:
                    self.login()
                elif action == 3:
                    self.sync()
                elif action == 4:
                    self.s.profile.set_media(not media)
                    self.notice(
                        "Media file sync OFF. Existing local and AnkiWeb files are retained. "
                        "Note content and media references still sync."
                        if media else
                        "Media file sync ON for the next Sync. Anki will merge media file "
                        "changes, including eligible deletions. No transfer has started yet."
                    )
                elif action == 5:
                    self.s.profile.logout()
                    self.notice("Local login token removed. Cards and account binding retained.")
            except Exception as exc:
                self.notice(f"{type(exc).__name__}: {exc}")
