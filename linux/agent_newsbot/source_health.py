"""Persistent provider circuit state; no raw URLs, keys, or error bodies are stored."""
from __future__ import annotations
import contextvars
import hashlib
import json
import math
import re
import sqlite3
import time
import uuid
from contextlib import contextmanager
from email.utils import parsedate_to_datetime
from pathlib import Path
from .private_files import private_database

CURRENT = contextvars.ContextVar('source_attempt', default=None)
MAX_SOURCES = 1024
LEASE_SECONDS = 300


def retry_time(raw, now):
    if not raw: return 0.0
    try:
        seconds = float(raw) if str(raw).strip().isascii() and str(raw).strip().isdigit() else parsedate_to_datetime(raw).timestamp() - now
        return now + min(86400, max(0, seconds))
    except (ValueError, TypeError, OverflowError): return 0.0


class Attempt:
    def __init__(self):
        self.errors = 0
        self.retry_at = 0.0
        self.code = ''
    def record(self, code, retry_after=None):
        self.errors += 1
        self.code = code
        self.retry_at = max(self.retry_at, retry_time(retry_after, time.time()))
    def check(self):
        if self.errors >= 3 or self.retry_at > time.time():
            raise RuntimeError('Source paused after repeated failures or Retry-After')


class SourceHealth:
    def __init__(self, path): self.path = Path(path)
    @contextmanager
    def transaction(self):
        private_database(self.path)
        db = sqlite3.connect(self.path, timeout=5)
        try:
            db.execute('CREATE TABLE IF NOT EXISTS source_health (id TEXT PRIMARY KEY, payload TEXT NOT NULL)')
            db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except BaseException:
            db.rollback(); raise
        finally: db.close()
    @staticmethod
    def identity(key): return hashlib.sha256(key.encode()).hexdigest()
    @staticmethod
    def decode(payload):
        row=json.loads(payload)
        if not isinstance(row,dict): raise ValueError("invalid health record")
        for key in ('id','source','provider','lease','last_state','last_error'):
            if not isinstance(row.get(key),str) or len(row[key])>200: raise ValueError("invalid health record")
        if not re.fullmatch('[a-f0-9]{64}',row['id']) or not re.fullmatch('([a-f0-9]{32})?',row['lease']): raise ValueError("invalid health identity")
        for key in ('last_attempt','last_success','last_usable','failure_streak','next_retry','lease_until'):
            value=row.get(key)
            if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or not 0<=value<=1e12: raise ValueError("invalid health time")
        if row['failure_streak']>30 or int(row['failure_streak'])!=row['failure_streak']: raise ValueError("invalid health streak")
        row['failure_streak']=int(row['failure_streak'])
        return row
    @staticmethod
    def put(db, row):
        db.execute('INSERT OR REPLACE INTO source_health VALUES (?,?)', (row['id'], json.dumps(row, sort_keys=True, separators=(',', ':'))))
    @staticmethod
    def public(row, now):
        row = dict(row); row.pop('lease', None)
        row['circuit'] = ('probing' if row['lease_until'] > now else
                          ('open' if row['failure_streak'] >= 3 else 'backoff') if row['next_retry'] > now else 'ready')
        return row
    def list(self, now=None):
        now = time.time() if now is None else now
        if not self.path.exists(): return []
        with self.transaction() as db:
            return sorted([self.public(self.decode(p), now) for (p,) in db.execute('SELECT payload FROM source_health LIMIT 1025')], key=lambda r:(r['provider'],r['source'],r['id']))
    def begin(self, key, source, provider='direct', now=None):
        now = time.time() if now is None else now
        sid = self.identity(key)
        with self.transaction() as db:
            existing = db.execute('SELECT payload FROM source_health WHERE id=?', (sid,)).fetchone()
            if existing:
                row = self.decode(existing[0])
                if max(row['next_retry'], row['lease_until']) > now: return None, self.public(row, now)
            else:
                if db.execute('SELECT count(*) FROM source_health').fetchone()[0] >= MAX_SOURCES: raise ValueError('source health capacity reached')
                row = dict(id=sid,source=source[:200],provider=provider[:40],last_attempt=0,last_success=0,last_usable=0,
                           failure_streak=0,next_retry=0,lease_until=0,lease='',last_state='unknown',last_error='')
            # An expired worker lease is evidence of an interrupted attempt, not success.
            if row['lease']:
                row['failure_streak'] = min(30,row['failure_streak']+1); row['last_error']='interrupted'; row['last_state']='unavailable'
            lease = uuid.uuid4().hex
            row.update(lease=lease,lease_until=now+LEASE_SECONDS,last_attempt=now)
            self.put(db,row)
            return lease,self.public(row,now)
    def finish(self, key, lease, state, code='', retry_at=0, now=None):
        now = time.time() if now is None else now
        with self.transaction() as db:
            existing = db.execute('SELECT payload FROM source_health WHERE id=?',(self.identity(key),)).fetchone()
            if not existing: raise ValueError('missing source health record')
            row=self.decode(existing[0])
            if row['lease'] != lease: return self.public(row,now)
            if state == 'cancelled':
                row.update(lease='',lease_until=0)
            else:
                success = state == 'success'
                healthy_transport = success or (state == 'partial' and not code and retry_at <= now)
                streak = 0 if healthy_transport else min(30,row['failure_streak']+1)
                delay = 0 if healthy_transport else min(21600,60*2**min(streak-1,9))
                row.update(lease='',lease_until=0,last_state=state,failure_streak=streak,
                           last_error='' if healthy_transport else code[:80] or 'content_or_request',
                           next_retry=0 if healthy_transport else max(now+delay,min(now+86400,retry_at)))
                if success: row['last_success']=now
                if state in ('success','partial'): row['last_usable']=now
            self.put(db,row)
            return self.public(row,now)
    def run(self, key, source, desk, operation, provider='direct'):
        from .config import DeskResult
        from .coverage import check, summary, result_checks
        def unavailable(message, history=None):
            c=check(source,'unavailable',message,provider)
            if history is not None: c['health']=history
            return [DeskResult(desk,[],source_errors=[message],source_checks=[c])]
        try: lease,history=self.begin(key,source,provider)
        except (OSError,sqlite3.Error,ValueError): return unavailable('Source health storage unavailable; request not started.')
        if lease is None: return unavailable('Source temporarily paused; see next retry and last-success history.',history)
        attempt=Attempt(); token=CURRENT.set(attempt)
        try:
            results=operation()
            state=summary([c for r in results for c in result_checks(r)])['state']
            try: history=self.finish(key,lease,state,attempt.code,attempt.retry_at)
            except (OSError,sqlite3.Error,ValueError):
                for r in results:
                    r.source_errors.append('Source health could not be saved; retrieved stories are retained.')
                    r.source_checks.append(check(source,'partial','Source health storage failure',provider))
                return results
            for r in results:
                if not r.source_checks: r.source_checks=[check(source,state,'',provider)]
                for c in r.source_checks: c['health']=history
            return results
        except Exception:
            try: history=self.finish(key,lease,'unavailable',attempt.code,attempt.retry_at)
            except (OSError,sqlite3.Error,ValueError): return unavailable('Source health could not be saved; check local storage.')
            return unavailable('Source request or processing failed.',history)
        finally: CURRENT.reset(token)
