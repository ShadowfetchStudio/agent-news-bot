"""Exercise the exact generated installer wrapper from outside the source tree."""
import json
import os
from pathlib import Path
import shutil
import shlex
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def test_installer_wrapper_finds_package_and_service_state(tmp_path):
    prefix = tmp_path / "prefix with spaces"
    state = tmp_path / "service state"
    prefix.mkdir(); state.mkdir()
    shutil.copytree(ROOT / "linux/agent_newsbot", prefix / "agent_newsbot")
    (prefix / ".venv/bin").mkdir(parents=True)
    interpreter = prefix / ".venv/bin/python"
    interpreter.write_text("#!/bin/sh\nexec " + shlex.quote(sys.executable) + ' "$@"\n')
    interpreter.chmod(0o755)
    shared = state / "shared"; shared.mkdir()
    (shared / "latest.json").write_text(json.dumps({"fetched_at": "2026-11-27T00:00:00Z", "total_articles": 0, "desks": [], "articles": []}))
    # Render the heredoc using the shell exactly as install.sh does, without root/service changes.
    installer = (ROOT / "linux/install.sh").read_text()
    body = installer.split("cat > /usr/local/bin/agent-newsbot <<EOF\n", 1)[1].split("\nEOF", 1)[0]
    wrapper = tmp_path / "agent-newsbot"
    env = dict(os.environ, PREFIX=str(prefix), STATE_DIR=str(state))
    for key in ("PYTHONPATH", "AGENT_NEWSBOT_HOME", "AGENT_NEWSBOT_SHARED", "AGENT_NEWSBOT_PREFIX"):
        env.pop(key, None)
    rendered = subprocess.check_output(["bash", "-c", "cat <<EOF\n" + body + "\nEOF\n"], env=env)
    wrapper.write_bytes(rendered); wrapper.chmod(0o755)
    result = subprocess.run([str(wrapper), "status"], cwd=tmp_path, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["total_articles"] == 0
    assert json.loads(result.stdout)["fetched_at"] == "2026-11-27T00:00:00Z"
    assert not (state / "export/bridge.token").exists(), "status should be read-only"
