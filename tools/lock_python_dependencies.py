#!/usr/bin/env python3
"""Regenerate hashes for explicitly selected versions using PyPI release metadata."""
import json
from pathlib import Path
import re
import urllib.request
ROOT=Path(__file__).resolve().parents[1]
versions=json.loads((ROOT/'linux/runtime-versions.json').read_text())
lines=['# Generated from runtime-versions.json and PyPI release metadata.', '# All release artifacts are hashed for supported platforms; no dependency is floating.']
provenance=[]
for name,version in sorted(versions.items()):
    if not re.fullmatch('[a-z0-9-]+',name) or not re.fullmatch('[0-9]+(?:[.][0-9]+)*',version): raise ValueError('invalid pinned package')
    url=f'https://pypi.org/pypi/{name}/{version}/json'
    with urllib.request.urlopen(url,timeout=20) as response: payload=json.load(response)
    if payload['info']['version']!=version: raise ValueError('release metadata mismatch')
    hashes=sorted({r['digests']['sha256'] for r in payload['urls'] if not r.get('yanked')})
    if not hashes or any(not re.fullmatch('[a-f0-9]{64}',h) for h in hashes): raise ValueError('missing valid release hashes')
    lines.append(name+'=='+version+' \\\n    '+' \\\n    '.join('--hash=sha256:'+h for h in hashes))
    provenance.append({'name':name,'version':version,'metadata_url':url,'requires_python':payload['info'].get('requires_python'),'artifact_count':len(hashes)})
(ROOT/'linux/requirements.lock').write_text('\n'.join(lines)+'\n')
(ROOT/'linux/dependency-provenance.json').write_text(json.dumps({'source':'PyPI release metadata','packages':provenance},indent=2)+'\n')
print('Locked',len(versions),'packages')
