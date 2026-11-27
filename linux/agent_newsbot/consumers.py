"""Consumer administration with replay fencing and optional exclusive leases."""
from __future__ import annotations
import secrets
import time
from datetime import datetime

CONSUMER_SCHEMA = """CREATE TABLE IF NOT EXISTS consumer_meta(
 name TEXT PRIMARY KEY,generation INTEGER NOT NULL DEFAULT 0,last_ack REAL,
 lease_token TEXT NOT NULL DEFAULT '',lease_worker TEXT NOT NULL DEFAULT '',lease_until REAL NOT NULL DEFAULT 0)"""
class ConsumerConflict(ValueError): pass


def delivery_guard(db, name, generation=None, lease=None, ack=False):
    db.execute("INSERT OR IGNORE INTO consumer_meta(name) VALUES (?)", (name,))
    gen, token, until = db.execute("SELECT generation,lease_token,lease_until FROM consumer_meta WHERE name=?", (name,)).fetchone()
    if generation is not None and (type(generation) is not int or generation != gen):
        raise ConsumerConflict("consumer generation changed; fetch again")
    if ack and gen > 0 and generation is None:
        raise ConsumerConflict("acknowledgement requires the current generation")
    if lease is not None or until > time.time():
        if not isinstance(lease, str) or not lease or until <= time.time() or not secrets.compare_digest(lease, token):
            raise ConsumerConflict("active matching worker lease required")
    return gen


class ConsumerMixin:
    def consumer_status(self):
        with self.lock, self.connect() as db:
            rows = db.execute("""SELECT c.name,c.cursor,c.offered,coalesce(m.generation,0),m.last_ack,
              coalesce(m.lease_worker,''),coalesce(m.lease_until,0),
              (SELECT count(*) FROM events WHERE seq>c.cursor) FROM consumers c
              LEFT JOIN consumer_meta m ON m.name=c.name ORDER BY c.name""").fetchall()
            latest = db.execute("SELECT coalesce(max(seq),0) FROM events").fetchone()[0]
        return {"latest_seq": latest, "consumers": [dict(consumer=r[0],cursor=r[1],offered=r[2],generation=r[3],
            last_ack_at=r[4],lease_worker=r[5] if r[6]>time.time() else None,
            lease_expires_at=r[6] if r[6]>time.time() else None,lag=r[7]) for r in rows]}

    def consumer_change(self, change):
        allowed = {'consumer','action','start','since','expected_generation','worker','lease','ttl_seconds'}
        if not isinstance(change, dict) or set(change)-allowed: raise ValueError('invalid consumer command')
        name, action = change.get('consumer'), change.get('action')
        if not isinstance(name, str): raise ValueError('consumer required')
        self.validate_consumer(name)
        if action not in ('create','reset','lease','renew','release'): raise ValueError('invalid consumer action')
        with self.lock, self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            existing = db.execute('SELECT cursor FROM consumers WHERE name=?',(name,)).fetchone()
            if action=='create' and existing: raise ConsumerConflict('consumer already exists; explicit reset required')
            if action!='create' and not existing: raise LookupError('consumer not found')
            db.execute('INSERT OR IGNORE INTO consumer_meta(name) VALUES (?)',(name,))
            gen, token, worker, until = db.execute('SELECT generation,lease_token,lease_worker,lease_until FROM consumer_meta WHERE name=?',(name,)).fetchone()
            if action!='create' and (type(change.get('expected_generation')) is not int or change['expected_generation']!=gen):
                raise ConsumerConflict('consumer generation changed; refresh status')
            now=time.time()
            if action in ('create','reset'):
                if until>now: raise ConsumerConflict('release the active lease before reset')
                mode=change.get('start')
                if mode=='history': cursor=0
                elif mode=='now': cursor=db.execute('SELECT coalesce(max(seq),0) FROM events').fetchone()[0]
                elif mode=='date':
                    value=change.get('since')
                    if not isinstance(value,str): raise ValueError('since requires an ISO-8601 timestamp with timezone')
                    try: date=datetime.fromisoformat(value.replace('Z','+00:00'))
                    except ValueError: raise ValueError('invalid since timestamp')
                    if date.tzinfo is None: raise ValueError('since requires timezone')
                    # Select the first matching event, then replay the complete suffix. Never filter delivered events.
                    cursor=db.execute("SELECT min(seq)-1 FROM events WHERE julianday(json_extract(payload,'$.updated_at'))>=julianday(?)",(date.isoformat(),)).fetchone()[0]
                    if cursor is None: cursor=db.execute('SELECT coalesce(max(seq),0) FROM events').fetchone()[0]
                else: raise ValueError('start must be history, now or date')
                gen+=1
                db.execute('INSERT OR REPLACE INTO consumers(name,cursor,offered) VALUES (?,?,?)',(name,cursor,cursor))
                db.execute("UPDATE consumer_meta SET generation=?,last_ack=NULL,lease_token='',lease_worker='',lease_until=0 WHERE name=?",(gen,name))
                return dict(consumer=name,cursor=cursor,generation=gen)
            if action=='lease':
                if until>now: raise ConsumerConflict('consumer already leased; renew using its token')
                worker=change.get('worker')
                if not isinstance(worker,str): raise ValueError('worker required')
                self.validate_consumer(worker)
                token=secrets.token_hex(32); gen+=1
            else:
                delivery_guard(db,name,gen,change.get('lease'),ack=True)
                if until<=now: raise ConsumerConflict('lease expired; acquire a new lease')
            if action=='release':
                db.execute("UPDATE consumer_meta SET lease_token='',lease_worker='',lease_until=0,generation=generation+1 WHERE name=?",(name,))
                db.execute('UPDATE consumers SET offered=cursor WHERE name=?',(name,))
                return dict(consumer=name,generation=gen+1,released=True)
            ttl=change.get('ttl_seconds',60)
            if type(ttl) is not int or not 10<=ttl<=300: raise ValueError('ttl_seconds must be 10-300')
            until=now+ttl
            db.execute('UPDATE consumer_meta SET generation=?,lease_token=?,lease_worker=?,lease_until=? WHERE name=?',(gen,token,worker,until,name))
            if action=='lease': db.execute('UPDATE consumers SET offered=cursor WHERE name=?',(name,))
            return dict(consumer=name,generation=gen,lease=token,worker=worker,lease_expires_at=until)
