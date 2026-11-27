"""Create application databases privately before SQLite can create sidecars."""
import os
import stat
from pathlib import Path


def private_database(path):
    path=Path(path)
    if any(os.path.lexists(path.parent / marker) for marker in ("INCOMPLETE.json", "RECOVERY_INCOMPLETE.json")):
        raise ValueError('incomplete recovery folder; restore into a new folder before activation')
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    fd=os.open(path,os.O_RDWR|os.O_CREAT|os.O_NONBLOCK|getattr(os,'O_NOFOLLOW',0),0o600)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode): raise ValueError('database must be a regular file')
        os.fchmod(fd,0o600)
    finally: os.close(fd)
