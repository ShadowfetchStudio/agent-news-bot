"""Budget exhaustion must preserve research and keep explicit recovery available."""
import contextlib
import json
import os
import sqlite3
import subprocess
import sys
import threading
from http.server import ThreadingHTTPServer

import pytest
import requests
from test_worldmonitor import (ROOT, article, swift, swift_harness, journal_runtime,
                               native_bridge, StoryStore, server)
from agent_newsbot.storage import DEFAULT, StorageBlocked, operations
from agent_newsbot.library import LibraryConflict
from agent_newsbot import recovery
from agent_newsbot.source_health import SourceHealth

MiB=1024**2


def change(version=0,ceiling=MiB,reserve=64*MiB):
    return dict(expected_version=version,max_journal_bytes=ceiling,min_free_bytes=reserve)


def fail_ingest(store,rows,request,tmp_path):
    if isinstance(store,StoryStore):
        with pytest.raises((sqlite3.Error,StorageBlocked)): store.ingest(rows)
    else:
        path=tmp_path/'oversize.json';path.write_text(json.dumps(rows))
        assert swift(request.getfixturevalue('swift_harness'),'ingest-error',store.path,path)['failed']


def test_storage_defaults_version_validation_and_no_ack(journal_runtime):
    store=journal_runtime
    row=store.ingest([article()])[0]
    pending=store.pending('reader')
    status=store.storage_status()
    assert status['policy']==DEFAULT
    assert status['stories']==status['events']==1 and not status['ingestion_blocked']
    assert status['scope']=='journal ingestion, not a filesystem quota'
    assert status['automatic_pruning'] is False
    saved=store.storage_change(change())
    assert saved==dict(version=1,max_journal_bytes=MiB,min_free_bytes=64*MiB)
    with pytest.raises(LibraryConflict):store.storage_change(change())
    for bad in [change(ceiling=MiB-1),change(reserve=0),change(version=True),change(ceiling=1.5),
                change(version=-1),change(version=2**63),dict(change(),unknown=3)]:
        with pytest.raises(ValueError):store.storage_change(bad)
    assert store.storage_status()['policy']==saved
    assert store.pending('reader')['events']==pending['events']
    assert store.story(row['id'])['revision']==1


def test_ingestion_ceiling_atomic_rollback_and_explicit_recovery(journal_runtime,request,tmp_path):
    store=journal_runtime
    original=store.ingest([article()])[0]
    store.pending('reader')
    store.alert_change(dict(action='subscribe',name='all',expected_version=0,filters={},enabled=True))
    before=store.storage_status()
    store.storage_change(change())
    oversized=article(summary='x'*(400*1024))
    fail_ingest(store,[oversized],request,tmp_path)
    after=store.storage_status()
    assert after['events']==before['events']==1 and after['unread_alerts']==0
    assert store.story(original['id'])['revision']==1
    # Editorial work can proceed despite the ingestion ceiling.
    edited=store.save_editorial(original['id'],dict(expected_revision=1,expected_editorial_version=0,notes='Keep my work',bookmarked=True))
    assert edited['editorial']['notes']=='Keep my work'
    store.storage_change(change(1,ceiling=8*MiB))
    assert store.ingest([oversized])[0]['revision']==2
    assert store.storage_status()['unread_alerts']==1
    # Lowering the ceiling never erases data. Reads and a later policy update recover.
    store.storage_change(change(2))
    assert store.storage_status()['blocking_reasons']==['journal_above_ceiling']
    fail_ingest(store,[article(summary='another update')],request,tmp_path)
    assert store.pending('reader')['count']==2
    assert store.research_export(original['id'])
    store.storage_change(change(3,ceiling=8*MiB))
    assert store.ingest([article(summary='another update')])[0]['revision']==3


def test_low_disk_preflight_and_refresh_job_does_not_contact_provider(monkeypatch,tmp_path):
    import agent_newsbot.storage as storage
    monkeypatch.setattr(server,'HOME',tmp_path);monkeypatch.setattr(server,'SHARED',tmp_path/'shared')
    state=server.NewsState()
    monkeypatch.setattr(storage.shutil,'disk_usage',lambda _:type('Usage',(),{'free':1})())
    monkeypatch.setattr(server,'fetch_all',lambda _:pytest.fail('provider must not be called'))
    assert state.stories.storage_status()['blocking_reasons']==['disk_below_reserve']
    with pytest.raises(StorageBlocked):state.refresh()
    assert state.fetch_job['state']=='failed' and state.fetching is False
    assert state.stories.storage_status()['events']==0


def test_operations_keeps_healthy_sections_on_failure(tmp_path,monkeypatch):
    journal=StoryStore(tmp_path/'stories.sqlite');journal.ingest([article()]);journal.pending('reader')
    health=SourceHealth(tmp_path/'health.sqlite')
    first=operations(journal,health)
    assert first['complete'] and first['sections']['delivery']['consumers'][0]['lag']==1
    monkeypatch.setattr(journal,'storage_status',lambda:(_ for _ in ()).throw(sqlite3.OperationalError('private detail')))
    result=operations(journal,health)
    assert not result['complete'] and set(result['sections'])=={'delivery','sources'}
    assert set(result['errors'])=={'storage'} and 'private detail' not in json.dumps(result)


@pytest.mark.parametrize('runtime',['python','swift'])
def test_operator_storage_http_contract(runtime,request,tmp_path,monkeypatch):
    with contextlib.ExitStack() as stack:
        if runtime=='swift':
            base,_=stack.enter_context(native_bridge(request.getfixturevalue('swift_harness'),tmp_path/'stories.sqlite'));token='integration-test-token'
        else:
            monkeypatch.setattr(server,'HOME',tmp_path);monkeypatch.setattr(server,'SHARED',tmp_path/'shared')
            state=server.NewsState();monkeypatch.setattr(server,'STATE',state)
            httpd=ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
            thread=threading.Thread(target=httpd.serve_forever,daemon=True);thread.start()
            def stop():httpd.shutdown();httpd.server_close();thread.join()
            stack.callback(stop);base=f'http://127.0.0.1:{httpd.server_port}';token=state.token
        headers={'Authorization':'Bearer '+token}
        def call(method,path,body=None,auth=True):return requests.request(method,base+path,headers=headers if auth else {},json=body,timeout=5)
        for path in ['/v1/operations','/v1/library/storage']:
            assert call('GET',path,auth=False).status_code==401
            assert call('GET',path+'?x=1').status_code==400
        observed=call('GET','/v1/operations').json()
        assert observed['complete'] and set(observed['sections'])=={'storage','delivery','sources'}
        assert call('POST','/v1/operations',{}).status_code==405
        assert call('POST','/v1/library/storage',{}).status_code==400
        assert call('POST','/v1/library/storage?x=1',change()).status_code==400
        assert call('POST','/v1/library/storage',change()).status_code==200
        assert call('POST','/v1/library/storage',change()).status_code==409
        assert call('GET','/v1/library/storage').json()['policy']['version']==1
        observed=call('GET','/v1/operations').json()
        assert observed['sections']['storage']['policy']['version']==1
        assert 'lease_token' not in json.dumps(observed)


def test_storage_policy_recovery_and_cli(tmp_path):
    home=tmp_path/'home';journal=StoryStore(home/'stories.sqlite');journal.ingest([article()])
    journal.storage_change(change())
    recovery.backup(home,tmp_path/'archive')
    recovery.restore(tmp_path/'archive',tmp_path/'restored')
    assert StoryStore(tmp_path/'restored/stories.sqlite').storage_status()['policy']['version']==1
    env=dict(os.environ,AGENT_NEWSBOT_HOME=str(home),PYTHONPATH=str(ROOT/'linux'))
    result=subprocess.run([sys.executable,'-m','agent_newsbot','operations'],env=env,text=True,capture_output=True)
    assert result.returncode==0,result.stderr
    assert json.loads(result.stdout)['sections']['storage']['policy']['version']==1
    changes=tmp_path/'changes.json';changes.write_text(json.dumps(change(1,2*MiB)))
    result=subprocess.run([sys.executable,'-m','agent_newsbot','storage-policy','--changes',str(changes)],env=env,text=True,capture_output=True)
    assert result.returncode==0,result.stderr
    assert json.loads(result.stdout)['version']==2
    with sqlite3.connect(journal.path) as db:db.execute("UPDATE library_preferences SET payload='{}' WHERE kind='storage'")
    with pytest.raises(recovery.RecoveryError,match='storage policy'):recovery.backup(home,tmp_path/'invalid')
