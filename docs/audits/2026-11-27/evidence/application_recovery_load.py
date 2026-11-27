"""Bounded application archive/restore trial; isolated synthetic data only."""
import hashlib,json,platform,sys,tempfile,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[4]
sys.path[:0]=[str(ROOT/'linux'),str(ROOT/'tests')]
from test_worldmonitor import article
from agent_newsbot.stories import StoryStore
from agent_newsbot import application_recovery as app
root=Path(tempfile.mkdtemp(prefix='anb-application-load-'));home=root/'home';store=StoryStore(home/'stories.sqlite')
for rev in range(3):
    store.ingest([article(url=f'https://example.com/station/{n}',title=f'Research station {n} issues instrument measurements',summary=f'Revision {rev} of measured data') for n in range(200)])
config=root/'config.env';config.write_text('AGENT_NEWSBOT_PORT=19876\nAGENT_NEWSBOT_FETCH_INTERVAL=1800\n');config.chmod(0o600)
exports=root/'exports';exports.mkdir(mode=0o700)
payload=b'Synthetic saved research export.\n'*2048
for n in range(128):(exports/f'research-{n:03}.md').write_bytes(payload)
(export_empty:=exports/'empty').mkdir();(exports/'bridge.token').write_text('excluded fixture credential')
measure={}
began=time.monotonic();manifest=app.backup(home,root/'archive',platform='linux',config=config,exports={'research':exports},writers_stopped=True);measure['backup_seconds']=time.monotonic()-began
began=time.monotonic();app.verify(root/'archive');measure['verify_seconds']=time.monotonic()-began
began=time.monotonic();app.restore(root/'archive',root/'restored');measure['restore_seconds']=time.monotonic()-began
for n in range(128):assert (root/'restored/exports/research'/f'research-{n:03}.md').read_bytes()==payload
assert (root/'restored/configuration/config.env').read_bytes()==config.read_bytes()
assert not list((root/'restored').rglob('bridge.token'))
assert (root/'restored/exports/research/empty').is_dir()
assert len(StoryStore(root/'restored/home/stories.sqlite').library({'limit':'100'})['stories'])==100
result=dict(platform=platform.platform(),fixture=str(root),scope='Warm synthetic application-data trial; not a production machine recovery, secret-store recovery or slow-disk benchmark',stories=200,events=600,export_files=128,export_bytes=len(payload)*128,payload_bytes=sum(x['bytes'] for x in manifest['files'].values())+sum(x['bytes'] for x in manifest['research']['files'].values()),all_exports_and_config_equal=True,bridge_credential_excluded=True,**measure)
(root/'measurement.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
