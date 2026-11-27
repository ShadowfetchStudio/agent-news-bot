"""Explicit native Linux smoke test. Creates/removes its own systemd user unit only.
Leaves isolated /tmp release/state/recovery artifacts for inspection. Not a pytest test.
"""
from pathlib import Path
import importlib.util
import hashlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('installer',ROOT/'linux/installer.py')
m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
base=Path(tempfile.mkdtemp(prefix='agent-newsbot-install-validation-'))
service='agent-news-bot-validation-'+base.name.rsplit('-',1)[1]+'.service'
unit_dir=Path.home()/'.config/systemd/user'
state=base/'state'; (state/'shared').mkdir(parents=True)
(state/'shared/latest.json').write_text(json.dumps({'articles':[],'desks':[],'total_articles':0}))
with socket.socket() as sock: sock.bind(('127.0.0.1',0)); port=sock.getsockname()[1]
config=base/'private config.env'; config.write_text(f'AGENT_NEWSBOT_PORT={port}\nAGENT_NEWSBOT_FETCH_INTERVAL=3600\nAGENT_NEWSBOT_WEATHER_CITY="Fixture City"\n'); config.chmod(0o600)
obj=m.Installer(base/'prefix',state,config,unit_dir,base/'bin',user=True,service=service)
report={'base':str(base),'service':service,'port':port}
try:
    original=config.read_bytes()
    first=obj.stage(ROOT/'linux'); obj.activate(first,port)
    assert obj.wait_health(port)
    env=dict(os.environ,AGENT_NEWSBOT_HOME=str(state),AGENT_NEWSBOT_CONFIG=str(config)); env.pop('PYTHONPATH',None)
    subprocess.run([str(first/'.venv/bin/python'),'-c',"from agent_newsbot.stories import StoryStore; from agent_newsbot.config import HOME; StoryStore(HOME/'stories.sqlite').consumer_change({'consumer':'preserved-reader','action':'create','start':'history'})"],check=True,env=env,cwd=base)
    second=obj.stage(ROOT/'linux'); obj.activate(second,port)
    status=subprocess.check_output([str(base/'bin/agent-newsbot'),'consumers'],env=env,cwd='/tmp',text=True)
    assert json.loads(status)['consumers'][0]['consumer']=='preserved-reader'
    assert original==config.read_bytes() and obj.current.resolve()==second
    icon_hash=subprocess.check_output([str(second/'.venv/bin/python'),'-c',"import hashlib,importlib.resources; print(hashlib.sha256((importlib.resources.files('agent_newsbot')/'resources/app-icon.png').read_bytes()).hexdigest())"],text=True,cwd='/tmp').strip()
    assert icon_hash==hashlib.sha256((ROOT/'linux/agent_newsbot/resources/app-icon.png').read_bytes()).hexdigest()
    operations=json.loads(subprocess.check_output([str(base/'bin/agent-newsbot'),'operations'],env=env,cwd='/tmp',text=True))
    assert operations['complete'] and operations['sections']['delivery']['consumers'][0]['consumer']=='preserved-reader'
    assert (state/'stories.sqlite').stat().st_mode & 0o777 == 0o600
    report.update(first_install=True,upgrade=True,config_preserved=True,consumer_preserved=True,installed_icon_sha256=icon_hash,installed_operations=True,private_journal=True)
    rollback=[sys.executable,str(ROOT/'linux/installer.py'),'--user','--rollback','--prefix',str(obj.prefix),'--state',str(state),'--config',str(config),'--unit-dir',str(unit_dir),'--bin-dir',str(obj.bin_dir),'--service',service]
    subprocess.run(rollback,check=True)
    assert obj.current.resolve()==first and obj.wait_health(port)
    subprocess.run(rollback,check=True)
    assert obj.current.resolve()==second and obj.wait_health(port)
    report['explicit_rollback']=True
    broken=obj.stage(ROOT/'linux')
    package=subprocess.check_output([str(broken/'.venv/bin/python'),'-c','import agent_newsbot; print(agent_newsbot.__path__[0])'],text=True).strip()
    (Path(package)/'__main__.py').write_text('raise SystemExit(7)\n')
    try: obj.activate(broken,port)
    except RuntimeError: pass
    else: raise AssertionError('broken release was accepted')
    assert obj.current.resolve()==second and obj.wait_health(port)
    assert original==config.read_bytes()
    report.update(rollback=True,restored_service_healthy=True)
finally:
    obj.systemctl('disable','--now',service,check=False)
    obj.dropin.unlink(missing_ok=True)
    if obj.dropin.parent.exists(): obj.dropin.parent.rmdir()
    obj.unit.unlink(missing_ok=True); obj.systemctl('daemon-reload',check=False)
    obj.systemctl('reset-failed',service,check=False)
    report['test_service_removed']=not obj.unit.exists()
    (base/'report.json').write_text(json.dumps(report,indent=2))
    print(json.dumps(report,indent=2))
