#!/usr/bin/env python3
"""Package a clean, matching native build and tracked Linux/source installer; never upload."""
import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path
import plistlib
import re
import shutil
import subprocess
import tarfile
import tempfile

ROOT=Path(__file__).resolve().parents[1]


def run(*args):
    return subprocess.check_output(args,cwd=ROOT,text=True).strip()


def digest(path):
    h=hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True,help='New artifact directory outside build/')
    args=parser.parse_args();out=args.output.absolute()
    if out.exists():parser.error('output already exists; use a new directory')
    if (ROOT/'build').resolve() in out.resolve().parents:parser.error('output cannot be inside build/')
    if run('git','status','--porcelain'):parser.error('commit or remove working-tree changes before packaging')
    commit=run('git','rev-parse','HEAD')
    app=ROOT/'build/Agent News Bot.app'
    info=plistlib.loads((app/'Contents/Info.plist').read_bytes());version=info['CFBundleShortVersionString']
    if not re.fullmatch(r'\d+\.\d+\.\d+',version):parser.error('invalid bundle version')
    metadata=json.loads((app/'Contents/Resources/BuildInfo.json').read_text())
    if metadata['source_commit']!=commit or metadata['source_dirty'] is not False:parser.error('rebuild this clean commit before packaging')
    if metadata['architecture']!='arm64':parser.error('this release recipe packages the validated Apple Silicon build only')
    subprocess.run(['codesign','--verify','--deep','--strict',str(app)],check=True)
    if run('lipo','-archs',str(app/'Contents/MacOS'/info['CFBundleExecutable']))!='arm64':parser.error('unexpected executable architecture')
    linux_version=re.search(r'^version = "([^"]+)"',(ROOT/'linux/pyproject.toml').read_text(),re.M).group(1)
    if linux_version!=version:parser.error('Mac and Python versions differ')
    out.mkdir(parents=True);marker=out/'INCOMPLETE';marker.write_text('Do not publish until packaging succeeds.\n')
    release_info=dict(version=version,source_commit=commit,mac=dict(architecture='arm64',minimum_macos='14.0',signing='ad-hoc',notarized=False),
                      linux=dict(kind='source installer',minimum_python='3.10',service='systemd',network_required_for_dependencies=True))
    manifest=json.dumps(release_info,indent=2)+'\n'
    with tempfile.TemporaryDirectory(prefix='.stage-',dir=out) as temporary:
        temp=Path(temporary);folder=temp/f'AgentNewsBot-{version}-macos-arm64-ad-hoc';folder.mkdir()
        subprocess.run(['ditto',str(app),str(folder/app.name)],check=True)
        shutil.copyfile(ROOT/'docs/MAC_INSTALL.md',folder/'READ-ME-FIRST.md')
        shutil.copyfile(ROOT/'LICENSE',folder/'LICENSE')
        (folder/'RELEASE-INFO.json').write_text(manifest)
        subprocess.run(['ditto','-c','-k','--sequesterRsrc','--keepParent',str(folder),str(out/(folder.name+'.zip'))],check=True)
        tar_path=temp/'source.tar';prefix=f'agent-news-bot-{version}/'
        with tar_path.open('wb') as handle:subprocess.run(['git','archive','--format=tar','--prefix='+prefix,commit],stdout=handle,cwd=ROOT,check=True)
        # Metadata is generated from the verified source commit, never from the user home.
        with tarfile.open(tar_path,'a') as archive:
            data=manifest.encode();entry=tarfile.TarInfo(prefix+'RELEASE-INFO.json');entry.size=len(data);entry.mode=0o644
            archive.addfile(entry,io.BytesIO(data))
        with tar_path.open('rb') as source,(out/f'AgentNewsBot-{version}-linux-source-installer.tar.gz').open('wb') as target:
            with gzip.GzipFile(filename='',mode='wb',fileobj=target,mtime=0) as gz:shutil.copyfileobj(source,gz)
    shutil.copyfile(ROOT/'Resources/AppIcon-1024.png',out/'AgentNewsBot-icon.png')
    (out/'RELEASE-INFO.json').write_text(manifest)
    assets=sorted(p for p in out.iterdir() if p.is_file() and p!=marker)
    (out/'SHA256SUMS').write_text(''.join(f'{digest(p)}  {p.name}\n' for p in assets))
    marker.unlink()
    print(json.dumps(dict(directory=str(out),source_commit=commit,assets=[p.name for p in assets]+['SHA256SUMS']),indent=2))


if __name__=='__main__':main()
