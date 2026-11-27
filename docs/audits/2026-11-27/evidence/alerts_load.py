"""Bounded synthetic 32-watch load; uses only an isolated temporary journal."""
import json, platform, sys, tempfile, time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[4]
sys.path[:0]=[str(ROOT/'linux'),str(ROOT/'tests')]
from test_worldmonitor import article
from agent_newsbot.stories import StoryStore
root=Path(tempfile.mkdtemp(prefix='anb-alert-load-'));store=StoryStore(root/'stories.sqlite')
for n in range(32):
    store.alert_change(dict(action='subscribe',name=f'Stations {n}',filters={'q':'station'},enabled=True,expected_version=0))
start=time.monotonic();durations=[]
for revision in range(5):
    began=time.monotonic()
    changed=store.ingest([article(url=f'https://example.com/station/{n}',title=f'Research station {n} issues instrument measurements',summary=f'Revision {revision}: recorded laboratory data.') for n in range(200)])
    durations.append(time.monotonic()-began)
    assert len(changed)==200
    if time.monotonic()-start>120:raise RuntimeError('bounded load exceeded two minutes')
start=time.monotonic();inbox=store.alerts({'limit':'100'});read_seconds=time.monotonic()-start
assert inbox['unread_count']==6400 and len(inbox['matches'])==100
assert all(row['matched_revision']==5 for row in inbox['matches'])
assert len(store.alerts()['subscriptions'])==32
result=dict(platform=platform.platform(),fixture=str(root),scope='Synthetic local 32 subscriptions, 200 stories and five revisions each; warm timing, not long-running production or concurrent-load acceptance',subscriptions=32,stories=200,events=1000,inbox_rows=6400,
            revision_batch_seconds=durations,read_100_seconds=read_seconds,coalescing_verified=True)
(root/'measurement.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
