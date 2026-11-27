# Runtime dependency locks and audit scope

`linux/runtime-versions.json` records explicitly selected runtime versions. `tools/lock_python_dependencies.py` obtains their non-yanked release artifact SHA-256 hashes from PyPI metadata and writes `linux/requirements.lock` plus `linux/dependency-provenance.json`. All eight transitive runtime packages are pinned. `linux/requirements.txt` includes the lock, so normal installer requirements installation uses pip's hash-checking mode.

To update deliberately, select versions, regenerate and inspect the lock/provenance diff, install in clean supported Python environments, run the behavioral tests, and run:

```sh
python tools/lock_python_dependencies.py
python -m pip install -r linux/requirements.txt
pip-audit -r linux/requirements.lock --require-hashes --strict --progress-spinner off
cd tools/twitter-text
npm audit --audit-level=low
```

CI has a dependency-audit job and installs the hash-locked runtime separately from pytest. The npm dependency tree uses its checked-in package lock. The November 27 audit of the eight runtime packages and JavaScript build dependencies found no known vulnerabilities. Retained results are under `docs/audits/2026-11-27/reaudit/`; they are dated observations, not a guarantee about future advisory databases.

The Python wheel declares compatible dependency bounds; installing only the wheel without the supplied runtime lock does not enforce these exact versions/hashes. The lock fixes release inputs, not reproducible machine code: source builds of QuickJS, the compiler/OS, packaging tools such as setuptools, and test tooling are outside this runtime lock. PyPI provenance is useful for review but is not independent vendor signature verification. A successful advisory scan is not a code audit of all native or vendored code.

The Python World Monitor adapter now runs a single request in an isolated child. The parent enforces a 20-second total deadline and kills/reaps a timed-out worker; the child caps decompressed response bytes at 4 MiB and refuses redirects. Credentials pass through standard input, not command-line arguments, and failures do not echo request details. Default tests cover a valid oversized JSON response and a server that trickles bytes without completing. Direct-provider paths also have bounded transfers and persistent circuit breakers; see [source health](SOURCE_HEALTH.md). These runtime controls are distinct from dependency provenance and advisory scanning.
