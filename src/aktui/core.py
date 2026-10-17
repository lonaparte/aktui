"""All collection writes and sync operations use the official Anki engine."""
from __future__ import annotations

from pathlib import Path
import time

from anki.collection import Collection
from anki.errors import SyncError, SyncErrorKind
from anki.sync_pb2 import SyncAuth, SyncCollectionResponse as SyncResult

from .storage import Profile


class Service:
    def __init__(self, profile: Profile):
        self.profile = profile
        self.col = Collection(str(profile.path / "collection.anki2"))
        self.pending = None

    def close(self):
        self.col.close()

    def login(self, username: str, password: str):
        username = username.strip()
        if not username or not password:
            raise ValueError("Email and password are required")
        bound = self.profile.account().get("username")
        if bound and bound.casefold() != username.casefold():
            raise ValueError("This profile belongs to another account. Use --profile NEW_NAME")
        auth = self.col.sync_login(username=username, password=password, endpoint=None)
        self.profile.save_account({"username": username, "hkey": auth.hkey,
                                   "endpoint": auth.endpoint})

    def auth(self):
        account = self.profile.account()
        if not account.get("hkey"):
            raise ValueError("Log in to AnkiWeb first")
        return SyncAuth(hkey=account["hkey"], endpoint=account.get("endpoint") or None,
                        io_timeout_secs=60)

    def backup(self, *, force: bool = True):
        folder = self.profile.path / "backups"
        folder.mkdir(mode=0o700, exist_ok=True)
        self.col.create_backup(backup_folder=str(folder), force=force, wait_for_completion=True)
        # Keep the three newest collection-only backups.
        files = sorted(folder.glob("*.colpkg"), key=lambda p: p.stat().st_mtime, reverse=True)
        for path in files[3:]:
            path.unlink()

    def sync(self):
        self.pending = None
        auth = self.auth()  # avoid backup work when not logged in
        # Respect Anki's backup interval for ordinary sync; full sync forces it.
        self.backup(force=False)
        try:
            out = self.col.sync_collection(auth, sync_media=False)
            if out.new_endpoint:
                account = self.profile.account()
                account["endpoint"] = out.new_endpoint
                self.profile.save_account(account)
            if out.required in (SyncResult.FULL_SYNC, SyncResult.FULL_UPLOAD, SyncResult.FULL_DOWNLOAD):
                self.pending = out
                return out
            if out.required != SyncResult.NO_CHANGES:
                raise RuntimeError(f"Unexpected sync result: {out.required}; retry sync")
            self.col._load_scheduler()  # also done by upstream aqt.sync
            self.sync_media()
            return out
        except SyncError as exc:
            if exc.kind == SyncErrorKind.AUTH:
                self.profile.logout()
            raise

    def full_sync(self, direction: str):
        if direction not in ("upload", "download"):
            raise ValueError("Invalid full-sync direction")
        out = self.pending
        if out is None:
            raise ValueError("Run sync before choosing a full-sync direction")
        upload = direction == "upload"
        if (upload and out.required == SyncResult.FULL_DOWNLOAD) or (
            not upload and out.required == SyncResult.FULL_UPLOAD
        ):
            raise ValueError("This sync direction is not available")
        auth = self.auth()
        self.backup()  # must succeed before replacing the collection
        self.col.close_for_full_sync()
        try:
            # None prevents implicit media transfer during full sync.
            self.col.full_upload_or_download(auth=auth, upload=upload, server_usn=None)
        except SyncError as exc:
            if exc.kind == SyncErrorKind.AUTH:
                self.profile.logout()
            raise
        finally:
            self.col.reopen(after_full_sync=True)
            self.pending = None
        self.sync_media()

    def sync_media(self):
        if not self.profile.settings.get("media", False):
            return
        try:
            self.col.sync_media(self.auth())
            while self.col.media_sync_status().active:
                time.sleep(0.2)
        except SyncError as exc:
            # Also handles token expiry after full sync.
            if exc.kind == SyncErrorKind.AUTH:
                self.profile.logout()
            raise

    def decks(self):
        return self.col.decks.all_names_and_ids()

    def select_deck(self, did: int):
        self.col.decks.select(did)

    def next_card(self):
        if not self.col.v3_scheduler():
            raise RuntimeError("Collection needs scheduler upgrade in desktop Anki first")
        queued = self.col.sched.get_queued_cards(fetch_limit=1)
        if not queued.cards:
            return None
        item = queued.cards[0]
        card = self.col.get_card(item.card.id)
        card.start_timer()
        return card, item.states

    def answer(self, card, states, rating: int):
        if rating not in range(1, 5):
            raise ValueError("Rating must be 1-4")
        from anki.scheduler.v3 import CardAnswer
        ratings = [CardAnswer.AGAIN, CardAnswer.HARD, CardAnswer.GOOD, CardAnswer.EASY]
        self.col.sched.answer_card(self.col.sched.build_answer(
            card=card, states=states, rating=ratings[rating - 1]))

    def note_page(self, did: int, after: int = 0, limit: int = 30, query: str = ""):
        # Bounded keyset pagination avoids loading all note IDs.
        ids = self.col.decks.deck_and_child_ids(did)
        escape = lambda s: s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        placeholders = ",".join("?" for _ in ids)
        return self.col.db.all(f"""
            select n.id, substr(n.sfld,1,200) from notes n
            where n.id > ? and n.flds like ? escape '\\'
            and exists (select 1 from cards c where c.nid=n.id
                        and c.did in ({placeholders}))
            order by n.id limit ?
        """, after, "%" + escape(query) + "%", *ids, min(limit, 100))
