from __future__ import annotations

import copy
import contextlib
import json
import os
import shutil
import subprocess
import sys
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "linux"))
from agent_newsbot import worldmonitor, engine, server
from agent_newsbot.stories import StoryStore
from agent_newsbot.store import FreshnessStore
from agent_newsbot.config import Article, DeskResult

NOW = datetime.fromtimestamp(1795780800, timezone.utc)
FIXTURE = ROOT / "tests/fixtures/worldmonitor-digest.json"


def article(url="https://example.com/a", title="NASA launches lunar orbiter to map south pole craters", summary="Launch report", provider="direct"):
    from agent_newsbot.store import article_id
    return dict(id=article_id(url, title), desk="science", category="Science", title=title, url=url,
                source="Example", published=NOW.isoformat(), summary=summary, tweet="draft", tweet_chars=5,
                hashtags=["#Science"], extra={"provider": provider, "metadata": {}})


@contextmanager
def provider(payload=None, status=200, redirect=False):
    calls = []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            calls.append((self.path, self.headers.get("X-WorldMonitor-Key")))
            self.send_response(status)
            if redirect:
                self.send_header("Location", "/stolen-key")
            self.end_headers()
            body = payload if isinstance(payload, bytes) else json.dumps(payload or {}).encode()
            self.wfile.write(body)
        def log_message(self, *args): pass
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True); thread.start()
    try: yield f"http://127.0.0.1:{httpd.server_port}", calls
    finally: httpd.shutdown(); httpd.server_close(); thread.join()


def test_digest_contract_preserves_context_and_rejects_invalid_items():
    results = worldmonitor.parse_digest(json.loads(FIXTURE.read_text()), 12, 20, NOW)
    items = [a for r in results for a in r.articles]
    assert len(items) == 2
    assert items[0].url == "https://science.example/lunar"
    assert items[0].summary == "A new mapping mission."
    assert items[0].extra["metadata"]["locationName"] == "Florida"
    assert items[0].extra["metadata"]["credibilityUnassessed"] is True
    assert sum(r.skipped_old for r in results) == 1
    assert any("partial" in e for r in results for e in r.source_errors)
    assert any("2 invalid" in e for r in results for e in r.source_errors)


@pytest.mark.parametrize("url", ["http://remote.example", "https://user:pass@example.com", "https://example.com?key=x", "file:///tmp/a", "https://example.com/api", "https://example.com/#x"])
def test_unsafe_configuration_rejected(url):
    with pytest.raises(ValueError): worldmonitor.validate_base_url(url)


def test_disabled_makes_no_requests(monkeypatch):
    monkeypatch.delenv("AGENT_NEWSBOT_WORLDMONITOR_ENABLED", raising=False)
    monkeypatch.setattr(requests, "get", lambda *a, **k: pytest.fail("network called"))
    assert worldmonitor.fetch(12, 20) == []


@pytest.mark.parametrize("status,body", [(401, {}), (429, {}), (503, {}), (200, b"not-json"), (200, {"categories": []})])
def test_provider_failure_isolated_and_credentials_redacted(monkeypatch, status, body):
    with provider(body, status) as (url, calls):
        monkeypatch.setenv("AGENT_NEWSBOT_WORLDMONITOR_ENABLED", "true")
        monkeypatch.setenv("AGENT_NEWSBOT_WORLDMONITOR_URL", url)
        monkeypatch.setenv("AGENT_NEWSBOT_WORLDMONITOR_KEY", "fixture-secret")
        result = worldmonitor.fetch(12, 20)
        assert result[0].source_errors and not result[0].articles
        assert "fixture-secret" not in str(result)
        assert calls[0][1] == "fixture-secret"


def test_redirect_does_not_forward_credentials(monkeypatch):
    with provider({}, 302, True) as (url, calls):
        monkeypatch.setenv("AGENT_NEWSBOT_WORLDMONITOR_ENABLED", "true")
        monkeypatch.setenv("AGENT_NEWSBOT_WORLDMONITOR_URL", url)
        monkeypatch.setenv("AGENT_NEWSBOT_WORLDMONITOR_KEY", "fixture-secret")
        assert worldmonitor.fetch(12, 20)[0].source_errors == ["World Monitor HTTP 302"]
        assert len(calls) == 1


def test_sources_revisions_restart_and_independent_delivery(tmp_path):
    path = tmp_path / "stories.sqlite"
    journal = StoryStore(path)
    first = article(); second = article("https://second.example/a")
    new = journal.ingest([first, second])
    # A transaction delivers the complete grouped story once, not interim source additions.
    assert len(new) == 1 and len(new[0]["sources"]) == 2 and new[0]["revision"] == 1
    assert journal.ingest([first, second]) == []
    journal = StoryStore(path)
    first["title"] += ": mission delayed"
    update = journal.ingest([first])[0]
    assert update["revision"] == 2 and update["change"] == "updated"
    assert len(update["sources"]) == 2
    one = journal.pending("briefing", 1)
    assert one == journal.pending("briefing", 1)
    assert one["next_cursor"] == 1
    assert journal.acknowledge("briefing", 1)["cursor"] == 1
    assert journal.pending("briefing")["count"] == 1
    assert journal.pending("video")["count"] == 2
    assert StoryStore(path).pending("briefing")["cursor"] == 1
    with pytest.raises(ValueError): journal.acknowledge("video", 3)
    assert journal.acknowledge("briefing", 0)["cursor"] == 1


def test_provider_metadata_changes_do_not_create_editorial_updates(tmp_path):
    journal = StoryStore(tmp_path / "stories.sqlite")
    a = article(provider="worldmonitor")
    journal.ingest([a])
    a["extra"]["metadata"] = {"importanceScore": 76, "storyMeta": {"mentionCount": 2}}
    assert journal.ingest([a]) == []
    assert journal.pending("agent")["count"] == 1


def test_direct_and_digest_snippets_do_not_oscillate(tmp_path):
    journal = StoryStore(tmp_path / "stories.sqlite")
    direct = article(summary="Full RSS summary with more detail")
    digest = article(summary="Short digest", provider="worldmonitor")
    journal.ingest([direct, digest])
    assert journal.ingest([direct, digest]) == []
    update = copy.deepcopy(direct); update["summary"] += " Updated."
    changed = journal.ingest([update, digest])[0]
    assert changed["summary"] == update["summary"]
    assert changed["sources"][0]["providers"] == ["direct", "worldmonitor"]


def test_invalid_batch_does_not_create_partial_delivery(tmp_path):
    journal = StoryStore(tmp_path / "stories.sqlite")
    with pytest.raises(KeyError): journal.ingest([article(), {}])
    assert journal.pending("agent")["count"] == 0
    assert journal.ingest([article()])[0]["revision"] == 1


def test_engine_combines_provider_with_existing_sources(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "ALL_DESKS", ["science"])
    a = article(); a.pop("category")
    monkeypatch.setattr(engine, "fetch_desk", lambda *args: DeskResult("science", [Article(**a)]))
    b = copy.deepcopy(a); b["url"] = "https://second.example/a"; b["id"] = "second"
    monkeypatch.setattr(worldmonitor, "fetch", lambda *args: [DeskResult("science", [Article(**b)], source_errors=["World Monitor coverage: partial"])])
    result = engine.fetch_all(FreshnessStore(tmp_path / "seen.sqlite"))
    assert result["total_articles"] == 1
    assert len(result["articles"][0]["sources"]) == 2
    assert len(result["desks"]) == 1
    assert result["desks"][0]["errors"]
    assert engine.fetch_all(FreshnessStore(tmp_path / "seen.sqlite"))["total_articles"] == 0


@pytest.fixture(scope="session")
def swift_harness(tmp_path_factory):
    if sys.platform != "darwin": pytest.skip("Swift macOS harness")
    dest = tmp_path_factory.mktemp("swift") / "integration-harness"
    files = ["Models", "HTTPTransport", "SnapshotWriter", "BridgeCredential", "ConsumerStore", "LibraryStore", "ResearchPackage", "AgentProcess", "HermesClient", "OpenClawClient", "Coverage", "SnapshotPolicy", "Freshness", "Hashtagger", "XText", "StoryStore", "WorldMonitorClient", "AgentBridge", "MarketsClient", "SkillInstaller", "SettingsStore", "Paths", "RSSClient", "Feeds"]
    subprocess.run(["swiftc", *[str(ROOT / f"Sources/{f}.swift") for f in files], str(ROOT / "tests/IntegrationHarness.swift"), "-o", str(dest)], check=True, capture_output=True)
    shutil.copyfile(ROOT / "linux/agent_newsbot/vendor/twitter-text.js", dest.parent / "twitter-text.js")
    return dest


def swift(binary, *args):
    return json.loads(subprocess.check_output([str(binary), *map(str, args)]))


def test_swift_python_digest_parity(swift_harness):
    native = swift(swift_harness, "digest", FIXTURE)
    py = worldmonitor.parse_digest(json.loads(FIXTURE.read_text()), 12, 20, NOW)
    native_items = sorted(native["articles"], key=lambda a: a["url"])
    py_items = sorted([a.as_dict() for r in py for a in r.articles], key=lambda a: a["url"])
    for native_item, py_item in zip(native_items, py_items, strict=True):
        for key in ("id", "title", "url", "source", "desk", "summary", "extra"):
            assert native_item[key] == py_item[key], key
    assert sorted(native["errors"]) == sorted(e for r in py for e in r.source_errors)


def test_swift_persistent_updates_and_consumers(swift_harness, tmp_path):
    data = tmp_path / "input.json"; db = tmp_path / "stories.sqlite"
    rows = [article(), article("https://second.example/a")]
    data.write_text(json.dumps(rows))
    result = swift(swift_harness, "ingest", db, data)
    assert len(result["articles"]) == 1
    assert len(result["articles"][0]["sources"]) == 2
    assert swift(swift_harness, "ingest", db, data)["articles"] == []
    assert swift(swift_harness, "pending", db, "a")["count"] == 1
    assert swift(swift_harness, "ack", db, "a", 1)["cursor"] == 1
    assert swift(swift_harness, "pending", db, "a")["count"] == 0
    assert swift(swift_harness, "pending", db, "b")["count"] == 1
    rows[0]["summary"] = "A corrected report."
    data.write_text(json.dumps(rows))
    assert swift(swift_harness, "ingest", db, data)["articles"][0]["revision"] == 2
    assert swift(swift_harness, "pending", db, "a")["count"] == 1


def test_swift_connector_http_and_redirect(swift_harness):
    payload = json.loads(FIXTURE.read_text())
    for row in payload["categories"]["tech"]["items"]:
        if row.get("publishedAt", 0) > 1000:
            row["publishedAt"] = int(datetime.now(timezone.utc).timestamp() * 1000)
    with provider(payload) as (url, calls):
        result = swift(swift_harness, "fetch", url)
        assert len(result["articles"]) == 2
        assert calls[0][1] == "test-fixture-key"
        assert "variant=tech" in calls[0][0]
    with provider({}, 302, True) as (url, calls):
        assert swift(swift_harness, "fetch", url)["errors"] == ["World Monitor HTTP 302"]
        assert len(calls) == 1


def test_oversized_body_and_timeout_are_isolated(monkeypatch):
    # Valid JSON would be returned without the stream cap; parser rejection alone
    # must not make this pass. Exercise the actual isolated worker.
    with provider({'categories': {}, 'padding': 'x' * (5 * 1024 * 1024)}) as (url, _):
        with pytest.raises(ValueError): worldmonitor.bounded_request(url, 'full', 'fixture-secret')


def test_worldmonitor_total_deadline_kills_and_reaps_trickling_worker(monkeypatch):
    import time
    class Trickle(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200); self.end_headers()
            try:
                for _ in range(100):
                    self.wfile.write(b' '); self.wfile.flush(); time.sleep(.1)
            except (BrokenPipeError, ConnectionResetError): pass
        def log_message(self, *args): pass
    httpd = ThreadingHTTPServer(('127.0.0.1', 0), Trickle)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True); thread.start()
    spawned = []; original = subprocess.Popen
    def tracked(*args, **kwargs):
        child = original(*args, **kwargs); spawned.append(child); return child
    monkeypatch.setattr(worldmonitor.subprocess, 'Popen', tracked)
    monkeypatch.setattr(worldmonitor, 'HTTP_DEADLINE', 1.0)
    try:
        start = time.monotonic()
        with pytest.raises(subprocess.TimeoutExpired):
            worldmonitor.bounded_request(f'http://127.0.0.1:{httpd.server_port}', 'full', 'fixture-secret')
        assert time.monotonic() - start < 3
        assert len(spawned) == 1 and spawned[0].poll() is not None
    finally:
        httpd.shutdown(); httpd.server_close(); thread.join(timeout=2)


@pytest.mark.parametrize("runtime", ["python", "swift"])
def test_real_http_consumer_delivery(runtime, request, tmp_path, monkeypatch):
    token = "integration-test-token"
    if runtime == "python":
        monkeypatch.setattr(server, "HOME", tmp_path)
        monkeypatch.setattr(server, "SHARED", tmp_path / "shared")
        state = server.NewsState()
        state.stories.ingest([article()])
        token = state.token
        monkeypatch.setattr(server, "STATE", state)
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True); thread.start()
        port = httpd.server_port
        def cleanup(): httpd.shutdown(); httpd.server_close(); thread.join()
    else:
        binary = request.getfixturevalue("swift_harness")
        data = tmp_path / "input.json"; data.write_text(json.dumps([article()]))
        db = tmp_path / "stories.sqlite"
        swift(binary, "ingest", db, data)
        import socket
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]
        proc = subprocess.Popen([str(binary), "serve", str(db), str(port)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        import select
        assert select.select([proc.stdout], [], [], 10)[0], "Swift server startup timed out"
        assert proc.stdout.readline().strip() == b"ready"
        def cleanup(): proc.terminate(); proc.wait(timeout=10)
    base = f"http://127.0.0.1:{port}"
    headers = {"Authorization": f"Bearer {token}"}
    try:
        assert requests.get(base + "/v1/events?consumer=a", timeout=5).status_code == 401
        page = requests.get(base + "/v1/events?consumer=a", headers=headers, timeout=5).json()
        assert page["count"] == 1
        story_id = page["events"][0]["story"]["id"]
        detail = requests.get(base + "/v1/stories/" + story_id, headers=headers, timeout=5)
        assert detail.status_code == 200 and detail.json()["story"]["id"] == story_id
        history = requests.get(base + "/v1/stories/" + story_id + "/revisions", headers=headers, timeout=5)
        assert history.status_code == 200 and len(history.json()["revisions"]) == 1
        assert requests.get(base + "/v1/stories/" + story_id + "/revisions?limit=0", headers=headers, timeout=5).status_code == 400
        assert requests.get(base + "/v1/stories/missing", headers=headers, timeout=5).status_code == 404
        assert requests.get(base + "/v1/events?consumer=a", headers=headers, timeout=5).json() == page
        assert requests.post(base + "/v1/events/ack?consumer=a&through=2", headers=headers, timeout=5).status_code == 400
        ack = requests.post(base + "/v1/events/ack?consumer=a&through=1", headers=headers, timeout=5)
        assert ack.status_code == 200 and ack.json()["cursor"] == 1
        assert requests.get(base + "/v1/events?consumer=a", headers=headers, timeout=5).json()["count"] == 0
        assert requests.get(base + "/v1/events?consumer=b", headers=headers, timeout=5).json()["count"] == 1
        assert requests.get(base + "/v1/events?consumer=a&limit=-1", headers=headers, timeout=5).status_code == 400
        assert requests.get(base + "/v1/events?consumer=bad%0Aname", headers=headers, timeout=5).status_code == 400
        assert requests.get(base + "/v1/events/ack?consumer=a&through=1", headers=headers, timeout=5).status_code == 405
        assert requests.post(base + "/v1/events?consumer=a", headers=headers, timeout=5).status_code == 405
    finally: cleanup()


def test_python_full_provider_http_ingestion(monkeypatch, tmp_path):
    payload = json.loads(FIXTURE.read_text())
    for row in payload["categories"]["tech"]["items"]:
        if row.get("publishedAt", 0) > 1000:
            row["publishedAt"] = int(datetime.now(timezone.utc).timestamp() * 1000)
    with provider(payload) as (url, calls):
        monkeypatch.setenv("AGENT_NEWSBOT_WORLDMONITOR_ENABLED", "true")
        monkeypatch.setenv("AGENT_NEWSBOT_WORLDMONITOR_URL", url)
        monkeypatch.setattr(engine, "ALL_DESKS", [])
        result = engine.fetch_all(FreshnessStore(tmp_path / "seen.sqlite"))
        assert result["total_articles"] == 1
        assert len(result["articles"][0]["sources"]) == 2
        assert StoryStore(tmp_path / "stories.sqlite").pending("video")["count"] == 1
        assert len(calls) == 1


def test_sources_different_subjects_not_grouped(tmp_path):
    journal = StoryStore(tmp_path / "stories.sqlite")
    stories = journal.ingest([article(), article("https://x.example/a", "Central bank changes benchmark interest rate")])
    assert len(stories) == 2


def test_failed_journal_write_does_not_acknowledge(tmp_path, monkeypatch):
    journal = StoryStore(tmp_path / "stories.sqlite")
    journal.ingest([article()]); journal.pending("agent")
    import sqlite3
    monkeypatch.setattr(journal, "connect", lambda: (_ for _ in ()).throw(sqlite3.OperationalError()))
    with pytest.raises(sqlite3.Error): journal.acknowledge("agent", 1)
    assert StoryStore(tmp_path / "stories.sqlite").pending("agent")["count"] == 1


def test_failed_journal_does_not_commit_rss_cache(tmp_path, monkeypatch):
    db = FreshnessStore(tmp_path / "seen.sqlite")
    class Response:
        status_code = 200
        headers = {"ETag": '"test-etag"'}
        content = b'<rss><channel><item><title>NASA launches lunar orbiter to map south pole craters</title><link>https://example.com/a</link></item></channel></rss>'
        def raise_for_status(self): pass
    monkeypatch.setattr(engine, "ALL_DESKS", ["science"])
    monkeypatch.setattr(engine, "RSS_FEEDS", {"science": [("https://example.com/feed", "Example")]})
    monkeypatch.setattr(engine, "_get", lambda *a, **kw: Response())
    monkeypatch.setattr(worldmonitor, "fetch", lambda *args: [])
    original = StoryStore.ingest
    def fail(*args): raise OSError("disk full")
    monkeypatch.setattr(StoryStore, "ingest", fail)
    with pytest.raises(OSError): engine.fetch_all(db)
    assert db.feed_cache("https://example.com/feed") == (None, None)
    monkeypatch.setattr(StoryStore, "ingest", original)
    assert engine.fetch_all(db)["total_articles"] == 1
    assert db.feed_cache("https://example.com/feed")[0] == '"test-etag"'


def test_overlapping_desks_do_not_emit_oscillating_updates(tmp_path):
    journal = StoryStore(tmp_path / "stories.sqlite")
    long = article(summary="A detailed RSS summary")
    short = article(summary="Short summary"); short["desk"] = "tech"
    assert len(journal.ingest([short, long])) == 1
    assert journal.ingest([long, short]) == []
    assert journal.pending("agent")["count"] == 1


def test_concurrent_fetches_emit_one_event(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    path = tmp_path / "stories.sqlite"
    journals = [StoryStore(path) for _ in range(4)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda j: j.ingest([article()]), journals))
    assert sum(len(r) for r in results) == 1
    assert journals[0].pending("agent")["count"] == 1


def test_failed_journal_does_not_commit_market_quote(tmp_path, monkeypatch):
    db = FreshnessStore(tmp_path / "seen.sqlite")
    class Response:
        def json(self): return {"chart": {"result": [{"meta": {"regularMarketPrice": 200, "chartPreviousClose": 100}}]}}
    monkeypatch.setattr(engine, "_get", lambda *a, **kw: Response())
    monkeypatch.setattr(engine, "ALL_DESKS", ["markets"])
    monkeypatch.setattr(engine, "MARKET_SYMBOLS", "TEST")
    monkeypatch.setattr(worldmonitor, "fetch", lambda *args: [])
    original = StoryStore.ingest
    def fail(*args): raise OSError("disk full")
    monkeypatch.setattr(StoryStore, "ingest", fail)
    with pytest.raises(OSError): engine.fetch_all(db)
    assert db.last_quote("TEST") is None
    monkeypatch.setattr(StoryStore, "ingest", original)
    assert engine.fetch_all(db)["total_articles"] == 1
    assert db.last_quote("TEST") is not None


@pytest.mark.parametrize("runtime", ["python", "swift"])
def test_database_failure_rolls_back_entire_batch(runtime, request, tmp_path):
    import sqlite3
    db = tmp_path / "stories.sqlite"
    journal = StoryStore(db)
    first = article()
    second = article("https://z.example/b", "Central bank changes benchmark interest rate")
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TRIGGER fail_second BEFORE INSERT ON stories WHEN NEW.id='" + second["id"] + "' BEGIN SELECT RAISE(ABORT, 'fixture failure'); END")
    if runtime == "python":
        with pytest.raises(sqlite3.Error): journal.ingest([first, second])
    else:
        binary = request.getfixturevalue("swift_harness")
        data = tmp_path / "input.json"; data.write_text(json.dumps([first, second]))
        assert swift(binary, "ingest-error", db, data)["failed"] is True
    assert journal.pending("agent")["count"] == 0
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT count(*) FROM stories").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM members").fetchone()[0] == 0


@contextmanager
def native_bridge(binary, db, mode="serve"):
    import socket, select
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]
    proc = subprocess.Popen([str(binary), mode, str(db), str(port)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        assert select.select([proc.stdout], [], [], 10)[0]
        assert proc.stdout.readline().strip() == b"ready"
        yield f"http://127.0.0.1:{port}", proc
    finally:
        if proc.poll() is None: proc.terminate()
        proc.wait(timeout=10)


def test_native_listener_restarts_and_failed_port_change(swift_harness, tmp_path):
    import socket
    with socket.socket() as a, socket.socket() as b:
        a.bind(("127.0.0.1", 0)); b.bind(("127.0.0.1", 0))
        ports = a.getsockname()[1], b.getsockname()[1]
    result = swift(swift_harness, "lifecycle", tmp_path / "journal.sqlite", *ports)
    assert result == {"failures": 0, "occupied_rejected": True, "old_listener_preserved": True}


def test_native_disconnect_does_not_kill_bridge(swift_harness, tmp_path):
    import socket, time
    from urllib.parse import urlsplit
    a = article(summary="x" * 1_000_000)
    data = tmp_path / "input.json"; data.write_text(json.dumps([a]))
    db = tmp_path / "journal.sqlite"
    swift(swift_harness, "ingest", db, data)
    with native_bridge(swift_harness, db) as (base, proc):
        for _ in range(20):
            with socket.create_connection(("127.0.0.1", urlsplit(base).port)) as sock:
                sock.sendall(b"GET /v1/events?consumer=cancel HTTP/1.1\r\nHost: localhost\r\nAuthorization: Bearer integration-test-token\r\n\r\n")
                sock.shutdown(socket.SHUT_RDWR)
            time.sleep(.01)
        assert proc.poll() is None
        assert requests.get(base + "/health", timeout=5).status_code == 200


@pytest.mark.parametrize("runtime", ["python", "swift"])
@pytest.mark.parametrize("fails", [False, True])
def test_fetch_job_truthful_completion(runtime, fails, request, tmp_path, monkeypatch):
    import time
    with contextlib.ExitStack() as stack:
        if runtime == "swift":
            binary = request.getfixturevalue("swift_harness")
            base, _ = stack.enter_context(native_bridge(binary, tmp_path / "stories.sqlite", "serve-fail" if fails else "serve-slow"))
            token = "integration-test-token"
        else:
            monkeypatch.setattr(server, "HOME", tmp_path)
            monkeypatch.setattr(server, "SHARED", tmp_path / "shared")
            state = server.NewsState(); monkeypatch.setattr(server, "STATE", state)
            def fetch(*args):
                time.sleep(2)
                if fails: raise OSError("fixture storage failure")
                return {"articles": [], "desks": []}
            monkeypatch.setattr(server, "fetch_all", fetch)
            httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
            thread = threading.Thread(target=httpd.serve_forever, daemon=True); thread.start()
            def stop(): httpd.shutdown(); httpd.server_close(); thread.join()
            stack.callback(stop)
            base = f"http://127.0.0.1:{httpd.server_port}"; token = state.token
        headers = {"Authorization": "Bearer " + token}
        start = time.monotonic()
        response = requests.post(base + "/v1/fetch", headers=headers, timeout=1.5)
        assert response.status_code == 202
        assert time.monotonic() - start < 1.5
        accepted = response.json()
        assert accepted["fetch_job"]["state"] == "running"
        assert "total_articles" not in accepted
        assert requests.post(base + "/v1/fetch", headers=headers, timeout=1).status_code == 409
        for _ in range(80):
            status = requests.get(base + "/v1/status", headers=headers, timeout=1).json()
            if status["fetch_job"]["state"] != "running": break
            time.sleep(.05)
        assert status["fetch_job"]["id"] == accepted["fetch_job"]["id"]
        assert status["fetch_job"]["state"] == ("failed" if fails else "completed")
        assert status["fetching"] is False
        assert ("error" in status["fetch_job"]) == fails


@pytest.mark.parametrize("symbols", ["AAA,BBB", "BBB,AAA"])
@pytest.mark.parametrize("limit", [0, 1, 2])
def test_market_checkpoint_matches_selected_symbol(monkeypatch, tmp_path, symbols, limit):
    class Quote:
        def __init__(self, url): self.url = url
        def json(self):
            return {"chart": {"result": [{"meta": {"regularMarketPrice": 101 if "/AAA?" in self.url else 150, "chartPreviousClose": 100}}]}}
    monkeypatch.setattr(engine, "_get", lambda url, **kw: Quote(url))
    result = engine.fetch_markets(FreshnessStore(tmp_path / "seen.sqlite"), symbols, limit)
    assert {q[0] for q in result.quote_updates} == {a.extra["symbol"] for a in result.articles}
    if limit == 1: assert result.articles[0].extra["symbol"] == "BBB"


def test_swift_market_checkpoint_and_skill_preservation(swift_harness, tmp_path):
    a = article(); a["desk"] = "markets"; a["extra"] = {"symbol": "BBB"}
    data = tmp_path / "articles.json"; data.write_text(json.dumps([a]))
    assert swift(swift_harness, "market-checkpoints", data)["symbols"] == ["BBB"]
    skill = tmp_path / "skills/agent-news-bot/SKILL.md"
    assert swift(swift_harness, "skill-install", skill, "custom first content")["installed"]
    assert not swift(swift_harness, "skill-install", skill, "replacement")["installed"]
    assert skill.read_text() == "custom first content"


def test_undated_stories_expire_and_carry_counts_are_explicit(tmp_path, monkeypatch):
    from datetime import timedelta
    monkeypatch.setattr(server, "HOME", tmp_path)
    monkeypatch.setattr(server, "SHARED", tmp_path / "shared")
    state = server.NewsState()
    old = article(); old.update(published=None, first_seen="2000-01-01T00:00:00Z")
    recent = article("https://example.com/recent"); recent.update(published=None, first_seen=datetime.now(timezone.utc).isoformat())
    previous = {"articles": [old, recent], "fetched_at": datetime.now(timezone.utc).isoformat()}
    result = state._keep_live_desks(previous, {"articles": [], "desks": [{"desk": "science", "fresh": 0}]})
    assert [a["id"] for a in result["articles"]] == [recent["id"]]
    desk = result["desks"][0]
    assert (desk["new"], desk["updated"], desk["carried"], desk["displayed"], desk["fresh"]) == (0, 0, 1, 1, 0)
    assert result["articles"][0]["first_seen"] == recent["first_seen"]
    recent["first_seen"] = (datetime.now(timezone.utc) - timedelta(hours=server.LOOKBACK_HOURS + 1)).isoformat()
    assert state._keep_live_desks(previous, {"articles": [], "desks": [{"desk": "science", "fresh": 0}]})["total_articles"] == 0


def test_restored_snapshot_is_pruned_without_refresh(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "HOME", tmp_path)
    monkeypatch.setattr(server, "SHARED", tmp_path / "shared")
    state = server.NewsState()
    old = article(); old.update(published=None, first_seen="2000-01-01T00:00:00Z")
    state.latest_path.write_text(json.dumps({"articles": [old], "desks": [{"desk": "science", "fresh": 1}], "fetched_at": "2000-01-01T00:00:00Z"}))
    restored = server.NewsState()
    assert restored.snapshot["total_articles"] == 0
    assert restored.snapshot["desks"][0]["displayed"] == 0


def test_keychain_errors_do_not_erase_or_hide_credentials(swift_harness):
    assert swift(swift_harness, "keychain-failure") == {
        "write_failed": True, "read_failed": True, "deletes": 0, "adds": 0,
    }


def test_native_private_credential_failures_concurrency_and_rotation(swift_harness, tmp_path):
    import concurrent.futures, os
    token_path = tmp_path / 'bridge.token'
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: swift(swift_harness, 'credential', token_path), range(24)))
    tokens = {row['token'] for row in results}
    assert len(tokens) == 1
    old = tokens.pop()
    assert len(old) == 64 and token_path.read_text().strip() == old
    assert token_path.stat().st_mode & 0o777 == 0o600
    assert 'error' in swift(swift_harness, 'credential', token_path, 'incorrect-existing-token')
    assert token_path.read_text().strip() == old
    new = swift(swift_harness, 'credential', token_path, old)['token']
    assert new != old and swift(swift_harness, 'credential', token_path)['token'] == new
    token_path.write_text('invalid')
    assert 'error' in swift(swift_harness, 'credential', token_path)
    assert token_path.read_text() == 'invalid'
    token_path.unlink(); token_path.mkdir()
    assert 'error' in swift(swift_harness, 'credential', token_path)
    token_path.rmdir()
    target = tmp_path / 'preserved'; target.write_text(old)
    token_path.symlink_to(target)
    assert 'error' in swift(swift_harness, 'credential', token_path)
    assert target.read_text() == old


def test_native_token_rotation_updates_running_listener(swift_harness, tmp_path):
    import socket, select
    token_path = tmp_path / 'bridge.token'
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
    proc = subprocess.Popen([str(swift_harness), 'credential-server', str(token_path), str(port)],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        assert select.select([proc.stdout], [], [], 10)[0]
        assert proc.stdout.readline().strip() == b'ready'
        old = token_path.read_text().strip(); base = f'http://127.0.0.1:{port}/v1/status'
        assert requests.get(base, headers={'Authorization': 'Bearer '+old}, timeout=2).status_code == 200
        proc.stdin.write(b'\n'); proc.stdin.flush()
        assert select.select([proc.stdout], [], [], 10)[0]
        assert proc.stdout.readline().strip() == b'rotated'
        new = token_path.read_text().strip()
        assert old != new
        assert requests.get(base, headers={'Authorization': 'Bearer '+old}, timeout=2).status_code == 401
        assert requests.get(base, headers={'Authorization': 'Bearer '+new}, timeout=2).status_code == 200
    finally:
        proc.terminate(); proc.wait(timeout=10)


@pytest.mark.parametrize('runtime', ['python', 'swift'])
def test_actionable_url_policy_parity(runtime, request, tmp_path):
    from agent_newsbot.url_safety import safe_article_url
    valid = ['https://example.com/news?q=x%20y', 'http://127.0.0.1:8080/a', 'https://[::1]/a',
             'https://bücher.example/über', 'HTTPS://EXAMPLE.COM/a', 'https://example.com/a%2Fb']
    invalid = ['file:///tmp/a', 'mailto:a@example.com', 'javascript:alert(1)', 'ssh://host/a',
               'https://user:password@example.com/a', 'https://@example.com/a', 'https://',
               'https://example.com\\@elsewhere/a', 'https://example.com/%5cfoo',
               'https://example.com/%0afoo', 'https://example.com/\nfoo',
               'https://example.com/a b', 'https://example.com:99999/a',
               'https://example..com/a', 'https://-example.com/a', 'https://example.com/%zz',
               'https://%65xample.com/a', 'https://[fe80::1%25en0]/a']
    rows = valid + invalid; expected = [True]*len(valid) + [False]*len(invalid)
    if runtime == 'python': assert [safe_article_url(url) for url in rows] == expected
    else:
        path = tmp_path / 'urls.json'; path.write_text(json.dumps(rows))
        assert swift(request.getfixturevalue('swift_harness'), 'safe-urls', path)['safe'] == expected


@pytest.mark.parametrize('runtime', ['python', 'swift'])
def test_unsafe_rss_url_rejected_with_visible_coverage(runtime, request, monkeypatch, tmp_path):
    from agent_newsbot import engine
    from agent_newsbot.store import FreshnessStore
    rss = b'<rss version="2.0"><channel><title>Fixture</title><item><title>Unsafe story URL</title><link>file:///tmp/unused</link></item></channel></rss>'
    with provider(rss) as (url, calls):
        if runtime == 'python':
            monkeypatch.setitem(engine.RSS_FEEDS, 'science', [(url, 'Fixture')])
            result = engine.fetch_rss('science', FreshnessStore(tmp_path/'cache.sqlite'), 12, 20)
            assert not result.articles and result.source_errors
            assert any(check['state']=='partial' for check in result.source_checks)
        else:
            binary = request.getfixturevalue('swift_harness')
            result = swift(binary, 'rss', url, tmp_path/'cache.sqlite', 12, 20)
            assert not result['articles'] and result['errors']
            assert result['coverage']['state'] == 'partial'


def test_legacy_unsafe_sources_block_research_exports(journal_runtime, request):
    import sqlite3
    row = journal_runtime.ingest([article()])[0]
    with sqlite3.connect(journal_runtime.path) as db:
        raw = json.loads(db.execute('SELECT payload FROM stories WHERE id=?', (row['id'],)).fetchone()[0])
        raw['sources'][0]['url'] = 'file:///tmp/unused'
        db.execute('UPDATE stories SET payload=? WHERE id=?', (json.dumps(raw), row['id']))
    if isinstance(journal_runtime, StoryStore):
        with pytest.raises(ValueError): journal_runtime.research_export(row['id'])
    else:
        with native_bridge(request.getfixturevalue('swift_harness'), journal_runtime.path) as (base, proc):
            response = requests.get(base+'/v1/library/export', params={'id': row['id']},
                headers={'Authorization':'Bearer integration-test-token'}, timeout=2)
            assert response.status_code == 400 and 'invalid source link' in response.json()['error']


@pytest.mark.parametrize('runtime', ['python', 'swift'])
def test_legacy_nonweb_snapshot_is_not_actionable(runtime, request, monkeypatch, tmp_path):
    import agent_newsbot.server as server
    safe = article(); bad = article(url='file:///tmp/unused')
    data = {'fetched_at': NOW.isoformat(), 'articles': [safe, bad], 'desks': [{'desk':'science'}]}
    if runtime == 'python':
        monkeypatch.setattr(server, 'LOOKBACK_HOURS', 100000)
        merged = server.STATE._keep_live_desks(data, {'fetched_at': NOW.isoformat(), 'articles': [], 'desks':[{'desk':'science'}]})
    else:
        path = tmp_path/'snapshot.json'; path.write_text(json.dumps(data))
        merged = swift(request.getfixturevalue('swift_harness'), 'snapshot', path)
    assert len(merged['articles']) == 1 and merged['articles'][0]['url'] == safe['url']

@pytest.fixture(params=["python", "swift"])
def journal_runtime(request, tmp_path):
    db = tmp_path / "journal.sqlite"
    if request.param == "python":
        return StoryStore(db)
    binary = request.getfixturevalue("swift_harness")
    class Native:
        path = db
        def ingest(self, rows):
            data = tmp_path / "rows.json"; data.write_text(json.dumps(rows))
            return swift(binary, "ingest", db, data)["articles"]
        def pending(self, consumer): return swift(binary, "pending", db, consumer)
        def story(self, story_id): return swift(binary, "story", db, story_id)["story"]
        def revisions(self, story_id): return swift(binary, "revisions", db, story_id)["revisions"]
        def op(self, value):
            data = tmp_path / "operation.json"; data.write_text(json.dumps(value))
            return swift(binary, "library-op", db, data)
        def library(self, query=None): return self.op({"op": "search", "query": query or {}})
        def editorial(self, sid): return self.op({"op": "editorial", "id": sid})
        def save_editorial(self, sid, change): return self.op({"op": "save", "id": sid, "change": change})
        def library_settings(self, kind=None, name=None, value=None, write=False): return self.op(dict(op="settings",kind=kind,name=name,value=value,write=write))
        def research_export(self, sid): return self.op({"op": "export", "id": sid})
    return Native()


def test_enrichment_delivered_and_score_churn_stays_quiet(journal_runtime):
    direct = article(summary="Full direct account with detail")
    digest = article(summary="Short digest", provider="worldmonitor")
    digest["extra"]["metadata"] = {"locationName": "Florida", "importanceScore": 80}
    rows = journal_runtime.ingest([direct, digest])
    event = journal_runtime.pending("test")["events"]
    assert len(event) == 1 and len(rows) == 1
    assert rows[0]["sources"][0]["metadata"]["locationName"] == "Florida"
    assert event[0]["story"]["sources"] == rows[0]["sources"]
    digest["extra"]["metadata"]["importanceScore"] = 85
    assert journal_runtime.ingest([direct, digest]) == []
    assert journal_runtime.story(rows[0]["id"])["sources"][0]["metadata"]["importanceScore"] == 85
    digest["extra"]["metadata"]["locationName"] = "California"
    enriched = journal_runtime.ingest([direct, digest])
    assert enriched[0]["change"] == "enriched"
    assert journal_runtime.pending("test")["count"] == 2


def test_newer_provider_correction_does_not_revert_to_unchanged_rss(journal_runtime):
    direct = article(summary="Full original reporting")
    digest = article(provider="worldmonitor")
    first = journal_runtime.ingest([direct, digest])[0]
    digest["title"] = "NASA cancels lunar orbiter launch after engine failure"
    digest["summary"] = "The launch has been cancelled."
    corrected = journal_runtime.ingest([digest])[0]
    assert corrected["title"] == digest["title"]
    assert corrected["disagreement"] is True
    assert journal_runtime.ingest([direct, digest]) == []
    current = journal_runtime.story(first["id"])
    assert current["title"] == digest["title"]
    assert current["sources"][0]["observations"]["direct"]["title"] == direct["title"]
    history = journal_runtime.revisions(first["id"])
    assert len(history) == 2
    assert history[-1]["diff"]["title"] == {"before": direct["title"], "after": digest["title"]}


def test_desk_membership_and_disagreement_survive_grouping(journal_runtime):
    first = article(title="NASA confirms lunar orbiter launch will proceed despite engine issue")
    same = copy.deepcopy(first); same["desk"] = "tech"
    other = article("https://second.example/report", title="NASA confirms lunar orbiter launch will not proceed despite engine issue")
    story = journal_runtime.ingest([first, same, other])[0]
    assert story["desks"] == ["science", "tech"]
    assert story["disagreement"] is True
    assert len(story["sources"]) == 2
    assert journal_runtime.ingest([first, same, other]) == []


@pytest.mark.parametrize('titles', [
    ('NASA launches new lunar orbiter to map south pole craters', 'China launches new lunar orbiter to map south pole craters'),
    ('nasa launches new lunar orbiter to map south pole craters', 'china launches new lunar orbiter to map south pole craters'),
    ('US approves new funding for national space research', 'UK approves new funding for national space research'),
    ('Earthquake magnitude 6.2 shakes Tokyo during morning commute', 'Earthquake magnitude 6.3 shakes Tokyo during morning commute'),
    ('NASA launches new lunar orbiter on January 8', 'NASA launches new lunar orbiter on January 9'),
    ('Army moves troops from Paris to Berlin', 'Army moves troops to Paris from Berlin'),
    ('NASA launches new lunar orbiter to map north pole craters', 'NASA launches new lunar orbiter to map south pole craters'),
])
def test_different_events_do_not_merge_by_word_overlap(journal_runtime, titles):
    rows = journal_runtime.ingest([article(url=f'https://example.com/report-{i}', title=t) for i,t in enumerate(titles)])
    assert len(rows) == 2 and all(len(row['sources']) == 1 for row in rows)
    assert len(journal_runtime.pending('research')['events']) == 2
    assert journal_runtime.ingest([article(url=f'https://example.com/report-{i}', title=t) for i,t in enumerate(titles)]) == []


def test_equivalent_headlines_still_group_and_source_corrections_keep_identity(journal_runtime):
    one = article(title='NASA launches a lunar orbiter to map the south pole craters')
    two = article(url='https://other.example/report', title='NASA launches lunar orbiter to map south pole craters!')
    first = journal_runtime.ingest([one, two])
    assert len(first) == 1 and len(first[0]['sources']) == 2
    before = journal_runtime.pending('before')
    one['title'] = 'NASA cancels lunar launch after engine failure'
    update = journal_runtime.ingest([one])[0]
    assert update['id'] == first[0]['id'] and update['revision'] == first[0]['revision'] + 1
    after = journal_runtime.pending('after')
    assert after['events'][0] == before['events'][0]


@pytest.mark.parametrize("runtime", ["python", "swift"])
def test_rss_cached_document_recovers_cap_and_lookback(runtime, request, tmp_path, monkeypatch):
    from email.utils import format_datetime
    from datetime import timedelta
    now = datetime.now(timezone.utc)
    recent, old = format_datetime(now), format_datetime(now - timedelta(hours=4))
    body = f'<rss version="2.0"><channel><title>Fixture</title><item><title>Recent</title><link>https://example.com/recent</link><pubDate>{recent}</pubDate></item><item><title>Older</title><link>https://example.com/older</link><pubDate>{old}</pubDate></item></channel></rss>'.encode()
    calls = []
    class Feed(BaseHTTPRequestHandler):
        def do_GET(self):
            cached = self.headers.get("If-None-Match") == '"stable"'
            calls.append(cached)
            self.send_response(304 if cached else 200)
            self.send_header("ETag", '"stable"'); self.end_headers()
            if not cached: self.wfile.write(body)
        def log_message(self, *args): pass
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Feed)
    worker = threading.Thread(target=httpd.serve_forever, daemon=True); worker.start()
    url = f"http://127.0.0.1:{httpd.server_port}/rss"
    cache = FreshnessStore(tmp_path / "seen.sqlite")
    # Upgrade case: the old version stored validators without a recoverable body.
    cache.save_feed_cache(url, '"stable"', None)
    import sqlite3
    with sqlite3.connect(tmp_path / "swift-seen.sqlite") as db:
        db.execute("CREATE TABLE feed_cache(url TEXT PRIMARY KEY,etag TEXT,last_modified TEXT,updated_at REAL)")
        db.execute("INSERT INTO feed_cache VALUES (?,?,?,?)", (url, '"stable"', "", 0))
    def collect(lookback, limit):
        if runtime == "swift":
            binary = request.getfixturevalue("swift_harness")
            result = swift(binary, "rss", url, tmp_path / "swift-seen.sqlite", lookback, limit)
            assert not result["errors"]
            return result["articles"]
        monkeypatch.setattr(engine, "RSS_FEEDS", {"science": [(url, "Fixture")]})
        result = engine.fetch_rss("science", cache, lookback, limit)
        assert not result.source_errors
        for update in result.feed_cache_updates: cache.save_feed_cache(*update)
        return [a.as_dict() for a in result.articles]
    try:
        assert len(collect(2, 1)) == 1
        assert len(collect(12, 1)) == 1
        assert len(collect(12, 2)) == 2
        assert calls == [False, True, True]
    finally: httpd.shutdown(); httpd.server_close(); worker.join()

@pytest.mark.parametrize("runtime", ["python", "swift"])
def test_canonical_identity_contract(runtime, request):
    from agent_newsbot.store import canonical_url, article_id
    fixture = ROOT / "tests/fixtures/canonical-urls.json"
    rows = json.loads(fixture.read_text())
    if runtime == "swift":
        actual = swift(request.getfixturevalue("swift_harness"), "canonical-fixtures", fixture)["items"]
    else:
        actual = [{"canonical": canonical_url(r["raw"]), "id": article_id(r["raw"], "")} for r in rows]
    for row, item in zip(rows, actual, strict=True):
        assert item["canonical"] == row["canonical"], row["raw"]
        assert item["id"] == article_id(row["canonical"], ""), row["raw"]
    assert actual[1]["id"] != actual[2]["id"]
    assert actual[6]["id"] != actual[7]["id"]


def test_identity_upgrade_keeps_history_and_old_story_id(journal_runtime):
    import sqlite3
    from agent_newsbot.store import article_id
    old_url = "https://example.com:443/migration"
    legacy = article(old_url); legacy["id"] = "legacy-stable-id"
    source = {k: legacy[k] for k in ("url", "title", "summary", "source", "published")}
    source.update(providers=["direct"], metadata={})
    legacy.update(sources=[source], revision=1, first_seen="2026-11-27T00:00:00Z")
    # Seed an actual pre-v2 database record, including an immutable delivered event.
    with sqlite3.connect(journal_runtime.path) as db:
        db.executescript('''CREATE TABLE IF NOT EXISTS stories(id TEXT PRIMARY KEY,payload TEXT NOT NULL,updated REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS members(url TEXT PRIMARY KEY,story_id TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY AUTOINCREMENT,story_id TEXT NOT NULL,payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS migrations(name TEXT PRIMARY KEY);''')
        db.execute("DELETE FROM migrations WHERE name='canonical-v2'")
        db.execute("INSERT INTO stories VALUES (?,?,?)", (legacy["id"], json.dumps(legacy), datetime.now(timezone.utc).timestamp()))
        db.execute("INSERT INTO members VALUES (?,?)", (old_url, legacy["id"]))
        db.execute("INSERT INTO events(story_id,payload) VALUES (?,?)", (legacy["id"], json.dumps(legacy)))
    if isinstance(journal_runtime, StoryStore): journal_runtime = StoryStore(journal_runtime.path)
    updated = article("https://example.com/migration", title=legacy["title"] + ": corrected")
    new = journal_runtime.ingest([updated])[0]
    assert new["id"] == legacy["id"]
    assert journal_runtime.story(article_id(updated["url"], ""))["id"] == legacy["id"]
    history = journal_runtime.revisions(legacy["id"])
    assert len(history) == 2
    assert history[0]["story"]["url"] == old_url
    assert history[1]["story"]["title"] == updated["title"]


def test_late_provider_enrichment_is_a_distinct_event_type(journal_runtime):
    direct = article(summary="A detailed direct report")
    journal_runtime.ingest([direct])
    digest = article(summary="Short", provider="worldmonitor")
    digest["extra"]["metadata"] = {"locationName": "Florida"}
    result = journal_runtime.ingest([digest])
    assert result[0]["change"] == "enriched"
    assert result[0]["summary"] == direct["summary"]
    assert journal_runtime.ingest([direct, digest]) == []



def test_native_atom_markup_alternate_link_and_invalid_document(swift_harness, tmp_path):
    feed = tmp_path / "feed.xml"
    feed.write_text('<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Correct title</title><link rel="self" href="https://example.com/feed"/><link rel="alternate" type="text/html" href="https://example.com/story"/><summary type="xhtml"><div xmlns="http://www.w3.org/1999/xhtml"><p>Hello <b>world</b></p><p>Second paragraph</p></div></summary><source><title>Not the article title</title></source></entry></feed>')
    row = swift(swift_harness, "rss-parse", feed)["items"][0]
    assert row == {"title": "Correct title", "url": "https://example.com/story", "summary": "Hello world Second paragraph"}
    feed.write_text('<html><body>Maintenance page</body></html>')
    assert swift(swift_harness, "rss-parse", feed)["error"] == "invalid feed"
    feed.write_text('<rss><channel><item><title>Incomplete')
    assert swift(swift_harness, "rss-parse", feed)["error"] == "invalid feed"


def test_official_x_weighted_conformance():
    from agent_newsbot.xtext import measure
    fixture = json.loads((ROOT / "tests/fixtures/twitter-weighted.json").read_text())
    for case in fixture["cases"]:
        assert measure(case["text"]) == case["expected"], case["description"]


def test_native_x_weighted_conformance(swift_harness, tmp_path):
    fixture = json.loads((ROOT / "tests/fixtures/twitter-weighted.json").read_text())
    path = tmp_path / "xtext.json"
    path.write_text(json.dumps(fixture["cases"]))
    actual = swift(swift_harness, "xtext", path)["items"]
    assert actual == [row["expected"] for row in fixture["cases"]]


def test_x_composer_parity_and_no_split_entities(swift_harness, tmp_path):
    from agent_newsbot.xtext import request, measure
    cases = [
        {"title": "中" * 150, "url": "https://example.com/story", "tags": []},
        {"title": "👨‍👩‍👧‍👦" * 150, "url": "https://example.com/story", "tags": ["#News"]},
        {"title": "Cafe\u0301 " * 70, "url": "https://例え.jp/path", "tags": ["#世界"]},
        {"title": "Read https://example.com/" + "a" * 300, "url": "https://example.com/2", "tags": []},
        {"title": "", "url": "https://example.com", "tags": []},
        {"title": "Story", "url": "", "tags": ["#" + "a" * 500, "#News"]},
        {"title": "Hello", "url": "", "tags": []},
    ]
    path = tmp_path / "xtext.json"
    path.write_text(json.dumps(cases))
    expected = [request(case) for case in cases]
    assert swift(swift_harness, "xtext", path)["items"] == expected
    for result in expected:
        assert result["valid"] and result["weightedLength"] <= 280
        assert measure(result["text"])["weightedLength"] == result["weightedLength"]
    assert expected[0]["weightedLength"] == 280
    family = expected[1]["text"].split("…")[0]
    assert not family.replace("👨‍👩‍👧‍👦", "")
    assert "Cafe\u0301" not in expected[2]["text"]
    assert cases[3]["title"] in expected[3]["text"]


def test_x_composer_linux_limits_and_parallel_workers():
    from concurrent.futures import ThreadPoolExecutor
    from agent_newsbot.hashtags import tweet
    from agent_newsbot.xtext import measure
    cases = [("中" * 150, "https://example.com", []), ("👩🏽‍🚀" * 180, "https://example.com", ["#Space"]),
             ("e\u0301" * 400, "https://example.com", ["#" + "a" * 500, "#News"])] * 6
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda args: tweet(*args), cases))
    for text, count in results:
        assert count <= 280 and measure(text)["valid"]
        assert count == measure(text)["weightedLength"]


@pytest.mark.parametrize("states,expected", [([], "unknown"), (["success"], "success"),
    (["unavailable", "unavailable"], "unavailable"), (["success", "unavailable"], "partial"),
    (["stale", "unavailable"], "stale"), (["partial"], "partial")])
def test_coverage_state_parity(swift_harness, tmp_path, states, expected):
    from agent_newsbot.coverage import summary
    rows = [{"source": str(i), "provider": "direct", "state": state, "detail": "", "checked_at": "fixture"} for i, state in enumerate(states)]
    path = tmp_path / "checks.json"; path.write_text(json.dumps(rows))
    native = swift(swift_harness, "coverage", path)
    assert native == summary(rows)
    assert native["state"] == expected


def test_all_failed_fetch_cannot_claim_success(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "ALL_DESKS", ["science", "world"])
    monkeypatch.setattr(engine, "fetch_desk", lambda desk, *_: DeskResult(desk, [], source_errors=["unavailable"]))
    monkeypatch.setattr(worldmonitor, "fetch", lambda *_: [])
    result = engine.fetch_all(FreshnessStore(tmp_path / "seen.sqlite"))
    assert result["coverage"]["state"] == "unavailable"
    assert result["total_articles"] == 0
    assert all(d["coverage"]["state"] == "unavailable" for d in result["desks"])


def test_valid_empty_feed_is_success_but_malformed_is_unavailable(tmp_path, monkeypatch):
    from agent_newsbot.coverage import summary, result_checks
    class Response:
        status_code = 200
        headers = {}
        def raise_for_status(self): pass
    response = Response()
    monkeypatch.setattr(engine, "RSS_FEEDS", {"science": [("https://example.com/feed", "Fixture")]})
    monkeypatch.setattr(engine, "_get", lambda *_args, **_kwargs: response)
    for body, expected in [(b'<rss version="2.0"><channel><title>Empty</title></channel></rss>', "success"),
                           (b'<html><title>Maintenance</title></html>', "unavailable")]:
        response.content = body
        result = engine.fetch_rss("science", FreshnessStore(tmp_path / "seen.sqlite"), 12, 20)
        assert not result.articles
        assert summary(result_checks(result))["state"] == expected


def test_missing_weather_observation_never_becomes_zero_forecast(tmp_path, monkeypatch):
    class Response:
        def __init__(self, payload): self.payload = payload
        def json(self): return self.payload
    replies = iter([Response({"results": [{"latitude": 40.7, "longitude": -74, "name": "New York"}]}), Response({"error": "maintenance"})])
    monkeypatch.setattr(engine, "_get", lambda *_args, **_kwargs: next(replies))
    result = engine.fetch_weather(FreshnessStore(tmp_path / "seen.sqlite"), "New York")
    assert not result.articles and result.source_errors


def test_native_separate_change_and_display_counts(swift_harness, tmp_path):
    rows = [dict(article("https://example.com/" + str(i)), change=change, carried=carried) for i, (change, carried) in
            enumerate([("new", False), ("updated", False), ("enriched", False), ("new", True)])]
    path = tmp_path / "articles.json"; path.write_text(json.dumps(rows))
    result = swift(swift_harness, "desk-counts", path)
    assert {k: result[k] for k in ("new", "updated", "carried", "fresh", "displayed")} == dict(new=1, updated=2, carried=1, fresh=3, displayed=4)


@pytest.mark.parametrize("body,expected", [(b'<rss version="2.0"><channel><title>Empty</title></channel></rss>', "success"),
                                           (b'<html><title>Maintenance</title></html>', "unavailable")])
def test_native_rss_coverage_real_http(swift_harness, tmp_path, body, expected):
    with provider(body) as (url, _):
        result = swift(swift_harness, "rss", url, tmp_path / "cache.sqlite", 12, 20)
        assert result["coverage"]["state"] == expected
        assert not result["articles"]


def test_native_snapshot_restores_without_renewing_dates_or_coverage(swift_harness, tmp_path):
    recent = article(); recent.update(first_seen="2026-11-27T10:00:00Z")
    ancient = dict(article("https://example.com/old"), published="", first_seen="2020-01-01T00:00:00Z")
    undated = dict(article("https://example.com/undated"), published="")
    future = dict(article("https://example.com/future"), published="2030-01-01T00:00:00Z")
    payload = {"fetched_at": "2026-11-27T11:00:00Z", "articles": [recent, ancient, undated, future],
               "desks": [{"desk": "science", "coverage": {"sources": [{"source": "Fixture", "provider": "direct", "state": "success", "checked_at": "2026-11-27T11:00:00Z"}]}}]}
    path = tmp_path / "snapshot.json"; path.write_text(json.dumps(payload))
    result = swift(swift_harness, "snapshot", path)
    assert {a["id"] for a in result["articles"]} == {recent["id"], undated["id"]}
    assert all(a["carried"] for a in result["articles"])
    assert result["desks"][0]["fresh"] == 0
    assert result["desks"][0]["coverage"]["state"] == "stale"
    restored_undated = next(a for a in result["articles"] if a["id"] == undated["id"])
    assert restored_undated["first_seen"] == "2026-11-27T11:00:00Z"
    assert result["fetched_at"] == payload["fetched_at"]


def test_native_new_story_does_not_erase_other_current_stories(swift_harness, tmp_path):
    old = article(); replacement = dict(old, summary="Corrected", change="updated")
    old2 = article("https://example.com/retained")
    new = article("https://example.com/new")
    path = tmp_path / "batch.json"; path.write_text(json.dumps({"previous": [old, old2], "fresh": [replacement, new]}))
    rows = swift(swift_harness, "snapshot-merge", path)["articles"]
    assert len(rows) == 3
    assert next(a for a in rows if a["id"] == old["id"])["summary"] == "Corrected"
    assert next(a for a in rows if a["id"] == old2["id"])["carried"] is True
    assert sum(not a["carried"] for a in rows) == 2


@pytest.mark.parametrize('runtime', ['python', 'swift'])
@pytest.mark.parametrize('limit', [0, 1, 20])
def test_independent_desk_caps_preserve_shared_membership(runtime, limit, request, monkeypatch, tmp_path):
    from datetime import timedelta
    # A busy desk fills first, but shared and quiet-desk stories still get their
    # own display allocation. Full source membership remains unchanged.
    fresh = []
    for n in range(25):
        fresh.append(dict(article(f'https://example.com/science/{n}'),
                          id=f'a{n:02}', published=(NOW-timedelta(minutes=n)).isoformat(), desks=['science']))
    shared = dict(article('https://example.com/shared'), id='shared',
                  published=(NOW-timedelta(minutes=30)).isoformat(), desks=['science', 'tech'])
    quiet = dict(article('https://example.com/tech'), id='tech', desk='tech', desks=['tech'],
                 published=(NOW-timedelta(minutes=31)).isoformat())
    old = [shared, quiet] + [dict(a, id='old'+a['id'], published=(NOW-timedelta(hours=1)).isoformat()) for a in fresh]
    original = copy.deepcopy(old + fresh)
    if runtime == 'swift':
        binary = request.getfixturevalue('swift_harness')
        path = tmp_path/'desk-batch.json'
        def merge(previous, fresh):
            path.write_text(json.dumps(dict(previous=previous, fresh=fresh, limit=limit)))
            return swift(binary, 'snapshot-merge', path)['articles']
    else:
        monkeypatch.setattr(server, 'MAX_PER_DESK', limit)
        monkeypatch.setattr(server, 'HOME', tmp_path)
        monkeypatch.setattr(server, 'SHARED', tmp_path/'shared')
        class FixedDate(datetime):
            @classmethod
            def now(cls, tz=None): return NOW
        monkeypatch.setattr(server, 'datetime', FixedDate)
        state = server.NewsState()
        def merge(previous, fresh):
            result = state._keep_live_desks({'articles': previous},
                {'articles': fresh, 'desks': [{'desk': 'science'}, {'desk': 'tech'}]})
            for desk in result['desks']:
                assert desk['displayed'] <= limit
                assert desk['new'] + desk['updated'] + desk['carried'] == desk['displayed']
            assert all(len(items) <= limit for items in result['categories'].values())
            return result['articles']
    rows = merge(old, fresh)
    assert old + fresh == original
    assert len({a['id'] for a in rows}) == len(rows)
    assert sum('science' in a['display_desks'] for a in rows) == min(limit, 25)
    assert sum('tech' in a['display_desks'] for a in rows) == min(limit, 2)
    if limit:
        shared_row = next(a for a in rows if a['id']=='shared')
        assert shared_row['desks'] == ['science', 'tech']
        assert shared_row['display_desks'] == ['tech']
    # Equal-date ordering and display allocations remain stable after restoration.
    restored = merge(list(reversed(rows)), [])
    assert [(a['id'], a['display_desks']) for a in restored] == [(a['id'], a['display_desks']) for a in rows]


def test_native_isolated_app_paths_and_credentials(swift_harness, tmp_path):
    env = dict(os.environ, AGENT_NEWSBOT_HOME=str(tmp_path / "isolated"))
    first = json.loads(subprocess.check_output([str(swift_harness), "paths"], env=env))
    assert first["support"] == str(tmp_path / "isolated")
    assert first["bundle"].startswith("ai.shadowfetch.agentnewsbot.test.")
    env["AGENT_NEWSBOT_HOME"] = str(tmp_path / "other")
    second = json.loads(subprocess.check_output([str(swift_harness), "paths"], env=env))
    assert second["bundle"] != first["bundle"]


def test_linux_new_story_does_not_erase_other_current_stories(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "HOME", tmp_path); monkeypatch.setattr(server, "SHARED", tmp_path / "shared")
    state = server.NewsState()
    stamp = datetime.now(timezone.utc).isoformat()
    old = dict(article(), published=stamp)
    new = dict(article("https://example.com/new"), published=stamp, change="new")
    result = state._keep_live_desks({"articles": [old], "fetched_at": stamp},
                                  {"articles": [new], "fetched_at": stamp, "desks": [{"desk": "science", "fresh": 1}]})
    assert result["total_articles"] == 2
    assert (result["desks"][0]["new"], result["desks"][0]["carried"]) == (1, 1)


def test_native_agent_process_output_failure_and_limit(swift_harness):
    result = swift(swift_harness, "process", "/bin/sh", 3, 1000000, "-c", "printf hello; printf diagnostic >&2")
    assert result == {"output": "hello"}
    failed = swift(swift_harness, "process", "/bin/sh", 3, 1000000, "-c", "printf failure >&2; exit 7")
    assert "exited 7" in failed["error"] and "failure" in failed["error"]
    missing = swift(swift_harness, "process", "/nonexistent/agent", 1, 1000)
    assert "Could not start" in missing["error"]
    flood = swift(swift_harness, "process", "/usr/bin/yes", 3, 10000, "output")
    assert "output limit" in flood["error"]


def test_native_agent_process_timeout_stops_owned_descendants(swift_harness, tmp_path):
    import time
    child_file = tmp_path / "child.pid"
    start = time.monotonic()
    result = swift(swift_harness, "process", "/bin/sh", .2, 10000, "-c", 'sleep 30 & echo $! > "$1"; wait', "fixture", child_file)
    assert "time limit" in result["error"]
    assert time.monotonic() - start < 3
    pid = int(child_file.read_text())
    # An exited child can briefly remain a zombie until launchd reaps it.
    state = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
    assert not state or state.startswith("Z"), state


def test_native_agent_process_cancellation(swift_harness):
    import time
    env = dict(os.environ, ANB_CANCEL_PROCESS="1")
    start = time.monotonic()
    result = json.loads(subprocess.check_output([str(swift_harness), "process", "/bin/sleep", "10", "10000", "20"], env=env))
    assert result["cancelled"] is True
    assert time.monotonic() - start < 3


def test_native_hermes_probe_rejects_unauthorized_api(swift_harness):
    with provider({}, 401) as (base, _):
        assert swift(swift_harness, "hermes-probe", base)["ready"] is False


@pytest.mark.parametrize("mode", ["success", "redirect", "oversize"])
def test_native_hermes_http_handoff_bounds(swift_harness, mode):
    calls = []
    class Agent(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            calls.append((self.path, body))
            self.send_response(302 if mode == "redirect" else 200)
            if mode == "redirect": self.send_header("Location", "/redirected")
            self.end_headers()
            result = b"x" * (2 * 1024 * 1024 + 1) if mode == "oversize" else json.dumps({"choices": [{"message": {"role": "assistant", "content": "Draft accepted"}, "finish_reason": "stop"}]}).encode()
            try: self.wfile.write(result)
            except (BrokenPipeError, ConnectionResetError): pass
        def log_message(self, *args): pass
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Agent)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True); thread.start()
    try:
        result = swift(swift_harness, "hermes-api", f"http://127.0.0.1:{httpd.server_port}")
        assert len(calls) == 1
        assert calls[0][0] == "/v1/chat/completions"
        assert "Prepare drafts only; do not publish" in calls[0][1]["messages"][0]["content"]
        assert result == ({"output": "Draft accepted"} if mode == "success" else {"error": "handoff failed"})
    finally:
        httpd.shutdown(); httpd.server_close(); thread.join()


def test_native_port_probe_invalid_and_listening(swift_harness):
    assert swift(swift_harness, "port-probe", "ws://127.0.0.1:99999")["ready"] is False
    with provider({}) as (base, _):
        assert swift(swift_harness, "port-probe", base)["ready"] is True
    assert swift(swift_harness, "port-probe", base)["ready"] is False


def test_library_search_watchlist_mute_and_persistence(journal_runtime):
    first = article(title="NASA lunar mission from Florida", provider="worldmonitor")
    first["extra"]["metadata"] = {"locationName": "Florida", "tickers": ["SPACE"]}
    second = article("https://second.example", title="Different topic about elections"); second["source"] = "Second"
    third = article("https://third.example", title="Science research on oceans"); third["source"] = "Third"
    rows = journal_runtime.ingest([first, second, third]); sid = next(r["id"] for r in rows if r["title"] == first["title"])
    assert len(journal_runtime.library()["stories"]) == 3
    assert journal_runtime.library({"q": "NASA"})["stories"][0]["id"] == sid
    assert journal_runtime.library({"entity": "Florida", "provider": "worldmonitor", "desk": "science"})["stories"][0]["id"] == sid
    assert len(journal_runtime.library({"source": "second.example"})["stories"]) == 1
    page = journal_runtime.library({"limit": "1"}); assert page["next_offset"] == 1
    assert journal_runtime.library({"limit": "1", "offset": "1"})["stories"][0]["id"] != page["stories"][0]["id"]
    journal_runtime.library_settings("search", "Florida watch", {"entity": "Florida"}, write=True)
    assert journal_runtime.library_settings()["preferences"][0]["value"] == {"entity": "Florida"}
    journal_runtime.library_settings("mute", "Example", True, write=True)
    assert len(journal_runtime.library()["stories"]) == 2
    assert len(journal_runtime.library({"include_muted": "true"})["stories"]) == 3
    assert len(journal_runtime.pending("not-filtered")["events"]) == 3
    journal_runtime.library_settings("mute", "Example", None, write=True)
    assert len(journal_runtime.library()["stories"]) == 3


def test_revision_bound_editorial_approval_and_research_export(journal_runtime):
    row = journal_runtime.ingest([article()])[0]; sid = row["id"]
    state = journal_runtime.save_editorial(sid, {"expected_revision": 1, "expected_editorial_version": 0, "generate": True, "bookmarked": True})
    assert state["editorial"]["status"] == "draft"
    assert set(state["editorial"]["drafts"]) == {"youtube_title", "youtube_description", "x_post", "tiktok_caption", "narration"}
    assert len(journal_runtime.library({"bookmarked": "true"})["stories"]) == 1
    approved = journal_runtime.save_editorial(sid, {"expected_revision": 1, "expected_editorial_version": 1, "status": "approved"})
    assert approved["editorial"]["approved_revision"] == 1
    export = journal_runtime.research_export(sid)
    assert export["approved"] is True and export["videobot"]["action"] == "inspect"
    assert export["source_urls"] == [row["url"]] and export["publishing"] == "manual only"
    changed = article(title="NASA cancels lunar orbiter mission after engine trouble")
    journal_runtime.ingest([changed])
    assert journal_runtime.editorial(sid)["editorial"]["status"] == "needs_review"
    assert journal_runtime.research_export(sid)["approved"] is False
    assert len(journal_runtime.library({"status": "needs_review"})["stories"]) == 1
    assert len(journal_runtime.pending("editorial-is-not-news")["events"]) == 2


@pytest.mark.parametrize("runtime", ["python", "swift"])
def test_library_http_editorial_conflicts_and_validation(runtime, request, tmp_path, monkeypatch):
    with contextlib.ExitStack() as stack:
        if runtime == "swift":
            binary = request.getfixturevalue("swift_harness")
            db = tmp_path / "stories.sqlite"
            data = tmp_path / "rows.json"; data.write_text(json.dumps([article()]))
            row = swift(binary, "ingest", db, data)["articles"][0]
            base, _ = stack.enter_context(native_bridge(binary, db))
            token = "integration-test-token"
        else:
            monkeypatch.setattr(server, "HOME", tmp_path); monkeypatch.setattr(server, "SHARED", tmp_path / "shared")
            state = server.NewsState(); monkeypatch.setattr(server, "STATE", state)
            row = state.stories.ingest([article()])[0]
            httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
            thread = threading.Thread(target=httpd.serve_forever, daemon=True); thread.start()
            def stop(): httpd.shutdown(); httpd.server_close(); thread.join()
            stack.callback(stop); base = f"http://127.0.0.1:{httpd.server_port}"; token = state.token
        headers = {"Authorization": "Bearer " + token}
        route = base + "/v1/library/editorial?id=" + row["id"]
        for bad_status in ([], {}, True, None):
            invalid = requests.post(route, headers=headers, json={"expected_revision": 1, "expected_editorial_version": 0, "status": bad_status}, timeout=5)
            assert invalid.status_code == 400
        assert requests.get(route, headers=headers, timeout=5).json()["story"]["editorial"]["version"] == 0
        assert requests.get(base + "/v1/library", timeout=5).status_code == 401
        assert requests.get(base + "/v1/library?limit=0", headers=headers, timeout=5).status_code == 400
        assert requests.get(base + "/v1/library?bookmarked=yes", headers=headers, timeout=5).status_code == 400
        first = {"expected_revision": 1, "expected_editorial_version": 0, "generate": True}
        response = requests.post(route, headers=headers, json=first, timeout=5)
        assert response.status_code == 200, response.text
        assert requests.post(route, headers=headers, json=first, timeout=5).status_code == 409
        drafts = response.json()["story"]["editorial"]["drafts"]
        drafts["x_post"] = "中" * 150
        invalid = requests.post(route, headers=headers, json={"expected_revision": 1, "expected_editorial_version": 1, "drafts": drafts}, timeout=5)
        assert invalid.status_code == 400
        saved = requests.get(route, headers=headers, timeout=5).json()["story"]["editorial"]
        assert saved["version"] == 1 and saved["drafts"]["x_post"] != drafts["x_post"]
        assert requests.post(route, headers=headers, data=b"[]", timeout=5).status_code == 400
        assert requests.post(route, headers=headers, data=b"{" + b" " * 66000, timeout=5).status_code == 400
        assert requests.post(base + "/v1/library", headers=headers, json={}, timeout=5).status_code == 405
        assert requests.get(base + "/v1/library/editorial?id=missing", headers=headers, timeout=5).status_code == 404
        assert requests.post(base + "/v1/library/settings", headers=headers, json={"kind": "search", "name": "NASA", "value": {"q": "NASA"}}, timeout=5).status_code == 200
        assert requests.get(base + "/v1/library/settings", headers=headers, timeout=5).json()["preferences"][0]["name"] == "NASA"
        exported = requests.get(base + "/v1/library/export?id=" + row["id"], headers=headers, timeout=5).json()
        assert exported["publishing"] == "manual only" and exported["approved"] is False


def test_library_files_export_private_and_no_overwrite(journal_runtime, tmp_path):
    from agent_newsbot.library import write_package
    row = journal_runtime.ingest([article()])[0]; sid = row["id"]
    journal_runtime.save_editorial(sid, {"expected_revision": 1, "expected_editorial_version": 0, "generate": True})
    parent = tmp_path / "exports"
    def export():
        if hasattr(journal_runtime, "op"):
            return Path(journal_runtime.op({"op": "export-files", "id": sid, "destination": str(parent)})["folder"])
        return write_package(journal_runtime.research_export(sid), parent)
    first, second = export(), export()
    assert first != second and first.is_dir() and second.is_dir()
    assert {p.name for p in first.iterdir()} == {"research.json", "sources.txt", "posts.md"}
    assert json.loads((first / "research.json").read_text())["story"]["id"] == sid
    assert (first / "sources.txt").read_text().strip() == row["url"]
    assert "youtube_title" in (first / "posts.md").read_text()
    assert first.stat().st_mode & 0o777 == 0o700
    assert all(p.stat().st_mode & 0o777 == 0o600 for p in first.iterdir())
    assert not list(parent.glob(".research-*"))


def test_library_cli_from_other_directory_and_conflict(tmp_path):
    home = tmp_path / 'news data'
    env = dict(os.environ, AGENT_NEWSBOT_HOME=str(home), PYTHONPATH=str(ROOT / 'linux'))
    def cli(*args):
        return subprocess.run([sys.executable, '-m', 'agent_newsbot', *args], cwd=tmp_path,
                              env=env, text=True, capture_output=True, timeout=20)
    missing = cli('library')
    assert missing.returncode == 1 and 'fetch first' in missing.stderr
    assert not home.exists()
    row = StoryStore(home / 'stories.sqlite').ingest([article()])[0]
    result = cli('library', '--entity', 'NASA', '--limit', '1')
    assert result.returncode == 0 and json.loads(result.stdout)['stories'][0]['id'] == row['id']
    changes = tmp_path / 'changes.json'
    changes.write_text(json.dumps({'expected_revision': 1, 'expected_editorial_version': 0, 'generate': True}))
    saved = cli('editorial', row['id'], '--changes', str(changes))
    assert saved.returncode == 0 and json.loads(saved.stdout)['editorial']['version'] == 1
    stale = cli('editorial', row['id'], '--changes', str(changes))
    assert stale.returncode == 1 and 'reload' in stale.stderr and 'Traceback' not in stale.stderr
    exported = cli('export-research', row['id'], '--destination', str(tmp_path / 'exports'))
    assert exported.returncode == 0
    folder = Path(json.loads(exported.stdout)['folder'])
    assert (folder / 'sources.txt').read_text().strip() == row['url']
    changes.write_text('[]')
    invalid = cli('preferences', '--changes', str(changes))
    assert invalid.returncode == 1 and 'JSON object' in invalid.stderr
    changes.write_text(json.dumps({'consumer':'cli-reader','action':'create','start':'now'}))
    created = cli('consumers', '--changes', str(changes))
    assert created.returncode == 0 and json.loads(created.stdout)['generation'] == 1
    assert json.loads(cli('consumers').stdout)['consumers'][0]['lag'] == 0
    assert cli('consumers', '--changes', str(changes)).returncode == 1
    (home / 'stories.sqlite').write_bytes(b'invalid sqlite file')
    broken = cli('library')
    assert broken.returncode == 1 and 'Traceback' not in broken.stderr


@pytest.mark.parametrize('runtime', ['python', 'swift'])
def test_consumer_controls_fence_replay_and_worker_leases(runtime, request, tmp_path, monkeypatch):
    import sqlite3
    with contextlib.ExitStack() as stack:
        rows = [article('https://example.com/' + str(i), 'Distinct story ' + str(i) + ' ' + ('ocean' if i==0 else 'election' if i==1 else 'space')) for i in range(3)]
        db = tmp_path / 'stories.sqlite'
        if runtime == 'swift':
            binary = request.getfixturevalue('swift_harness')
            data = tmp_path / 'rows.json'; data.write_text(json.dumps(rows))
            swift(binary, 'ingest', db, data)
            base, _ = stack.enter_context(native_bridge(binary, db)); token = 'integration-test-token'
        else:
            monkeypatch.setattr(server, 'HOME', tmp_path); monkeypatch.setattr(server, 'SHARED', tmp_path/'shared')
            state = server.NewsState(); monkeypatch.setattr(server, 'STATE', state); state.stories.ingest(rows)
            httpd = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
            thread = threading.Thread(target=httpd.serve_forever, daemon=True); thread.start()
            def stop(): httpd.shutdown(); httpd.server_close(); thread.join()
            stack.callback(stop); base=f'http://127.0.0.1:{httpd.server_port}'; token=state.token
        headers={'Authorization':'Bearer '+token}
        with sqlite3.connect(db) as conn:
            assert conn.execute('SELECT count(*) FROM events').fetchone()[0] == 3
            for seq in range(1,4):
                payload=json.loads(conn.execute('SELECT payload FROM events WHERE seq=?',(seq,)).fetchone()[0])
                payload['updated_at']=f'2026-01-0{seq}T12:00:00Z'
                conn.execute('UPDATE events SET payload=? WHERE seq=?',(json.dumps(payload),seq))
        def change(action, expected=0, **other):
            return requests.post(base+'/v1/consumers',headers=headers,json=dict(consumer='worker',action=action,expected_generation=expected,**other),timeout=5)
        def pending(lease=None, **extra):
            return requests.get(base+'/v1/events',headers=dict(headers,**({'X-Consumer-Lease':lease} if lease else {})),params=dict(consumer='worker',**extra),timeout=5)
        def ack(gen=None, lease=None, through=3):
            params={'consumer':'worker','through':through}
            if gen is not None: params['generation']=gen
            return requests.post(base+'/v1/events/ack',headers=dict(headers,**({'X-Consumer-Lease':lease} if lease else {})),params=params,timeout=5)
        assert requests.get(base+'/v1/consumers',timeout=5).status_code==401
        assert change('create',start='history').json()['generation']==1
        assert change('create',start='now').status_code==409
        assert pending(desk='science').status_code==400
        assert pending().json()['count']==3
        assert ack().status_code==409
        assert ack(1).json()['cursor']==3
        status=requests.get(base+'/v1/consumers',headers=headers,timeout=5).json()['consumers'][0]
        assert status['lag']==0 and status['last_ack_at']>0
        assert change('reset',expected=1,start='history').json()['generation']==2
        assert ack(1).status_code==409
        assert pending().json()['count']==3
        acquired=change('lease',expected=2,worker='process-a',ttl_seconds=10)
        assert acquired.status_code==200, acquired.text
        lease=acquired.json()['lease']; assert acquired.json()['generation']==3
        assert ack(3,lease).status_code==400  # old offered range was fenced out
        assert pending().status_code==409
        assert pending('wrong').status_code==409
        assert pending(lease).json()['generation']==3
        assert change('lease',expected=3,worker='process-b').status_code==409
        assert change('reset',expected=3,start='now').status_code==409
        assert change('renew',expected=3,lease=lease,ttl_seconds=20).json()['lease']==lease
        assert ack(3,lease).status_code==200
        public=requests.get(base+'/v1/consumers',headers=headers,timeout=5).text
        assert lease not in public and 'process-a' in public
        assert change('release',expected=3,lease=lease).json()['generation']==4
        assert ack(3,lease).status_code==409
        new=change('lease',expected=4,worker='process-b').json()
        assert new['generation']==5
        with sqlite3.connect(db) as conn: conn.execute('UPDATE consumer_meta SET lease_until=0 WHERE name=?',('worker',))
        assert pending(new['lease']).status_code==409
        assert change('renew',expected=5,lease=new['lease']).status_code==409
        assert change('reset',expected=5,start='date',since='2026-01-02T12:00:00Z').json()['cursor']==1
        assert pending().json()['count']==2
        assert change('reset',expected=6,start='date',since='not-a-date').status_code==400
        assert change('reset',expected=6,start='now').json()['cursor']==3
        assert pending().json()['count']==0
        assert change('reset',expected=6,start='history').status_code==409


def test_story_database_is_private(journal_runtime):
    journal_runtime.ingest([article()])
    assert journal_runtime.path.stat().st_mode & 0o777 == 0o600


def test_token_creation_is_atomic_private_and_does_not_replace_invalid_file(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    state=object.__new__(server.NewsState); state.token_path=tmp_path/'export/bridge.token'
    with ThreadPoolExecutor(max_workers=8) as workers:
        tokens=list(workers.map(lambda _:state._load_or_create_token(),range(32)))
    assert len(set(tokens))==1 and len(tokens[0])==64
    assert state.token_path.stat().st_mode&0o777==0o600
    assert not list(state.token_path.parent.glob('.bridge-token-*'))
    state.token_path.write_text('bad')
    with pytest.raises(ValueError,match='invalid bridge token'): state._load_or_create_token()
    assert state.token_path.read_text()=='bad'


def test_native_snapshot_atomic_failure_preservation_and_retry(swift_harness, tmp_path):
    destination = tmp_path/'exports'; destination.mkdir()
    latest = destination/'latest.json'
    latest.write_text('{"original": true}')
    original = latest.read_bytes()
    for mode in ['full', 'large']:
        result = swift(swift_harness, 'snapshot-save', destination, mode)
        assert 'error' in result
        assert latest.read_bytes() == original
        assert sorted(p.name for p in destination.iterdir()) == ['latest.json']
    first = swift(swift_harness, 'snapshot-save', destination, 'retry')
    assert json.loads(latest.read_text()) == {'value': 'retry'}
    archive = Path(first['archive'])
    assert archive.read_bytes() == latest.read_bytes()
    assert archive.stat().st_mode & 0o777 == 0o600
    assert latest.stat().st_mode & 0o777 == 0o600
    second = swift(swift_harness, 'snapshot-save', destination, 'next')
    assert first['archive'] != second['archive']
    assert json.loads(archive.read_text()) == {'value': 'retry'}
    assert not list(destination.glob('.snapshot-*'))


@pytest.mark.parametrize('kind', ['directory', 'symlink'])
def test_native_snapshot_invalid_destination_preserved(swift_harness, tmp_path, kind):
    destination = tmp_path/'exports'; destination.mkdir()
    latest = destination/'latest.json'
    sentinel = tmp_path/'sentinel'; sentinel.write_text('preserve me')
    if kind == 'directory': latest.mkdir()
    else: latest.symlink_to(sentinel)
    result = swift(swift_harness, 'snapshot-save', destination)
    assert 'error' in result
    assert sentinel.read_text() == 'preserve me'
    assert latest.is_dir() if kind == 'directory' else latest.is_symlink()
    assert len(list(destination.iterdir())) == 1


@pytest.mark.parametrize('body,can_send', [({}, False), ({'ok': True}, False),
    ({'object':'list','data':[]}, False),
    ({'object':'list','data':[{'id':'different-model'}]}, False),
    ({'object':'list','data':[{'id':'hermes-agent'}]}, True)])
def test_agent_readiness_requires_compatible_model_list(swift_harness, body, can_send):
    with provider(body) as (base, calls):
        result = swift(swift_harness, 'hermes-probe', base)
        assert result['ready'] is False  # Discovery never claims an actual reply.
        assert result['can_send'] is can_send
        assert [path for path, _ in calls] == ['/v1/models']


@pytest.mark.parametrize('value', [{}, {'error':{'message':'failed'}}, {'choices':[]},
    {'choices':[{'message':{'role':'assistant','content':''},'finish_reason':'stop'}]},
    {'choices':[{'message':{'role':'assistant','content':'   '},'finish_reason':'stop'}]},
    {'choices':[{'message':{'role':'user','content':'echoed'},'finish_reason':'stop'}]},
    {'choices':[{'message':{'role':'assistant','content':'diagnostic'},'finish_reason':'error'}]},
    {'choices':[{'message':{'role':'assistant','content':'partial'},'finish_reason':'length'}]},
    {'choices':[{'message':{'role':'assistant','content':'cancelled'},'finish_reason':'cancelled'}]}])
def test_agent_invalid_and_incomplete_completion_is_not_success(swift_harness, value):
    with agent_response(value) as (base, _):
        assert 'error' in swift(swift_harness, 'hermes-api', base)


@contextmanager
def agent_response(value):
    calls=[]
    class Agent(BaseHTTPRequestHandler):
        def do_POST(self):
            calls.append((self.path, json.loads(self.rfile.read(int(self.headers['Content-Length'])))))
            self.send_response(200); self.end_headers()
            self.wfile.write(json.dumps(value).encode())
        def log_message(self,*args): pass
    httpd=ThreadingHTTPServer(('127.0.0.1',0),Agent)
    thread=threading.Thread(target=httpd.serve_forever,daemon=True); thread.start()
    try: yield f'http://127.0.0.1:{httpd.server_port}', calls
    finally: httpd.shutdown(); httpd.server_close(); thread.join()


@pytest.mark.parametrize('reply,passed', [('NEWSBOT_CONNECTION_OK',True), ('something else',False)])
def test_agent_explicit_reply_test_and_v1_base(swift_harness, reply, passed):
    value={'choices':[{'message':{'role':'assistant','content':reply},'finish_reason':'stop'}]}
    with agent_response(value) as (base,calls):
        result=swift(swift_harness,'hermes-test',base+'/v1','custom-profile')
        assert result.get('tested',False) is passed
        assert calls[0][0]=='/v1/chat/completions'
        assert calls[0][1]['model']=='custom-profile'
        assert calls[0][1]['stream'] is False
        assert 'Do not use tools' in calls[0][1]['messages'][0]['content']


@pytest.mark.parametrize('runtime',['python','swift'])
@pytest.mark.parametrize('mode',['oversize','compressed','trickle','redirect_loop'])
def test_direct_provider_stream_bounds(runtime, mode, request):
    import gzip, time
    from agent_newsbot import http_client
    sent=[]; calls=[]
    class Source(BaseHTTPRequestHandler):
        def do_GET(self):
            calls.append(self.path)
            if mode=='redirect_loop':
                self.send_response(302); self.send_header('Location','/loop'); self.end_headers(); return
            self.send_response(200)
            if mode=='compressed': self.send_header('Content-Encoding','gzip')
            self.end_headers()
            try:
                if mode=='compressed':
                    self.wfile.write(gzip.compress(b'x'*(6*1024*1024))); return
                if mode=='trickle':
                    for _ in range(80):
                        self.wfile.write(b'x'); self.wfile.flush(); time.sleep(.05)
                    return
                for _ in range(96):
                    self.wfile.write(b'x'*65536); self.wfile.flush(); sent.append(65536); time.sleep(.005)
            except (BrokenPipeError, ConnectionResetError): pass
        def log_message(self,*args): pass
    httpd=ThreadingHTTPServer(('127.0.0.1',0),Source)
    thread=threading.Thread(target=httpd.serve_forever,daemon=True); thread.start()
    binary=request.getfixturevalue('swift_harness') if runtime=='swift' else None
    base=f'http://127.0.0.1:{httpd.server_port}'
    timeout=0.8 if mode=='trickle' else 8
    start=time.monotonic()
    try:
        if runtime=='swift': assert 'error' in swift(binary,'http-get',base,timeout)
        else:
            with pytest.raises(http_client.RequestFailure): engine._get(base,timeout=timeout)
        if mode=='trickle': assert time.monotonic()-start < 3
        if mode=='oversize': assert sum(sent) < 6*1024*1024
        if mode=='redirect_loop': assert len(calls)<=6
    finally: httpd.shutdown(); httpd.server_close(); thread.join()


def test_direct_provider_timeout_reaps_worker(monkeypatch):
    from agent_newsbot import http_client
    import time
    class Slow(BaseHTTPRequestHandler):
        def do_GET(self): time.sleep(2)
        def log_message(self,*args): pass
    httpd=ThreadingHTTPServer(('127.0.0.1',0),Slow)
    thread=threading.Thread(target=httpd.serve_forever,daemon=True); thread.start()
    spawned=[]; original=subprocess.Popen
    def tracked(*args,**kwargs):
        child=original(*args,**kwargs); spawned.append(child); return child
    monkeypatch.setattr(http_client.subprocess,'Popen',tracked)
    try:
        with pytest.raises(http_client.RequestFailure) as exc: http_client.get(f'http://127.0.0.1:{httpd.server_port}',timeout=.5)
        assert exc.value.code=='total_timeout'
        assert len(spawned)==1 and spawned[0].poll() is not None
    finally: httpd.shutdown(); httpd.server_close(); thread.join()


@pytest.mark.parametrize('status',[401,403,404,429])
def test_agent_probe_actionable_http_errors(swift_harness,status):
    with provider({'error':'do not echo provider body'}, status) as (base,_):
        result=swift(swift_harness,'hermes-probe',base)
        assert result['ready'] is False and result['can_send'] is False
        assert str(status) in result['detail']
        assert 'do not echo' not in result['detail']


def test_direct_provider_http_error_retains_retry_after():
    from agent_newsbot import http_client
    class Limited(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(429); self.send_header('Retry-After','60'); self.end_headers()
            self.wfile.write(b'private diagnostic')
        def log_message(self,*args): pass
    httpd=ThreadingHTTPServer(('127.0.0.1',0),Limited)
    thread=threading.Thread(target=httpd.serve_forever,daemon=True); thread.start()
    try:
        with pytest.raises(http_client.RequestFailure) as exc: engine._get(f'http://127.0.0.1:{httpd.server_port}')
        assert (exc.value.status,exc.value.retry_after)==(429,'60')
        assert 'private' not in str(exc.value)
    finally: httpd.shutdown(); httpd.server_close(); thread.join()
