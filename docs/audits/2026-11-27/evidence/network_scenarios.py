import os,sys,json,tempfile,subprocess,socket,time,select,threading,struct
from pathlib import Path
os.environ['AGENT_NEWSBOT_HOME']='/tmp/anb-audit-20261127/network-state'
ROOT=Path(__file__).resolve().parents[4]
sys.path.insert(0,str(ROOT/'linux'))
import requests
from agent_newsbot.stories import StoryStore
from agent_newsbot.store import article_id
HAR='/tmp/anb-audit-20261127/harness'
base=Path('/tmp/anb-audit-20261127')
def server(mode,path):
 with socket.socket() as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
 p=subprocess.Popen([HAR,mode,str(path),str(port)],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
 if not select.select([p.stdout],[],[],10)[0]:raise RuntimeError('startup timeout')
 assert p.stdout.readline().strip()==b'ready'
 return p,port
out={}
p,port=server('timeout',base/'timeout.sqlite')
try:
 start=time.monotonic();r=requests.post(f'http://127.0.0.1:{port}/v1/fetch',headers={'Authorization':'Bearer audit-token'},timeout=70)
 out['fetch_timeout']={'elapsed_seconds':round(time.monotonic()-start,1),'http':r.status_code,'body':r.json(),'handler_completion_seconds':65}
finally:p.terminate();p.wait(timeout=5)
# Test disconnected consumer against the isolated native bridge, never the installed app.
j=StoryStore(base/'disconnect.sqlite');url='https://example.com/long';j.ingest([dict(id=article_id(url,''),desk='science',title='Large article',url=url,source='Fixture',published='',summary='x'*1000000,tweet='test',tweet_chars=4,hashtags=[],extra={})])
p,port=server('serve',base/'disconnect.sqlite')
try:
 for _ in range(12):
  if p.poll() is not None:break
  with socket.create_connection(('127.0.0.1',port)) as s:
   s.sendall(b'GET /v1/events?consumer=test HTTP/1.1\r\nHost: localhost\r\nAuthorization: Bearer audit-token\r\n\r\n')
   s.shutdown(socket.SHUT_RDWR)
  time.sleep(.1)
 time.sleep(.2)
 out['disconnect']={'process_returncode':p.poll(),'attempts':_+1}
finally:
 if p.poll() is None:p.terminate()
 p.wait(timeout=5)
print(json.dumps(out,indent=2));(base/'network-findings.json').write_text(json.dumps(out,indent=2))
