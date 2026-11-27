from __future__ import annotations
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading
from contextlib import ExitStack
from http.server import ThreadingHTTPServer
import pytest
import requests
from test_worldmonitor import ROOT, article, journal_runtime, swift_harness, native_bridge, repair_request
from agent_newsbot.stories import StoryStore
from agent_newsbot.library import LibraryConflict
from agent_newsbot import server, recovery, maintenance


def subscribe(store, name='Lunar', filters=None, version=0, enabled=True):
    return store.alert_change(dict(action='subscribe',name=name,filters={'q':'lunar'} if filters is None else filters,
                                   enabled=enabled,expected_version=version))


def seen(store,item):
    return store.alert_change(dict(action='mark_seen',name=item['name'],story_id=item['story_id'],expected_seq=item['seq']))


def test_subscription_starts_now_and_coalesces_reopens_on_new_revision(journal_runtime):
    store=journal_runtime
    row=store.ingest([article()])[0]
    sub=subscribe(store)
    assert store.alerts()['matches']==[]
    assert store.ingest([article()])==[]
    store.ingest([article(summary='Correction to lunar flight report')])
    item=store.alerts()['matches'][0]
    assert item['matched_revision']==2 and item['unread']
    store.ingest([article(summary='Another correction to lunar flight report')])
    assert store.alerts()['unread_count']==1
    with pytest.raises(LibraryConflict): seen(store,item)
    current=store.alerts()['matches'][0]
    assert current['matched_revision']==3
    before=store.pending('agent')
    assert seen(store,current)['seen_seq']==current['seq']
    assert seen(store,current)['seen_seq']==current['seq']
    assert store.alerts()['unread_count']==0 and store.alerts()['matches']==[]
    assert not store.alerts({'unread':'false'})['matches'][0]['unread']
    assert store.pending('agent')==before
    assert store.editorial(row['id'])['editorial']['version']==0
    store.ingest([article(summary='Latest correction to lunar flight report')])
    assert store.alerts()['unread_count']==1
    assert store.alerts()['matches'][0]['matched_revision']==4


def test_filters_pause_resume_and_stale_subscription_versions(journal_runtime):
    store=journal_runtime
    first=subscribe(store,filters={'desk':'science','provider':'worldmonitor','q':'lunar'})
    store.ingest([article()]); assert store.alerts()['unread_count']==0
    store.ingest([article(provider='worldmonitor')]); assert store.alerts()['unread_count']==1
    paused=subscribe(store,filters=first['filters'],version=first['version'],enabled=False)
    with pytest.raises(LibraryConflict):subscribe(store,version=first['version'])
    store.ingest([article(summary='Lunar correction during pause',provider='worldmonitor')])
    assert store.alerts()['matches'][0]['matched_revision']==2
    resumed=subscribe(store,filters=first['filters'],version=paused['version'])
    assert store.alerts()['matches'][0]['matched_revision']==2
    store.ingest([article(summary='Lunar correction after resume',provider='worldmonitor')])
    assert store.alerts()['matches'][0]['subscription_version']==resumed['version']
    assert store.alerts()['matches'][0]['matched_revision']==4
    assert store.alerts()['matches'][0]['current_revision']==4


def test_deleted_name_reuse_does_not_accept_old_editor(journal_runtime):
    store=journal_runtime;first=subscribe(store)
    store.alert_change(dict(action='delete',name='Lunar',expected_version=first['version']))
    replacement=subscribe(store)
    assert replacement['version']>first['version']
    with pytest.raises(LibraryConflict):subscribe(store,version=first['version'])
    with pytest.raises(LibraryConflict):store.alert_change(dict(action='delete',name='Lunar',expected_version=first['version']))
    assert store.alerts()['subscriptions'][0]['version']==replacement['version']


def test_corrected_platform_approval_appears_in_review_queue_and_subscription(journal_runtime):
    store=journal_runtime;row=store.ingest([article()])[0];sid=row['id']
    store.save_editorial(sid,dict(expected_revision=1,expected_editorial_version=0,draft_patch={'x_post':'Reviewed X text'},platform_status={'x':'approved'}))
    subscribe(store,filters={'status':'needs_review'})
    store.ingest([article(summary='A correction invalidates the approval')])
    assert store.alerts()['unread_count']==1
    assert [s['id'] for s in store.library({'status':'needs_review','include_muted':'true'})['stories']]==[sid]
    seen(store,store.alerts()['matches'][0])
    assert len(store.library({'status':'needs_review'})['stories'])==1
    state=store.editorial(sid)['editorial']
    store.save_editorial(sid,dict(expected_revision=2,expected_editorial_version=state['version'],platform_status={'x':'approved'}))
    assert store.library({'status':'needs_review'})['stories']==[]


def test_group_repair_matches_once_and_keeps_inbox_across_nonmatching_changes(journal_runtime):
    store=journal_runtime;a,b=article(),article(url='https://second.example/other')
    row=store.ingest([a,b])[0];subscribe(store,filters={'status':'needs_review'})
    request=repair_request(store,row['id'],source_urls=[b['url']])
    store.repair_group(request)
    matches=store.alerts()['matches']
    assert len(matches)==2
    store.repair_group(request)
    assert store.alerts()['matches']==matches
    for item in matches: assert item['current_revision']==item['matched_revision']


def test_alert_filters_do_not_depend_on_library_page(journal_runtime):
    store=journal_runtime;subscribe(store,filters={'source':'needle'})
    # Many unrelated sources are newer than the match, but ingestion emits the
    # matching story directly; library pagination cannot hide it from the watch.
    wanted=article(url='https://needle.example/a');wanted['source']='needle'
    rest=[article(url=f'https://other.example/{n}',title=f'Independent station {n} releases geological findings') for n in range(110)]
    store.ingest([wanted]+rest)
    assert store.alerts()['unread_count']==1
    assert store.alerts()['matches'][0]['story_id']==wanted['id']


def test_alert_mutations_validate_and_preserve_prior_state(journal_runtime):
    store=journal_runtime;sub=subscribe(store)
    for change in [dict(action='subscribe',name='Lunar',expected_version=True,filters={},enabled=True),
                   dict(action='subscribe',name='Lunar',expected_version=sub['version'],filters={'limit':'1'},enabled=True),
                   dict(action='subscribe',name='Lunar',expected_version=sub['version'],filters={},enabled=1),
                   dict(action='delete',name='Lunar',expected_version=sub['version'],extra=True)]:
        with pytest.raises(ValueError):store.alert_change(change)
    assert store.alerts()['subscriptions']==[sub]
    for n in range(31):subscribe(store,name=f'Watch{n}')
    with pytest.raises(LibraryConflict):subscribe(store,name='Over capacity')
    assert len(store.alerts()['subscriptions'])==32


def test_restart_backup_and_retention_preserve_unread_match(tmp_path):
    path=tmp_path/'home/stories.sqlite';store=StoryStore(path)
    subscribe(store,filters={'q':'special phrase'})
    sid=store.ingest([article(summary='A special phrase in this report')])[0]['id']
    for n in range(5):store.ingest([article(summary=f'Later corrected account {n}')])
    batch=store.pending('agent');store.acknowledge('agent',batch['next_cursor'])
    reopened=StoryStore(path);item=reopened.alerts()['matches'][0]
    assert item['matched_revision']==1 and item['current_revision']==6
    value=maintenance.plan(path.parent,'2099-01-01T00:00:00Z')
    assert 1 not in [x['seq'] for x in value['selection']['entries']]
    recovery.backup(path.parent,tmp_path/'backup')
    maintenance.prune(path.parent,value,tmp_path/'backup')
    recovery.backup(path.parent,tmp_path/'pruned');recovery.restore(tmp_path/'pruned',tmp_path/'restored')
    restored=StoryStore(tmp_path/'restored/stories.sqlite')
    assert restored.alerts()['matches']==reopened.alerts()['matches']
    seen(restored,item)
    value=maintenance.plan(restored.path.parent,'2099-01-01T00:00:00Z')
    assert 1 in [x['seq'] for x in value['selection']['entries']]
    assert restored.story(sid)['revision']==6


def test_alert_failure_rolls_back_story_and_event(tmp_path,monkeypatch):
    import agent_newsbot.stories as module
    store=StoryStore(tmp_path/'stories.sqlite');subscribe(store)
    def fail(db,ids):raise OSError('injected inbox persistence failure')
    monkeypatch.setattr(module,'record_alerts',fail)
    with pytest.raises(OSError):store.ingest([article()])
    assert store.library()['stories']==[] and store.pending('agent')['count']==0


@pytest.mark.parametrize('runtime',['python','swift'])
def test_alerts_http_contract(runtime,request,tmp_path,monkeypatch):
    path=tmp_path/'stories.sqlite';store=StoryStore(path)
    with ExitStack() as stack:
        if runtime=='swift':
            base,_=stack.enter_context(native_bridge(request.getfixturevalue('swift_harness'),path));token='integration-test-token'
        else:
            monkeypatch.setattr(server,'HOME',tmp_path);monkeypatch.setattr(server,'SHARED',tmp_path/'shared')
            state=server.NewsState();monkeypatch.setattr(server,'STATE',state)
            httpd=ThreadingHTTPServer(('127.0.0.1',0),server.Handler);thread=threading.Thread(target=httpd.serve_forever,daemon=True);thread.start()
            def stop():httpd.shutdown();httpd.server_close();thread.join()
            stack.callback(stop);base=f'http://127.0.0.1:{httpd.server_port}';token=state.token
        url=base+'/v1/library/alerts';headers={'Authorization':'Bearer '+token}
        assert requests.get(url,timeout=5).status_code==401
        assert requests.put(url,headers=headers,timeout=5).status_code==405
        assert requests.get(url+'?bogus=true',headers=headers,timeout=5).status_code==400
        command=dict(action='subscribe',name='Lunar',filters={'q':'lunar'},expected_version=0,enabled=True)
        assert requests.post(url+'?name=Lunar',json=command,headers=headers,timeout=5).status_code==400
        response=requests.post(url,json=command,headers=headers,timeout=5)
        assert response.status_code==200,response.text
        assert requests.post(url,json=command,headers=headers,timeout=5).status_code==409
        store.ingest([article()])
        data=requests.get(url,headers=headers,timeout=5).json();item=data['matches'][0]
        assert data['unread_count']==1
        command=dict(action='mark_seen',name='Lunar',story_id=item['story_id'],expected_seq=item['seq'])
        assert requests.post(url,json=command,headers=headers,timeout=5).status_code==200
        assert requests.get(url,headers=headers,timeout=5).json()['unread_count']==0
        assert requests.get(base+'/v1/capabilities',headers=headers,timeout=5).json()['features']['saved_search_alerts']==1


def test_alerts_cli_preserves_subscription_and_unread(tmp_path):
    home=tmp_path/'home';store=StoryStore(home/'stories.sqlite')
    command=tmp_path/'change.json';command.write_text(json.dumps(dict(action='subscribe',name='Lunar',filters={},expected_version=0,enabled=True)))
    env=dict(os.environ,PYTHONPATH=str(ROOT/'linux'),AGENT_NEWSBOT_HOME=str(home))
    process=subprocess.run([sys.executable,'-m','agent_newsbot','alerts','--changes',str(command)],env=env,capture_output=True,text=True)
    assert process.returncode==0,process.stderr
    store.ingest([article()])
    process=subprocess.run([sys.executable,'-m','agent_newsbot','alerts'],env=env,capture_output=True,text=True)
    assert process.returncode==0,process.stderr
    assert json.loads(process.stdout)['unread_count']==1


def test_match_write_failure_is_atomic_in_both_runtimes(journal_runtime):
    store=journal_runtime;subscribe(store)
    with sqlite3.connect(store.path) as db:
        db.execute("CREATE TRIGGER fail_match BEFORE INSERT ON watch_matches BEGIN SELECT RAISE(ABORT,'injected inbox failure'); END")
    with pytest.raises((sqlite3.Error,subprocess.CalledProcessError)):store.ingest([article()])
    with sqlite3.connect(store.path) as db:
        assert db.execute('SELECT count(*) FROM stories').fetchone()[0]==0
        assert db.execute('SELECT count(*) FROM events').fetchone()[0]==0
        db.execute('DROP TRIGGER fail_match')
    store.ingest([article()])
    assert store.alerts()['unread_count']==1


def test_pre_alert_archive_restores_and_current_runtime_initializes_tables(tmp_path):
    path=tmp_path/'home/stories.sqlite';store=StoryStore(path);store.ingest([article()])
    with sqlite3.connect(path) as db:
        for table in ['watch_matches','watch_alerts','watch_clock']:db.execute('DROP TABLE '+table)
    recovery.backup(path.parent,tmp_path/'old')
    recovery.restore(tmp_path/'old',tmp_path/'restored')
    new=StoryStore(tmp_path/'restored/stories.sqlite')
    assert new.alerts()['subscriptions']==[]
    assert subscribe(new)['version']==1


@pytest.mark.parametrize('damage',['clock','orphan','unread_event','version','filters','filter_value','matched_revision'])
def test_recovery_rejects_invalid_alert_state(tmp_path,damage):
    path=tmp_path/'home/stories.sqlite';store=StoryStore(path);subscribe(store);store.ingest([article()])
    with sqlite3.connect(path) as db:
        if damage=='clock':db.execute('DELETE FROM watch_clock')
        elif damage=='orphan':db.execute("UPDATE watch_matches SET alert='missing'")
        elif damage=='unread_event':db.execute('UPDATE watch_matches SET seq=999')
        elif damage=='version':db.execute('UPDATE watch_matches SET subscription_version=999')
        elif damage=='matched_revision':db.execute('UPDATE watch_matches SET revision=999')
        else:
            raw=json.loads(db.execute('SELECT payload FROM watch_alerts').fetchone()[0]);raw['filters']={'status':'not-a-status'} if damage=='filter_value' else {'limit':'1'}
            db.execute('UPDATE watch_alerts SET payload=?',(json.dumps(raw),))
    with pytest.raises(recovery.RecoveryError):recovery.backup(path.parent,tmp_path/'invalid')


def test_alert_pagination_and_muted_sources(journal_runtime):
    store=journal_runtime
    subscribe(store,filters={});subscribe(store,name='Including muted',filters={'include_muted':'true'})
    store.library_settings('mute','Example',True,write=True)
    store.ingest([article()])
    page=store.alerts({'limit':'1'})
    assert page['unread_count']==1 and page['matches'][0]['name']=='Including muted'
    store.library_settings('mute','Example',None,write=True)
    store.ingest([article(summary='Later correction to report')])
    page=store.alerts({'limit':'1'})
    assert page['unread_count']==2 and page['next_offset']==1
    second=store.alerts({'limit':'1','offset':'1'})
    assert second['matches'][0]['name']!=page['matches'][0]['name'] and second['next_offset'] is None
