"""Retain three distinct logical collection versions as Anki packages."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile

from anki._backend import RustBackend
from anki.collection import Collection, media_paths_from_col_path
from anki.decks_pb2 import Deck
from anki.errors import InvalidInput

from .storage import atomic_json


INDEX = ".versions.json"
TABLES = (
    "col", "notes", "cards", "revlog", "decks", "deck_config",
    "notetypes", "fields", "templates", "tags", "config",
)
VOLATILE_CONFIG = {
    "activeDecks", "curDeck", "curModel", "nextPos", "lastUnburied",
    "lastSync", "lastSyncTime", "lastMediaSync", "lastBackup", "lastBackupTime",
    "localOffset",
}


def _quoted(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def content_fingerprint(collection: Collection) -> str:
    """Ignore sync bookkeeping, clock changes, selection, and SQLite layout."""
    digest = hashlib.sha256(b"aktui-content-backup-v1\0")
    for table in TABLES:
        schema = collection.db.all(f"pragma table_info({_quoted(table)})")
        columns = [
            row[1] for row in schema
            if row[1].lower() not in {"usn", "mod", "mtime_secs"}
            and not (table == "tags" and row[1] == "collapsed")
            and not (table == "col" and row[1] not in {"id", "crt", "ver"})
        ]
        keys = [row[1] for row in sorted(schema, key=lambda row: row[5]) if row[5]]
        if not columns or not keys:
            raise RuntimeError(f"Unsupported backup table schema: {table}")
        positions = [columns.index(key) for key in keys]
        names = ",".join(map(_quoted, columns))
        order = ",".join(map(_quoted, keys))
        digest.update(json.dumps([table, columns]).encode("utf-8") + b"\n")
        last = None
        while True:
            where = "" if last is None else f" where ({order}) > ({','.join('?' for _ in keys)})"
            rows = collection.db.all(
                f"select {names} from {_quoted(table)}{where} order by {order} limit 256",
                *(last or ()),
            )
            if not rows:
                break
            last = tuple(rows[-1][position] for position in positions)
            for row in rows:
                values = dict(zip(columns, row))
                if table == "config":
                    if values["KEY"] in VOLATILE_CONFIG:
                        continue
                    values["val"] = json.loads(bytes(values["val"]))
                elif table == "decks":
                    common = Deck.Common()
                    common.ParseFromString(bytes(values["common"]))
                    for field in common.DESCRIPTOR.fields:
                        if field.name != "other":
                            common.ClearField(field.name)
                    values["common"] = list(common.SerializeToString(deterministic=True))
                digest.update(json.dumps(values, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode("utf-8") + b"\n")
    return digest.hexdigest()


def _package_fingerprint(package: Path) -> str:
    """Inspect old packages through Anki without changing the saved package."""
    with tempfile.TemporaryDirectory(prefix="aktui-backup-inspect-") as root:
        path = Path(root) / "collection.anki2"
        media_dir, media_db = media_paths_from_col_path(str(path))
        RustBackend().import_collection_package(
            col_path=str(path), backup_path=str(package),
            media_folder=media_dir, media_db=media_db,
        )
        restored = Collection(str(path))
        try:
            return content_fingerprint(restored)
        finally:
            restored.close()


def _entry(path: Path, fingerprint: str) -> dict:
    info = path.stat()
    return {"file": path.name, "content": fingerprint, "size": info.st_size, "mtime_ns": info.st_mtime_ns}


def create_backup(collection: Collection, folder: Path) -> Path:
    """Publish a complete package before pruning duplicate or older versions."""
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    fingerprint = content_fingerprint(collection)
    index = folder / INDEX
    cached = {}
    saved = None
    if index.is_file():
        try:
            saved = json.loads(index.read_text(encoding="utf-8"))
            if saved.get("version") == 1:
                cached = {entry["file"]: entry for entry in saved["backups"]}
        except (ValueError, KeyError, TypeError, AttributeError):
            cached = {}

    files = sorted(folder.glob("*.colpkg"), key=lambda path: (path.stat().st_mtime_ns, path.name), reverse=True)
    entries = []
    for path in files:
        info = path.stat()
        old = cached.get(path.name, {})
        if old.get("size") == info.st_size and old.get("mtime_ns") == info.st_mtime_ns and isinstance(old.get("content"), str):
            entries.append(old)
        else:
            entries.append(_entry(path, _package_fingerprint(path)))

    if not entries or entries[0]["content"] != fingerprint:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        final = folder / f"backup-{stamp}-{fingerprint[:12]}.colpkg"
        if final.exists():
            raise FileExistsError(f"Backup already exists: {final}")
        with tempfile.TemporaryDirectory(prefix=".pending-", dir=folder) as temporary:
            package = Path(temporary) / "collection.colpkg"
            try:
                collection.export_collection_package(str(package), include_media=False, legacy=False)
            finally:
                if collection.db is None:
                    try:
                        collection.reopen()
                    except InvalidInput as exc:
                        if str(exc) != "CollectionAlreadyOpen":
                            raise
                        # Export can fail before the Rust backend closes the DB.
                        collection.reopen(after_full_sync=True)
            with package.open("rb") as handle:
                os.fsync(handle.fileno())
            package.replace(final)
        entries.insert(0, _entry(final, fingerprint))

    keep = []
    seen = set()
    for entry in entries:
        if entry["content"] not in seen and len(keep) < 3:
            keep.append(entry)
            seen.add(entry["content"])
    updated = {"version": 1, "backups": keep}
    if saved != updated:
        atomic_json(index, updated)
    retained = {entry["file"] for entry in keep}
    for entry in entries:
        if entry["file"] not in retained:
            (folder / entry["file"]).unlink()
    return folder / keep[0]["file"]
