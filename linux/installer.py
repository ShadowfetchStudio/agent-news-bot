#!/usr/bin/env python3
"""Staged system/user installation with an atomic release link and health rollback."""
from __future__ import annotations
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import pwd
import shlex
import sqlite3
import subprocess
import sys
import time
import urllib.request
import uuid

MARKER='# Managed by Agent News Bot staged installer'


@contextmanager
def creation_mask(value):
    old=os.umask(value)
    try: yield
    finally: os.umask(old)


def atomic_file(path, content, mode=0o600):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_name('.'+path.name+'.'+uuid.uuid4().hex)
    try:
        with open(temporary,'xb',opener=lambda p,f:os.open(p,f,mode)) as handle:
            handle.write(content); handle.flush(); os.fsync(handle.fileno())
        os.replace(temporary,path)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_link(path,target):
    path=Path(path); temporary=path.with_name('.'+path.name+'.'+uuid.uuid4().hex)
    try: temporary.symlink_to(target); os.replace(temporary,path)
    finally: temporary.unlink(missing_ok=True)


def wrapper(prefix,state,config):
    # Values are shell literals, not interpolated commands or executable configuration.
    return ('#!/bin/sh\n'+MARKER+'\n'
        'if [ -z "${AGENT_NEWSBOT_HOME+x}" ]; then export AGENT_NEWSBOT_HOME='+shlex.quote(str(state))+'; fi\n'
        'if [ -r '+shlex.quote(str(config))+' ]; then export AGENT_NEWSBOT_CONFIG='+shlex.quote(str(config))+'; fi\n'
        'unset PYTHONPATH\nexec '+shlex.quote(str(prefix/'current/.venv/bin/python'))+' -m agent_newsbot "$@"\n').encode()


def systemd_quote(value):
    return json.dumps(str(value).replace('%','%%'))


def runtime_dropin(prefix,state,config):
    return (MARKER+'\n[Service]\nExecStart=\nExecStart='+systemd_quote(prefix/'current/.venv/bin/python')+' -m agent_newsbot serve\n'
        'Environment=PYTHONPATH=\nEnvironment='+systemd_quote('AGENT_NEWSBOT_HOME='+str(state))+'\n'
        'Environment='+systemd_quote('AGENT_NEWSBOT_SHARED='+str(state/'shared'))+'\n'
        'Environment=AGENT_NEWSBOT_CONFIG=\nEnvironmentFile='+str(config).replace('%','%%')+'\nUMask=0077\n').encode()


def health(port,pid):
    try:
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(f'http://127.0.0.1:{port}/health',timeout=2) as response:
            data=json.loads(response.read(4096))
        return data.get('ok') is True and data.get('app')=='Agent News Bot' and data.get('pid')==pid
    except (OSError,ValueError): return False


class Installer:
    def __init__(self,prefix,state,config,unit_dir,bin_dir,user=False,service='agent-news-bot.service',svc_user='agentnewsbot',no_start=False):
        self.prefix,self.state,self.config=map(Path,(prefix,state,config))
        self.unit_dir,self.bin_dir=map(Path,(unit_dir,bin_dir))
        self.user,self.service,self.svc_user,self.no_start=user,service,svc_user,no_start
        self.command=['systemctl']+(['--user'] if user else [])
        self.unit=self.unit_dir/service
        self.dropin=self.unit_dir/(service+'.d')/'20-agent-news-bot-runtime.conf'
        self.current=self.prefix/'current'

    def systemctl(self,*args,check=True):
        return subprocess.run([*self.command,*args],check=check,text=True,capture_output=True)

    def active(self): return self.systemctl('is-active','--quiet',self.service,check=False).returncode==0

    def wait_health(self,port):
        deadline=time.monotonic()+30
        while time.monotonic()<deadline:
            pid=self.systemctl('show',self.service,'--property=MainPID','--value',check=False).stdout.strip()
            if pid.isdigit() and int(pid)>0 and self.active():
                # Use the service's effective environment, including preserved legacy EnvironmentFiles.
                try:
                    env=dict(item.split(b'=',1) for item in Path('/proc',pid,'environ').read_bytes().split(b'\0') if b'=' in item)
                    argv=Path('/proc',pid,'cmdline').read_bytes().split(b'\0')
                    if not argv or os.fsdecode(argv[0])!=str(self.current/'.venv/bin/python') or env.get(b'PYTHONPATH') or env.get(b'PYTHONHOME'):
                        time.sleep(.5); continue
                    effective_port=int(env.get(b'AGENT_NEWSBOT_PORT',str(port).encode()))
                    effective_home=Path(os.fsdecode(env.get(b'AGENT_NEWSBOT_HOME',os.fsencode(self.state))))
                    if effective_home==self.state and 1<=effective_port<=65535 and health(effective_port,int(pid)): return True
                except (OSError,ValueError): pass
            time.sleep(.5)
        return False

    def stage(self,source):
        with creation_mask(0o022): return self._stage(source)

    def _stage(self,source):
        self.prefix.mkdir(parents=True,exist_ok=True)
        releases=self.prefix/'releases'; releases.mkdir(exist_ok=True)
        release=releases/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:8])
        release.mkdir(mode=0o755)
        # venv scripts contain absolute paths: build at the permanent release location, never relocate it.
        subprocess.run([sys.executable,'-m','venv',str(release/'.venv')],check=True)
        python=release/'.venv/bin/python'
        env=dict(os.environ); env.pop('PYTHONPATH',None); env['AGENT_NEWSBOT_CONFIG']=''
        subprocess.run([str(python),'-m','pip','install','--disable-pip-version-check','-r',str(source/'requirements.txt')],check=True,env=env)
        subprocess.run([str(python),'-m','pip','install','--disable-pip-version-check','--no-deps',str(source)],check=True,env=env)
        subprocess.run([str(python),'-m','pip','check'],check=True,env=env)
        subprocess.run([str(python),'-c',"import sqlite3; from agent_newsbot.xtext import measure; assert measure('中'*140)['weightedLength']==280; assert sqlite3.connect(':memory:').execute(\"select json_extract('{\\\"x\\\":1}','$.x')\").fetchone()[0]==1"],check=True,cwd=release,env=env)
        atomic_file(release/'READY',b'preflight passed\n',0o644)
        return release

    def activate(self,release,port):
        if not (release/'READY').is_file(): raise RuntimeError('release has not passed preflight')
        if self.current.exists() and not self.current.is_symlink(): raise RuntimeError('current must be an installer-managed symlink')
        if self.dropin.exists() and MARKER not in self.dropin.read_text(): raise RuntimeError('custom runtime drop-in preserved; move it after review before installing')
        previous=os.readlink(self.current) if self.current.is_symlink() else None
        prior_dropin=self.dropin.read_bytes() if self.dropin.exists() else None
        prior_unit=self.unit.read_bytes() if self.unit.exists() else None
        cli=self.bin_dir/'agent-newsbot'
        prior_cli=cli.read_bytes() if cli.exists() else None
        replace_cli=prior_cli is None or MARKER.encode() in prior_cli or b'AGENT_NEWSBOT_PREFIX' in prior_cli
        was_active=self.active() if not self.no_start else False
        was_enabled=self.systemctl('is-enabled','--quiet',self.service,check=False).returncode==0 if not self.no_start else False
        recovery=self.prefix/'recovery'/(release.name+'-'+uuid.uuid4().hex[:8]); recovery.mkdir(parents=True,mode=0o700)
        os.chmod(recovery,0o700)
        if prior_dropin is not None: atomic_file(recovery/'runtime.conf',prior_dropin)
        if prior_unit is not None: atomic_file(recovery/'service.unit',prior_unit)
        if prior_cli is not None: atomic_file(recovery/'cli',prior_cli)
        atomic_file(recovery/'state.json',json.dumps({'previous':previous,'was_active':was_active}).encode())
        switched=False; enabled_by_us=False
        try:
            if not self.no_start: self.systemctl('stop',self.service,check=False)
            # Preserve the journal including consumer positions/drafts before any new runtime opens it.
            source=self.state/'stories.sqlite'
            if source.is_file():
                with sqlite3.connect(source.as_uri()+'?mode=ro',uri=True) as src, sqlite3.connect(recovery/'stories.sqlite') as dst:
                    src.backup(dst)
                    if dst.execute('PRAGMA integrity_check').fetchone()[0]!='ok': raise RuntimeError('journal backup integrity failed')
                os.chmod(recovery/'stories.sqlite',0o600)
            if prior_unit is None:
                identity='' if self.user else f'User={self.svc_user}\nGroup={self.svc_user}\n'
                hardening='' if self.user else 'PrivateTmp=true\nProtectSystem=full\nProtectHome=read-only\nReadWritePaths='+systemd_quote(self.state)+'\nRestrictAddressFamilies=AF_UNIX AF_INET AF_INET6\nRestrictSUIDSGID=true\n'
                unit=(MARKER+'\n[Unit]\nDescription=Agent News Bot\nAfter=network-online.target\n[Service]\nType=simple\n'+identity+
                    'Restart=on-failure\nRestartSec=5\nTimeoutStopSec=30\nNoNewPrivileges=true\n'+hardening+'[Install]\nWantedBy='+('default.target' if self.user else 'multi-user.target')+'\n')
                atomic_file(self.unit,unit.encode(),0o644)
            atomic_file(self.dropin,runtime_dropin(self.prefix,self.state,self.config),0o644)
            atomic_link(self.current,release); switched=True
            if not self.no_start:
                self.systemctl('daemon-reload'); self.systemctl('start',self.service)
                if not self.wait_health(port): raise RuntimeError('new service did not pass its own PID/health check')
                enabled_by_us=not was_enabled
                self.systemctl('enable',self.service)
            if replace_cli: atomic_file(cli,wrapper(self.prefix,self.state,self.config),0o755)
            else: print('Preserved custom CLI wrapper at',cli)
            if previous is not None: atomic_link(self.prefix/'previous',previous)
        except BaseException as failure:
            if not self.no_start: self.systemctl('stop',self.service,check=False)
            if enabled_by_us: self.systemctl('disable',self.service,check=False)
            if switched:
                if previous is None: self.current.unlink(missing_ok=True)
                else: atomic_link(self.current,previous)
            if prior_dropin is None: self.dropin.unlink(missing_ok=True)
            else: atomic_file(self.dropin,prior_dropin,0o644)
            if prior_unit is None: self.unit.unlink(missing_ok=True)
            if replace_cli:
                if prior_cli is None: cli.unlink(missing_ok=True)
                else: atomic_file(cli,prior_cli,0o755)
            if not self.no_start:
                self.systemctl('daemon-reload',check=False)
                if was_active:
                    restarted=self.systemctl('start',self.service,check=False)
                    if restarted.returncode!=0 or not self.active():
                        raise RuntimeError('upgrade failed and the previous service did not restart; inspect recovery records at '+str(recovery)) from failure
            raise
        print('Activated release:',release)
        print('Recovery records:',recovery)
        if self.no_start: print('Service was not started or health-verified (--no-start).')


def main():
    # Package files must be readable by the separate service account; private state/config use explicit modes.
    os.umask(0o022)
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--user',action='store_true'); parser.add_argument('--no-start',action='store_true')
    parser.add_argument('--rollback',action='store_true',help='Activate the previous successful release without changing configuration or restoring old data')
    parser.add_argument('--prefix'); parser.add_argument('--state'); parser.add_argument('--config')
    parser.add_argument('--unit-dir'); parser.add_argument('--bin-dir'); parser.add_argument('--service',default='agent-news-bot.service')
    parser.add_argument('--service-user',default=os.environ.get('SVC_USER','agentnewsbot'))
    args=parser.parse_args()
    if not args.user and os.geteuid()!=0: parser.error('system installation requires root; --user installs a user service')
    if '/' in args.service or not args.service.endswith('.service') or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.@' for c in args.service): parser.error('invalid service name')
    home=Path.home()
    prefix=Path(args.prefix or os.environ.get('PREFIX') or (home/'.local/opt/agent-news-bot' if args.user else '/opt/agent-news-bot')).absolute()
    state=Path(args.state or os.environ.get('STATE_DIR') or (home/'.local/share/agent-news-bot' if args.user else '/var/lib/agent-news-bot')).absolute()
    config=Path(args.config or (home/'.config/agent-news-bot/config.env' if args.user else '/etc/agent-news-bot/config.env')).absolute()
    unit_dir=Path(args.unit_dir or (home/'.config/systemd/user' if args.user else '/etc/systemd/system')).absolute()
    bin_dir=Path(args.bin_dir or (home/'.local/bin' if args.user else '/usr/local/bin')).absolute()
    if any('\n' in str(p) or '\r' in str(p) for p in (prefix,state,config,unit_dir,bin_dir)): parser.error('paths cannot contain newlines')
    prefix.mkdir(parents=True,exist_ok=True)
    with open(prefix/'.install.lock','a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if not args.user:
            try: account=pwd.getpwnam(args.service_user)
            except KeyError:
                subprocess.run(['useradd','--system','--create-home','--home-dir',str(state),'--shell','/usr/sbin/nologin',args.service_user],check=True)
                account=pwd.getpwnam(args.service_user)
        state.mkdir(parents=True,exist_ok=True,mode=0o700)
        if not args.user: os.chown(state,account.pw_uid,account.pw_gid)
        if not config.exists():
            initial=b'# Private Agent News Bot settings. Existing values survive upgrades.\n'
            if not (unit_dir/args.service).exists(): initial+=b'AGENT_NEWSBOT_PORT=18765\nAGENT_NEWSBOT_FETCH_INTERVAL=900\nAGENT_NEWSBOT_WORLDMONITOR_ENABLED=0\n'
            atomic_file(config,initial)
            if not args.user: os.chown(config,account.pw_uid,account.pw_gid)
        sys.path.insert(0,str(Path(__file__).parent))
        from agent_newsbot.private_config import load_private_config
        settings={'AGENT_NEWSBOT_CONFIG':str(config)}; load_private_config(settings)
        if 'AGENT_NEWSBOT_HOME' in settings and Path(settings['AGENT_NEWSBOT_HOME'])!=state:
            parser.error('configured HOME differs from --state; pass the same state directory to preserve backups')
        port=int(settings.get('AGENT_NEWSBOT_PORT','18765'))
        if not 1<=port<=65535: parser.error('invalid configured port')
        install=Installer(prefix,state,config,unit_dir,bin_dir,args.user,args.service,args.service_user,args.no_start)
        if args.rollback:
            previous=prefix/'previous'
            if not previous.is_symlink(): raise RuntimeError('no previous staged release is available')
            release=previous.resolve()
            if release.parent!=(prefix/'releases').resolve(): raise RuntimeError('previous release is outside the managed releases directory')
        else: release=install.stage(Path(__file__).parent)
        install.activate(release,port)


if __name__=='__main__':
    try: main()
    except Exception as exc:
        print('Installation failed:',str(exc),file=sys.stderr); sys.exit(1)
