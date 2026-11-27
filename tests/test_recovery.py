from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time

import pytest
from test_worldmonitor import ROOT, article, swift, swift_harness
from agent_newsbot.stories import StoryStore
from agent_newsbot.store import FreshnessStore
from agent_newsbot.source_health import SourceHealth
from agent_newsbot import recovery


@pytest.fixture
def home(tmp_path):
    path = tmp_path / "original"
    journal = StoryStore(path / "stories.sqlite")
    item = journal.ingest([article()])[0]
    journal.save_editorial(item["id"], dict(expected_revision=item["revision"], expected_editorial_version=0,
                                          notes="Keep my researched notes", bookmarked=True, generate=True))
    journal.library_settings("search", "Lunar", {"q": "lunar"}, write=True)
    journal.library_settings("mute", "Muted publisher", True, write=True)
    reader = journal.consumer_change(dict(consumer="reader", action="create", start="history"))
    journal.pending("reader")
    journal.consumer_change(dict(consumer="reader", action="lease", expected_generation=reader["generation"], worker="fixture"))
    FreshnessStore(path / "seen.sqlite").mark(item["id"], item["url"], item["title"], "science")
    SourceHealth(path / "source-health.sqlite").begin("fixture", "Fixture")
    (path / "bridge.token").write_text("never-copy-this-secret")
    (path / "config.env").write_text("provider-secret")
    return path, journal, item


def test_backup_restore_preserves_research_and_starts_new_worker_session(home, tmp_path):
    path, journal, item = home
    old_health = SourceHealth(path / "source-health.sqlite").list()
    old_status = journal.consumer_status()
    destination, restored = tmp_path / "backup", tmp_path / "restored"
    manifest = recovery.backup(path, destination)
    assert set(manifest["files"]) == set(recovery.SCHEMAS)
    assert recovery.verify(destination)["files"] == manifest["files"]
    result = recovery.restore(destination, restored)
    assert result["restored_files"]["stories.sqlite"]["counts"]["stories"] == 1
    new = StoryStore(restored / "stories.sqlite")
    assert new.editorial(item["id"]) == journal.editorial(item["id"])
    assert new.library_settings() == journal.library_settings()
    assert new.revisions(item["id"]) == journal.revisions(item["id"])
    state = new.consumer_status()["consumers"][0]
    assert state["cursor"] == old_status["consumers"][0]["cursor"] == 0
    assert state["offered"] == 0 and state["lease_worker"] is None
    assert state["generation"] == old_status["consumers"][0]["generation"] + 1
    assert journal.consumer_status() == old_status
    assert SourceHealth(path / "source-health.sqlite").list() == old_health
    with sqlite3.connect(restored / "source-health.sqlite") as db:
        row = json.loads(db.execute("SELECT payload FROM source_health").fetchone()[0])
        assert row["lease"] == "" and row["lease_until"] == 0
    for folder in (destination, restored):
        assert folder.stat().st_mode & 0o777 == 0o700
        for file in folder.iterdir():
            assert file.stat().st_mode & 0o777 == 0o600
            assert b"never-copy-this-secret" not in file.read_bytes()
        assert not (folder / "bridge.token").exists()
        assert not (folder / "config.env").exists()


def test_backup_includes_committed_wal_but_not_uncommitted_work(home, tmp_path):
    path, journal, _ = home
    db = sqlite3.connect(path / "stories.sqlite")
    try:
        db.execute("PRAGMA wal_autocheckpoint=0")
        db.execute("INSERT INTO library_preferences VALUES ('watchlist','committed','{}')")
        db.commit()
        db.execute("INSERT INTO library_preferences VALUES ('watchlist','uncommitted','{}')")
        assert Path(str(path / "stories.sqlite") + "-wal").stat().st_size > 0
        recovery.backup(path, tmp_path / "backup")
        with sqlite3.connect(tmp_path / "backup/stories.sqlite") as copied:
            assert copied.execute("SELECT 1 FROM library_preferences WHERE name='committed'").fetchone()
            assert not copied.execute("SELECT 1 FROM library_preferences WHERE name='uncommitted'").fetchone()
    finally:
        db.rollback()
        db.close()


@pytest.mark.parametrize("operation", ["backup", "restore"])
def test_recovery_never_overwrites_existing_even_empty_folders(home, tmp_path, operation):
    path, _, _ = home
    archive = tmp_path / "archive"
    recovery.backup(path, archive)
    target = tmp_path / "existing"
    target.mkdir()
    with pytest.raises(FileExistsError):
        getattr(recovery, operation)(path if operation == "backup" else archive, target)
    assert list(target.iterdir()) == []
    with pytest.raises(FileExistsError):
        getattr(recovery, operation)(path if operation == "backup" else archive, path)


@pytest.mark.parametrize("damage", ["hash", "size", "traversal", "symlink", "sidecar", "incomplete", "schema", "json", "orphan", "cursor"])
def test_restore_rejects_damaged_or_unexpected_backup_before_creating_destination(home, tmp_path, damage):
    path, _, _ = home
    archive = tmp_path / "backup"
    recovery.backup(path, archive)
    manifest_path = archive / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    db_path = archive / "stories.sqlite"
    if damage == "hash": manifest["files"]["stories.sqlite"]["sha256"] = "0" * 64
    elif damage == "size": manifest["files"]["stories.sqlite"]["bytes"] = True
    elif damage == "traversal": manifest["files"]["../escaped"] = manifest["files"]["stories.sqlite"]
    elif damage == "symlink":
        db_path.unlink()
        db_path.symlink_to(path / "stories.sqlite")
    elif damage == "sidecar": (archive / "stories.sqlite-wal").write_bytes(b"untrusted")
    elif damage == "incomplete": (archive / "INCOMPLETE.json").write_text("{}")
    else:
        with sqlite3.connect(db_path) as db:
            if damage == "schema": db.execute("CREATE VIEW unexpected AS SELECT * FROM stories")
            elif damage == "json": db.execute("UPDATE editorial SET payload='broken'")
            elif damage == "orphan": db.execute("INSERT INTO members VALUES ('https://example.com/orphan','missing')")
            else: db.execute("UPDATE consumers SET cursor=99999,offered=99999")
        size, sha = recovery.digest(db_path, recovery.DEFAULT_LIMIT)
        manifest["files"]["stories.sqlite"].update(bytes=size, sha256=sha)
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises((recovery.RecoveryError, sqlite3.Error)):
        recovery.restore(archive, tmp_path / "restored")
    assert not (tmp_path / "restored").exists()


def test_backup_failure_is_visible_and_previous_backup_is_untouched(home, tmp_path, monkeypatch):
    path, _, _ = home
    old = tmp_path / "good"
    recovery.backup(path, old)
    original = (old / "manifest.json").read_bytes()
    monkeypatch.setattr(recovery, "snapshot", lambda *args: (_ for _ in ()).throw(OSError("fixture write failure")))
    with pytest.raises(OSError): recovery.backup(path, tmp_path / "failed")
    assert (tmp_path / "failed/INCOMPLETE.json").exists()
    assert not (tmp_path / "failed/manifest.json").exists()
    assert (old / "manifest.json").read_bytes() == original
    assert recovery.verify(old)


def test_bounds_and_deadline_interrupt_snapshot(home, tmp_path):
    path, _, _ = home
    with pytest.raises(recovery.RecoveryError, match="deadline"):
        recovery.snapshot(path / "stories.sqlite", tmp_path / "incomplete.sqlite", recovery.DEFAULT_LIMIT, time.monotonic() - 1)
    with pytest.raises(recovery.RecoveryError): recovery.backup(path, tmp_path / "tiny", max_bytes=1)
    assert not (tmp_path / "tiny").exists()


def test_recovery_runs_without_site_packages_or_valid_runtime_config(home, tmp_path):
    path, _, _ = home
    env = dict(os.environ, AGENT_NEWSBOT_CONFIG=str(tmp_path / "missing.env"))
    script = ROOT / "linux/agent_newsbot/recovery.py"
    standalone = subprocess.run([sys.executable, "-S", str(script), "backup", "--home", str(path), "--destination", str(tmp_path / "archive")], env=env, capture_output=True, text=True)
    assert standalone.returncode == 0, standalone.stderr
    cli = subprocess.run([sys.executable, "-S", "-m", "agent_newsbot", "verify-backup", "--backup", str(tmp_path / "archive")], env=dict(env, PYTHONPATH=str(ROOT / "linux")), capture_output=True, text=True)
    assert cli.returncode == 0, cli.stderr
    assert json.loads(cli.stdout)["format"] == recovery.FORMAT


def test_native_story_database_roundtrip_is_readable_by_both_runtimes(swift_harness, tmp_path):
    home = tmp_path / "native"
    home.mkdir()
    rows = tmp_path / "rows.json"
    rows.write_text(json.dumps([article()]))
    created = swift(swift_harness, "ingest", home / "stories.sqlite", rows)["articles"][0]
    expected = swift(swift_harness, "story", home / "stories.sqlite", created["id"])
    recovery.backup(home, tmp_path / "archive")
    recovery.restore(tmp_path / "archive", tmp_path / "restored")
    assert swift(swift_harness, "story", tmp_path / "restored/stories.sqlite", created["id"]) == expected
    assert StoryStore(tmp_path / "restored/stories.sqlite").story(created["id"])["id"] == created["id"]


def test_incomplete_recovery_home_cannot_be_opened_for_writes(home, tmp_path):
    path, _, _ = home
    recovery.backup(path, tmp_path / "archive")
    recovery.restore(tmp_path / "archive", tmp_path / "restored")
    root = tmp_path / "restored"
    before = (root / "stories.sqlite").read_bytes()
    (root / "RECOVERY_INCOMPLETE.json").write_text("{}")
    with pytest.raises(ValueError, match="incomplete"):
        StoryStore(root / "stories.sqlite")
    with pytest.raises(ValueError, match="incomplete"):
        FreshnessStore(root / "seen.sqlite")
    with pytest.raises(ValueError, match="incomplete"):
        recovery.backup(root, tmp_path / "should-not-be-complete")
    assert not (tmp_path / "should-not-be-complete").exists()
    assert (root / "stories.sqlite").read_bytes() == before


def test_native_incomplete_home_rejects_ingestion(swift_harness, tmp_path):
    root = tmp_path / "partial"
    root.mkdir()
    (root / "RECOVERY_INCOMPLETE.json").write_text("{}")
    rows = tmp_path / "rows.json"
    rows.write_text(json.dumps([article()]))
    assert swift(swift_harness, "ingest-error", root / "stories.sqlite", rows)["failed"]
    assert not (root / "stories.sqlite").exists()


def test_actual_byte_budget_stops_large_cache_backup(home, tmp_path):
    path, _, _ = home
    with sqlite3.connect(path / "seen.sqlite") as db:
        db.execute("INSERT INTO feed_documents VALUES ('fixture',?,0)", ("x" * (3 * 1024 * 1024),))
    with pytest.raises(recovery.RecoveryError, match="byte limit"):
        recovery.backup(path, tmp_path / "limited", max_bytes=1024 * 1024)
    assert (tmp_path / "limited/INCOMPLETE.json").exists()
    assert not (tmp_path / "limited/manifest.json").exists()


def test_restore_rechecks_hash_after_source_changes(home, tmp_path, monkeypatch):
    path, _, _ = home
    archive = tmp_path / "archive"
    recovery.backup(path, archive)
    real_verify = recovery.verify
    def mutated(*args, **kwargs):
        verified = real_verify(*args, **kwargs)
        with (archive / "stories.sqlite").open("r+b") as db:
            db.seek(100)
            db.write(b"changed-after-verification")
        return verified
    monkeypatch.setattr(recovery, "verify", mutated)
    with pytest.raises(recovery.RecoveryError, match="changed during restore"):
        recovery.restore(archive, tmp_path / "restored")
    assert (tmp_path / "restored/RECOVERY_INCOMPLETE.json").exists()
    assert not (tmp_path / "restored/RECOVERY.json").exists()
