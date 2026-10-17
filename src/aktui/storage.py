"""Private profiles, atomic credential storage, and cross-process exclusion."""
from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import re
import tempfile


def atomic_json(path: Path, value: dict) -> None:
    fd, name = tempfile.mkstemp(prefix=".write-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            json.dump(value, out, ensure_ascii=False)
            out.flush()
            os.fsync(out.fileno())
        os.replace(name, path)
        directory = os.open(path.parent, os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(name):
            os.unlink(name)


class Profile:
    def __init__(self, root: Path, name: str):
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name):
            raise ValueError("Profile must contain 1-64 letters, digits, _ or -")
        os.umask(0o077)
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = root / name
        if self.path.is_symlink():
            raise ValueError("Profile must not be a symlink")
        self.path.mkdir(mode=0o700, exist_ok=True)
        self.path.chmod(0o700)
        self.lock = (self.path / "session.lock").open("a")
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.lock.close()
            raise RuntimeError("This profile is already open (TUI or scheduled sync)") from None
        self.auth_path = self.path / "account.json"
        self.config_path = self.path / "settings.json"
        self.settings = self._read(self.config_path)

    @staticmethod
    def _read(path: Path) -> dict:
        return json.loads(path.read_text()) if path.exists() else {}

    def account(self) -> dict:
        return self._read(self.auth_path)

    def save_account(self, value: dict) -> None:
        atomic_json(self.auth_path, value)

    def logout(self) -> None:
        # Keep account binding: logging out must not permit cross-account merging.
        account = self.account()
        self.save_account({"username": account.get("username", "")})

    def set_media(self, enabled: bool) -> None:
        self.settings["media"] = enabled
        atomic_json(self.config_path, self.settings)

    def close(self) -> None:
        self.lock.close()
