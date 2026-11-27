from __future__ import annotations
import json
import os
from pathlib import Path
import plistlib
import sqlite3
import subprocess
import sys
import pytest
from test_worldmonitor import ROOT, article
from agent_newsbot.stories import StoryStore
from agent_newsbot import application_recovery as app, recovery


@pytest.fixture
def inputs(tmp_path):
    home = tmp_path/'original'
    store = StoryStore(home/'stories.sqlite')
    story = store.ingest([article()])[0]
    store.save_editorial(story['id'], dict(expected_revision=1,expected_editorial_version=0,notes='Retain researched notes',generate=True))
    config = tmp_path/'config.env'
    config.write_text('AGENT_NEWSBOT_WORLDMONITOR_KEY="private-config-secret"\nAGENT_NEWSBOT_PORT=19876\n')
    config.chmod(0o600)
    exports = tmp_path/'selected exports'; exports.mkdir()
    (exports/'empty').mkdir(); (exports/'Research-one').mkdir()
    (exports/'latest.json').write_text(json.dumps({'articles':[story]}))
    (exports/'Research-one/posts.md').write_text('Saved platform text\n')
    (exports/'Research-one/sources.txt').write_text('https://example.com/a\n')
    (exports/'zero.txt').touch()
    (exports/'bridge.token').write_text('old-bridge-credential')
    (home/'bridge.token').write_text('old-bridge-credential')
    return home, config, exports, story


def create(inputs,tmp_path,**overrides):
    home,config,exports,_=inputs
    args=dict(platform='linux',config=config,exports={'snapshots':exports},writers_stopped=True)
    args.update(overrides)
    return app.backup(home,tmp_path/'archive',**args)


def test_application_restore_preserves_config_exports_empty_directories_and_research(inputs,tmp_path):
    home,config,exports,story=inputs
    before=(home/'stories.sqlite').read_bytes()
    result=create(inputs,tmp_path)
    manifest=app.verify(tmp_path/'archive')
    assert manifest['configuration_may_contain_secrets'] is True
    assert manifest['omitted_export_credentials']=={'snapshots':['bridge.token']}
    restored=app.restore(tmp_path/'archive',tmp_path/'recovered')
    assert restored['activated'] is False
    assert (tmp_path/'recovered/configuration/config.env').read_bytes()==config.read_bytes()
    for name in ['latest.json','zero.txt','Research-one/posts.md','Research-one/sources.txt']:
        assert (tmp_path/'recovered/exports/snapshots'/name).read_bytes()==(exports/name).read_bytes()
    assert (tmp_path/'recovered/exports/snapshots/empty').is_dir()
    assert not list((tmp_path/'recovered').rglob('bridge.token'))
    assert not list((tmp_path/'recovered').rglob('*INCOMPLETE*'))
    new=StoryStore(tmp_path/'recovered/home/stories.sqlite')
    assert new.editorial(story['id'])==StoryStore(home/'stories.sqlite').editorial(story['id'])
    assert before==(home/'stories.sqlite').read_bytes()
    for folder in [tmp_path/'archive',tmp_path/'recovered']:
        assert folder.stat().st_mode&0o777==0o700
        for path in folder.rglob('*'):
            assert path.stat().st_mode&0o777==(0o700 if path.is_dir() else 0o600)
    assert app.verify(tmp_path/'archive')==manifest


def test_mac_preferences_and_multiple_export_roots_roundtrip(inputs,tmp_path):
    home,config,exports,_=inputs
    config.write_bytes(plistlib.dumps({'enabledDesks':['science'],'autoRefresh':False,'hermesModel':'local-model'}))
    extra=tmp_path/'extra';extra.mkdir();(extra/'notes.md').write_text('Separate research export')
    create(inputs,tmp_path,platform='macos',exports={'snapshots':exports,'research':extra})
    app.restore(tmp_path/'archive',tmp_path/'recovered')
    assert plistlib.loads((tmp_path/'recovered/configuration/settings.plist').read_bytes())['hermesModel']=='local-model'
    assert (tmp_path/'recovered/exports/research/notes.md').read_text()=='Separate research export'


def test_explicit_no_exports_and_stopped_writers(inputs,tmp_path):
    with pytest.raises(ValueError,match='Stop'):
        create(inputs,tmp_path,writers_stopped=False)
    assert not (tmp_path/'archive').exists()
    create(inputs,tmp_path,exports={})
    assert app.verify(tmp_path/'archive')['directories']==['configuration','exports']


@pytest.mark.parametrize('kind',['config','export','research','extra','missing','path','directory','credential'])
def test_tampered_application_archives_fail_before_restore(inputs,tmp_path,kind):
    create(inputs,tmp_path);archive=tmp_path/'archive'
    manifest=json.loads((archive/'manifest.json').read_text())
    if kind=='config': (archive/'configuration/config.env').write_text('changed')
    elif kind=='export': (archive/'exports/snapshots/latest.json').write_text('changed')
    elif kind=='research':
        with sqlite3.connect(archive/'research/stories.sqlite') as db:db.execute('DELETE FROM editorial')
    elif kind=='extra': (archive/'extra').touch()
    elif kind=='missing': (archive/'exports/snapshots/zero.txt').unlink()
    elif kind=='path': manifest['files']['exports/../outside']={'bytes':0,'sha256':'a'*64}
    elif kind=='directory': manifest['directories'].append('exports/../outside')
    elif kind=='credential': manifest['files']['exports/snapshots/bridge.token']={'bytes':0,'sha256':'a'*64}
    (archive/'manifest.json').write_text(json.dumps(manifest))
    with pytest.raises((ValueError,OSError)):
        app.restore(archive,tmp_path/'restored')
    assert not (tmp_path/'restored').exists()


@pytest.mark.parametrize('kind',['symlink-file','symlink-directory','hardlink','fifo','linked-config'])
def test_linked_or_special_inputs_rejected(inputs,tmp_path,kind):
    _,config,exports,_=inputs
    if kind=='symlink-file':(exports/'link').symlink_to(config)
    elif kind=='symlink-directory':(exports/'link').symlink_to(tmp_path)
    elif kind=='hardlink':os.link(config,exports/'link')
    elif kind=='fifo':os.mkfifo(exports/'pipe')
    else:
        original=tmp_path/'actual';config.rename(original);config.symlink_to(original)
    with pytest.raises(ValueError):create(inputs,tmp_path)
    assert not (tmp_path/'archive').exists()


def test_source_mutation_is_not_reported_as_complete(inputs,tmp_path,monkeypatch):
    original=app.copy_file
    def changing(*args,**kwargs):
        value=original(*args,**kwargs)
        if args[0].name=='zero.txt':inputs[1].write_text('Changed while archiving')
        return value
    monkeypatch.setattr(app,'copy_file',changing)
    with pytest.raises(ValueError,match='Inputs changed'):create(inputs,tmp_path)
    assert (tmp_path/'archive/INCOMPLETE.json').exists()
    with pytest.raises((ValueError,OSError)):app.verify(tmp_path/'archive')


def test_existing_or_nested_destinations_preserved(inputs,tmp_path):
    home,config,exports,_=inputs
    (tmp_path/'archive').mkdir()
    with pytest.raises(FileExistsError):create(inputs,tmp_path)
    assert list((tmp_path/'archive').iterdir())==[]
    with pytest.raises(ValueError,match='outside'):
        app.backup(home,exports/'archive',platform='linux',config=config,exports={'a':exports},writers_stopped=True)
    assert not (exports/'archive').exists()


def test_failure_after_database_restore_keeps_home_incomplete(inputs,tmp_path,monkeypatch):
    create(inputs,tmp_path)
    original=recovery.write_json
    def fail(path,value):
        if path.name=='APPLICATION_RECOVERY.json':raise OSError('injected receipt failure')
        return original(path,value)
    monkeypatch.setattr(recovery,'write_json',fail)
    with pytest.raises(OSError):app.restore(tmp_path/'archive',tmp_path/'restored')
    assert (tmp_path/'restored/INCOMPLETE.json').exists()
    assert (tmp_path/'restored/home/RECOVERY_INCOMPLETE.json').exists()
    with pytest.raises(ValueError):StoryStore(tmp_path/'restored/home/stories.sqlite').library()


def test_application_byte_limit(inputs,tmp_path):
    (inputs[2]/'large').write_bytes(b'x'*(2*1024*1024))
    with pytest.raises(ValueError):create(inputs,tmp_path,max_bytes=1024*1024)
    assert (tmp_path/'archive/INCOMPLETE.json').exists()
    with pytest.raises((ValueError,OSError)):app.verify(tmp_path/'archive')


def test_archive_changes_during_restore_rejected(inputs,tmp_path,monkeypatch):
    create(inputs,tmp_path);original=app.copy_file
    def changing(source,*args,**kwargs):
        if source.name=='config.env':source.write_text('changed after verification')
        return original(source,*args,**kwargs)
    monkeypatch.setattr(app,'copy_file',changing)
    with pytest.raises(ValueError):app.restore(tmp_path/'archive',tmp_path/'restored')
    assert (tmp_path/'restored/INCOMPLETE.json').exists()
    assert not (tmp_path/'restored/home').exists()


def test_cli_and_packaged_helper_work_without_dependencies_or_valid_runtime_config(inputs,tmp_path):
    home,config,exports,_=inputs
    env=dict(os.environ,PYTHONPATH=str(ROOT/'linux'),AGENT_NEWSBOT_CONFIG='/missing/intentional')
    cmd=[sys.executable,'-S','-m','agent_newsbot','backup-application','--home',str(home),'--config',str(config),
         '--platform','linux','--exports','snapshots='+str(exports),'--writers-stopped','--destination',str(tmp_path/'archive')]
    result=subprocess.run(cmd,cwd=tmp_path,env=env,capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    assert 'private-config-secret' not in result.stdout+result.stderr
    standalone=subprocess.run([sys.executable,'-S',str(ROOT/'linux/agent_newsbot/application_recovery.py'),
                               'restore-application','--backup',str(tmp_path/'archive'),'--destination',str(tmp_path/'recovered')],
                              cwd=tmp_path,env=env,capture_output=True,text=True)
    assert standalone.returncode==0,standalone.stderr
    assert 'private-config-secret' not in standalone.stdout+standalone.stderr
    assert (tmp_path/'recovered/configuration/config.env').read_bytes()==config.read_bytes()


def test_low_disk_keeps_incomplete_archive(inputs,tmp_path,monkeypatch):
    from collections import namedtuple
    usage=namedtuple('Usage','total used free')
    monkeypatch.setattr(app.shutil,'disk_usage',lambda _:usage(100,99,1))
    with pytest.raises(ValueError):create(inputs,tmp_path)
    assert (tmp_path/'archive/INCOMPLETE.json').exists()
    assert not (tmp_path/'archive/manifest.json').exists()


def test_expired_operation_keeps_existing_inputs(inputs,tmp_path,monkeypatch):
    original=app.copy_file
    def expire(*args,**kwargs):
        value=original(*args,**kwargs)
        monkeypatch.setattr(app.time,'monotonic',lambda:float('inf'))
        return value
    monkeypatch.setattr(app,'copy_file',expire)
    with pytest.raises(ValueError):create(inputs,tmp_path)
    assert (tmp_path/'archive/INCOMPLETE.json').exists()
    assert inputs[1].read_text().startswith('AGENT_NEWSBOT_')


def test_manifest_inventory_and_depth_bounds(inputs,tmp_path,monkeypatch):
    monkeypatch.setattr(app,'MAX_ENTRIES',3)
    with pytest.raises(ValueError):create(inputs,tmp_path)
    assert not (tmp_path/'archive').exists()


def test_verifier_rejects_linked_archive_directory(inputs,tmp_path):
    create(inputs,tmp_path)
    linked=tmp_path/'linked';linked.symlink_to(tmp_path/'archive',target_is_directory=True)
    with pytest.raises(ValueError):app.verify(linked)


def test_cli_requires_explicit_exports_scope_and_unique_labels(inputs,tmp_path):
    home,config,exports,_=inputs
    base=[sys.executable,'-S',str(ROOT/'linux/agent_newsbot/application_recovery.py'),'backup-application',
          '--home',str(home),'--config',str(config),'--platform','linux','--destination',str(tmp_path/'archive'),'--writers-stopped']
    assert subprocess.run(base,capture_output=True).returncode==2
    result=subprocess.run(base+['--exports','a='+str(exports),'--exports','a='+str(exports)],capture_output=True)
    assert result.returncode==1
    assert not (tmp_path/'archive').exists()


def test_restored_linux_configuration_is_readable_by_runtime_loader(inputs,tmp_path):
    from agent_newsbot.private_config import load_private_config
    create(inputs,tmp_path)
    app.restore(tmp_path/'archive',tmp_path/'recovered')
    env={'AGENT_NEWSBOT_CONFIG':str(tmp_path/'recovered/configuration/config.env')}
    load_private_config(env)
    assert env['AGENT_NEWSBOT_PORT']=='19876'
    assert env['AGENT_NEWSBOT_WORLDMONITOR_KEY']=='private-config-secret'


@pytest.mark.skipif(sys.platform!='darwin',reason='Native macOS defaults export/import')
def test_native_mac_preferences_export_archive_restore_and_import(inputs,tmp_path):
    import uuid
    old='ai.shadowfetch.agentnewsbot.recovery-test.'+uuid.uuid4().hex
    new='ai.shadowfetch.agentnewsbot.recovery-test.'+uuid.uuid4().hex
    config=inputs[1]
    try:
        subprocess.run(['defaults','write',old,'autoRefresh','-bool','false'],check=True)
        subprocess.run(['defaults','write',old,'enabledDesks','-array','science','tech'],check=True)
        subprocess.run(['defaults','write',old,'bridgePort','-int','19876'],check=True)
        subprocess.run(['defaults','export',old,str(config)],check=True,capture_output=True)
        create(inputs,tmp_path,platform='macos')
        app.restore(tmp_path/'archive',tmp_path/'recovered')
        subprocess.run(['defaults','import',new,str(tmp_path/'recovered/configuration/settings.plist')],check=True,capture_output=True)
        restored=subprocess.run(['defaults','export',new,'-'],check=True,capture_output=True).stdout
        values=plistlib.loads(restored)
        assert values['autoRefresh'] is False and values['bridgePort']==19876
        assert values['enabledDesks']==['science','tech']
    finally:
        for domain in (old,new):subprocess.run(['defaults','delete',domain],capture_output=True)


@pytest.mark.parametrize('operation',['backup','restore'])
def test_finalization_failure_reinstates_incomplete_marker(inputs,tmp_path,monkeypatch,operation):
    if operation=='restore':create(inputs,tmp_path)
    original=recovery.sync_directory
    target=tmp_path/('archive' if operation=='backup' else 'restored')
    def fail(path):
        if path==target.parent and (target/'manifest.json' if operation=='backup' else target/'APPLICATION_RECOVERY.json').exists():
            raise OSError('injected directory sync failure')
        return original(path)
    monkeypatch.setattr(recovery,'sync_directory',fail)
    with pytest.raises(OSError):
        if operation=='backup':create(inputs,tmp_path)
        else:app.restore(tmp_path/'archive',target)
    assert (target/'INCOMPLETE.json').exists()
    if operation=='restore':assert (target/'home/RECOVERY_INCOMPLETE.json').exists()
