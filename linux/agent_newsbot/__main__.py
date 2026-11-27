from __future__ import annotations

import argparse
import json
import logging
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agent-newsbot")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("serve", help="Run the localhost API and periodic fetch loop")
    sub.add_parser("fetch", help="Run one fresh fetch and print JSON")
    sub.add_parser("status", help="Show last snapshot counts")
    library = sub.add_parser("library", help="Search retained research stories")
    for name in ("q", "entity", "desk", "source", "provider", "status", "sort", "limit", "offset"):
        library.add_argument("--" + name)
    library.add_argument("--bookmarked", action="store_true")
    library.add_argument("--include-muted", action="store_true")
    editorial = sub.add_parser("editorial", help="Read editorial state or save an explicit revision-bound change file")
    editorial.add_argument("id"); editorial.add_argument("--changes")
    preferences = sub.add_parser("preferences", help="List watchlists/mutes or apply a JSON preference change")
    preferences.add_argument("--changes")
    consumers = sub.add_parser("consumers", help="Show backlog/leases or apply an explicit consumer administration command")
    consumers.add_argument("--changes")
    export = sub.add_parser("export-research", help="Export saved drafts, provenance and VideoBot source URLs")
    export.add_argument("id"); export.add_argument("--destination", required=True)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if args.cmd == "serve":
        from .server import serve

        serve()
        return 0

    from .config import SHARED
    if args.cmd == "fetch":
        from .server import STATE
        snapshot = STATE.refresh()
        json.dump({"total_articles": snapshot.get("total_articles"), "desks": snapshot.get("desks")}, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0

    if args.cmd in ("library", "editorial", "preferences", "export-research", "consumers"):
        from pathlib import Path
        import sqlite3
        from .config import HOME
        from .stories import StoryStore
        from .library import write_package
        if not (HOME / "stories.sqlite").is_file():
            print("no story journal yet; fetch first", file=sys.stderr); return 1
        try:
            journal = StoryStore(HOME / "stories.sqlite")
            change = None
            if getattr(args, "changes", None):
                path = Path(args.changes)
                if path.stat().st_size > 65536: raise ValueError("change file exceeds 64 KiB")
                change = json.loads(path.read_text())
                if not isinstance(change, dict): raise ValueError("change file must be a JSON object")
            if args.cmd == "library":
                query = {k: str(v) for k, v in vars(args).items() if k != "cmd" and v is not None and not isinstance(v, bool)}
                for key in ("bookmarked", "include_muted"):
                    if getattr(args, key): query[key] = "true"
                result = journal.library(query)
            elif args.cmd == "editorial":
                result = journal.save_editorial(args.id, change) if change is not None else journal.editorial(args.id)
                if result is None: raise LookupError("story not found")
            elif args.cmd == "preferences":
                result = journal.library_settings(change.get("kind"), change.get("name"), change.get("value"), write=True) if change is not None else journal.library_settings()
            elif args.cmd == "consumers":
                result = journal.consumer_change(change) if change is not None else journal.consumer_status()
            else:
                result = {"folder": str(write_package(journal.research_export(args.id), args.destination))}
            json.dump(result, sys.stdout, indent=2); sys.stdout.write("\n"); return 0
        except (ValueError, LookupError, OSError, sqlite3.Error) as exc:
            print(str(exc), file=sys.stderr); return 1

    latest = SHARED / "latest.json"
    if latest.exists():
        data = json.loads(latest.read_text())
        json.dump(
            {
                "fetched_at": data.get("fetched_at"),
                "total_articles": data.get("total_articles"),
                "desks": data.get("desks"),
            },
            sys.stdout,
            indent=2,
        )
        sys.stdout.write("\n")
        return 0
    print("no snapshot yet", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
