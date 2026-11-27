"""Standard-library-only research recovery. Never replaces a live application home.

This module can also be run as a standalone file on a Mac with Python 3.10+.
SQLite's backup API includes committed WAL data. Each database has its own
snapshot boundary; this is not a machine/configuration/credential backup.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import stat
import sys
import time
from contextlib import closing
from datetime import datetime, timezone

FORMAT = "agent-newsbot-research-backup"
VERSION = 1
DEFAULT_LIMIT = 512 * 1024 * 1024
RESERVE = 64 * 1024 * 1024
SCHEMAS = {
    "stories.sqlite": {
        "stories": "id payload updated", "members": "url story_id",
        "events": "seq story_id payload", "consumers": "name cursor offered",
        "identity_aliases": "alias story_id", "migrations": "name",
        "editorial": "story_id payload", "library_preferences": "kind name payload",
        "consumer_meta": "name generation last_ack lease_token lease_worker lease_until",
        "group_repairs": "id request result created", "sqlite_sequence": "name seq",
    },
    "seen.sqlite": {
        "seen": "id url title desk seen_at", "quotes": "symbol payload updated_at",
        "feed_documents": "url body observed_at", "feed_cache": "url etag last_modified updated_at",
    },
    "source-health.sqlite": {"source_health": "id payload"},
}
OPTIONAL_SCHEMAS = {"stories.sqlite": {"journal_retention": "id payload", "watch_clock": "id value", "watch_alerts": "name payload", "watch_matches": "alert story_id seq revision subscription_version seen_seq"}}
EXCLUDED = ["bridge credentials", "Keychain secrets", "application preferences and Linux config",
            "exported files and snapshots", "agent inboxes/outboxes", "application binaries"]


class RecoveryError(ValueError):
    pass


def regular(path: Path, limit: int) -> int:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise RecoveryError(f"Expected an unlinked regular file: {path.name}")
    if info.st_size > limit:
        raise RecoveryError(f"File exceeds the configured byte limit: {path.name}")
    return info.st_size


def directory(path: Path):
    if not stat.S_ISDIR(path.lstat().st_mode):
        raise RecoveryError("Expected a real directory, not a link")


def private_file(path: Path):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    return os.fdopen(fd, "wb")


def digest(path: Path, limit: int, deadline=float("inf")):
    regular(path, limit)
    value = hashlib.sha256()
    total = 0
    with path.open("rb") as handle:
        while block := handle.read(1024 * 1024):
            total += len(block)
            if total > limit or time.monotonic() >= deadline:
                raise RecoveryError("File grew beyond the configured byte/time limit")
            value.update(block)
    return total, value.hexdigest()


def connection(path: Path, *, immutable=False):
    db = sqlite3.connect(path.absolute().as_uri() + "?mode=ro" + ("&immutable=1" if immutable else ""), uri=True, timeout=2)
    db.execute("PRAGMA trusted_schema=OFF")
    db.execute("PRAGMA query_only=ON")
    return db


def inspect(path: Path, name: str, deadline: float):
    with closing(connection(path, immutable=True)) as db:
        db.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
        if db.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
            raise RecoveryError(f"SQLite integrity check failed: {name}")
        if db.execute("SELECT 1 FROM sqlite_master WHERE type IN ('view','trigger') LIMIT 1").fetchone():
            raise RecoveryError("Unexpected executable database schema")
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        optional = OPTIONAL_SCHEMAS.get(name, {})
        if not set(SCHEMAS[name]) <= tables or tables - set(SCHEMAS[name]) - set(optional):
            raise RecoveryError(f"Unsupported database schema: {name}")
        counts = {}
        for table, fields in {**SCHEMAS[name], **{k:v for k,v in optional.items() if k in tables}}.items():
            if [r[1] for r in db.execute(f'PRAGMA table_info("{table}")')] != fields.split():
                raise RecoveryError(f"Unsupported table schema: {table}")
            counts[table] = db.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]
            if table != "quotes":
                for field in {"payload", "request", "result"} & set(fields.split()):
                    # Keep untrusted historical text as evidence, but require a
                    # structurally readable object before calling recovery valid.
                    invalid = f'NOT json_valid("{field}")' if table == "library_preferences" else f'CASE WHEN json_valid("{field}") THEN json_type("{field}")!=\'object\' ELSE 1 END'
                    if db.execute(f'SELECT 1 FROM "{table}" WHERE {invalid} LIMIT 1').fetchone():
                        raise RecoveryError(f"Invalid JSON record: {table}")
        if name == "stories.sqlite":
            if "journal_retention" in tables:
                rows = db.execute("SELECT id,payload FROM journal_retention").fetchall()
                if len(rows) > 1 or any(row[0] != 1 for row in rows):
                    raise RecoveryError("Invalid retention state")
                if rows:
                    state = json.loads(rows[0][1])
                    floor = state.get("pruned_through")
                    latest = db.execute("SELECT coalesce(max(seq),0) FROM events").fetchone()[0]
                    if type(floor) is not int or not 0 <= floor <= latest or db.execute("SELECT 1 FROM consumers WHERE cursor<?", (floor,)).fetchone():
                        raise RecoveryError("Invalid retention boundary or consumer checkpoint")
            if "watch_alerts" in tables:
                if "watch_clock" not in tables:
                    raise RecoveryError("Missing saved-search alert clock")
                rows = db.execute("SELECT id,value FROM watch_clock").fetchall()
                subscriptions=db.execute("SELECT name,payload FROM watch_alerts").fetchall()
                if len(subscriptions)>32:
                    raise RecoveryError("Too many saved-search subscriptions")
                for alert_name,raw in subscriptions:
                    alert=json.loads(raw)
                    valid_filters=alert.get("filters")
                    if not isinstance(alert_name,str) or not 1<=len(alert_name)<=80 or type(alert.get("version")) is not int or alert["version"]<1 or type(alert.get("enabled")) is not bool or type(alert.get("start_seq")) is not int or alert["start_seq"]<0 or not isinstance(valid_filters,dict) or set(valid_filters)-{"q","entity","desk","source","provider","status","bookmarked","include_muted"} or any(not isinstance(v,str) or len(v)>300 for v in valid_filters.values()):
                        raise RecoveryError("Invalid saved-search subscription")
                    if valid_filters.get("status", "") not in ("", "unreviewed", "draft", "needs_review", "approved", "rejected") or any(valid_filters.get(k,"false") not in ("true","false") for k in ("bookmarked","include_muted")):
                        raise RecoveryError("Invalid saved-search filter value")
                maximum = db.execute("SELECT coalesce(max(json_extract(payload,'$.version')),0) FROM watch_alerts").fetchone()[0]
                if len(rows)>1 or (rows and (rows[0][0]!=1 or type(rows[0][1]) is not int or rows[0][1]<maximum)) or (not rows and maximum):
                    raise RecoveryError("Invalid saved-search alert clock")
            if "watch_matches" in tables:
                if "watch_alerts" not in tables or db.execute("SELECT 1 FROM watch_matches m LEFT JOIN watch_alerts a ON a.name=m.alert LEFT JOIN stories s ON s.id=m.story_id WHERE a.name IS NULL OR s.id IS NULL OR typeof(m.seq)!='integer' OR m.seq<1 OR typeof(m.seen_seq)!='integer' OR m.seen_seq<0 OR m.seen_seq>m.seq OR typeof(m.revision)!='integer' OR m.revision<1 OR typeof(m.subscription_version)!='integer' OR m.subscription_version<1 OR m.subscription_version>(SELECT value FROM watch_clock WHERE id=1) OR (m.seen_seq<m.seq AND NOT EXISTS(SELECT 1 FROM events e WHERE e.seq=m.seq AND e.story_id=m.story_id AND json_extract(e.payload,'$.revision')=m.revision)) LIMIT 1").fetchone():
                    raise RecoveryError("Invalid saved-search alert reference")
            # Referential checks that SQLite foreign keys cannot cover in this schema.
            for table in ("members", "events", "identity_aliases", "editorial"):
                if db.execute(f'SELECT 1 FROM "{table}" r LEFT JOIN stories s ON s.id=r.story_id WHERE s.id IS NULL LIMIT 1').fetchone():
                    raise RecoveryError(f"Orphaned research record: {table}")
            if db.execute("SELECT 1 FROM consumers WHERE typeof(cursor)!='integer' OR typeof(offered)!='integer' OR cursor<0 OR offered<cursor OR offered>coalesce((SELECT max(seq) FROM events),0) LIMIT 1").fetchone():
                raise RecoveryError("Invalid consumer checkpoint")
        return counts


def snapshot(source: Path, target: Path, limit: int, deadline: float):
    regular(source, limit)
    # Fail on linked sidecars rather than letting SQLite follow them.
    for suffix in ("-wal", "-shm", "-journal"):
        sidecar = Path(str(source) + suffix)
        if os.path.lexists(sidecar):
            regular(sidecar, limit)
    with private_file(target):
        pass
    with closing(connection(source)) as src, closing(sqlite3.connect(target)) as dest:
        def progress(status, remaining, pages):
            if time.monotonic() >= deadline:
                raise RecoveryError("Backup deadline reached; no complete backup was published")
            if target.stat().st_size > limit:
                raise RecoveryError("Snapshot exceeds the configured byte limit")
            if shutil.disk_usage(target.parent).free < RESERVE:
                raise RecoveryError("Insufficient free space; 64 MiB reserve required")
        src.backup(dest, pages=128, progress=progress, sleep=0.05)
        dest.execute("PRAGMA journal_mode=DELETE")
    regular(target, limit)
    with target.open("rb") as handle:
        os.fsync(handle.fileno())


def write_json(path, value):
    with private_file(path) as handle:
        handle.write((json.dumps(value, indent=2, sort_keys=True) + "\n").encode())
        handle.flush()
        os.fsync(handle.fileno())


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def bounds(max_bytes, timeout):
    if type(max_bytes) is not int or not 1024 * 1024 <= max_bytes <= 8 * 1024**3:
        raise RecoveryError("Byte limit must be between 1 MiB and 8 GiB")
    if not isinstance(timeout, (int, float)) or not 1 <= timeout <= 600:
        raise RecoveryError("Timeout must be between 1 and 600 seconds")
    return time.monotonic() + timeout


def backup(home, destination, *, max_bytes=DEFAULT_LIMIT, timeout=60):
    deadline = bounds(max_bytes, timeout)
    home, destination = Path(home).absolute(), Path(destination).absolute()
    directory(home)
    if any(os.path.lexists(home / marker) for marker in ("INCOMPLETE.json", "RECOVERY_INCOMPLETE.json")):
        raise RecoveryError("Cannot back up an incomplete recovery home")
    if not (home / "stories.sqlite").exists():
        raise RecoveryError("No research journal exists in the selected home")
    names = [name for name in SCHEMAS if os.path.lexists(home / name)]
    for name in names:
        regular(home / name, max_bytes)
    directory(destination.parent)
    destination.mkdir(mode=0o700)  # Exclusive reservation; never overwrites even an empty folder.
    write_json(destination / "INCOMPLETE.json", {"operation": "backup", "format": FORMAT})
    files, remaining = {}, max_bytes
    for name in names:
        target = destination / name
        snapshot(home / name, target, remaining, deadline)
        counts = inspect(target, name, deadline)
        size, sha = digest(target, remaining, deadline)
        remaining -= size
        files[name] = dict(bytes=size, sha256=sha, counts=counts)
    manifest = dict(format=FORMAT, version=VERSION, created_at=datetime.now(timezone.utc).isoformat(),
                    consistency="per-database SQLite snapshots", files=files, excluded=EXCLUDED)
    write_json(destination / "manifest.json", manifest)
    (destination / "INCOMPLETE.json").unlink()
    sync_directory(destination)
    sync_directory(destination.parent)
    return dict(folder=str(destination), **manifest)


def verify(archive, *, max_bytes=DEFAULT_LIMIT, timeout=60):
    deadline = bounds(max_bytes, timeout)
    archive = Path(archive).absolute()
    directory(archive)
    regular(archive / "manifest.json", 65536)
    manifest = json.loads((archive / "manifest.json").read_bytes())
    if not isinstance(manifest, dict) or manifest.get("format") != FORMAT or type(manifest.get("version")) is not int or manifest["version"] != VERSION:
        raise RecoveryError("Unsupported backup format")
    files = manifest.get("files")
    if not isinstance(files, dict) or "stories.sqlite" not in files or set(files) - set(SCHEMAS):
        raise RecoveryError("Invalid backup file list")
    if {p.name for p in archive.iterdir()} != set(files) | {"manifest.json"}:
        raise RecoveryError("Unexpected files or incomplete backup")
    remaining = max_bytes
    for name, entry in files.items():
        if not isinstance(entry, dict) or type(entry.get("bytes")) is not int or entry["bytes"] <= 0 or not isinstance(entry.get("sha256"), str):
            raise RecoveryError("Invalid backup entry")
        size, sha = digest(archive / name, remaining, deadline)
        remaining -= size
        if size != entry["bytes"] or sha != entry["sha256"]:
            raise RecoveryError(f"Backup checksum mismatch: {name}")
        if inspect(archive / name, name, deadline) != entry.get("counts"):
            raise RecoveryError(f"Backup table counts mismatch: {name}")
    return manifest


def restore(archive, destination, *, max_bytes=DEFAULT_LIMIT, timeout=60):
    started = time.monotonic()
    manifest = verify(archive, max_bytes=max_bytes, timeout=timeout)
    archive, destination = Path(archive).absolute(), Path(destination).absolute()
    directory(destination.parent)
    destination.mkdir(mode=0o700)
    write_json(destination / "RECOVERY_INCOMPLETE.json", {"operation": "restore", "format": FORMAT})
    deadline = started + timeout
    for name, entry in manifest["files"].items():
        with (archive / name).open("rb") as source, private_file(destination / name) as target:
            total, value = 0, hashlib.sha256()
            while block := source.read(1024 * 1024):
                total += len(block)
                if total > entry["bytes"] or time.monotonic() >= deadline:
                    raise RecoveryError("Restore changed or exceeded its size/time bound")
                if shutil.disk_usage(destination).free < RESERVE:
                    raise RecoveryError("Insufficient free space; 64 MiB reserve required")
                target.write(block)
                value.update(block)
            target.flush()
            os.fsync(target.fileno())
        if total != entry["bytes"] or value.hexdigest() != entry["sha256"]:
            raise RecoveryError("Backup changed during restore")
        inspect(destination / name, name, deadline)
    # A recovered library starts a new service session. Do not restore worker ownership.
    with closing(sqlite3.connect(destination / "stories.sqlite")) as db:
        with db:
            db.execute("INSERT OR IGNORE INTO consumer_meta(name) SELECT name FROM consumers")
            db.execute("UPDATE consumer_meta SET generation=generation+1,lease_token='',lease_worker='',lease_until=0")
            db.execute("UPDATE consumers SET offered=cursor")
    health = destination / "source-health.sqlite"
    if health.exists():
        with closing(sqlite3.connect(health)) as db:
            with db:
                for key, payload in db.execute("SELECT id,payload FROM source_health").fetchall():
                    row = json.loads(payload)
                    row["lease"], row["lease_until"] = "", 0
                    db.execute("UPDATE source_health SET payload=? WHERE id=?", (json.dumps(row), key))
    restored_files = {}
    for name in manifest["files"]:
        size, sha = digest(destination / name, max_bytes, deadline)
        restored_files[name] = dict(bytes=size, sha256=sha, counts=inspect(destination / name, name, deadline))
    report = dict(format=FORMAT, version=VERSION, restored_at=datetime.now(timezone.utc).isoformat(),
                  restored_files=restored_files,
                  source_manifest=manifest, consumer_leases_cleared=True, source_probes_cleared=True,
                  consumer_warning="Cursors are from the backup. Reconcile downstream side effects before reconnecting; events after the backup are absent.")
    write_json(destination / "RECOVERY.json", report)
    (destination / "RECOVERY_INCOMPLETE.json").unlink()
    sync_directory(destination)
    sync_directory(destination.parent)
    return dict(folder=str(destination), **report)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("backup")
    create.add_argument("--home", required=True)
    create.add_argument("--destination", required=True)
    check = sub.add_parser("verify-backup")
    check.add_argument("--backup", required=True)
    recover = sub.add_parser("restore-research")
    recover.add_argument("--backup", required=True)
    recover.add_argument("--destination", required=True)
    for command in (create, check, recover):
        command.add_argument("--max-bytes", type=int, default=DEFAULT_LIMIT)
        command.add_argument("--timeout", type=float, default=60)
    args = parser.parse_args(argv)
    os.umask(0o077)
    try:
        options = dict(max_bytes=args.max_bytes, timeout=args.timeout)
        if args.command == "backup":
            result = backup(args.home, args.destination, **options)
        elif args.command == "verify-backup":
            result = verify(args.backup, **options)
        else:
            result = restore(args.backup, args.destination, **options)
        json.dump(result, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0
    except (OSError, ValueError, sqlite3.Error) as exc:
        print(f"Recovery failed: {exc}. Existing libraries are unchanged. Any incomplete destination must not be activated.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
