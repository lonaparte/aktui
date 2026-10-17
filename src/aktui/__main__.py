from __future__ import annotations

import argparse
import curses
import getpass
import locale
import os
from pathlib import Path
import sys

from .storage import Profile


def main():
    parser = argparse.ArgumentParser(description="Anki terminal client")
    parser.add_argument("--data-dir", type=Path, default=Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share"))) / "aktui")
    parser.add_argument("--profile", default="default")
    parser.add_argument("command", nargs="?", default="tui", choices=["tui", "login", "logout", "sync", "media-on", "media-off"])
    args = parser.parse_args()
    profile = None
    service = None
    try:
        profile = Profile(args.data_dir, args.profile)
        if args.command == "logout":
            profile.logout()
            print("Local login token removed; collection retained.")
            return 0
        if args.command.startswith("media-"):
            profile.set_media(args.command == "media-on")
            print("Media file sync ON for the next sync; Anki will merge file changes, including eligible deletions."
                  if args.command == "media-on" else
                  "Media file sync OFF; existing files are retained and note references still sync.")
            return 0
        from .core import Service
        service = Service(profile)
        if args.command == "login":
            service.login(input("AnkiWeb email: "), getpass.getpass("Password: "))
            print("Signed in. Run aktui to synchronize.")
        elif args.command == "sync":
            out = service.sync()
            if out.server_message:
                from .render import safe_text
                print(safe_text(out.server_message))
            if service.pending:
                print("Full sync needs a direction. Open the TUI to choose; nothing was overwritten.", file=sys.stderr)
                return 2
            print("Collection sync complete." + (" Media synced." if profile.settings.get("media") else " Media disabled."))
        else:
            locale.setlocale(locale.LC_ALL, "")
            from .ui import UI
            curses.wrapper(lambda screen: UI(screen, service).run())
        return 0
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130
    except Exception as exc:
        from .render import safe_text
        print(f"Error: {safe_text(str(exc))}", file=sys.stderr)
        return 1
    finally:
        try:
            if service:
                service.close()
        finally:
            if profile:
                profile.close()


if __name__ == "__main__":
    sys.exit(main())
