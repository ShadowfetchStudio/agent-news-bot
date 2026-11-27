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
        self.session.trust_env = False  # Do not send the local bearer token through an environment proxy.
        self.session.headers.update({'Authorization':'Bearer '+token})
        self.lease=None

    def call(self, method, path, **kwargs):
        # GET and generation-bound acknowledgement are replay-safe. A lost admin
        # response needs reconciliation; retrying lease/create/reset may change ownership.
        attempts = 5 if method == 'GET' or (method == 'POST' and path == '/v1/events/ack') else 1
        for attempt in range(attempts):
            try:
                headers={'X-Consumer-Lease':self.lease} if self.lease else {}
                response=self.session.request(method,self.base+path,headers=headers,timeout=(3,8),allow_redirects=False,**kwargs)
                if response.status_code in (429,502,503,504) and attempt<attempts-1:
                    time.sleep(min(8,.5*2**attempt)); continue
                if response.status_code!=200:
                    raise RuntimeError(f'Bridge returned HTTP {response.status_code}; inspect status before retrying this operation')
                return response.json()
            except (requests.Timeout, requests.ConnectionError):
                if attempt==attempts-1: raise
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


def check_capabilities(client):
    value = client.call('GET', '/v1/capabilities')
    required = ('consumer_delivery', 'consumer_generations', 'consumer_leases')
    if not isinstance(value, dict) or any(type(value.get(key)) is not int or value[key] != 1 for key in ('api_version', 'contract_version', 'error_schema_version')):
        raise RuntimeError('Unsupported bridge contract; update the client before consuming')
    features = value.get('features', {})
    if not isinstance(features, dict) or any(type(features.get(k)) is not int or features[k] != 1 for k in required):
        raise RuntimeError('Bridge lacks compatible delivery, generation or lease capabilities')
    return value


def validate_page(page, limit):
    if not isinstance(page, dict): raise RuntimeError('Invalid event page')
    for key in ('cursor', 'next_cursor', 'generation', 'count'):
        if type(page.get(key)) is not int or page[key] < 0: raise RuntimeError('Invalid event page counters')
    floor = page.get('pruned_through', 0)
    if type(floor) is not int or not 0 <= floor <= page['cursor']:
        raise RuntimeError('Event page crosses the retained-history boundary')
    events = page.get('events')
    if not isinstance(events, list) or page['count'] != len(events) or len(events) > limit:
        raise RuntimeError('Invalid event page length')
    previous = page['cursor']
    for event in events:
        if not isinstance(event, dict) or type(event.get('seq')) is not int or event['seq'] <= previous or not isinstance(event.get('story'), dict):
            raise RuntimeError('Invalid event sequence or payload')
        previous = event['seq']
    if previous != page['next_cursor']: raise RuntimeError('Event page does not cover its acknowledgement boundary')


def run_batch(client, consumer, inbox, limit=100):
    page=client.call('GET','/v1/events',params={'consumer':consumer,'limit':limit})
    validate_page(page,limit)
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
    parser.add_argument('--start',choices=['history','retained','now'],default='history',help='Only used when creating a new consumer')
    parser.add_argument('--follow',action='store_true')
    args=parser.parse_args()
    token_path=Path(args.token_file)
    if token_path.stat().st_size>8192: raise ValueError('invalid token file')
    token=token_path.read_text().strip()
    if not token: raise ValueError('empty token file')
    client=Client(args.base,token)
    capabilities = check_capabilities(client)
    if args.start == 'retained' and capabilities['features'].get('retained_history') != 1:
        raise RuntimeError('Bridge lacks compatible retained-history onboarding')
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
        detail = str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__
        print(f'Consumer stopped: {detail}. Check bridge status and consumer generation.',file=sys.stderr)
        sys.exit(1)
