#!/usr/bin/env python3
"""Copy a delivered batch to a private idempotent SQLite inbox, then acknowledge.

Requires requests. No publishing, agent calls or media downloads. One batch by default.
Use a separate inbox per server journal; a restored/replaced journal is a new stream.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import secrets
import sqlite3
import time
from urllib.parse import urlsplit
import requests


class Client:
    def __init__(self, base, token, session=None):
        url=urlsplit(base)
        if url.scheme not in ('http','https') or url.hostname not in ('localhost','127.0.0.1','::1') or url.username or url.password or url.query or url.fragment or url.path not in ('','/'):
            raise ValueError('base URL must be the localhost bridge origin')
        self.base=base.rstrip('/'); self.session=session or requests.Session()
        self.session.headers.update({'Authorization':'Bearer '+token})
        self.lease=None

    def call(self, method, path, **kwargs):
        for attempt in range(5):
            try:
                headers={'X-Consumer-Lease':self.lease} if self.lease else {}
                response=self.session.request(method,self.base+path,headers=headers,timeout=(3,8),allow_redirects=False,**kwargs)
                if response.status_code in (429,502,503,504) and attempt<4:
                    time.sleep(min(8,.5*2**attempt)); continue
                if response.status_code!=200:
                    raise RuntimeError(f'Bridge returned HTTP {response.status_code}; inspect status before retrying this operation')
                return response.json()
            except (requests.Timeout, requests.ConnectionError):
                if attempt==4: raise
                time.sleep(min(8,.5*2**attempt))
        raise RuntimeError('retry limit reached')


def commit_batch(path, consumer, events):
    """The inbox transaction is the example's entire side effect; retries are idempotent."""
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    # Restrict creation before SQLite opens its journal too.
    old=os.umask(0o077)
    try:
        with sqlite3.connect(path) as db:
            os.chmod(path,0o600)
            db.execute('CREATE TABLE IF NOT EXISTS inbox(consumer TEXT,seq INTEGER,story TEXT,PRIMARY KEY(consumer,seq))')
            for event in events:
                raw=json.dumps(event['story'],sort_keys=True,separators=(',',':'))
                prior=db.execute('SELECT story FROM inbox WHERE consumer=? AND seq=?',(consumer,event['seq'])).fetchone()
                if prior and prior[0]!=raw:
                    raise RuntimeError('Event identity changed; use a new inbox for this server journal')
                db.execute('INSERT OR IGNORE INTO inbox VALUES (?,?,?)',(consumer,event['seq'],raw))
    finally: os.umask(old)


def run_batch(client, consumer, inbox, limit=100):
    page=client.call('GET','/v1/events',params={'consumer':consumer,'limit':limit})
    commit_batch(inbox,consumer,page['events'])
    # A crash before this request leaves unacknowledged rows; replay hits the same SQLite keys.
    client.call('POST','/v1/events/ack',params={'consumer':consumer,'through':page['next_cursor'],'generation':page['generation']})
    return page['count']


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base',default='http://127.0.0.1:18765')
    parser.add_argument('--token-file',required=True)
    parser.add_argument('--consumer',required=True)
    parser.add_argument('--inbox',required=True)
    parser.add_argument('--start',choices=['history','now'],default='history',help='Only used when creating a new consumer')
    parser.add_argument('--follow',action='store_true')
    args=parser.parse_args()
    token_path=Path(args.token_file)
    if token_path.stat().st_size>8192: raise ValueError('invalid token file')
    token=token_path.read_text().strip()
    if not token: raise ValueError('empty token file')
    client=Client(args.base,token)
    rows=client.call('GET','/v1/consumers')['consumers']
    current=next((r for r in rows if r['consumer']==args.consumer),None)
    if current is None:
        current=client.call('POST','/v1/consumers',json={'consumer':args.consumer,'action':'create','start':args.start})
    # Administration is fenced, not blindly retried after a conflict. Inspect status on HTTP 409.
    lease=client.call('POST','/v1/consumers',json={'consumer':args.consumer,'action':'lease','worker':'reference-'+secrets.token_hex(6),'expected_generation':current['generation'],'ttl_seconds':60})
    client.lease=lease['lease']; generation=lease['generation']; renewed=time.monotonic()
    try:
        while True:
            if time.monotonic()-renewed>20:
                client.call('POST','/v1/consumers',json={'consumer':args.consumer,'action':'renew','lease':client.lease,'expected_generation':generation,'ttl_seconds':60})
                renewed=time.monotonic()
            count=run_batch(client,args.consumer,args.inbox)
            print(json.dumps({'consumer':args.consumer,'committed':count}),flush=True)
            if not args.follow: break
            if count==0: time.sleep(5)
    finally:
        try: client.call('POST','/v1/consumers',json={'consumer':args.consumer,'action':'release','lease':client.lease,'expected_generation':generation})
        except Exception: pass  # An unrenewed lease expires; never mask the original processing error.


if __name__=='__main__':
    try: main()
    except KeyboardInterrupt: pass
    except Exception as exc:
        import sys
        # Avoid printing requests exceptions containing request credentials or response bodies.
        print(f'Consumer stopped: {type(exc).__name__}. Check bridge status and consumer generation.',file=sys.stderr)
        sys.exit(1)
