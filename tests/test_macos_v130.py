"""Regression tests for macOS native v1.3.0 hardening and workflow upgrades."""
from pathlib import Path
import re
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_macos_skill_and_version_parity():
    mac_version = re.search(r'static let appVersion = "([^"]+)"', (ROOT / "Sources/APIContract.swift").read_text())[1]
    assert mac_version == "1.3.0"
    assert f"<string>{mac_version}</string>" in (ROOT / "build.sh").read_text()
    skill = (ROOT / "skills/agent-news-bot/SKILL.md").read_text()
    assert f"version: {mac_version}" in skill
    for required in ("/v1/capabilities", "/v1/sources", "/v1/operations", "/v1/library/brief", "platform_approval", "editorial_brief", "retained_history"):
        assert required in skill
    # UserAgent must not lag behind APIContract.appVersion.
    models = (ROOT / "Sources/Models.swift").read_text()
    assert "AgentNewsBot/\\(APIContract.appVersion)" in models
    assert "AgentNewsBot/1.1" not in models


def test_macos_security_and_ux_invariants():
    # HermesClient enforces HTTPS for non-loopback endpoints.
    hermes = (ROOT / "Sources/HermesClient.swift").read_text()
    assert 'scheme == "https" || ["localhost", "127.0.0.1", "::1", "[::1]"].contains(host)' in hermes
    # App.swift uses reopenable Window + openWindow.
    app = (ROOT / "Sources/App.swift").read_text()
    assert 'Window("Agent News Bot", id: "desk")' in app
    assert 'openWindow(id: "desk")' in app
    # AgentBridge guards empty token, returns pid on /health, and blocks repairs during fetch.
    bridge = (ROOT / "Sources/AgentBridge.swift").read_text()
    assert "!token.isEmpty" in bridge
    assert '"pid": Int(ProcessInfo.processInfo.processIdentifier)' in bridge
    assert "fetch in progress; retry the repair after completion" in bridge
    # StoryStore uses PRAGMA trusted_schema=OFF and readOnly BEGIN DEFERRED.
    story_store = (ROOT / "Sources/StoryStore.swift").read_text()
    assert 'PRAGMA trusted_schema=OFF' in story_store
    assert 'readOnly ? "BEGIN DEFERRED" : "BEGIN IMMEDIATE"' in story_store
    # XText verifies pinned SHA-256.
    xtext = (ROOT / "Sources/XText.swift").read_text()
    sha = (ROOT / "linux/agent_newsbot/vendor/SHA256SUMS").read_text().split()[0]
    assert sha in xtext


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS app bundle check")
def test_bundled_macos_helpers_run_standalone_if_built(tmp_path):
    resources = ROOT / "build/Agent News Bot.app/Contents/Resources"
    if not resources.is_dir():
        pytest.skip("build/Agent News Bot.app not built")
    assert (resources / "SKILL.md").is_file()
    for helper in ("recovery.py", "maintenance.py", "retention.py", "application_recovery.py"):
        script = resources / helper
        assert script.is_file()
        env = {"PATH": "/usr/bin:/bin:/opt/homebrew/bin"}
        proc = subprocess.run([sys.executable, str(script), "--help"], cwd=tmp_path, env=env, capture_output=True, text=True)
        assert proc.returncode == 0, f"{helper} failed standalone: {proc.stderr}"
