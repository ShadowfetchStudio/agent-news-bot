"""Ingestion budgets and operator observations; never prunes or acknowledges work."""
import json
import os
from pathlib import Path
import shutil
import sqlite3
from datetime import datetime, timezone
from .library import LibraryConflict

DEFAULT = dict(version=0, max_journal_bytes=2*1024**3, min_free_bytes=256*1024**2)


class StorageBlocked(RuntimeError):
    pass


def validate(value):
    if not isinstance(value,dict) or set(value)!=set(DEFAULT) or any(type(v) is not int for v in value.values()):
        raise ValueError('invalid ingestion storage policy')
    if not 0<=value['version']<=2**63-1 or not 1024**2<=value['max_journal_bytes']<=8*1024**3 or not 64*1024**2<=value['min_free_bytes']<=8*1024**3:
        raise ValueError('storage limits outside supported bounds')
    return value


def policy(db):
    row=db.execute("SELECT payload FROM library_preferences WHERE kind='storage' AND name='ingestion-policy'").fetchone()
    return validate(json.loads(row[0])) if row else dict(DEFAULT)


def file_size(path):
    if not os.path.lexists(path):return 0
    import stat
    info=path.lstat()
    if not stat.S_ISREG(info.st_mode):raise OSError('storage sidecar must be a regular file')
    return info.st_size


def snapshot(db,path):
    value=policy(db); page_size=db.execute('PRAGMA page_size').fetchone()[0]
    pages=db.execute('PRAGMA page_count').fetchone()[0]
    reusable=db.execute('PRAGMA freelist_count').fetchone()[0]
    free=shutil.disk_usage(path.parent).free; wal=file_size(Path(str(path)+'-wal'))
    reasons=[]
    if pages*page_size>value['max_journal_bytes']:reasons.append('journal_above_ceiling')
    if free<value['min_free_bytes']:reasons.append('disk_below_reserve')
    if wal>value['max_journal_bytes']:reasons.append('wal_above_ceiling')
    count=lambda sql:db.execute(sql).fetchone()[0]
    return dict(policy=value,database_bytes=pages*page_size,database_file_bytes=file_size(path),
                reusable_bytes=reusable*page_size,wal_bytes=wal,free_bytes=free,
                stories=count('SELECT count(*) FROM stories'),events=count('SELECT count(*) FROM events'),
                unread_alerts=count('SELECT count(*) FROM watch_matches WHERE seen_seq<seq'),
                bookmarked_stories=count("SELECT count(*) FROM editorial WHERE json_extract(payload,'$.bookmarked')=1"),
                ingestion_blocked=bool(reasons),blocking_reasons=reasons,
                nearing_ceiling=(pages-reusable)*page_size>=value['max_journal_bytes']*.9,
                automatic_pruning=False,scope='journal ingestion, not a filesystem quota')


def guard(db,path):
    value=snapshot(db,path)
    if value['ingestion_blocked']:
        raise StorageBlocked('Research ingestion paused by storage policy. Inspect operations, back up and review retention or increase the budget.')
    page_size=db.execute('PRAGMA page_size').fetchone()[0]
    expected=value['policy']['max_journal_bytes']//page_size
    actual=db.execute(f'PRAGMA max_page_count={expected}').fetchone()[0]
    if actual!=expected:raise StorageBlocked('Journal exceeds its configured ingestion ceiling')


def operations(stories,health):
    sections={};errors={}
    for name,read in [('storage',stories.storage_status),('delivery',stories.consumer_status),('sources',health.list)]:
        try:sections[name]=read()
        except (OSError,sqlite3.Error,ValueError,RuntimeError):errors[name]='Local storage unavailable; inspect the selected application home.'
    return dict(observed_at=datetime.now(timezone.utc).isoformat(),sections=sections,errors=errors,
                complete=not errors,consistency='separate local snapshots; reload after changes')


class StorageMixin:
    def storage_status(self):
        with self.lock,self.connect() as db:
            db.execute('BEGIN')
            return snapshot(db,self.path)

    def storage_preflight(self):
        with self.lock,self.connect() as db:
            db.execute('BEGIN IMMEDIATE');guard(db,self.path)

    def storage_change(self,value):
        if not isinstance(value,dict) or set(value)!={'expected_version','max_journal_bytes','min_free_bytes'} or type(value['expected_version']) is not int:
            raise ValueError('expected_version and both storage limits required')
        validate(dict(version=value['expected_version'],max_journal_bytes=value['max_journal_bytes'],min_free_bytes=value['min_free_bytes']))
        with self.lock,self.connect() as db:
            db.execute('BEGIN IMMEDIATE');old=policy(db)
            if old['version']!=value['expected_version']:raise LibraryConflict('storage policy changed; reload before saving')
            if old['version']==2**63-1:raise ValueError('storage policy version exhausted')
            new=dict(version=old['version']+1,max_journal_bytes=value['max_journal_bytes'],min_free_bytes=value['min_free_bytes'])
            db.execute("INSERT OR REPLACE INTO library_preferences VALUES ('storage','ingestion-policy',?)",(json.dumps(new),))
            return new
