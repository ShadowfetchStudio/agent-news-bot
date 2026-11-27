import json,sqlite3,sys,tempfile,time,hashlib,platform
from pathlib import Path
from datetime import datetime,timezone
ROOT=Path(__file__).resolve().parents[4]
sys.path[:0]=[str(ROOT/'linux'),str(ROOT/'tests')]
from test_worldmonitor import article
from agent_newsbot.stories import StoryStore
from agent_newsbot import maintenance as m,recovery
root=Path(tempfile.mkdtemp(prefix='anb-retention-load-'))
store=StoryStore(root/'home/stories.sqlite')
start=time.monotonic()
for revision in range(30):
    rows=[article(url=f'https://example.com/event/{n}',title=f'Research station {n} publishes its independent instrument report',summary=f'Revision {revision}. '+('Measured observation with attributed context. '*24)) for n in range(200)]
    changed=store.ingest(rows)
    assert len(changed)==200
    if revision==0:
        for row in changed[:10]:
            store.save_editorial(row['id'],dict(expected_revision=1,expected_editorial_version=0,bookmarked=True))
        for row in changed[10:20]:
            store.save_editorial(row['id'],dict(expected_revision=1,expected_editorial_version=0,generate=True))
    if time.monotonic()-start>120:raise RuntimeError('bounded load ingestion exceeded two minutes')
ingest_seconds=time.monotonic()-start
for reader in ['fast','slow']:
    batch=store.pending(reader,limit=500)
    store.acknowledge(reader,batch['next_cursor'],generation=batch['generation'])
while True:
    batch=store.pending('fast',limit=500)
    store.acknowledge('fast',batch['next_cursor'],generation=batch['generation'])
    if not batch['count']:break
before=m.storage_status(store.path.parent)
blocked=m.plan(store.path.parent,'2099-01-01T00:00:00Z')
assert all(row['seq']<=500 for row in blocked['selection']['entries'])
while True:
    batch=store.pending('slow',limit=500)
    store.acknowledge('slow',batch['next_cursor'],generation=batch['generation'])
    if not batch['count']:break
start=time.monotonic();archive=root/'archive';recovery.backup(store.path.parent,archive);backup_seconds=time.monotonic()-start
start=time.monotonic();receipts=[]
while True:
    value=m.plan(store.path.parent,'2099-01-01T00:00:00Z')
    if not value['selection']['entries']:break
    receipts.append(m.prune(store.path.parent,value,archive))
    if len(receipts)>10:raise RuntimeError('unexpected pruning growth')
prune_seconds=time.monotonic()-start
after=m.storage_status(store.path.parent)
assert before['events']==6000 and before['stories']==200
assert sum(r['pruned_count'] for r in receipts)==5310
assert after['events']==690 and after['stories']==200
with sqlite3.connect(store.path) as db:
    for n in range(200):
        sid=article(url=f'https://example.com/event/{n}')['id']
        revisions=[json.loads(r[0])['revision'] for r in db.execute('SELECT payload FROM events WHERE story_id=? ORDER BY seq',(sid,))]
        assert revisions[-2:]==[29,30]
    assert db.execute('SELECT min(cursor) FROM consumers').fetchone()[0]==6000
for revision in range(30,35):
    store.ingest([article(url=f'https://example.com/event/{n}',title=f'Research station {n} publishes its independent instrument report',summary=f'Revision {revision}. '+('Measured observation with attributed context. '*24)) for n in range(200)])
regrown=m.storage_status(store.path.parent)
assert regrown['events']==1690
value=dict(observed_at=datetime.now(timezone.utc).isoformat(),platform=platform.platform(),scope='Synthetic local 200-story / 30-revision ingestion, bounded archive/prune and five further revisions; not a long-running production or slow-disk benchmark',
           fixture=str(root),ingestion_seconds=ingest_seconds,backup_seconds=backup_seconds,prune_seconds=prune_seconds,
           before=before,after=after,after_1000_new_events=regrown,pruned_events=5310,batches=len(receipts),protected_history_verified=True,
           archive_sha256=recovery.digest(archive/'stories.sqlite',recovery.DEFAULT_LIMIT)[1])
(root/'measurement.json').write_text(json.dumps(value,indent=2)+'\n')
print(json.dumps({key:value[key] for key in ['fixture','ingestion_seconds','backup_seconds','prune_seconds','pruned_events','batches']}))
print(json.dumps({label:{key:obj[key] for key in ['events','database_bytes','reusable_bytes','wal_bytes']} for label,obj in [('before',before),('after',after),('regrown',regrown)]}))
