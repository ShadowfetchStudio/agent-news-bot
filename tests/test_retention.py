from __future__ import annotations
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from contextlib import ExitStack
from http.server import ThreadingHTTPServer
import threading
import pytest
import requests
from test_worldmonitor import ROOT, article, swift, swift_harness, native_bridge
from agent_newsbot.stories import StoryStore
from agent_newsbot.consumers import ConsumerConflict
from agent_newsbot import maintenance as m, recovery, server

BEFORE = '2099-01-01T00:00:00+00:00'


@pytest.fixture
def journal(tmp_path):
    store = StoryStore(tmp_path / 'home' / 'stories.sqlite')
    for n in range(8):
        row = store.ingest([article(summary=f'Corrected research account number {n}')])[0]
    return store, row['id']


def acknowledge_all(store, name='reader'):
    batch = store.pending(name)
    store.acknowledge(name, batch['next_cursor'], generation=batch['generation'])


def archive_plan(store, tmp_path):
    value = m.plan(store.path.parent, BEFORE)
    folder = tmp_path / 'archive'
    recovery.backup(store.path.parent, folder)
    return value, folder


def event_count(store):
    with sqlite3.connect(store.path) as db:
        return db.execute('SELECT count(*) FROM events').fetchone()[0]


def test_no_consumers_and_unacknowledged_consumers_block_pruning(journal):
    store, _ = journal
    assert m.plan(store.path.parent, BEFORE)['selection']['entries'] == []
    acknowledge_all(store)
    store.pending('lagging')  # Offered is not acknowledged.
    assert m.plan(store.path.parent, BEFORE)['selection']['entries'] == []
    status = m.storage_status(store.path.parent)
    assert status['events'] == 8 and status['consumers'][0]['lag'] == 8
    assert status['automatic_pruning'] is False and status['database_bytes'] > 0


def test_archive_prune_replay_and_restore_boundary(journal, tmp_path):
    store, sid = journal
    acknowledge_all(store)
    original = store.story(sid)
    value, folder = archive_plan(store, tmp_path)
    assert [x['seq'] for x in value['selection']['entries']] == list(range(1,7))
    receipt = m.prune(store.path.parent, value, folder)
    assert receipt['pruned_count'] == 6 and receipt['pruned_through'] == 6
    assert m.prune(store.path.parent, value, folder) == dict(receipt, replayed=True)
    assert event_count(store) == 2 and store.story(sid) == original
    assert store.pending('reader')['events'] == []
    for action in [lambda:store.pending('new'), lambda:store.consumer_change(dict(consumer='new',action='create',start='history')),
                   lambda:store.consumer_change(dict(consumer='new',action='create',start='date',since=BEFORE))]:
        with pytest.raises(ConsumerConflict): action()
    assert [r['consumer'] for r in store.consumer_status()['consumers']] == ['reader']
    state = store.consumer_change(dict(consumer='new',action='create',start='retained'))
    assert state['cursor'] == 6 and store.pending('new')['count'] == 2
    state = store.consumer_change(dict(consumer='future',action='create',start='now'))
    assert state['cursor'] == 8
    history = store.revisions(sid)
    assert history[0]['history_gap_before'] is True and history[0]['diff'] == {}
    assert history[1]['history_gap_before'] is False and 'summary' in history[1]['diff']
    recovery.backup(store.path.parent, tmp_path/'pruned-backup')
    recovery.restore(tmp_path/'pruned-backup', tmp_path/'restored')
    restored = StoryStore(tmp_path/'restored/stories.sqlite')
    assert restored.consumer_status()['retention']['pruned_through'] == 6
    assert restored.pending('new')['count'] == 2
    # The archive still contains all eight exact revisions, with its old boundary.
    recovery.restore(folder, tmp_path/'full-history')
    assert len(StoryStore(tmp_path/'full-history/stories.sqlite').revisions(sid)) == 8


def test_bookmarks_draft_references_approvals_and_repairs_preserved(journal, tmp_path):
    store, sid = journal
    acknowledge_all(store)
    with sqlite3.connect(store.path) as db:
        editorial = {'bookmarked':True,'source_revision':2,'approved_revision':3,
                     'platforms':{'x':{'approved_revision':4}}}
        db.execute('INSERT INTO editorial VALUES (?,?)',(sid,json.dumps(editorial)))
    assert m.plan(store.path.parent, BEFORE)['selection']['entries'] == []
    with sqlite3.connect(store.path) as db:
        editorial['bookmarked'] = False
        db.execute('UPDATE editorial SET payload=?',(json.dumps(editorial),))
        row=json.loads(db.execute('SELECT payload FROM events WHERE seq=5').fetchone()[0]); row['change']='group_repaired'
        db.execute('UPDATE events SET payload=? WHERE seq=5',(json.dumps(row),))
    value, folder = archive_plan(store, tmp_path)
    assert [e['seq'] for e in value['selection']['entries']] == [1,6]
    m.prune(store.path.parent,value,folder)
    assert [r['story']['revision'] for r in store.revisions(sid)] == [2,3,4,5,7,8]


@pytest.mark.parametrize('change',['bookmark','consumer_reset','new_revision','payload'])
def test_stale_plan_rolls_back_without_removing_history(journal,tmp_path,change):
    store,sid=journal; acknowledge_all(store)
    value,folder=archive_plan(store,tmp_path)
    if change=='bookmark': store.save_editorial(sid,dict(expected_revision=8,expected_editorial_version=0,bookmarked=True))
    elif change=='consumer_reset': store.consumer_change(dict(consumer='reader',action='reset',start='history',expected_generation=0))
    elif change=='new_revision': store.ingest([article(summary='A newer revision arrived')]); acknowledge_all(store)
    else:
        with sqlite3.connect(store.path) as db:
            raw=json.loads(db.execute('SELECT payload FROM events WHERE seq=1').fetchone()[0]); raw['summary']='changed'
            db.execute('UPDATE events SET payload=? WHERE seq=1',(json.dumps(raw),))
    count=event_count(store)
    with pytest.raises(ValueError,match='stale'): m.prune(store.path.parent,value,folder)
    assert event_count(store)==count
    assert store.consumer_status()['retention']['pruned_through']==0


def test_changed_backup_after_verification_cannot_prune(journal,tmp_path,monkeypatch):
    store,_=journal; acknowledge_all(store)
    value,folder=archive_plan(store,tmp_path)
    original=recovery.verify
    def change(*args,**kwargs):
        result=original(*args,**kwargs)
        with sqlite3.connect(folder/'stories.sqlite') as db: db.execute("UPDATE migrations SET name='changed'")
        return result
    monkeypatch.setattr(recovery,'verify',change)
    with pytest.raises(ValueError,match='changed'): m.prune(store.path.parent,value,folder)
    assert event_count(store)==8


def test_wrong_valid_backup_and_edited_plan_rejected(journal,tmp_path):
    store,_=journal; acknowledge_all(store)
    value,folder=archive_plan(store,tmp_path)
    altered=dict(value,limit=1)
    with pytest.raises(ValueError,match='checksum'): m.prune(store.path.parent,altered,folder)
    other=StoryStore(tmp_path/'other/stories.sqlite'); other.ingest([article(summary='Different event')])
    recovery.backup(other.path.parent,tmp_path/'wrong')
    with pytest.raises(ValueError,match='exact planned'): m.prune(store.path.parent,value,tmp_path/'wrong')
    assert event_count(store)==8


def test_delete_and_boundary_commit_are_atomic(journal,tmp_path,monkeypatch):
    store,_=journal; acknowledge_all(store)
    value,folder=archive_plan(store,tmp_path)
    original=m.canonical
    def fail(value):
        if 'receipts' in value: raise OSError('injected failure after deletion before state save')
        return original(value)
    monkeypatch.setattr(m,'canonical',fail)
    with pytest.raises(OSError): m.prune(store.path.parent,value,folder)
    assert event_count(store)==8 and store.consumer_status()['retention']['pruned_through']==0


def test_old_v1_archive_still_recovers(journal,tmp_path):
    store,_=journal
    with sqlite3.connect(store.path) as db: db.execute('DROP TABLE journal_retention')
    recovery.backup(store.path.parent,tmp_path/'old')
    recovery.restore(tmp_path/'old',tmp_path/'restored')
    assert len(StoryStore(tmp_path/'restored/stories.sqlite').pending('new')['events'])==8


def test_private_cli_works_without_dependencies_or_config(journal,tmp_path):
    store,_=journal; acknowledge_all(store)
    plan_path=tmp_path/'plan.json'
    env=dict(os.environ,PYTHONPATH=str(ROOT/'linux'),AGENT_NEWSBOT_CONFIG='/missing/config')
    command=[sys.executable,'-S','-m','agent_newsbot']
    result=subprocess.run(command+['retention-plan','--home',str(store.path.parent),'--before',BEFORE,'--output',str(plan_path)],env=env,capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    assert plan_path.stat().st_mode & 0o777==0o600
    assert subprocess.run(command+['retention-plan','--home',str(store.path.parent),'--before',BEFORE,'--output',str(plan_path)],env=env,capture_output=True).returncode==1
    recovery.backup(store.path.parent,tmp_path/'archive')
    result=subprocess.run([sys.executable,'-S',str(ROOT/'linux/agent_newsbot/maintenance.py'),'prune-history','--home',str(store.path.parent),'--plan',str(plan_path),'--backup',str(tmp_path/'archive')],env=env,capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    assert json.loads(result.stdout)['pruned_count']==6


@pytest.mark.parametrize('runtime',['python','swift'])
def test_retained_consumer_http_and_timeline(runtime,request,journal,tmp_path,monkeypatch):
    store,sid=journal; acknowledge_all(store)
    value,folder=archive_plan(store,tmp_path); m.prune(store.path.parent,value,folder)
    with ExitStack() as stack:
        if runtime=='swift':
            base,_=stack.enter_context(native_bridge(request.getfixturevalue('swift_harness'),store.path)); token='integration-test-token'
        else:
            monkeypatch.setattr(server,'HOME',store.path.parent); monkeypatch.setattr(server,'SHARED',tmp_path/'shared')
            state=server.NewsState(); monkeypatch.setattr(server,'STATE',state)
            httpd=ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
            thread=threading.Thread(target=httpd.serve_forever,daemon=True);thread.start()
            def stop():httpd.shutdown();httpd.server_close();thread.join()
            stack.callback(stop);base=f'http://127.0.0.1:{httpd.server_port}';token=state.token
        headers={'Authorization':'Bearer '+token}
        assert requests.get(base+'/v1/capabilities',headers=headers,timeout=5).json()['features']['retained_history']==1
        assert requests.get(base+'/v1/events?consumer=late',headers=headers,timeout=5).status_code==409
        for start in ['history','date']:
            body=dict(consumer='late',action='create',start=start)
            if start=='date':body['since']=BEFORE
            assert requests.post(base+'/v1/consumers',json=body,headers=headers,timeout=5).status_code==409
        r=requests.post(base+'/v1/consumers',json=dict(consumer='late',action='create',start='retained'),headers=headers,timeout=5)
        assert r.status_code==200,r.text
        batch=requests.get(base+'/v1/events?consumer=late',headers=headers,timeout=5).json()
        assert batch['pruned_through']==6 and batch['count']==2
        history=requests.get(base+f'/v1/stories/{sid}/revisions',headers=headers,timeout=5).json()['revisions']
        assert history[0]['history_gap_before'] and history[0]['diff']=={}
        assert not history[1]['history_gap_before']

@pytest.mark.parametrize('failure',['space','timeout','sidecar_link','incomplete'])
def test_environment_failures_preserve_journal(journal,tmp_path,monkeypatch,failure):
    store,_=journal; acknowledge_all(store)
    value,folder=archive_plan(store,tmp_path)
    if failure=='space':
        from collections import namedtuple
        usage=namedtuple('usage','total used free')
        monkeypatch.setattr(m.shutil,'disk_usage',lambda _:usage(1000,999,1))
    elif failure=='sidecar_link':
        sidecar=Path(str(store.path)+'-journal'); target=tmp_path/'target';target.write_bytes(b'')
        sidecar.symlink_to(target)
    elif failure=='incomplete': (store.path.parent/'RECOVERY_INCOMPLETE.json').write_text('{}')
    kwargs={'timeout':0} if failure=='timeout' else {}
    with pytest.raises((OSError,ValueError)): m.prune(store.path.parent,value,folder,**kwargs)
    if failure=='sidecar_link': sidecar.unlink()
    assert event_count(store)==8


def test_acknowledged_history_can_prune_with_active_lease_without_disrupting_delivery(journal,tmp_path):
    store,_=journal
    batch=store.pending('reader')
    store.acknowledge('reader',4,generation=batch['generation'])
    lease=store.consumer_change(dict(consumer='reader',action='lease',expected_generation=0,worker='agent'))
    value,folder=archive_plan(store,tmp_path)
    assert [e['seq'] for e in value['selection']['entries']]==[1,2,3,4]
    m.prune(store.path.parent,value,folder)
    pending=store.pending('reader',lease=lease['lease'])
    assert [e['seq'] for e in pending['events']]==[5,6,7,8]
    store.acknowledge('reader',8,generation=lease['generation'],lease=lease['lease'])
    with pytest.raises(ConsumerConflict): store.consumer_change(dict(consumer='reader',action='reset',start='retained',expected_generation=lease['generation']))


def test_consumer_created_between_plan_and_prune_blocks_transaction(journal,tmp_path,monkeypatch):
    store,_=journal; acknowledge_all(store)
    value,folder=archive_plan(store,tmp_path)
    original=recovery.verify
    def concurrent(*args,**kwargs):
        result=original(*args,**kwargs)
        StoryStore(store.path).consumer_change(dict(consumer='arriving',action='create',start='history'))
        return result
    monkeypatch.setattr(recovery,'verify',concurrent)
    with pytest.raises(ValueError,match='stale'):m.prune(store.path.parent,value,folder)
    assert event_count(store)==8 and store.pending('arriving')['count']==8


def test_date_cutoff_and_bounded_batches(journal,tmp_path):
    store,_=journal; acknowledge_all(store)
    assert not m.plan(store.path.parent,'2000-01-01T00:00:00Z')['selection']['entries']
    with pytest.raises(ValueError):m.plan(store.path.parent,'2026-01-01')
    with pytest.raises(ValueError):m.plan(store.path.parent,BEFORE,limit=True)
    value=m.plan(store.path.parent,BEFORE,limit=2)
    assert [e['seq'] for e in value['selection']['entries']]==[1,2]
    recovery.backup(store.path.parent,tmp_path/'archive')
    m.prune(store.path.parent,value,tmp_path/'archive')
    next_plan=m.plan(store.path.parent,BEFORE,limit=2)
    assert [e['seq'] for e in next_plan['selection']['entries']]==[3,4]
    m.prune(store.path.parent,next_plan,tmp_path/'archive')
    assert store.consumer_status()['retention']['pruned_through']==4
    assert m.storage_status(store.path.parent)['retention']['pruned_events']==4
