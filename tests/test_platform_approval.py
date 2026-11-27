import contextlib
import json
import sqlite3
import threading
from pathlib import Path
from http.server import ThreadingHTTPServer
import pytest
import requests
from test_worldmonitor import journal_runtime, swift_harness, swift, native_bridge, article, repair_request
from agent_newsbot import server
from agent_newsbot.stories import StoryStore
from agent_newsbot.library import write_package


def save(store, sid, **change):
    row = store.editorial(sid)
    return store.save_editorial(sid, dict(expected_revision=row['revision'], expected_editorial_version=row['editorial']['version'], **change))['editorial']


def test_approve_x_without_other_platforms_and_preserve_on_notes(journal_runtime):
    store=journal_runtime; sid=store.ingest([article()])[0]['id']
    state=save(store,sid,draft_patch={'x_post':'An independently reviewed post.'},platform_status={'x':'approved'})
    assert state['status']=='draft' and state['platforms']['x']['status']=='approved'
    assert state['drafts']['youtube_title']==''
    assert store.research_export(sid)['approved'] is False
    assert store.research_export(sid)['platform_approvals']['x']['approved_revision']==1
    state=save(store,sid,notes='A note should not approve or revoke content')
    assert state['platforms']['x']['status']=='approved'
    assert len(store.pending('events')['events'])==1
    assert store.editorial(sid)['editorial']['platforms']==state['platforms']


def test_exported_files_distinguish_platform_approval(journal_runtime,tmp_path):
    store=journal_runtime;sid=store.ingest([article()])[0]['id']
    save(store,sid,draft_patch={'x_post':'Reviewed X-only post'},platform_status={'x':'approved'})
    parent=tmp_path/'exports'
    if isinstance(store,StoryStore): folder=write_package(store.research_export(sid),parent)
    else: folder=Path(store.op({'op':'export-files','id':sid,'destination':str(parent)})['folder'])
    payload=json.loads((folder/'research.json').read_text())
    assert payload['approved'] is False and payload['platform_approvals']['x']['status']=='approved'
    text=(folder/'posts.md').read_text()
    assert 'Platform approval: x — approved' in text
    assert 'Platform approval: youtube — unreviewed' in text


def test_edit_one_platform_preserves_other_approvals_and_noop_save(journal_runtime):
    store=journal_runtime; sid=store.ingest([article()])[0]['id']
    save(store,sid,generate=True); state=save(store,sid,status='approved')
    assert all(p['status']=='approved' for p in state['platforms'].values())
    same=save(store,sid,drafts=state['drafts'])
    assert same['status']=='approved'
    changed=save(store,sid,draft_patch={'x_post':'New wording to review'})
    assert changed['platforms']['x']['status']=='draft'
    assert changed['platforms']['youtube']['status']=='approved'
    assert changed['platforms']['narration']['status']=='approved'
    assert changed['status']=='draft' and changed['approved_revision'] is None
    assert save(store,sid,platform_status={'x':'approved'})['status']=='approved'
    regenerated=save(store,sid,generate=True)
    assert all(p['status']=='draft' for p in regenerated['platforms'].values())


def test_source_change_invalidates_partial_approval_and_review_queue(journal_runtime):
    store=journal_runtime; sid=store.ingest([article()])[0]['id']
    save(store,sid,draft_patch={'x_post':'A post'},platform_status={'x':'approved'})
    store.ingest([article(title='NASA cancels lunar orbiter mission after engine trouble')])
    state=store.editorial(sid)['editorial']
    assert state['status']=='needs_review' and state['platforms']['x']['status']=='needs_review'
    assert store.library({'status':'needs_review'})['stories'][0]['id']==sid
    assert not store.library({'status':'approved'})['stories']
    state=save(store,sid,platform_status={'x':'approved'})
    assert state['platforms']['x']=={'status':'approved','approved_revision':2}
    assert state['status']=='draft'


def test_legacy_whole_package_approval_migrates_without_losing_decision(journal_runtime):
    store=journal_runtime; sid=store.ingest([article()])[0]['id']
    save(store,sid,generate=True); state=save(store,sid,status='approved')
    state.pop('platforms')
    with sqlite3.connect(store.path) as db: db.execute('UPDATE editorial SET payload=? WHERE story_id=?',(json.dumps(state),sid))
    restored=store.editorial(sid)['editorial']
    assert all(p['status']=='approved' for p in restored['platforms'].values())
    rejected=save(store,sid,status='rejected')
    assert all(p['status']=='rejected' for p in rejected['platforms'].values())


def test_group_repair_clears_parent_and_child_platform_approvals(journal_runtime):
    store=journal_runtime; a,b=article(),article(url='https://second.example/b')
    sid=store.ingest([a,b])[0]['id']
    save(store,sid,draft_patch={'x_post':'A reviewed group'},platform_status={'x':'approved'})
    receipt=store.repair_group(repair_request(store,sid,source_urls=[b['url']]))
    for ref in receipt['after']:
        state=store.editorial(ref['id'])['editorial']
        assert all(p['status']=='needs_review' and p['approved_revision'] is None for p in state['platforms'].values())


def test_all_platform_reapproval_updates_legacy_package_revision(journal_runtime):
    store=journal_runtime; sid=store.ingest([article()])[0]['id']
    save(store,sid,generate=True);save(store,sid,status='approved')
    store.ingest([article(title='NASA cancels lunar orbiter mission after engine trouble')])
    state=save(store,sid,platform_status={p:'approved' for p in ('youtube','x','tiktok','narration')})
    assert state['status']=='approved' and state['source_revision']==state['approved_revision']==2
    assert store.research_export(sid)['approved'] is True


@pytest.mark.parametrize('runtime',['python','swift'])
def test_platform_http_validation_conflicts_and_atomicity(runtime,request,tmp_path,monkeypatch):
    with contextlib.ExitStack() as stack:
        if runtime=='swift':
            binary=request.getfixturevalue('swift_harness'); db=tmp_path/'stories.sqlite'
            rows=tmp_path/'rows.json'; rows.write_text(json.dumps([article()]))
            sid=swift(binary,'ingest',db,rows)['articles'][0]['id']
            base,_=stack.enter_context(native_bridge(binary,db));token='integration-test-token'
        else:
            monkeypatch.setattr(server,'HOME',tmp_path);monkeypatch.setattr(server,'SHARED',tmp_path/'shared')
            state=server.NewsState();monkeypatch.setattr(server,'STATE',state)
            sid=state.stories.ingest([article()])[0]['id']
            httpd=ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
            thread=threading.Thread(target=httpd.serve_forever,daemon=True);thread.start()
            def stop(): httpd.shutdown();httpd.server_close();thread.join()
            stack.callback(stop);base=f'http://127.0.0.1:{httpd.server_port}';token=state.token
        headers={'Authorization':'Bearer '+token};url=base+'/v1/library/editorial?id='+sid
        bad=[{'platform_status':x} for x in (None,[],{},True,{'unknown':'approved'},{'x':True},{'x':'published'},{'x':'approved'})]
        bad += [{'draft_patch':x} for x in (None,[],{},True,{'unknown':'text'},{'x_post':1},{'x_post':'中'*141})]
        bad += [{'draft_patch':{'x_post':'one'},'drafts':{}},{'generate':True,'draft_patch':{'x_post':'one'}},
                {'status':'approved','platform_status':{'x':'approved'}},
                {'draft_patch':{'x_post':'one'},'platform_status':{'x':'approved','youtube':'approved'}}]
        for change in bad:
            response=requests.post(url,headers=headers,json=dict(expected_revision=1,expected_editorial_version=0,**change),timeout=5)
            assert response.status_code==400,(change,response.text)
            assert requests.get(url,headers=headers,timeout=5).json()['story']['editorial']['version']==0
        valid=dict(expected_revision=1,expected_editorial_version=0,draft_patch={'x_post':'A reviewed post'},platform_status={'x':'approved'})
        response=requests.post(url,headers=headers,json=valid,timeout=5)
        assert response.status_code==200,response.text
        assert requests.post(url,headers=headers,json=valid,timeout=5).status_code==409
        export=requests.get(base+'/v1/library/export?id='+sid,headers=headers,timeout=5).json()
        assert export['platform_approvals']['x']['status']=='approved' and export['approved'] is False
        assert requests.get(base+'/v1/capabilities',headers=headers,timeout=5).json()['features']['platform_approval']==1
