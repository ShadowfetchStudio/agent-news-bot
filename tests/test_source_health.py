from __future__ import annotations
import json
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import pytest
from test_worldmonitor import swift_harness, swift, native_bridge, article, ROOT
from agent_newsbot.source_health import SourceHealth, retry_time, Attempt, CURRENT
from agent_newsbot import engine, server
from agent_newsbot.store import FreshnessStore
from agent_newsbot.config import DeskResult,Article
from agent_newsbot.coverage import summary, result_checks
import requests


@pytest.fixture(params=['python','swift'])
def health(request,tmp_path):
    path=tmp_path/'source-health.sqlite'
    native=request.getfixturevalue('swift_harness') if request.param=='swift' else None
    class Client:
        def call(self,action,now=1000,key='direct:fixture',lease='',state='',code='',retry=0):
            if native:
                args=['health',path,action,now,key]
                if action=='finish': args.extend([lease,state,code,retry])
                value=swift(native,*args)
                if 'error' in value: raise ValueError(value['error'])
                return value
            store=SourceHealth(path)
            if action=='begin':
                token,row=store.begin(key,'Fixture',now=now); return dict(lease=token,row=row)
            if action=='finish': return store.finish(key,lease,state,code,retry,now=now)
            if action=='retry': return {'retry_at':retry_time(key,now)}
            return {'sources':store.list(now=now)}
    client=Client(); client.path=path; client.runtime=request.param;client.binary=native
    return client


def test_source_health_backoff_restart_and_recovery(health):
    first=health.call('begin'); assert first['lease']
    success=health.call('finish',1001,lease=first['lease'],state='success')
    assert success['last_success']==success['last_usable']==1001 and success['circuit']=='ready'
    now=1002
    for streak,delay in [(1,60),(2,120),(3,240),(4,480)]:
        claim=health.call('begin',now)
        failed=health.call('finish',now+1,lease=claim['lease'],state='unavailable',code='http_503')
        assert failed['failure_streak']==streak and failed['next_retry']==now+1+delay
        assert failed['last_success']==1001 and failed['last_error']=='http_503'
        assert health.call('begin',now+2)['lease'] is None
        assert health.call('list',now+2)['sources'][0]['circuit']==('open' if streak>=3 else 'backoff')
        now=failed['next_retry']+1
    probe=health.call('begin',now);assert probe['row']['circuit']=='probing'
    recovered=health.call('finish',now+1,lease=probe['lease'],state='success')
    assert recovered['failure_streak']==0 and recovered['next_retry']==0 and recovered['last_success']==now+1
    assert health.path.stat().st_mode & 0o777==0o600


def test_source_health_partial_retry_after_and_fenced_leases(health):
    first=health.call('begin')
    partial=health.call('finish',1001,lease=first['lease'],state='partial',code='http_429',retry=2000)
    assert partial['last_success']==0 and partial['last_usable']==1001 and partial['next_retry']==2000
    second=health.call('begin',2001)
    assert health.call('begin',2002)['lease'] is None
    third=health.call('begin',2302) # Simulated process death; reclaim expired lease.
    assert third['lease']!=second['lease'] and third['row']['last_error']=='interrupted'
    before=health.call('list',2303)
    health.call('finish',2303,lease=second['lease'],state='success')
    assert health.call('list',2303)==before
    cancelled=health.call('finish',2304,lease=third['lease'],state='cancelled')
    assert cancelled['last_success']==0 and cancelled['last_error']=='interrupted'
    assert health.call('begin',2305)['lease']


@pytest.mark.parametrize('raw,expected',[('120',1120),('0',1000),('-1',0),('garbage',0),
    ('Thu, 01 Jan 1970 00:20:00 GMT',1200),('Thu, 01 Jan 1970 00:00:00 GMT',1000),('999999999',87400)])
def test_retry_after_bounded_date_and_seconds(health,raw,expected):
    assert health.call('retry',key=raw)=={'retry_at':expected}


def test_source_health_concurrent_claims_are_single_and_private(health):
    with ThreadPoolExecutor(max_workers=8) as pool:
        results=list(pool.map(lambda _:health.call('begin'),range(16)))
    assert sum(r['lease'] is not None for r in results)==1
    public=health.call('list')['sources'][0]
    assert 'lease' not in public and public['circuit']=='probing'


@pytest.mark.parametrize('kind',['corrupt','symlink','directory','schema'])
def test_source_health_invalid_storage_fails_closed(health,tmp_path,kind):
    if kind=='corrupt': health.path.write_bytes(b'not sqlite')
    elif kind=='symlink':
        target=tmp_path/'keep';target.write_text('preserve');health.path.symlink_to(target)
    elif kind=='directory':health.path.mkdir()
    else:
        health.call('begin')
        with sqlite3.connect(health.path) as db:db.execute('UPDATE source_health SET payload=?',(json.dumps({'id':'invalid'}),))
    with pytest.raises((ValueError,OSError,sqlite3.Error)):health.call('begin')
    if kind=='symlink':assert target.read_text()=='preserve'


def test_source_health_capacity_is_bounded_without_erasing_history(health):
    health.call('begin')
    with sqlite3.connect(health.path) as db:
        original=json.loads(db.execute('SELECT payload FROM source_health').fetchone()[0])
        for n in range(1023):
            row=dict(original,id=SourceHealth.identity(str(n)))
            db.execute('INSERT INTO source_health VALUES (?,?)',(row['id'],json.dumps(row)))
    with pytest.raises(ValueError):health.call('begin',key='one too many')
    assert len(health.call('list')['sources'])==1024


def test_health_write_failure_keeps_retrieved_stories(tmp_path,monkeypatch):
    health=SourceHealth(tmp_path/'health.sqlite')
    story=article();story.pop('category')
    result=DeskResult('science',[Article(**story)],source_checks=[{'source':'Fixture','provider':'direct','state':'success','detail':'','checked_at':'now'}])
    def failure(*args,**kwargs):raise sqlite3.OperationalError('disk full')
    monkeypatch.setattr(health,'finish',failure)
    returned=health.run('feed','Fixture','science',lambda:[result])
    assert len(returned[0].articles)==1 and returned[0].source_errors
    assert summary(result_checks(returned[0]))['state']=='partial'


def test_source_health_storage_failure_never_starts_network(tmp_path):
    path=tmp_path/'health';path.mkdir()
    result=SourceHealth(path).run('fixture','Fixture','science',lambda:pytest.fail('request started'))
    assert result[0].source_errors and not result[0].articles


def test_real_feed_retry_after_blocks_second_request_across_runtime_restart(health,tmp_path,monkeypatch):
    calls=[]
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            calls.append(self.path);self.send_response(429);self.send_header('Retry-After','3600');self.end_headers()
        def log_message(self,*args):pass
    httpd=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=httpd.serve_forever,daemon=True);thread.start()
    url=f'http://127.0.0.1:{httpd.server_port}/rss'
    monkeypatch.setattr(engine,'RSS_FEEDS',{'science':[(url,'Fixture')]})
    def collect():
        if health.runtime=='swift':return swift(health.binary,'rss',url,tmp_path/'seen.sqlite',12,20)['coverage']
        result=engine.fetch_rss('science',FreshnessStore(tmp_path/'seen.sqlite'),12,20)
        return summary(result_checks(result))
    try:
        before=time.time();first=collect();second=collect()
        assert len(calls)==1 and first['state']==second['state']=='unavailable'
        state=SourceHealth(health.path).list()[0]
        assert state['last_error']=='http_429' and state['failure_streak']==1
        assert state['next_retry']>=before+3599 and state['last_success']==0
        assert second['sources'][0]['health']['next_retry']==state['next_retry']
    finally:httpd.shutdown();httpd.server_close();thread.join()


def test_source_health_database_interoperability(swift_harness,tmp_path):
    path=tmp_path/'health.sqlite';py=SourceHealth(path)
    lease,_=py.begin('fixture','Fixture',now=1000)
    native=swift(swift_harness,'health',path,'finish',1001,'fixture',lease,'unavailable','http_503',2000)
    assert native['next_retry']==2000
    assert py.begin('fixture','Fixture',now=1100)[0] is None
    claimed=swift(swift_harness,'health',path,'begin',2001,'fixture')
    row=py.finish('fixture',claimed['lease'],'success',now=2002)
    assert swift(swift_harness,'health',path,'list',2002)['sources']==[row]


@pytest.mark.parametrize('status,retry_header,expected_requests',[(503,None,3),(429,'120',1)])
def test_provider_attempt_stops_after_failures_or_retry_after(health,status,retry_header,expected_requests):
    from agent_newsbot import http_client
    calls=[]
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            calls.append(self.path);self.send_response(status)
            if retry_header:self.send_header('Retry-After',retry_header)
            self.end_headers()
        def log_message(self,*args):pass
    httpd=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=httpd.serve_forever,daemon=True);thread.start()
    url=f'http://127.0.0.1:{httpd.server_port}/items'
    def operation():
        errors=[]
        for _ in range(10):
            try:http_client.get(url)
            except Exception:errors.append('request failed')
        return [DeskResult('science',[],source_errors=errors)]
    try:
        for _ in range(2):
            if health.runtime=='swift':result=swift(health.binary,'health-network',health.path,url)['coverage']
            else:result=summary([c for r in SourceHealth(health.path).run('network-fixture','Fixture','science',operation) for c in result_checks(r)])
            assert result['state']=='unavailable'
        assert len(calls)==expected_requests
        row=SourceHealth(health.path).list()[0]
        assert row['failure_streak']==1 and row['last_error']==f'http_{status}'
    finally:httpd.shutdown();httpd.server_close();thread.join()


def test_source_health_api_auth_errors_and_offline_cli(health,tmp_path,monkeypatch):
    import contextlib,os,sys,subprocess
    created=health.call('begin');health.call('finish',1001,lease=created['lease'],state='success')
    with contextlib.ExitStack() as stack:
        if health.runtime=='swift':
            base,_=stack.enter_context(native_bridge(health.binary,tmp_path/'stories.sqlite'))
            token='integration-test-token'
        else:
            monkeypatch.setattr(server,'HOME',tmp_path);monkeypatch.setattr(server,'SHARED',tmp_path/'shared')
            state=server.NewsState();monkeypatch.setattr(server,'STATE',state)
            httpd=ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
            thread=threading.Thread(target=httpd.serve_forever,daemon=True);thread.start()
            def stop():httpd.shutdown();httpd.server_close();thread.join()
            stack.callback(stop);base=f'http://127.0.0.1:{httpd.server_port}';token=state.token
        headers={'Authorization':'Bearer '+token}
        assert requests.get(base+'/v1/sources',timeout=5).status_code==401
        response=requests.get(base+'/v1/sources',headers=headers,timeout=5)
        assert response.status_code==200 and response.json()['sources']==SourceHealth(health.path).list()
        assert requests.get(base+'/v1/sources?unknown=1',headers=headers,timeout=5).status_code==400
        assert requests.post(base+'/v1/sources',headers=headers,json={},timeout=5).status_code==405
        env=dict(os.environ,PYTHONPATH=str(ROOT/'linux'),AGENT_NEWSBOT_HOME=str(tmp_path))
        cli=subprocess.run([sys.executable,'-m','agent_newsbot','source-health'],cwd=tmp_path,env=env,capture_output=True,text=True,timeout=10)
        assert cli.returncode==0 and json.loads(cli.stdout)==response.json()
        with sqlite3.connect(health.path) as db:db.execute("UPDATE source_health SET payload='{}'")
        failed=requests.get(base+'/v1/sources',headers=headers,timeout=5)
        assert failed.status_code==503 and failed.json()['error_detail']['code']=='temporarily_unavailable'


def test_same_feed_in_two_desks_retains_membership_without_false_busy(monkeypatch,tmp_path):
    from test_worldmonitor import provider
    with provider(b'<rss version="2.0"><channel><title>Empty</title></channel></rss>') as (base,calls):
        monkeypatch.setattr(engine,'RSS_FEEDS',{'science':[(base,'Fixture')],'tech':[(base,'Fixture')]})
        store=FreshnessStore(tmp_path/'seen.sqlite')
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda desk:engine.fetch_rss(desk,store,12,20),['science','tech']))
        assert len(calls)==2 and all(summary(result_checks(r))['state']=='success' for r in results)
        assert len(store.health.list())==2


def test_partial_coverage_with_healthy_transport_keeps_polling(health):
    first=health.call('begin')
    partial=health.call('finish',1001,lease=first['lease'],state='partial')
    assert partial['last_success']==0 and partial['last_usable']==1001
    assert partial['next_retry']==0 and partial['failure_streak']==0 and partial['last_state']=='partial'
    again=health.call('begin',1002)
    assert again['lease']
    broken=health.call('finish',1003,lease=again['lease'],state='partial',code='http_503')
    assert broken['next_retry']==1063 and broken['failure_streak']==1
