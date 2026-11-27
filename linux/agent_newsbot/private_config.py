"""Read a deliberately small, non-executable private environment-file format."""
import os
from pathlib import Path
import re
import shlex
import stat


def load_private_config(environ=None):
    env=os.environ if environ is None else environ
    raw=env.get('AGENT_NEWSBOT_CONFIG')
    path=Path(raw) if raw else Path(env.get('XDG_CONFIG_HOME',str(Path.home()/'.config')))/'agent-news-bot/config.env'
    if raw=='': return
    if not path.exists():
        if raw: raise ValueError('configured environment file does not exist')
        return
    info=path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or (os.getuid()!=0 and info.st_uid not in (0,os.getuid())):
        raise ValueError('configuration must be a private regular file (0600), owned by this user or root')
    if info.st_size>65536: raise ValueError('configuration exceeds 64 KiB')
    values={}
    for number,line in enumerate(path.read_text().splitlines(),1):
        line=line.strip()
        if not line or line.startswith('#'): continue
        if '=' not in line: raise ValueError(f'invalid configuration at line {number}')
        key,value=line.split('=',1)
        if not re.fullmatch(r'AGENT_NEWSBOT_[A-Z0-9_]+',key) or key=='AGENT_NEWSBOT_CONFIG':
            raise ValueError(f'unsupported configuration key at line {number}')
        # No expansion, commands, multiline values, export statements or inline comments.
        words=shlex.split(value,comments=False,posix=True)
        if len(words)>1: raise ValueError(f'quote values containing spaces at line {number}')
        values[key]=words[0] if words else ''
    for key,value in values.items(): env.setdefault(key,value)
