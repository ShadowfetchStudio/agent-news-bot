from __future__ import annotations

import argparse
import json
import logging
import sys
import os


def main(argv: list[str] | None = None) -> int:
    # Recovery remains available when runtime configuration or third-party
    # dependencies are broken. Explicit paths avoid activating the current home.
    recovery_commands = {"backup", "verify-backup", "restore-research"}
    raw = sys.argv[1:] if argv is None else argv
    if raw and raw[0] in recovery_commands:
        from .recovery import main as recovery_main
        return recovery_main(raw)
    maintenance_commands = {"storage-status", "retention-plan", "prune-history"}
    if raw and raw[0] in maintenance_commands:
        from .maintenance import main as maintenance_main
        return maintenance_main(raw)
    parser = argparse.ArgumentParser(prog="agent-newsbot")
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in sorted(recovery_commands | maintenance_commands):
        sub.add_parser(name, help=("Explicit research maintenance; use " if name in maintenance_commands else "Research recovery into a new folder; use ") + name + " --help")
    sub.add_parser("serve", help="Run the localhost API and periodic fetch loop")
    sub.add_parser("fetch", help="Run one fresh fetch and print JSON")
    sub.add_parser("test-worldmonitor", help="Test configured World Monitor access without saving stories (reads one digest)")
    sub.add_parser("source-health", help="Read persistent source health and next-retry times without fetching")
    sub.add_parser("status", help="Show last snapshot counts")
    library = sub.add_parser("library", help="Search retained research stories")
    for name in ("q", "entity", "desk", "source", "provider", "status", "sort", "limit", "offset"):
        library.add_argument("--" + name)
    library.add_argument("--bookmarked", action="store_true")
    library.add_argument("--include-muted", action="store_true")
    library.add_argument("--include-merged", action="store_true")
    brief = sub.add_parser("brief", help="Read a source-linked editorial brief from retained research; no fetching or model call")
    brief.add_argument("id")
    editorial = sub.add_parser("editorial", help="Read editorial state or save an explicit revision-bound change file")
    editorial.add_argument("id"); editorial.add_argument("--changes")
    preferences = sub.add_parser("preferences", help="List watchlists/mutes or apply a JSON preference change")
    preferences.add_argument("--changes")
    consumers = sub.add_parser("consumers", help="Show backlog/leases or apply an explicit consumer administration command")
    consumers.add_argument("--changes")
    repairs = sub.add_parser("repairs", help="Apply a grouping repair through the running localhost service, or read a saved receipt")
    repair_choice = repairs.add_mutually_exclusive_group(required=True)
    repair_choice.add_argument("--changes"); repair_choice.add_argument("--receipt")
    export = sub.add_parser("export-research", help="Export saved drafts, provenance and VideoBot source URLs")
    export.add_argument("id"); export.add_argument("--destination", required=True)
    args = parser.parse_args(argv)
    os.umask(0o077)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if args.cmd == "serve":
        from .server import serve

        serve()
        return 0

    from .config import SHARED
    if args.cmd == "source-health":
        from .config import HOME
        from .source_health import SourceHealth
        import sqlite3
        try:
            json.dump({"sources":SourceHealth(HOME/"source-health.sqlite").list()},sys.stdout,indent=2); sys.stdout.write("\n")
            return 0
        except (OSError,sqlite3.Error,ValueError):
            print("Source health storage unavailable",file=sys.stderr); return 1
    if args.cmd == "test-worldmonitor":
        from .worldmonitor import test_connection
        result = test_connection(os.getenv("AGENT_NEWSBOT_WORLDMONITOR_URL", "http://127.0.0.1:3000"),
                                 os.getenv("AGENT_NEWSBOT_WORLDMONITOR_VARIANT", "full"),
                                 os.getenv("AGENT_NEWSBOT_WORLDMONITOR_KEY", ""))
        json.dump(result, sys.stdout, indent=2); sys.stdout.write("\n")
        return 0 if result["succeeded"] else 1
    if args.cmd == "fetch":
        from .server import STATE
        snapshot = STATE.refresh()
        json.dump({"total_articles": snapshot.get("total_articles"), "desks": snapshot.get("desks")}, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0

    if args.cmd in ("library", "brief", "editorial", "preferences", "export-research", "consumers", "repairs"):
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
                for key in ("bookmarked", "include_muted", "include_merged"):
                    if getattr(args, key): query[key] = "true"
                result = journal.library(query)
            elif args.cmd == "repairs":
                if args.receipt:
                    result = journal.repair_receipt(args.receipt)
                    if result is None: raise LookupError("repair not found")
                else:
                    # The running service updates its visible batch as part of the repair.
                    # No direct offline mutation can leave a live server with stale groups.
                    import http.client
                    from .config import PORT
                    from .group_repairs import validate_request
                    validate_request(change)
                    token = (HOME / "export" / "bridge.token").read_text().strip()
                    connection = http.client.HTTPConnection("127.0.0.1", PORT, timeout=10)
                    try:
                        connection.request("POST", "/v1/library/repairs", json.dumps(change),
                                           {"Authorization": "Bearer " + token, "Content-Type": "application/json"})
                        response = connection.getresponse()
                        raw = response.read(4 * 1024 * 1024 + 1)
                        if len(raw) > 4 * 1024 * 1024: raise ValueError("repair response too large; inspect the saved receipt")
                        result = json.loads(raw)
                        if response.status != 200: raise ValueError(result.get("error", "repair request failed"))
                    except (OSError, http.client.HTTPException) as exc:
                        raise ValueError("Repair service unavailable or connection interrupted. Start the service, check the operation receipt, then retry the SAME change file.") from exc
                    finally: connection.close()
            elif args.cmd == "brief":
                result = journal.research_brief(args.id)
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
