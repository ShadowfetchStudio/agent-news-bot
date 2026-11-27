import os,sys,json,tempfile,subprocess,socket,threading,time,sqlite3,struct
from pathlib import Path
from datetime import datetime, timezone
os.environ['AGENT_NEWSBOT_HOME']='/tmp/anb-audit-20261127/state'
ROOT=Path(__file__).resolve().parents[4]
sys.path.insert(0,str(ROOT/'linux'))
from agent_newsbot.stories import StoryStore
from agent_newsbot import store, engine, server
from agent_newsbot.config import Article
HAR='/tmp/anb-audit-20261127/harness'
out={}
def article(url='https://example.com/a', title='NASA launches lunar orbiter to map south pole craters', summary='Original report', provider='direct'):
 return dict(id=store.article_id(url,title),desk='science',category='Science',title=title,url=url,source='Example',published=datetime.now(timezone.utc).isoformat(),summary=summary,tweet=title,tweet_chars=len(title),hashtags=[],extra={'provider':provider,'metadata':{}})
def journal(name): return StoryStore(Path(tempfile.mkdtemp(prefix=name,dir='/tmp/anb-audit-20261127'))/'stories.sqlite')
j=journal('enrichment');a=article();wm=article(provider='worldmonitor');wm['extra']['metadata']={'locationName':'Florida','importanceScore':80}
fresh=j.ingest([a,wm]);event=j.pending('agent')['events'][0]['story']
with j.connect() as db: persisted=json.loads(db.execute('SELECT payload FROM stories').fetchone()[0])
out['metadata_delivery']={'fresh_metadata':fresh[0]['sources'][0]['metadata'],'event_metadata':event['sources'][0]['metadata'],'stored_metadata':persisted['sources'][0]['metadata']}
wm['title']='NASA cancels lunar orbiter launch after engine failure';wm['summary']='Updated report: the launch was cancelled.'
changes=j.ingest([wm]);out['wm_correction_after_direct']={'events_emitted':len(changes),'latest_deliverable_title':j.pending('agent')['events'][-1]['story']['title']}
j=journal('negation');a=article(title='NASA confirms lunar orbiter launch will proceed despite engine issue');b=article('https://second.example/b',title='NASA confirms lunar orbiter launch will not proceed despite engine issue')
rows=j.ingest([a,b]);out['opposing_headlines']={'story_groups':len(rows),'sources':len(rows[0]['sources']),'titles':[s['title'] for s in rows[0]['sources']]}
urls=['https://example.com:8443/story','https://example.com:9443/story','https://example.com/story/?id=42','https://example.com/story?id=42']
out['canonical_parity']=[{'input':u,'python':store.canonical_url(u),'swift':subprocess.check_output([HAR,'canonical',u],text=True).strip()} for u in urls]
with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
out['bridge_restart']=subprocess.check_output([HAR,'restart',str(port)],text=True).strip()
s=server.NewsState();old=article();old['published']='';old['first_seen']='2000-01-01T00:00:00Z';previous={'articles':[old]};snapshot={'articles':[],'desks':[{'desk':'science','fresh':0,'errors':['feed unreachable']}],'fetched_at':datetime.now(timezone.utc).isoformat()}
result=s._keep_live_desks(previous,snapshot);out['undated_carryover']={'articles_retained':result['total_articles'],'original_first_seen':result['articles'][0]['first_seen'],'fresh_count':result['desks'][0]['fresh']}
# An additional article below the per-desk cap is never revisited after HTTP 304.
cache=store.FreshnessStore(Path(tempfile.mkdtemp(dir='/tmp/anb-audit-20261127'))/'seen.sqlite')
class Response:
 def __init__(self,status):self.status_code=status;self.headers={'ETag':'"same"'};self.content=b'<rss><channel><item><title>First launch story</title><link>https://example.com/first</link></item><item><title>Second distinct story</title><link>https://example.com/second</link></item></channel></rss>'
 def raise_for_status(self):pass
old_get=engine._get;old_feeds=engine.RSS_FEEDS
engine.RSS_FEEDS={'science':[('https://example.com/feed','Fixture')]};engine._get=lambda *a,**k:Response(304 if k.get('extra_headers') else 200)
r1=engine.fetch_rss('science',cache,12,1)
for update in r1.feed_cache_updates:cache.save_feed_cache(*update)
r2=engine.fetch_rss('science',cache,12,2)
out['rss_cap_checkpoint']={'first_urls':[a.url for a in r1.articles],'second_urls':[a.url for a in r2.articles],'deferred_story_ever_returned':any(a.url.endswith('second') for a in r1.articles+r2.articles)}
engine._get=old_get;engine.RSS_FEEDS=old_feeds
# Installer-generated wrapper: reproduce its actual module lookup from outside source.
venv='/tmp/agent-newsbot-integration-venv/bin/python'
env=os.environ.copy();env.pop('PYTHONPATH',None);env['AGENT_NEWSBOT_PREFIX']=str(ROOT/'linux')
proc=subprocess.run([venv,'-m','agent_newsbot','status'],cwd='/tmp',env=env,capture_output=True,text=True)
out['installed_cli_lookup']={'returncode':proc.returncode,'stderr':proc.stderr.strip()}
# Unix default umask means research data can be world-readable in manual installs.
out['journal_permissions']=oct(j.path.stat().st_mode & 0o777)
print(json.dumps(out,indent=2))
Path('/tmp/anb-audit-20261127/findings.json').write_text(json.dumps(out,indent=2))
