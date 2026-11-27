"""Explicit, backup-gated research history maintenance (stdlib only).

No automatic deletion and no network requests. Works against either runtime's
journal. A saved plan is re-evaluated inside the same transaction as the delete.
"""
from __future__ import annotations
import argparse
from contextlib import closing, contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import time
import uuid

try:
    from . import recovery
    from .retention import retention_state
except ImportError:  # Packaged native-app helper, python3 maintenance.py
    import recovery
    from retention import retention_state

FORMAT = 'agent-newsbot-retention-plan'
PLAN_LIMIT = 2 * 1024 * 1024
PAYLOAD_LIMIT = 32 * 1024 * 1024


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def sha(value):
    return hashlib.sha256(value).hexdigest()


def cutoff(value):
    if not isinstance(value, str):
        raise ValueError('before requires an ISO-8601 timestamp with timezone')
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('before requires timezone')
    return parsed.astimezone(timezone.utc).isoformat()


@contextmanager
def journal(home, *, write=False, timeout=60, max_bytes=recovery.DEFAULT_LIMIT):
    deadline = recovery.bounds(max_bytes, timeout)
    home = Path(home).absolute()
    recovery.directory(home)
    if any(os.path.lexists(home / marker) for marker in ('INCOMPLETE.json', 'RECOVERY_INCOMPLETE.json')):
        raise ValueError('incomplete recovery home')
    path = home / 'stories.sqlite'
    recovery.regular(path, max_bytes)
    for suffix in ('-wal', '-shm', '-journal'):
        sidecar = Path(str(path) + suffix)
        if os.path.lexists(sidecar):
            recovery.regular(sidecar, max_bytes)
    with closing(sqlite3.connect(path.as_uri() + ('?mode=rw' if write else '?mode=ro'), uri=True,
                                 timeout=min(2, timeout))) as db:
        db.execute('PRAGMA trusted_schema=OFF')
        db.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
        if db.execute("SELECT 1 FROM sqlite_master WHERE type IN ('view','trigger') LIMIT 1").fetchone():
            raise ValueError('unexpected executable database schema')
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='journal_retention'").fetchone():
            raise ValueError('open this journal with the current app before maintenance')
        if not write:
            db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
        try:
            yield db, path, deadline
            if time.monotonic() >= deadline:
                raise ValueError('maintenance deadline reached')
            db.commit()
        except BaseException:
            db.rollback()
            raise


def storage_status(home, **bounds):
    with journal(home, **bounds) as (db, path, _):
        page_size = db.execute('PRAGMA page_size').fetchone()[0]
        pages = db.execute('PRAGMA page_count').fetchone()[0]
        reusable = db.execute('PRAGMA freelist_count').fetchone()[0]
        consumers = [dict(consumer=name, cursor=cursor, lag=lag) for name, cursor, lag in db.execute(
            'SELECT name,cursor,(SELECT count(*) FROM events WHERE seq>cursor) FROM consumers ORDER BY name')]
        return dict(stories=db.execute('SELECT count(*) FROM stories').fetchone()[0],
                    events=db.execute('SELECT count(*) FROM events').fetchone()[0],
                    database_bytes=pages * page_size, reusable_bytes=reusable * page_size,
                    wal_bytes=Path(str(path) + '-wal').stat().st_size if Path(str(path) + '-wal').exists() else 0,
                    free_bytes=shutil.disk_usage(path.parent).free, consumers=consumers,
                    retention=retention_state(db), automatic_pruning=False)


def selection(db, before, limit):
    # No registered consumers is not consent to discard their future backlog.
    boundary = db.execute('SELECT min(cursor) FROM consumers').fetchone()[0]
    if boundary is None:
        boundary = 0
    alert_guard = 'AND NOT EXISTS (SELECT 1 FROM watch_matches w WHERE w.seq=e.seq AND w.seen_seq<w.seq)' if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='watch_matches'").fetchone() else ''
    rows = db.execute('''SELECT e.seq,e.story_id,e.payload FROM events e
      LEFT JOIN editorial d ON d.story_id=e.story_id
      WHERE e.seq<=? AND julianday(json_extract(e.payload,'$.updated_at'))<julianday(?)
      AND coalesce(json_extract(e.payload,'$.change'),'')!='group_repaired'
      AND e.seq NOT IN (SELECT seq FROM events WHERE story_id=e.story_id ORDER BY seq DESC LIMIT 2)
      AND coalesce(json_extract(d.payload,'$.bookmarked'),0)!=1
      AND NOT (json_extract(e.payload,'$.revision') IS json_extract(d.payload,'$.source_revision'))
      AND NOT (json_extract(e.payload,'$.revision') IS json_extract(d.payload,'$.approved_revision'))
      AND NOT EXISTS (SELECT 1 FROM json_each(d.payload,'$.platforms') p
        WHERE json_extract(p.value,'$.approved_revision')=json_extract(e.payload,'$.revision'))
      ''' + alert_guard + ' ORDER BY e.seq LIMIT ?', (boundary, before, limit))
    total = 0
    entries = []
    for seq, story_id, raw in rows:
        data = raw.encode()
        total += len(data)
        if total > PAYLOAD_LIMIT:
            raise ValueError('eligible payload exceeds 32 MiB; use a smaller --limit')
        entries.append(dict(seq=seq, story_id=story_id, sha256=sha(data)))
    return dict(acknowledged_through=boundary, entries=entries, payload_bytes=total,
                pruned_through=retention_state(db)['pruned_through'])


def plan(home, before, *, limit=1000, **bounds):
    before = cutoff(before)
    if type(limit) is not int or not 1 <= limit <= 10000:
        raise ValueError('limit must be 1-10000')
    with journal(home, **bounds) as (db, _, _):
        result = dict(format=FORMAT, version=1, operation_id=str(uuid.uuid4()), before=before, limit=limit,
                      selection=selection(db, before, limit))
    result['sha256'] = sha(canonical(result))
    return result


def validate_plan(value):
    if not isinstance(value, dict) or set(value) != {'format','version','operation_id','before','limit','selection','sha256'}:
        raise ValueError('invalid retention plan')
    if value['format'] != FORMAT or type(value['version']) is not int or value['version'] != 1:
        raise ValueError('unsupported retention plan')
    if not isinstance(value['operation_id'], str) or str(uuid.UUID(value['operation_id'])) != value['operation_id']:
        raise ValueError('invalid operation ID')
    if cutoff(value['before']) != value['before'] or type(value['limit']) is not int or not 1 <= value['limit'] <= 10000:
        raise ValueError('invalid plan bounds')
    if len(canonical(value)) > PLAN_LIMIT or sha(canonical({k:v for k,v in value.items() if k != 'sha256'})) != value['sha256']:
        raise ValueError('retention plan checksum mismatch')
    if not isinstance(value['selection'], dict) or not isinstance(value['selection'].get('entries'), list):
        raise ValueError('invalid selection')
    return value


def prune(home, value, archive, *, max_bytes=recovery.DEFAULT_LIMIT, timeout=60):
    value = validate_plan(value)
    started = time.monotonic()
    manifest = recovery.verify(archive, max_bytes=max_bytes, timeout=timeout)
    archive = Path(archive).absolute()
    backup = archive / 'stories.sqlite'
    backup_sha = manifest['files']['stories.sqlite']['sha256']
    remaining = timeout - (time.monotonic() - started)
    if remaining < 1:
        raise ValueError('maintenance deadline reached before write transaction')
    with journal(home, write=True, timeout=remaining, max_bytes=max_bytes) as (db, path, deadline):
        if path.samefile(backup):
            raise ValueError('backup must be separate from the active journal')
        state = retention_state(db)
        for receipt in state.get('receipts', []):
            if receipt['operation_id'] == value['operation_id']:
                if receipt['plan_sha256'] != value['sha256']:
                    raise ValueError('operation ID was used for a different plan')
                return dict(receipt, replayed=True)
        current = selection(db, value['before'], value['limit'])
        if current != value['selection']:
            raise ValueError('retention plan is stale; generate and review a new plan')
        if not current['entries']:
            raise ValueError('no eligible history; nothing was pruned')
        # Compare every exact payload against the verified independent snapshot.
        with closing(recovery.connection(backup, immutable=True)) as saved:
            saved.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
            for entry in current['entries']:
                row = saved.execute('SELECT story_id,payload FROM events WHERE seq=?', (entry['seq'],)).fetchone()
                if not row or row[0] != entry['story_id'] or sha(row[1].encode()) != entry['sha256']:
                    raise ValueError('backup does not contain the exact planned history')
        if recovery.digest(backup, max_bytes, deadline)[1] != backup_sha:
            raise ValueError('backup changed during maintenance')
        if shutil.disk_usage(path.parent).free < recovery.RESERVE:
            raise ValueError('insufficient free space; 64 MiB reserve required')
        receipt = dict(operation_id=value['operation_id'], plan_sha256=value['sha256'],
                       pruned_count=len(current['entries']),
                       pruned_through=max(state['pruned_through'], current['entries'][-1]['seq']),
                       at=datetime.now(timezone.utc).isoformat(), backup_sha256=backup_sha)
        db.executemany('DELETE FROM events WHERE seq=?', [(e['seq'],) for e in current['entries']])
        state.update(pruned_through=receipt['pruned_through'],
                     pruned_events=state.get('pruned_events',0) + receipt['pruned_count'],
                     last_pruned_at=receipt['at'], last_backup_sha256=backup_sha,
                     receipts=(state.get('receipts', []) + [receipt])[-20:])
        db.execute('INSERT OR REPLACE INTO journal_retention VALUES (1,?)', (canonical(state).decode(),))
        return dict(receipt, replayed=False)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('storage-status', 'retention-plan', 'prune-history'):
        cmd = sub.add_parser(name)
        cmd.add_argument('--home', required=True)
        cmd.add_argument('--max-bytes', type=int, default=recovery.DEFAULT_LIMIT)
        cmd.add_argument('--timeout', type=float, default=60)
        if name == 'retention-plan':
            cmd.add_argument('--before', required=True)
            cmd.add_argument('--limit', type=int, default=1000)
            cmd.add_argument('--output', required=True, help='New private plan file; existing files are never overwritten')
        if name == 'prune-history':
            cmd.add_argument('--plan', required=True)
            cmd.add_argument('--backup', required=True)
    args = parser.parse_args(argv)
    os.umask(0o077)
    try:
        bounds = dict(max_bytes=args.max_bytes, timeout=args.timeout)
        if args.command == 'storage-status':
            result = storage_status(args.home, **bounds)
        elif args.command == 'retention-plan':
            result = plan(args.home, args.before, limit=args.limit, **bounds)
            recovery.write_json(Path(args.output), result)
        else:
            recovery.regular(Path(args.plan), PLAN_LIMIT)
            result = prune(args.home, json.loads(Path(args.plan).read_bytes()), args.backup, **bounds)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error) as error:
        print(f'Maintenance failed: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
