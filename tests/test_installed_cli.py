"""Installer wrapper and switch/rollback invariants; native service checks are separate."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import shlex
import sqlite3
import subprocess
import sys
import pytest
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('installer',ROOT/'linux/installer.py')
installer=importlib.util.module_from_spec(spec); spec.loader.exec_module(installer)


def test_installer_wrapper_finds_package_and_service_state(tmp_path):
    prefix=tmp_path/'prefix with spaces'; state=tmp_path/'service state'
    release=prefix/'releases/one'; release.mkdir(parents=True); state.mkdir()
    shutil.copytree(ROOT/'linux/agent_newsbot',release/'agent_newsbot')
    (release/'.venv/bin').mkdir(parents=True)
    interpreter=release/'.venv/bin/python'
    interpreter.write_text('#!/bin/sh\nexport PYTHONPATH='+shlex.quote(str(release))+'\nexec '+shlex.quote(sys.executable)+' "$@"\n'); interpreter.chmod(0o755)
    (prefix/'current').symlink_to(release)
    shared=state/'shared'; shared.mkdir()
    (shared/'latest.json').write_text(json.dumps({'fetched_at':'2026-11-27T00:00:00Z','total_articles':0,'desks':[],'articles':[]}))
    wrapper=tmp_path/'agent-newsbot'; wrapper.write_bytes(installer.wrapper(prefix,state,tmp_path/'absent-config')); wrapper.chmod(0o755)
    env={k:v for k,v in os.environ.items() if not k.startswith('AGENT_NEWSBOT_') and k!='PYTHONPATH'}
    result=subprocess.run([str(wrapper),'status'],cwd=tmp_path,env=env,capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    assert json.loads(result.stdout)['total_articles']==0
    assert not (state/'export/bridge.token').exists()


class FakeInstaller(installer.Installer):
    healthy=True
    def systemctl(self,*args,check=True):
        self.calls.append(args)
        return subprocess.CompletedProcess(args,0,stdout='',stderr='')
    def active(self): return True
    def wait_health(self,port): return self.healthy


def prepared(tmp_path):
    prefix=tmp_path/'prefix'; state=tmp_path/'state'; state.mkdir()
    old=prefix/'releases/old'; new=prefix/'releases/new'
    old.mkdir(parents=True); new.mkdir(); (new/'READY').write_text('ready')
    (prefix/'current').symlink_to(old)
    config=tmp_path/'config.env'; config.write_text('AGENT_NEWSBOT_PORT=19009\n'); config.chmod(0o600)
    obj=FakeInstaller(prefix,state,config,tmp_path/'units',tmp_path/'bin',user=True); obj.calls=[]
    obj.unit.parent.mkdir(); obj.unit.write_text('[Service]\nEnvironment=CUSTOM=keep\n')
    obj.dropin.parent.mkdir(); obj.dropin.write_text(installer.MARKER+'\nold runtime\n')
    obj.bin_dir.mkdir(); (obj.bin_dir/'agent-newsbot').write_text('#!/bin/sh\n'+installer.MARKER+'\nold CLI\n')
    with sqlite3.connect(state/'stories.sqlite') as db:
        db.execute('CREATE TABLE consumers(name TEXT,cursor INTEGER)'); db.execute("INSERT INTO consumers VALUES ('reader',7)")
    return obj,old,new


def test_upgrade_preserves_config_unit_and_journal_backup(tmp_path):
    obj,old,new=prepared(tmp_path); unit=obj.unit.read_bytes(); config=obj.config.read_bytes()
    obj.activate(new,19009)
    assert obj.current.resolve()==new
    assert obj.unit.read_bytes()==unit and obj.config.read_bytes()==config
    assert 'EnvironmentFile=' in obj.dropin.read_text()
    backup=next((obj.prefix/'recovery').glob('new-*/stories.sqlite'))
    with sqlite3.connect(backup) as db:
        assert db.execute('SELECT cursor FROM consumers').fetchone()[0]==7
    assert backup.stat().st_mode&0o777==0o600
    assert ('start',obj.service) in obj.calls and ('enable',obj.service) in obj.calls


def test_failed_health_restores_previous_release_unit_and_cli(tmp_path):
    obj,old,new=prepared(tmp_path); obj.healthy=False
    before=[p.read_bytes() for p in (obj.unit,obj.dropin,obj.bin_dir/'agent-newsbot',obj.config)]
    with pytest.raises(RuntimeError,match='health check'): obj.activate(new,19009)
    assert obj.current.resolve()==old
    assert before==[p.read_bytes() for p in (obj.unit,obj.dropin,obj.bin_dir/'agent-newsbot',obj.config)]
    assert obj.calls[-1]==('start',obj.service)
    assert ('enable',obj.service) not in obj.calls


def test_unprepared_release_does_not_stop_working_service(tmp_path):
    obj,old,new=prepared(tmp_path); (new/'READY').unlink()
    with pytest.raises(RuntimeError,match='preflight'): obj.activate(new,19009)
    assert not obj.calls and obj.current.resolve()==old


def test_reactivate_previous_release_retains_distinct_recovery_records(tmp_path):
    obj,old,new=prepared(tmp_path); (old/'READY').write_text('ready')
    obj.activate(new,19009)
    assert (obj.prefix/'previous').resolve()==old
    obj.activate(old,19009)
    assert obj.current.resolve()==old and (obj.prefix/'previous').resolve()==new
    obj.activate(new,19009)
    assert len(list((obj.prefix/'recovery').iterdir()))==3


def test_staging_restores_callers_umask(tmp_path,monkeypatch):
    obj,old,new=prepared(tmp_path)
    def stage(source):
        value=os.umask(0o022); assert value==0o022
        return new
    monkeypatch.setattr(obj,'_stage',stage)
    original=os.umask(0o077)
    try:
        assert obj.stage(tmp_path)==new
        observed=os.umask(0o077)
        assert observed==0o077
    finally: os.umask(original)


def test_late_cli_failure_restores_disabled_service_state(tmp_path,monkeypatch):
    obj,old,new=prepared(tmp_path)
    original=obj.systemctl
    def ctl(*args,check=True):
        if args[0]=='is-enabled': return subprocess.CompletedProcess(args,1,stdout='',stderr='')
        return original(*args,check=check)
    monkeypatch.setattr(obj,'systemctl',ctl)
    def failure(*args): raise RuntimeError('cannot prepare CLI')
    monkeypatch.setattr(installer,'wrapper',failure)
    with pytest.raises(RuntimeError,match='CLI'): obj.activate(new,19009)
    assert obj.current.resolve()==old and ('disable',obj.service) in obj.calls
